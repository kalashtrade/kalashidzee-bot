"""Живой режим: цена опрашивается каждую секунду, сигнал уходит сразу после закрытия часовой свечи.
Один запуск живёт RUN_HOURS часов, потом GitHub запускает следующий (см. .github/workflows/live.yml).
На своём сервере можно запустить бесконечно: python live.py --forever
"""
import json, os, subprocess, sys, time
import pandas as pd
import bot, config, okx

H1 = 3_600_000
POLL = float(getattr(config, "POLL_SECONDS", 1.0))
RUN_S = float(os.getenv("RUN_HOURS", getattr(config, "RUN_HOURS", 5.4))) * 3600
FOREVER = "--forever" in sys.argv


def signature(s):
    return json.dumps([[t["symbol"], t["status"], t.get("hit"), t.get("closed"), t.get("cur_stop")] for t in s["open"]]
                      + [s["stats"], s["last_signal"]], sort_keys=True)


def persist():
    """В GitHub Actions сохраняем состояние в репозиторий, чтобы следующий запуск продолжил с того же места."""
    if not os.getenv("GITHUB_ACTIONS"):
        return
    try:
        subprocess.run(["git", "config", "user.name", "signal-bot"], check=False)
        subprocess.run(["git", "config", "user.email", "signal-bot@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", "state.json"], check=False)
        if subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode != 0:
            subprocess.run(["git", "commit", "-m", "state update [skip ci]"], check=False)
            subprocess.run(["git", "pull", "--rebase", "--autostash"], check=False)
            subprocess.run(["git", "push"], check=False)
    except Exception as e:
        print("не удалось сохранить состояние:", e)


def scan(s, tick, bar_start, now_ms):
    """Ищем сигналы по закрытым часовым свечам. Возвращает True, если свечи всех монет уже готовы."""
    ready = True
    for sym in config.SYMBOLS:
        iid = config.inst(sym)
        if any(t["symbol"] == sym for t in s["open"]):
            continue
        vol = tick.get(iid, (0, 0))[1]
        if tick and vol < config.MIN_VOLUME_24H_USDT:
            continue
        try:
            df1h = okx.candles(iid, config.BAR, 300)
        except Exception as e:
            print(sym, "скан не вышел:", e); ready = False; continue
        if len(df1h) < 150:
            continue
        if int(df1h.iloc[-1]["ts"]) < bar_start - H1:
            ready = False        # свеча только что закрылась, биржа ещё не отдала её как закрытую
        pd_ = bot.find_signal(sym, df1h, s, now_ms)
        if pd_:
            s["last_signal"][f"{sym}_{pd_['side']}"] = pd_["ts"]
            s["open"].append(pd_)
            bot.send(bot.fmt_pending(sym, pd_))
    return ready


def main():
    s = bot.load()
    started = time.time()
    # догоняем всё, что могло случиться между запусками (по 15-минутным свечам)
    for sym in {t["symbol"] for t in s["open"]}:
        try:
            bot.process(sym, okx.candles(config.inst(sym), config.TRACK_BAR, 300), int(time.time() * 1000), s)
        except Exception as e:
            print(sym, "догон не вышел:", e)
    last_sig = signature(s)
    bot.save(s)
    scanned = None
    print("Живой режим запущен", flush=True)
    while FOREVER or time.time() - started < RUN_S:
        t0 = time.time()
        now_ms = int(t0 * 1000)
        try:
            tick = okx.tickers()
            # 1) следим за ордерами и сделками по текущей цене
            for sym in {t["symbol"] for t in s["open"]}:
                px = tick.get(config.inst(sym), (None,))[0]
                if px is None:
                    continue
                df = pd.DataFrame([[now_ms, px, px, px, px, 0.0]], columns=["ts", "open", "high", "low", "close", "vol"])
                bot.process(sym, df, now_ms, s)
            # 2) новая закрытая часовая свеча: ищем сигнал в первые секунды часа
            bar_start = now_ms // H1 * H1
            if scanned != bar_start and now_ms - bar_start >= 1500:
                ready = scan(s, tick, bar_start, now_ms)
                if ready or now_ms - bar_start > 60_000:
                    scanned = bar_start
            # 3) сохраняем при изменениях
            sig = signature(s)
            if sig != last_sig:
                bot.save(s); persist(); last_sig = sig
        except Exception as e:
            print("сбой цикла:", e, flush=True)
            time.sleep(3)
        time.sleep(max(0.0, POLL - (time.time() - t0)))
    bot.save(s); persist()
    print("Запуск завершён, следующий подхватит", flush=True)


if __name__ == "__main__":
    main()
