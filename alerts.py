"""Алерт на резкое движение BTC и ETH за час. Запуск: python alerts.py [--dry]
Каждые 15 минут по расписанию. Не чаще одного алерта на монету и сторону раз в COOLDOWN_H часов.
"""
import json, os, sys, time
import requests
import okx

DRY = "--dry" in sys.argv
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
COINS = [("BTC", "🟠"), ("ETH", "🔷")]
THRESHOLD_PCT = 3.0      # порог движения за час, %
COOLDOWN_H = 2
STATE = "alerts_state.json"


def send(text):
    if DRY or not TOKEN or not CHAT:
        print("---\n" + text); return
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json=dict(chat_id=CHAT, text=text, parse_mode="HTML", disable_web_page_preview=True), timeout=20)
    if r.status_code != 200:
        print("Telegram error:", r.text)


def money(x):
    return f"{x:,.0f}".replace(",", " ") if x >= 1000 else f"{x:,.2f}".replace(",", " ")


def main():
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    now = time.time()
    for sym, icon in COINS:
        try:
            df = okx.candles(f"{sym}-USDT-SWAP", "15m", 10)
        except Exception as e:
            print(sym, "ошибка:", e); continue
        if len(df) < 4:
            continue
        w = df.iloc[-4:]                       # последний час: 4 закрытые свечи по 15 минут
        start, end = float(w.iloc[0]["open"]), float(w.iloc[-1]["close"])
        ch = (end / start - 1) * 100
        print(f"{sym}: {ch:+.2f}% за час")
        if abs(ch) < THRESHOLD_PCT:
            continue
        key = f"{sym}_{'up' if ch > 0 else 'down'}"
        if now - st.get(key, 0) < COOLDOWN_H * 3600:
            continue
        st[key] = now
        word, arrow = ("вырос", "🟢⬆️") if ch > 0 else ("упал", "🔴⬇️")
        send(f"⚡ {icon} <b>{sym}</b> резко {word}: {arrow} <b>{ch:+.1f}%</b> за час\n{money(start)} → {money(end)} $")
    json.dump(st, open(STATE, "w"))


if __name__ == "__main__":
    main()
