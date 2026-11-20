"""Бэктест стратегии на истории OKX.
Пример:  python backtest.py --days 90
         python backtest.py --days 120 --symbols BTC ETH SOL HYPE --no-trend --max-range 6
"""
import argparse
import pandas as pd
import config
from strategy import Params, prepare, signal_at, simulate
import okx


def run_symbol(df, p, fee):
    d = prepare(df, p)
    trades, i, n = [], 0, len(d)
    while i < n - 2:
        sig = signal_at(d, i, p)
        if sig:
            r, bars, out = simulate(d, i, sig, p, fee)
            trades.append(dict(ts=pd.to_datetime(sig["ts"], unit="ms"), side=sig["side"], entry=sig["entry"],
                               stop=sig["stop"], stop_pct=round(sig["stop_pct"], 2), vol_x=round(sig["vol_x"], 2),
                               R=round(r, 3), bars=bars, outcome=out))
            i += max(bars, 1) + 1  # пока сделка живёт, новых не открываем
        else:
            i += 1
    return trades


def summarize(t: pd.DataFrame, title="ИТОГО"):
    if t.empty:
        print(f"{title}: сделок нет")
        return
    win = (t["R"] > 0).mean() * 100
    gp, gl = t.loc[t["R"] > 0, "R"].sum(), -t.loc[t["R"] < 0, "R"].sum()
    pf = gp / gl if gl > 0 else float("inf")
    eq = t.sort_values("ts")["R"].cumsum()
    dd = (eq.cummax() - eq).max()
    print(f"{title}: сделок {len(t)}, винрейт {win:.0f}%, средний R {t['R'].mean():.2f}, сумма R {t['R'].sum():.1f}, "
          f"profit factor {pf:.2f}, макс. просадка {dd:.1f}R")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--symbols", nargs="*", default=config.SYMBOLS)
    ap.add_argument("--no-trend", action="store_true")
    ap.add_argument("--max-range", type=float, default=None)
    ap.add_argument("--vol-mult", type=float, default=None)
    ap.add_argument("--fee", type=float, default=config.FEE_PCT)
    ap.add_argument("--csv", default="backtest_trades.csv")
    a = ap.parse_args()
    kw = dict(config.STRATEGY)
    if a.no_trend: kw["use_trend"] = False
    if a.max_range: kw["max_range_pct"] = a.max_range
    if a.vol_mult: kw["vol_mult"] = a.vol_mult
    p = Params(**kw)
    allt = []
    for s in a.symbols:
        try:
            df = okx.history(config.inst(s), config.BAR, a.days)
        except Exception as e:
            print(f"{s}: пропуск ({e})")
            continue
        tr = run_symbol(df, p, a.fee)
        t = pd.DataFrame(tr)
        if not t.empty:
            t.insert(0, "symbol", s)
            allt.append(t)
        summarize(t, f"{s:5s}")
    if allt:
        T = pd.concat(allt, ignore_index=True)
        T.to_csv(a.csv, index=False)
        print("-" * 70)
        summarize(T)
        print("\nПо сторонам:")
        for side, g in T.groupby("side"):
            summarize(g, side)
        print(f"\nСделки сохранены в {a.csv}. Комиссия учтена: {a.fee}% за сторону.")
    else:
        print("Сделок не найдено. Попробуй --max-range 7 или --vol-mult 1.2")


if __name__ == "__main__":
    main()
