"""Бэктест той же логики, что у бота: 1ч, пробой флэта, вход лимиткой на ретесте.
Пример: python backtest.py --days 270
"""
import argparse
import pandas as pd
import config, okx
from strategy import Params
import optimize as O


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=270)
    ap.add_argument("--symbols", nargs="*", default=config.SYMBOLS)
    a = ap.parse_args()
    p = Params(**config.STRATEGY)
    allr = []
    for s in a.symbols:
        try:
            raw = okx.history(config.inst(s), "15m", a.days)
        except Exception as e:
            print(s, "пропуск:", e); continue
        d = O.prep(O.resample_1h(raw), ema_len=p.ema_len)
        r = O.run_pair(d, p, True, config.MAX_HOLD_HOURS, 0, len(d))
        allr += r
        print(f"{s:5s}", O.fmt(O.stats(r)))
    print("-" * 70)
    print("ИТОГО", O.fmt(O.stats(allr)))


if __name__ == "__main__":
    main()
