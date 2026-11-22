"""Один проход бота v2: пробой флэта на 1ч, вход лимиткой на ретесте уровня.
Запускается по расписанию в GitHub Actions каждые 15 минут. Локально: python bot.py --dry
"""
import dataclasses, json, os, sys, time
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
P_SIG = dataclasses.replace(P, min_stop_pct=0.0, max_stop_pct=1e9)  # границы стопа проверяем сами, по цене лимитки
H1 = 3_600_000


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


def hhmm(ms):
    return time.strftime("%H:%M", time.gmtime(ms / 1000))


def load():
    try:
        s = json.load(open(STATE_FILE))
    except Exception:
        s = {}
    s.setdefault("open", []); s.setdefault("last_signal", {}); s.setdefault("stats", dict(n=0, wins=0, r=0.0))
    s["open"] = [t for t in s["open"] if "status" in t]  # старые сделки версии 1 не продолжаем
    return s


def save(s):
    json.dump(s, open(STATE_FILE, "w"), ensure_ascii=False, indent=1)


def levels_text(tr):
    r = config.STRATEGY["tp_r"]; sp = config.STRATEGY["tp_split"]
    return [f"TP1: {fp(tr['tps'][0])} ({r[0]:g}R, закрыть {sp[0]*100:.0f}%, стоп в безубыток)",
            f"TP2: {fp(tr['tps'][1])} ({r[1]:g}R, {sp[1]*100:.0f}%)",
            f"TP3: {fp(tr['tps'][2])} ({r[2]:g}R, {sp[2]*100:.0f}%)"]


def size_line(stop_pct):
    if DEPOSIT <= 0:
        return []
    pos = DEPOSIT * RISK / 100 / (stop_pct / 100)
    return [f"Риск {RISK:g}% от {DEPOSIT:.0f}$ = {DEPOSIT*RISK/100:.1f}$ → объём позиции ≈ {pos:.0f}$ (плечо ≈ {pos/DEPOSIT:.1f}x)"]


def fmt_pending(sym, pd_):
    long = pd_["side"] == "long"
    head = "🟢 ЛИМИТНЫЙ ЛОНГ" if long else "🔴 ЛИМИТНЫЙ ШОРТ"
    lv = "потолок" if long else "пол"
    lines = [f"{head} <b>{sym}/USDT</b> · пробой флэта (1ч) + ретест",
             f"Поставь лимитку: <b>{fp(pd_['entry'])}</b> ({'купить' if long else 'продать'})",
             f"Стоп: <b>{fp(pd_['stop'])}</b> ({pd_['stop_pct']:.2f}% от входа)", *levels_text(pd_), "",
             f"Почему: часовая свеча закрылась за {lv} флэта {fp(pd_['level'])} (флэт {pd_['range_pct']:.1f}% за 24ч), "
             f"объём ×{pd_['vol_x']:.1f}, тренд совпадает. Ждём возврата цены к уровню.",
             f"⏳ Лимитка действует до {hhmm(pd_['expire_ts'])} UTC. Не исполнилась до этого времени или цена ушла к стопу: сделку пропускаем.",
             *size_line(pd_["stop_pct"]),
             "\nНе финансовая рекомендация. Риск на сделку не больше 1%. kalashidzee trade"]
    return "\n".join(lines)


def process(sym, df15, now_ms, s):
    for tr in [t for t in s["open"] if t["symbol"] == sym]:
        removed = False
        for row in df15[df15["ts"] > tr["last_ts"]].itertuples():
            ts, h, l, c = int(row.ts), float(row.high), float(row.low), float(row.close)
            if tr["status"] == "pending":
                long = tr["side"] == "long"
                if ts >= tr["expire_ts"]:
                    send(f"⌛ <b>{sym}</b> {tr['side'].upper()}: лимитка на {fp(tr['entry'])} не исполнилась за 8 часов, сделку пропускаем")
                    removed = True; break
                if (long and l <= tr["stop"]) or ((not long) and h >= tr["stop"]):
                    send(f"🚫 <b>{sym}</b> {tr['side'].upper()}: цена дошла до стопа {fp(tr['stop'])} без входа, ордер отменяем")
                    removed = True; break
                touched = (long and l <= tr["entry"]) or ((not long) and h >= tr["entry"])
                if touched:
                    new = Trade(dict(side=tr["side"], entry=tr["entry"], stop=tr["stop"], tps=tr["tps"], ts=ts,
                                     stop_pct=tr["stop_pct"], level=tr["level"]), P, sym).d
                    new["status"] = "open"
                    tr.clear(); tr.update(new)
                    send(f"✅ <b>{sym}</b> {tr['side'].upper()}: лимитка исполнена по {fp(tr['entry'])}. Сделка открыта. Стоп {fp(tr['stop'])}, TP1 {fp(tr['tps'][0])}")
                else:
                    tr["last_ts"] = ts
                continue
            T = Trade.from_dict(tr)
            for name, price in T.on_bar(ts, h, l, c):
                icon = {"SL": "❌", "BE": "⚪", "TP1": "✅", "TP2": "✅✅", "TP3": "🏆"}[name]
                msg = {"SL": "стоп сработал", "BE": "остаток закрыт в безубыток", "TP1": "первая цель, стоп переносим в безубыток",
                       "TP2": "вторая цель", "TP3": "третья цель, сделка закрыта"}[name]
                send(f"{icon} <b>{sym}</b> {tr['side'].upper()}: {msg} на {fp(price)}")
            if T.d["closed"]:
                break
        if removed:
            s["open"] = [t for t in s["open"] if t is not tr]
            continue
        if tr["status"] == "open" and not tr["closed"] and now_ms - tr["ts"] > config.MAX_HOLD_HOURS * 3600 * 1000:
            T = Trade.from_dict(tr); price = float(df15.iloc[-1]["close"]); T.force_close(price)
            send(f"⏱ <b>{sym}</b> {tr['side'].upper()}: прошло {config.MAX_HOLD_HOURS}ч, закрыто по рынку {fp(price)}")
        if tr["status"] == "open" and tr["closed"]:
            r = tr["r"]; st = s["stats"]; st["n"] += 1; st["wins"] += 1 if r > 0 else 0; st["r"] += r
            send(f"📊 {sym} итог сделки: {r:+.2f}R. Всего у бота: {st['n']} сделок, плюс в {st['wins']} ({st['wins']/st['n']*100:.0f}%), сумма {st['r']:+.1f}R")
            s["open"] = [t for t in s["open"] if t is not tr]


