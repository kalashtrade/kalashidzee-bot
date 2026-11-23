"""Стратегия пробоя флэта (реконструкция по скринам канала, это гипотеза, а не гарантия)."""
from dataclasses import dataclass, field, asdict
import numpy as np
import pandas as pd


@dataclass
class Params:
    range_bars: int = 96
    max_range_pct: float = 5.0
    break_buffer_pct: float = 0.10
    vol_mult: float = 1.5
    vol_len: int = 20
    close_strength: float = 0.65
    atr_len: int = 14
    stop_atr: float = 0.5
    min_stop_pct: float = 0.3
    max_stop_pct: float = 3.0
    use_trend: bool = True
    ema_len: int = 200
    tp_r: tuple = (1.0, 2.0, 3.0)
    tp_split: tuple = (0.5, 0.3, 0.2)


def prepare(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """df: колонки ts (мс), open, high, low, close, vol; только закрытые свечи, по возрастанию."""
    d = df.copy().reset_index(drop=True)
    prev_close = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev_close).abs(), (d["low"] - prev_close).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(p.atr_len).mean()
    d["ema"] = d["close"].ewm(span=p.ema_len, adjust=False).mean()
    d["vol_ma"] = d["vol"].rolling(p.vol_len).mean().shift(1)
    return d


def signal_at(d: pd.DataFrame, i: int, p: Params):
    """Проверка сигнала на закрытой свече с индексом i. Возвращает dict или None."""
    if i < max(p.range_bars, p.ema_len // 2, p.vol_len + 1):
        return None
    win = d.iloc[i - p.range_bars:i]
    R, S = float(win["high"].max()), float(win["low"].min())
    width = (R - S) / S * 100
    if width > p.max_range_pct:
        return None
    row = d.iloc[i]
    o, h, l, c, v = (float(row[k]) for k in ("open", "high", "low", "close", "vol"))
    atr, ema, vma = float(row["atr"]), float(row["ema"]), float(row["vol_ma"])
    if np.isnan(atr) or np.isnan(vma) or vma <= 0 or h == l:
        return None
    vol_x = v / vma
    if vol_x < p.vol_mult:
        return None
    pos_in_bar = (c - l) / (h - l)
    buf = p.break_buffer_pct / 100

    side = None
    if c > R * (1 + buf) and pos_in_bar >= p.close_strength and (not p.use_trend or c > ema):
        side, level = "long", R
        stop = R - p.stop_atr * atr
    elif c < S * (1 - buf) and pos_in_bar <= 1 - p.close_strength and (not p.use_trend or c < ema):
        side, level = "short", S
        stop = S + p.stop_atr * atr
    if side is None:
        return None
    entry = c
    risk = abs(entry - stop)
    stop_pct = risk / entry * 100
    if stop_pct < p.min_stop_pct or stop_pct > p.max_stop_pct:
        return None
    sgn = 1 if side == "long" else -1
    tps = [entry + sgn * risk * r for r in p.tp_r]
    return dict(side=side, entry=entry, stop=stop, tps=tps, level=level, range_pct=width,
                vol_x=vol_x, stop_pct=stop_pct, ts=int(row["ts"]), atr=atr)


class Trade:
    """Сопровождение сделки: TP1 -> стоп в безубыток, TP2, TP3. Если в одной свече и стоп, и цель, считаем стоп (консервативно)."""

    def __init__(self, sig: dict, p: Params, symbol: str = ""):
        self.d = dict(sig)
        self.d.update(symbol=symbol, cur_stop=sig["stop"], hit=0, remaining=1.0, r=0.0, closed=False,
                      tp_r=list(p.tp_r), split=list(p.tp_split), last_ts=sig["ts"])

    @classmethod
    def from_dict(cls, d):
        t = cls.__new__(cls)
        t.d = d
        return t

    def on_bar(self, ts, h, l, c):
        d = self.d
        ev = []
        if d["closed"]:
            return ev
        long = d["side"] == "long"
        d["last_ts"] = ts
        if (long and l <= d["cur_stop"]) or ((not long) and h >= d["cur_stop"]):
            if d["hit"] == 0:
                d["r"] += -1.0 * d["remaining"]
                ev.append(("SL", d["cur_stop"]))
            else:
                ev.append(("BE", d["cur_stop"]))
            d["remaining"] = 0.0
            d["closed"] = True
            return ev
        while d["hit"] < len(d["tps"]):
            tp = d["tps"][d["hit"]]
            reached = (long and h >= tp) or ((not long) and l <= tp)
            if not reached:
                break
            w = d["split"][d["hit"]]
            risk_px = abs(d["entry"] - d["stop"])
            d["r"] += w * (1 if long else -1) * (tp - d["entry"]) / risk_px
            d["remaining"] = round(d["remaining"] - w, 10)
            d["hit"] += 1
            ev.append((f"TP{d['hit']}", tp))
            if d["hit"] == 1:
                d["cur_stop"] = d["entry"]
        if d["hit"] >= len(d["tps"]):
            d["closed"] = True
        return ev

    def force_close(self, price):
        d = self.d
        if d["closed"]:
            return
        risk = abs(d["entry"] - d["stop"])
        sgn = 1 if d["side"] == "long" else -1
        d["r"] += d["remaining"] * sgn * (price - d["entry"]) / risk
        d["remaining"] = 0.0
        d["closed"] = True


def simulate(d: pd.DataFrame, i: int, sig: dict, p: Params, fee_pct: float = 0.06, max_hold: int = 192):
    """Прогон сделки по истории. Возвращает (R после комиссий, число баров, исход)."""
    t = Trade(sig, p)
    last = min(len(d) - 1, i + max_hold)
    outcome = "timeout"
    for j in range(i + 1, last + 1):
        row = d.iloc[j]
        ev = t.on_bar(int(row["ts"]), float(row["high"]), float(row["low"]), float(row["close"]))
        if t.d["closed"]:
            outcome = ev[-1][0] if ev else "?"
            break
    if not t.d["closed"]:
        t.force_close(float(d.iloc[last]["close"]))
    fee_r = 2 * fee_pct / sig["stop_pct"]
    return t.d["r"] - fee_r, j - i, outcome
