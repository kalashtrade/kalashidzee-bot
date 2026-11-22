"""Утреннее сообщение: дата и текущая цена BTC и ETH (спот OKX, ключи не нужны).
Запуск: python morning.py [--dry]
"""
import os, sys, time
import requests
import okx

DRY = "--dry" in sys.argv
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
COINS = [("BTC", "🟠"), ("ETH", "🔷")]   # можно добавить: ("SOL", "🟣"), ("BNB", "🟡")
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def send(text):
    if DRY or not TOKEN or not CHAT:
        print("---\n" + text); return
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json=dict(chat_id=CHAT, text=text, parse_mode="HTML", disable_web_page_preview=True), timeout=20)
    if r.status_code != 200:
        print("Telegram error:", r.text)


def money(x):
    return f"{x:,.0f}".replace(",", " ") if x >= 1000 else f"{x:,.2f}".replace(",", " ")


FNG_RU = {"Extreme Fear": "сильный страх", "Fear": "страх", "Neutral": "нейтрально", "Greed": "жадность", "Extreme Greed": "сильная жадность"}


def fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/", params=dict(limit=2), timeout=15)
        r.raise_for_status()
        d = r.json()["data"]
        now = d[0]
        txt = f"😨 Индекс страха и жадности: <b>{now['value']}</b> из 100, {FNG_RU.get(now['value_classification'], now['value_classification'])}"
        if len(d) > 1:
            txt += f" (вчера {d[1]['value']})"
        return txt
    except Exception as e:
        print("индекс страха не получен:", e)
        return None


def funding_oi(sym="BTC"):
    """Фандинг и открытый интерес по бессрочному контракту OKX."""
    out = []
    inst = f"{sym}-USDT-SWAP"
    try:
        f = float(okx._get("/api/v5/public/funding-rate", dict(instId=inst))[0]["fundingRate"]) * 100
        if f > 0.03: note = "сильный перекос в лонги, рынок перегрет"
        elif f > 0.005: note = "лонги платят шортам, небольшой перекос в лонги"
        elif f < -0.03: note = "сильный перекос в шорты, возможен сквиз вверх"
        elif f < -0.005: note = "шорты платят лонгам, небольшой перекос в шорты"
        else: note = "нейтрально"
        out.append(f"📊 Фандинг {sym}: <b>{f:+.4f}%</b> за 8ч, {note}")
    except Exception as e:
        print("фандинг не получен:", e)
    try:
        oi = float(okx._get("/api/v5/public/open-interest", dict(instType="SWAP", instId=inst))[0]["oiUsd"])
        out.append(f"📈 Открытый интерес {sym}: <b>${oi/1e9:.2f} млрд</b>")
    except Exception as e:
        print("открытый интерес не получен:", e)
    return out


def main():
    t = time.gmtime()
    lines = [f"☀️ <b>Доброе утро!</b> {time.strftime('%d.%m.%Y', t)}, {DAYS[t.tm_wday]}", ""]
    ok = 0
    for sym, icon in COINS:
        try:
            d = okx._get("/api/v5/market/ticker", dict(instId=f"{sym}-USDT"))[0]
            last, op, hi, lo = float(d["last"]), float(d["open24h"]), float(d["high24h"]), float(d["low24h"])
        except Exception as e:
            print(sym, "ошибка:", e); continue
        ch = (last / op - 1) * 100
        arrow = "🟢" if ch >= 0 else "🔴"
        lines.append(f"{icon} <b>{sym}</b>: {money(last)} $  ({arrow} {ch:+.1f}% за 24ч)")
        lines.append(f"    24ч: {money(lo)} – {money(hi)}")
        ok += 1
    if not ok:
        print("Цены не получены, сообщение не отправляю"); return
    extra = []
    fg = fear_greed()
    if fg: extra.append(fg)
    extra += funding_oi("BTC")
    if extra:
        lines.append("")
        lines += extra
    send("\n".join(lines))


if __name__ == "__main__":
    main()
