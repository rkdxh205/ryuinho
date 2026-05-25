import re
import html
import os
from email.utils import parsedate_to_datetime

import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
CHAT_IDS = [cid.strip() for cid in os.environ["CHAT_IDS"].split(",") if cid.strip()]

SEEN_FILE = "seen_urls.txt"
TARGET_NAME = "유인호"
REQUIRED_KEYWORDS = ["세종", "세종시", "세종특별자치시", "세종시의원", "세종특별자치시의원", "세종시의회", "세종후보", "세종특별자치시후보", "세종 후보"]
EXCLUDE_CONTEXTS = ["배우", "가수", "감독", "작가", "교수", "부산", "대구", "인천", "광주", "대전", "울산", "수원", "성남", "고양"]


def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_seen(seen: set[str]):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(seen)))


def is_target_person(text: str) -> bool:
    if TARGET_NAME not in text:
        return False
    if not any(kw in text for kw in REQUIRED_KEYWORDS):
        return False
    has_exclude = any(kw in text for kw in EXCLUDE_CONTEXTS)
    if has_exclude and not any(kw in text for kw in ["세종", "세종시"]):
        return False
    return True


def fetch_naver_news() -> list[dict]:
    articles = []
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    queries = ["유인호 세종특별자치시의원", "유인호 세종 후보"]
    seen_urls: set[str] = set()
    for query in queries:
        url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(query)}&display=20&sort=date"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            title = html.unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))
            link = item.get("link") or item.get("originallink", "")
            desc = html.unescape(re.sub(r"<[^>]+>", "", item.get("description", "")))
            try:
                pub_dt = parsedate_to_datetime(item.get("pubDate", ""))
                date_str = pub_dt.strftime("%Y년 %m월 %d일 %H:%M")
            except Exception:
                date_str = "날짜 미상"
            if link not in seen_urls and is_target_person(title + " " + desc):
                seen_urls.add(link)
                articles.append({"title": title, "url": link, "summary": desc, "date": date_str})
    return articles


def send_message(chat_id: str, text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        timeout=15,
    )


def format_article(article: dict) -> str:
    summary = article["summary"][:250] + ("…" if len(article["summary"]) > 250 else "")
    return (
        f"🔔 새 뉴스 알림\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📅 {article['date']}\n\n"
        f"📌 {article['title']}\n\n"
        f"📝 {summary}\n\n"
        f"🔗 {article['url']}"
    )


def main():
    seen = load_seen()
    articles = fetch_naver_news()

    new_articles = [a for a in articles if a["url"] not in seen]
    print(f"전체 {len(articles)}건 / 새 기사 {len(new_articles)}건")

    for article in new_articles:
        seen.add(article["url"])
        msg = format_article(article)
        for chat_id in CHAT_IDS:
            send_message(chat_id, msg)
            print(f"전송: {article['title'][:50]}")

    save_seen(seen)


if __name__ == "__main__":
    main()
