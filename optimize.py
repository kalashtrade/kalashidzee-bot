"""Подбор параметров с проверкой на невиданных данных (walk-forward).
Первые 60% истории: перебор вариантов и выбор лучших. Последние 40%: честная проверка выбранных.
Запуск: python optimize.py --days 270
"""
import argparse, itertools
import numpy as np
import pandas as pd
import config, okx
from strategy import Params, Trade

FEE = config.FEE_PCT
TP_SETS = {"1-2-3": ((1, 2, 3), (.5, .3, .2)), "1-2-4 тянем": ((1, 2, 4), (.3, .3, .4)), "2-3-5": ((2, 3, 5), (.5, .25, .25))}


def resample_1h(df):
    g = df.assign(h=df["ts"] // 3600000)
    out = g.groupby("h").agg(ts=("ts", "first"), open=("open", "first"), high=("high", "max"),
                             low=("low", "min"), close=("close", "last"), vol=("vol", "sum"), n=("ts", "count"))
    return out[out["n"] == 4].drop(columns="n").reset_index(drop=True)


def prep(df, atr_len=14, ema_len=200, vol_len=20):
    d = df.copy().reset_index(drop=True)
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(atr_len).mean()
    d["ema"] = d["close"].ewm(span=ema_len, adjust=False).mean()
    d["vol_ma"] = d["vol"].rolling(vol_len).mean().shift(1)
    return d


def candidates(d, p):
    """Векторный поиск сигналов (те же правила, что в strategy.signal_at)."""
    R = d["high"].rolling(p.range_bars).max().shift(1)
    S = d["low"].rolling(p.range_bars).min().shift(1)
    width = (R - S) / S * 100
    volx = d["vol"] / d["vol_ma"]
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    pos = (d["close"] - d["low"]) / rng
    buf = p.break_buffer_pct / 100
    base = (width <= p.max_range_pct) & (volx >= p.vol_mult)
    lg = base & (d["close"] > R * (1 + buf)) & (pos >= p.close_strength)
    sh = base & (d["close"] < S * (1 - buf)) & (pos <= 1 - p.close_strength)
    if p.use_trend:
        lg &= d["close"] > d["ema"]
        sh &= d["close"] < d["ema"]
    lstop = R - p.stop_atr * d["atr"]
    sstop = S + p.stop_atr * d["atr"]
    first = max(p.range_bars, p.ema_len // 2, p.vol_len + 1)
    out = []
    for i in np.flatnonzero((lg | sh).to_numpy()):
        if i < first:
            continue
        if lg.iloc[i]:
            out.append((i, "long", float(R.iloc[i]), float(lstop.iloc[i]), float(volx.iloc[i]), float(width.iloc[i])))
        else:
            out.append((i, "short", float(S.iloc[i]), float(sstop.iloc[i]), float(volx.iloc[i]), float(width.iloc[i])))
    return out


def run_pair(d, p, retest, max_hold, lo, hi, floor=0.0):
    """Прогон по куску истории [lo, hi). Возвращает список R сделок."""
    H, L, C, T = d["high"].to_numpy(), d["low"].to_numpy(), d["close"].to_numpy(), d["ts"].to_numpy()
    res, busy = [], -1
    for (i, side, level, stop, volx, width) in candidates(d, p):
        if i < lo or i >= hi or i <= busy:
            continue
        sgn = 1 if side == "long" else -1
        start = i
        if retest:
            entry = None
            for j in range(i + 1, min(i + 9, len(d))):
                if (side == "long" and L[j] <= stop) or (side == "short" and H[j] >= stop):
                    break
                touch = level * (1 + sgn * 0.0005)
                if (side == "long" and L[j] <= touch) or (side == "short" and H[j] >= touch):
                    entry, start = touch, j
                    break
            if entry is None:
                continue
        else:
            entry = float(C[i])
        risk = (entry - stop) * sgn
        if risk <= 0:
            continue
        stop_pct = risk / entry * 100
        if stop_pct < p.min_stop_pct or stop_pct > p.max_stop_pct:
            continue
        tps = [entry + sgn * risk * r for r in p.tp_r]
        if floor and stop_pct < floor:
            stop = entry - sgn * entry * floor / 100
            stop_pct = floor
        t = Trade(dict(side=side, entry=entry, stop=stop, tps=tps, ts=int(T[start])), p)
        last = min(len(d) - 1, start + max_hold)
        j = start
        for j in range(start + 1, last + 1):
            t.on_bar(int(T[j]), float(H[j]), float(L[j]), float(C[j]))
            if t.d["closed"]:
                break
        if not t.d["closed"]:
            t.force_close(float(C[last]))
        res.append((int(T[start]), t.d["r"] - 2 * FEE / stop_pct))
        busy = j
    return res


def stats(rs):
    r = np.array([x[1] for x in sorted(rs)]) if rs else np.array([])
    if len(r) == 0:
        return dict(n=0, avg=0, pf=0, total=0, dd=0, win=0)
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    eq = np.cumsum(r)
    return dict(n=len(r), avg=r.mean(), pf=(gp / gl if gl > 0 else 9.99), total=r.sum(),
                dd=(np.maximum.accumulate(eq) - eq).max(), win=(r > 0).mean() * 100)


def fmt(s):
    return f"сделок {s['n']:3d}, винрейт {s['win']:2.0f}%, средний R {s['avg']:+.2f}, PF {s['pf']:.2f}, сумма {s['total']:+.1f}R, просадка {s['dd']:.1f}R"


def make_params(bar, width, volx, stop_atr, tps, trend):
    tp_r, split = TP_SETS[tps]
    if bar == "15m":
        return Params(range_bars=96, max_range_pct=width, vol_mult=volx, stop_atr=stop_atr, tp_r=tp_r, tp_split=split,
                      use_trend=trend, ema_len=200, max_stop_pct=3.0), 192
    return Params(range_bars=24, max_range_pct=width, vol_mult=volx, stop_atr=stop_atr, tp_r=tp_r, tp_split=split,
                  use_trend=trend, ema_len=100, max_stop_pct=4.0), 48


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=270)
    ap.add_argument("--symbols", nargs="*", default=config.SYMBOLS)
    ap.add_argument("--train", type=float, default=0.6)
    a = ap.parse_args()
    data = {}
    for s in a.symbols:
        try:
            raw = okx.history(config.inst(s), "15m", a.days)
        except Exception as e:
            print(s, "пропуск:", e)
            continue
        data[s] = {"15m": prep(raw), "1h": prep(resample_1h(raw), ema_len=100)}
        print(s, "загружено свечей 15м:", len(raw), flush=True)
    if not data:
        print("Нет данных"); return
    t0 = min(d["15m"]["ts"].iloc[0] for d in data.values())
    t1 = max(d["15m"]["ts"].iloc[-1] for d in data.values())
    cut = t0 + (t1 - t0) * a.train
    print(f"\nОбучение: до {pd.to_datetime(cut, unit='ms'):%Y-%m-%d}, проверка: после (данные, которых оптимизатор не видел)\n")

    def evaluate(cfg, part):
        bar, width, volx, stop_atr, tps, trend, retest = cfg
        p, mh = make_params(bar, width, volx, stop_atr, tps, trend)
        allr = []
        for s, dd in data.items():
            d = dd[bar]
            ts = d["ts"].to_numpy()
            if part == "train":
                lo, hi = 0, int(np.searchsorted(ts, cut))
            else:
                lo, hi = int(np.searchsorted(ts, cut)), len(d)
            allr += run_pair(d, p, retest, mh, lo, hi)
        return stats(allr)

    grid = list(itertools.product(["15m", "1h"], [3.0, 5.0], [1.5, 2.5], [0.5, 1.0], list(TP_SETS), [True, False], [False, True]))
    print(f"Перебираю {len(grid)} вариантов на обучающей части...", flush=True)
    rows = []
    for k, cfg in enumerate(grid):
        s = evaluate(cfg, "train")
        rows.append((cfg, s))
        if (k + 1) % 32 == 0:
            print(f"  {k + 1}/{len(grid)}", flush=True)
    base = (("15m", 5.0, 1.5, 0.5, "1-2-3", True, False))
    ok = [r for r in rows if r[1]["n"] >= 30]
    ok.sort(key=lambda r: r[1]["avg"], reverse=True)
    top = ok[:5]
    names = lambda c: f"ТФ {c[0]}, флэт≤{c[1]}%, объём×{c[2]}, стоп {c[3]}ATR, цели {c[4]}, тренд={'да' if c[5] else 'нет'}, вход={'ретест' if c[6] else 'по закрытию'}"
    print("\n=== БАЗОВЫЕ ПАРАМЕТРЫ БОТА ===")
    print("обучение:", fmt(evaluate(base, "train")))
    print("проверка:", fmt(evaluate(base, "test")))
    print("\n=== ТОП-5 ПО ОБУЧЕНИЮ И ИХ ПРОВЕРКА НА НЕВИДАННЫХ ДАННЫХ ===")
    good = []
    out = []
    for cfg, st in top:
        te = evaluate(cfg, "test")
        print("\n" + names(cfg))
        print("  обучение:", fmt(st))
        print("  ПРОВЕРКА:", fmt(te))
        out.append(dict(config=names(cfg), **{f"train_{k}": v for k, v in st.items()}, **{f"test_{k}": v for k, v in te.items()}))
        if te["n"] >= 30 and te["avg"] > 0.10 and te["pf"] > 1.2:
            good.append((cfg, te))
    pd.DataFrame(out).to_csv("optimize_results.csv", index=False)
    print("\n=== ВЕРДИКТ ===")
    if good:
        print(f"Выдержали проверку: {len(good)} из {len(top)}. Это кандидаты, а не гарантия: дальше нужен демо-счёт.")
        for cfg, te in good:
            print(" +", names(cfg))
    elif not top:
        print("Слишком мало сделок на обучающей части (нужно 30+ на вариант). Увеличь --days.")
    else:
        print("Ни один вариант не показал устойчивый плюс на проверочных данных (нужно: 30+ сделок, средний R > 0.10, PF > 1.2).")
        print("Это значит, что пробой флэта в такой форме не даёт преимущества. Торговать по нему нельзя.")


if __name__ == "__main__":
    main()