def diagnose(sym, d):
    """Печатает в лог, почему сигнала нет (видно в Actions)."""
    try:
        w = d.iloc[-1 - P.range_bars:-1]
        R, S_ = float(w["high"].max()), float(w["low"].min())
        row = d.iloc[-1]
        print(f"{sym}: флэт {(R-S_)/S_*100:.1f}% (нужно ≤{P.max_range_pct}), объём ×{row['vol']/row['vol_ma']:.1f} (нужно ≥{P.vol_mult}), "
              f"закрытие {row['close']:.4g} при потолке {R:.4g} / поле {S_:.4g}, EMA {row['ema']:.4g}")
    except Exception as e:
        print(sym, "диагностика не вышла:", e)


def find_signal(sym, df1h, s, now_ms):
    d = prepare(df1h, P)
    for i in range(len(d) - 2, len(d)):  # две последние закрытые часовые свечи
        g = signal_at(d, i, P_SIG)
        if not g or g["ts"] <= s["last_signal"].get(f"{sym}_{g['side']}", 0):
            continue
        sgn = 1 if g["side"] == "long" else -1
        entry = g["level"] * (1 + sgn * config.RETEST_BUFFER_PCT / 100)
        risk = (entry - g["stop"]) * sgn
        if risk <= 0:
            continue
        stop_pct = risk / entry * 100
        if stop_pct < P.min_stop_pct or stop_pct > P.max_stop_pct:
            continue
        start = g["ts"] + H1
        print(f"{sym}: СИГНАЛ {g['side']}")
        return dict(symbol=sym, status="pending", side=g["side"], level=g["level"], entry=entry, stop=g["stop"],
                    stop_pct=stop_pct, tps=[entry + sgn * risk * r for r in P.tp_r], ts=g["ts"], start_ts=start,
                    last_ts=start - 1, expire_ts=start + config.RETEST_WAIT_BARS * H1, vol_x=g["vol_x"], range_pct=g["range_pct"])
    diagnose(sym, d)
    return None


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
            df1h = okx.candles(iid, config.BAR, 300)
            df15 = okx.candles(iid, config.TRACK_BAR, 300)
        except Exception as e:
            print(sym, "skip:", e); continue
        if len(df1h) < 150 or len(df15) < 50:
            print(sym, "мало данных"); continue
        process(sym, df15, now_ms, s)
        print(f"{sym}: оборот за 24ч ≈ {vols.get(iid, 0)/1e6:.0f}M USDT")
        if vols and vols.get(iid, 0) < config.MIN_VOLUME_24H_USDT:
            print(sym, "низкая ликвидность, пропуск"); continue
        if any(t["symbol"] == sym for t in s["open"]):
            continue
        pd_ = find_signal(sym, df1h, s, now_ms)
        if pd_:
            s["last_signal"][f"{sym}_{pd_['side']}"] = pd_["ts"]
            s["open"].append(pd_)
            send(fmt_pending(sym, pd_))
    day = time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000))
    if time.gmtime(now_ms / 1000).tm_hour >= 9 and s.get("hb") != day:
        s["hb"] = day
        pend = sum(1 for t in s["open"] if t["status"] == "pending"); opn = sum(1 for t in s["open"] if t["status"] == "open")
        st = s["stats"]
        send(f"🤖 <b>kalashidzee trade</b>: бот работает. Проверяю {len(config.SYMBOLS)} монет каждые 15 минут.\n"
             f"Сейчас: лимиток в ожидании {pend}, открытых сделок {opn}. Закрыто всего: {st['n']}, сумма {st['r']:+.1f}R.\n"
             f"Тишина между сигналами нормальна: нужны флэт, пробой и объём одновременно.")
    save(s)


if __name__ == "__main__":
    main()
