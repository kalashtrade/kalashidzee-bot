"""Публичные данные биржи OKX (ключи не нужны)."""
import time
import requests
import pandas as pd

BASE = "https://www.okx.com"
COLS = ["ts", "open", "high", "low", "close", "vol"]


def _get(path, params, tries=3):
    last = None
    for _ in range(tries):
        try:
            r = requests.get(BASE + path, params=params, timeout=20)
            r.raise_for_status()
            j = r.json()
            if j.get("code") == "0":
                return j["data"]
            last = j
        except Exception as e:  # сеть может моргнуть
            last = e
        time.sleep(1.5)
    raise RuntimeError(f"OKX error {path}: {last}")


def _to_df(rows):
    rows = [r for r in rows if r[8] == "1"]  # только закрытые свечи
    df = pd.DataFrame([[int(r[0])] + [float(x) for x in r[1:6]] for r in rows], columns=COLS)
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def candles(inst_id, bar="15m", limit=300):
    return _to_df(_get("/api/v5/market/candles", dict(instId=inst_id, bar=bar, limit=limit)))


def history(inst_id, bar="15m", days=90):
    """Длинная история для бэктеста (постранично назад)."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    out, after = [], end_ms
    while True:
        rows = _get("/api/v5/market/history-candles", dict(instId=inst_id, bar=bar, after=after, limit=100))
        if not rows:
            break
        out += rows
        oldest = int(rows[-1][0])
        if oldest <= start_ms:
            break
        after = oldest
        time.sleep(0.15)
    df = _to_df(out)
    return df[df["ts"] >= start_ms].reset_index(drop=True)


def volumes_usdt():
    """Суточный оборот в USDT по всем бессрочным контрактам: {instId: usdt}."""
    data = _get("/api/v5/market/tickers", dict(instType="SWAP"))
    res = {}
    for t in data:
        try:
            res[t["instId"]] = float(t["volCcy24h"]) * float(t["last"])
        except Exception:
            pass
    return res
