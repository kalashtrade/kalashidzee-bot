"""Самопроверка логики на синтетических данных (без интернета): python tests.py"""
import numpy as np, pandas as pd
from strategy import Params, prepare, signal_at, simulate, Trade

def make(n=500, breakout=True, side="long", seed=1):
    rng = np.random.default_rng(seed)
    price, rows, ts = 100.0, [], 1_700_000_000_000
    for i in range(n):
        drift = 0.0
        if i < 250:   # плавный тренд, чтобы EMA была под/над ценой
            drift = 0.03 if side == "long" else -0.03
        o = price
        c = o * (1 + drift / 100 + rng.normal(0, 0.05) / 100)
        if 250 <= i < 380:  # флэт
            c = o + (100 + 7.5 - o) * 0.0 + rng.normal(0, 0.05)
            c = min(max(c, 107.0), 108.0) if side == "long" else min(max(c, 92.0), 93.0)
        h, l = max(o, c) * 1.0007, min(o, c) * 0.9993
        v = 1000 + rng.normal(0, 50)
        if i == 380 and breakout:   # пробойная свеча
            c = (h_prev * 1.006) if side == "long" else (l_prev * 0.994)
            h = c * 1.0003 if side == "long" else o
            l = o if side == "long" else c * 0.9997
            v = 4000
        if 250 <= i < 380:
            h_prev, l_prev = max(h, locals().get("h_prev", 0)), min(l, locals().get("l_prev", 1e9))
        rows.append([ts + i * 900000, o, h, l, c, v]); price = c
        if i >= 380:
            price = c
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol"])

p = Params(max_range_pct=8.0, use_trend=False)
for side in ("long", "short"):
    df = make(side=side)
    d = prepare(df, p)
    sig = None
    for i in range(300, 460):
        g = signal_at(d, i, p)
        if g:
            sig = (i, g); break
    assert sig, f"сигнал {side} не найден"
    print(side, "сигнал на баре", sig[0], {k: round(v, 3) if isinstance(v, float) else v for k, v in sig[1].items() if k in ("entry", "stop", "stop_pct", "vol_x")})
# ложных сигналов во флэте без пробоя быть не должно
df = make(breakout=False); d = prepare(df, p)
assert not any(signal_at(d, i, p) for i in range(300, 480)), "ложный сигнал без пробоя"

# логика сделки: TP1 -> БУ -> TP2 -> TP3
sig = dict(side="long", entry=100, stop=99, tps=[101, 102, 103], ts=0, stop_pct=1.0)
t = Trade(sig, Params())
assert t.on_bar(1, 101.2, 100.2, 101) == [("TP1", 101)] and t.d["cur_stop"] == 100
assert t.on_bar(2, 100.5, 99.9, 100) == [("BE", 100)] and t.d["closed"] and abs(t.d["r"] - 0.5) < 1e-9
t = Trade(sig, Params())
assert t.on_bar(1, 100.5, 98.9, 99) == [("SL", 99)] and abs(t.d["r"] + 1) < 1e-9
t = Trade(sig, Params())
ev = t.on_bar(1, 103.5, 100.1, 103)
assert [e[0] for e in ev] == ["TP1", "TP2", "TP3"] and abs(t.d["r"] - 1.7) < 1e-9
# стоп и цель в одной свече: считаем стоп
t = Trade(sig, Params()); assert t.on_bar(1, 101.5, 98.5, 100)[0][0] == "SL"
# шорт
s2 = dict(side="short", entry=100, stop=101, tps=[99, 98, 97], ts=0, stop_pct=1.0)
t = Trade(s2, Params()); assert [e[0] for e in t.on_bar(1, 100.2, 96.5, 97)] == ["TP1", "TP2", "TP3"]
print("ВСЕ ТЕСТЫ ПРОШЛИ")
