"""Бэктест той же логики, что у бота: 1ч, пробой флэта, вход лимиткой на ретесте.
Печатает два варианта: как в тесте (стоп по уровню) и со стопом не ближе STOP_FLOOR_PCT от входа.
Пример: python backtest.py --days 270
"""
import argparse
import config, okx
from strategy import Params
import optimize as O


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=270)
    ap.add_argument("--symbols", nargs="*", default=config.SYMBOLS)
    ap.add_argument("--stop-floor", type=float, default=getattr(config, "STOP_FLOOR_PCT", 0.0))
    a = ap.parse_args()
    p = Params(**config.STRATEGY)
    floors = [0.0] + ([a.stop_floor] if a.stop_floor else [])
    res = {f: [] for f in floors}
    for s in a.symbols:
        try:
            raw = okx.history(config.inst(s), "15m", a.days)
        except Exception as e:
            print(s, "пропуск:", e); continue
        d = O.prep(O.resample_1h(raw), ema_len=p.ema_len)
        for f in floors:
            r = O.run_pair(d, p, True, config.MAX_HOLD_HOURS, 0, len(d), f)
            res[f] += r
            print(f"{s:5s} стоп {'по уровню' if not f else 'не ближе %g%%' % f:>16}:", O.fmt(O.stats(r)))
    print("-" * 80)
    for f in floors:
        print(f"ИТОГО, стоп {'по уровню (как в тесте)' if not f else 'не ближе %g%% от входа' % f}:", O.fmt(O.stats(res[f])))


if __name__ == "__main__":
    main()
