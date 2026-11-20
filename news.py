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
MAX_AGE_H = 6
MAX_COIN_ITEMS = 6
MAX_GENERAL = 2


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


def digest(items, seen):
    now = time.time()
    fresh = [i for i in items if i["link"] not in seen and now - i["ts"] < MAX_AGE_H * 3600]
    fresh.sort(key=lambda i: -i["ts"])
    coin, general = [], []
    for i in fresh:
        name, icon = tag(i["title"])
        i["tag"], i["icon"] = name, icon
        (coin if name else general).append(i)
    pick = coin[:MAX_COIN_ITEMS] + general[:MAX_GENERAL]
    return pick


def fmt_digest(pick):
    t = time.strftime("%H:%M", time.gmtime())
    lines = [f"📰 <b>Крипто-новости</b> · {t} UTC", ""]
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
    s = load()
    items, bad = fetch_rss()
    pick = digest(items, set(s["seen"]))
    if pick:
        send(fmt_digest(pick))
        s["seen"] = (s["seen"] + [i["link"] for i in pick])[-500:]
    else:
        print("Новых важных новостей нет")
    run_x(s)
    json.dump(s, open(STATE, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
