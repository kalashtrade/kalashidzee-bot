"""Новости и инсайды: RSS крипто-СМИ + (по желанию) твиты CZ и других через twitterapi.io.
Шлёт в тот же Telegram-чат. Запуск: python news.py [--dry]
"""
import html, json, os, re, sys, time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import requests

DRY = "--dry" in sys.argv
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
X_KEY = os.getenv("TWITTERAPI_IO_KEY", "")
STATE = "news_state.json"

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}
# Какие аккаунты читать в X (нужен ключ TWITTERAPI_IO_KEY). Добавляй свои.
X_ACCOUNTS = ["cz_binance"]
X_IMPORTANT = re.compile(r"\b(binance|bnb|listing|list(ed)?|launch|token|burn|airdrop|bitcoin|btc|eth|solana|sol|sec|etf|hack|exploit|ban|safu)\b|\$[A-Za-z]{2,10}", re.I)

COINS = [  # (метка, значок, regex)
    ("BNB", "🟡", r"\b(bnb|binance|bnb chain|changpeng|cz)\b"),
    ("BTC", "🟠", r"\b(bitcoin|btc)\b"),
    ("ETH", "🔷", r"\b(ethereum|ether|eth)\b"),
    ("SOL", "🟣", r"\b(solana|sol)\b"),
    ("HYPE", "💧", r"\b(hyperliquid|hype)\b"),
]


def send(text):
    if DRY or not TOKEN or not CHAT:
        print("---\n" + text); return
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json=dict(chat_id=CHAT, text=text, parse_mode="HTML", disable_web_page_preview=True), timeout=20)
    if r.status_code != 200:
        print("Telegram error:", r.text)


def tr(text):
    """Перевод на русский без ключей: Google (gtx), запасной MyMemory. Не вышло, вернём оригинал."""
    text = text.strip()
    if not text or re.search(r"[а-яё]", text, re.I):
        return text
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params=dict(client="gtx", sl="en", tl="ru", dt="t", q=text), timeout=10)
        r.raise_for_status()
        out = "".join(p[0] for p in r.json()[0] if p and p[0])
        if out:
            return out
    except Exception as e:
        print("перевод Google не вышел:", e)
    try:
        r = requests.get("https://api.mymemory.translated.net/get", params=dict(q=text[:450], langpair="en|ru"), timeout=10)
        out = r.json()["responseData"]["translatedText"]
        if out and "MYMEMORY" not in out.upper():
            return out
    except Exception as e:
        print("перевод MyMemory не вышел:", e)
    return text


def load():
    try:
        s = json.load(open(STATE))
    except Exception:
        s = {}
    s.setdefault("seen", []); s.setdefault("x_last", {})
    return s


def tag(title):
    for name, icon, rx in COINS:
        if re.search(rx, title, re.I):
            return name, icon
    return None, "🌐"


def fetch_rss():
    items, bad = [], []
    for src, url in FEEDS.items():
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 kalashidzee-bot"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                pub = it.findtext("pubDate")
                try:
                    ts = parsedate_to_datetime(pub).timestamp()
                except Exception:
                    ts = time.time()
                if title and link:
                    items.append(dict(src=src, title=title, link=link, ts=ts))
        except Exception as e:
            bad.append(src); print(src, "ошибка:", e)
    return items, bad


IMPORTANT = re.compile(r"\b(sec|etf|hack(ed)?|exploit|ban(ned)?|approv\w+|lawsuit|sues?|listing|lists?|delist\w*|launch\w*|record|crash\w*|surge[sd]?|plunge[sd]?|soar\w*|liquidat\w*|bankrupt\w*|fed|rate|treasury|whale|upgrade|halving|airdrop|burn|stablecoin|regulat\w+|arrest\w*|binance|changpeng|cz)\b", re.I)
SEND_HOUR_UTC = 15      # вечерний дайджест в 15:00 UTC (20:00 по UTC+5). Поменяй под себя.
MAX_COIN_ITEMS = 8
MAX_GENERAL = 2


def score(title, tag_name):
    sc = 3 if tag_name else 0
    sc += min(3, len(IMPORTANT.findall(title)))
    if tag_name in ("BNB", "BTC"):
        sc += 1
    return sc


def words(t):
    return set(re.findall(r"[a-z0-9]{4,}", t.lower()))


