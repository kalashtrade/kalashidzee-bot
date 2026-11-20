"""Один проход бота: ищет сигналы, сопровождает открытые сделки, шлёт всё в Telegram.
Запускается по расписанию в GitHub Actions каждые 15 минут. Локально: python bot.py --dry
"""
import json, os, sys, time
import requests
import config
from strategy import Params, prepare, signal_at, Trade
import okx

STATE_FILE = "state.json"
DRY = "--dry" in sys.argv
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
DEPOSIT = float(os.getenv("DEPOSIT_USD", "0") or 0)
RISK = float(os.getenv("RISK_PCT", "1") or 1)
P = Params(**config.STRATEGY)


def send(text):
    if DRY or not TOKEN or not CHAT:
        print("---\n" + text)
        return
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json=dict(chat_id=CHAT, text=text, parse_mode="HTML", disable_web_page_preview=True), timeout=20)
    if r.status_code != 200:
        print("Telegram error:", r.text)


def fp(x):
    if x >= 1000: return f"{x:,.1f}".replace(",", " ")
    if x >= 10: return f"{x:.2f}"
    if x >= 1: return f"{x:.3f}"
    return f"{x:.5f}"


def load():
    try:
        s = json.load(open(STATE_FILE))
    except Exception:
        s = {}
    s.setdefault("open", []); s.setdefault("last_signal", {}); s.setdefault("stats", dict(n=0, wins=0, r=0.0))
    return s


def save(s):
    json.dump(s, open(STATE_FILE, "w"), ensure_ascii=False, indent=1)


def fmt_signal(sym, g, now_price):
    arrow = "🟢 ЛОНГ" if g["side"] == "long" else "🔴 ШОРТ"
    lv = "потолок" if g["side"] == "long" else "пол"
    t = [f"{arrow} <b>{sym}/USDT</b> · пробой флэта",
         f"Вход: <b>{fp(g['entry'])}</b> (цена сейчас {fp(now_price)})",
         f"Стоп: <b>{fp(g['stop'])}</b> ({g['stop_pct']:.2f}% от входа)",
         f"TP1: {fp(g['tps'][0])} (50%, затем стоп в безубыток)",
         f"TP2: {fp(g['tps'][1])} (30%)",
         f"TP3: {fp(g['tps'][2])} (20%)",
         "",
         f"Почему: цена закрылась за {lv} флэта {fp(g['level'])} (флэт {g['range_pct']:.1f}% за 24ч), "
         f"объём в {g['vol_x']:.1f} раза выше среднего, тренд совпадает (EMA200).",
         ]
    if DEPOSIT > 0:
        pos = DEPOSIT * RISK / 100 / (g["stop_pct"] / 100)
        t.append(f"Риск {RISK}% от {DEPOSIT:.0f}$ = {DEPOSIT*RISK/100:.1f}$ → объём позиции ≈ {pos:.0f}$ (плечо ≈ {pos/DEPOSIT:.1f}x)")
    t.append("\nНе финансовая рекомендация. Риск на сделку не больше 1%. kalashidzee trade")
    return "\n".join(t)


def main():
    s = load()
    try:
        vols = okx.volumes_usdt()
    except Exception as e:
        print("tickers fail:", e); vols = {}
    now_ms = int(time.time() * 1000)
    for sym in config.SYMBOLS:
        iid = config.inst(sym)
        try:
            df = okx.candles(iid, config.BAR, 300)
        except Exception as e:
            print(sym, "skip:", e); continue
        if len(df) < 250:
            print(sym, "мало данных"); continue
        # 1) сопровождение открытых сделок
        for tr in [t for t in s["open"] if t["symbol"] == sym]:
            T = Trade.from_dict(tr)
            for _, row in df[df["ts"] > tr["last_ts"]].iterrows():
                for name, price in T.on_bar(int(row["ts"]), float(row["high"]), float(row["low"]), float(row["close"])):
                    icon = {"SL": "❌", "BE": "⚪", "TP1": "✅", "TP2": "✅✅", "TP3": "🏆"}[name]
                    msg = {"SL": "стоп сработал", "BE": "закрыто в безубыток (остаток)", "TP1": "первая цель, стоп переносим в безубыток",
                           "TP2": "вторая цель", "TP3": "третья цель, сделка закрыта"}[name]
                    send(f"{icon} <b>{sym}</b> {tr['side'].upper()}: {msg} на {fp(price)}")
                if T.d["closed"]:
                    break
            if (not T.d["closed"]) and (now_ms - tr["ts"] > 48 * 3600 * 1000):
                T.force_close(float(df.iloc[-1]["close"]))
                send(f"⏱ <b>{sym}</b> {tr['side'].upper()}: прошло 48ч, закрыто по рынку {fp(float(df.iloc[-1]['close']))}")
            if T.d["closed"]:
                r = T.d["r"]
                st = s["stats"]; st["n"] += 1; st["wins"] += 1 if r > 0 else 0; st["r"] += r
                send(f"📊 {sym} итог сделки: {r:+.2f}R. Всего у бота: {st['n']} сделок, плюс в {st['wins']} ({st['wins']/st['n']*100:.0f}%), сумма {st['r']:+.1f}R")
                s["open"] = [t for t in s["open"] if t is not tr]
        # 2) новый сигнал
        if vols and vols.get(iid, 0) < config.MIN_VOLUME_24H_USDT:
            print(sym, "низкая ликвидность", vols.get(iid, 0)); continue
        d = prepare(df, P)
        sig = None
        for i in range(len(d) - 3, len(d)):  # последние 3 закрытые свечи: на случай задержки запуска
            g = signal_at(d, i, P)
            if g and g["ts"] > s["last_signal"].get(f"{sym}_{g['side']}", 0):
                sig = g
                break
        if not sig:
            continue
        key = f"{sym}_{sig['side']}"
        if now_ms - sig["ts"] > 3 * 15 * 60 * 1000 + 60000:
            continue
        if any(t["symbol"] == sym for t in s["open"]):
            continue
        last_ts = s["last_signal"].get(key, 0)
        if sig["ts"] - last_ts < config.COOLDOWN_HOURS * 3600 * 1000:
            continue
        s["last_signal"][key] = sig["ts"]
        s["open"].append(Trade(sig, P, sym).d)
        send(fmt_signal(sym, sig, float(df.iloc[-1]["close"])))
    save(s)


if __name__ == "__main__":
    main()