def pick_digest(pool, now):
    fresh = [i for i in pool if now - i["ts"] < 24 * 3600]
    for i in fresh:
        i["tag"], i["icon"] = tag(i["title"])
        i["score"] = score(i["title"], i["tag"])
    fresh.sort(key=lambda i: (-i["score"], -i["ts"]))
    coin, general, taken = [], [], []
    for i in fresh:
        w = words(i["title"])
        if any(len(w & t) / max(1, len(w | t)) > 0.5 for t in taken):  # почти дубль другой новости
            continue
        if i["tag"] and len(coin) < MAX_COIN_ITEMS:
            coin.append(i); taken.append(w)
        elif not i["tag"] and i["score"] > 0 and len(general) < MAX_GENERAL:
            general.append(i); taken.append(w)
    return coin + general


def fmt_digest(pick):
    d = time.strftime("%d.%m", time.gmtime())
    lines = [f"📰 <b>Главное за день</b> · {d}", ""]
    for i in pick:
        label = f"<b>{i['tag']}</b>" if i["tag"] else "Рынок"
        lines.append(f"{i['icon']} {label} · <a href=\"{html.escape(i['link'])}\">{html.escape(tr(i['title']))}</a> <i>({i['src']})</i>")
    lines.append("\nЗаголовки переведены автоматически.")
    return "\n".join(lines)


def fetch_tweets(user):
    r = requests.get("https://api.twitterapi.io/twitter/user/last_tweets",
                     params=dict(userName=user, includeReplies="false"), headers={"X-API-Key": X_KEY}, timeout=25)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j)
    tw = data.get("tweets") if isinstance(data, dict) else data
    return tw or []


def tweet_time(tw):
    try:
        return parsedate_to_datetime(tw.get("createdAt")).timestamp()
    except Exception:
        return time.time()


def run_x(s):
    if not X_KEY:
        print("TWITTERAPI_IO_KEY не задан: твиты пропускаю")
        return
    for user in X_ACCOUNTS:
        try:
            tws = fetch_tweets(user)
        except Exception as e:
            print("X", user, "ошибка:", e); continue
        last = int(s["x_last"].get(user, 0))
        new = [t for t in tws if str(t.get("id", "0")).isdigit() and int(t["id"]) > last
               and not t.get("isReply") and not str(t.get("text", "")).startswith("RT @")]
        if tws:
            s["x_last"][user] = str(max(int(t["id"]) for t in tws if str(t.get("id", "0")).isdigit()))
        if not last:  # первый запуск: не заваливаем старьём, берём только свежие за 3 часа
            new = [t for t in new if time.time() - tweet_time(t) < 3 * 3600]
        for t in sorted(new, key=lambda x: int(x["id"])):
            text = str(t.get("text", ""))
            likes = int(t.get("likeCount") or 0)
            if not (X_IMPORTANT.search(text) or likes >= 2000):
                continue
            url = t.get("url") or f"https://x.com/{user}/status/{t['id']}"
            stats = f"❤ {likes}  🔁 {int(t.get('retweetCount') or 0)}"
            send(f"🐦 <b>@{html.escape(user)}</b> · {time.strftime('%H:%M', time.gmtime(tweet_time(t)))} UTC\n\n"
                 f"{html.escape(tr(text[:700]))}\n\n<i>{html.escape(text[:300])}</i>\n\n{stats}\n<a href=\"{html.escape(url)}\">Открыть в X</a>")


def main():
    force = "--force" in sys.argv
    s = load()
    s.setdefault("pool", []); s.setdefault("last_sent", "")
    now = time.time()
    items, bad = fetch_rss()
    known = set(s["seen"]) | {p["link"] for p in s["pool"]}
    for i in items:
        if i["link"] not in known and now - i["ts"] < 36 * 3600:
            s["pool"].append(i); known.add(i["link"])
    s["pool"] = [p for p in s["pool"] if now - p["ts"] < 36 * 3600][-400:]
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    due = time.gmtime(now).tm_hour >= SEND_HOUR_UTC and s["last_sent"] != today
    if due or force:
        pick = pick_digest(s["pool"], now)
        if pick:
            send(fmt_digest(pick))
            s["seen"] = (s["seen"] + [i["link"] for i in s["pool"]])[-800:]
            s["pool"] = []
        else:
            print("Важных новостей для дайджеста нет")
        s["last_sent"] = today
    else:
        print(f"Собрано в пул: {len(s['pool'])}. Дайджест уйдёт в {SEND_HOUR_UTC}:00 UTC")
    run_x(s)
    json.dump(s, open(STATE, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
