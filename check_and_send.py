import re
import html
import os
import json
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse

import requests

KST = timezone(timedelta(hours=9))
RECENT_HOURS = 15  # 오후6시 → 다음날 오전8시(14h) + Actions 지연 여유 1h

BOT_TOKEN = os.environ["BOT_TOKEN"]
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

SEEN_FILE = "seen_urls.txt"
SUBSCRIBERS_FILE = "subscribers.json"
TARGET_NAME = "유인호"
REQUIRED_KEYWORDS = ["세종", "세종시", "세종특별자치시", "세종시의원", "세종특별자치시의원", "세종시의회", "세종후보", "세종특별자치시후보", "세종 후보"]
EXCLUDE_CONTEXTS = ["배우", "가수", "감독", "작가", "교수", "부산", "대구", "인천", "광주", "대전", "울산", "수원", "성남", "고양"]

TGAPI = f"https://api.telegram.org/bot{BOT_TOKEN}"


def normalize_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


# ── 구독자 관리 ───────────────────────────
def load_subscribers() -> dict:
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"chat_ids": [], "offset": 0}


def save_subscribers(data: dict):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── seen_urls 관리 ───────────────────────
def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_seen(seen: set[str]):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(seen)))


# ── 텔레그램 ─────────────────────────────
def tg_get(method: str, **params):
    r = requests.get(f"{TGAPI}/{method}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def send_message(chat_id, text: str):
    requests.post(f"{TGAPI}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }, timeout=15)


# ── 커맨드 처리 ──────────────────────────
def process_updates(data: dict):
    offset = data.get("offset", 0)
    chat_ids: list = data.get("chat_ids", [])

    resp = tg_get("getUpdates", offset=offset, timeout=10)
    updates = resp.get("result", [])

    for upd in updates:
        offset = upd["update_id"] + 1
        msg = upd.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = msg.get("chat", {}).get("id")
        if not chat_id:
            continue

        if text == "/start":
            send_message(chat_id,
                "안녕하세요! 👋\n"
                "유인호 세종특별자치시의원·후보 뉴스 알림 봇입니다.\n\n"
                "📋 명령어\n"
                "/subscribe  — 뉴스 알림 구독\n"
                "/unsubscribe — 구독 취소\n"
                "/status    — 구독 상태 확인"
            )

        elif text == "/subscribe":
            if chat_id not in chat_ids:
                chat_ids.append(chat_id)
                send_message(chat_id,
                    "구독 완료! ✅\n"
                    "유인호 세종특별자치시의원·후보 관련 새 뉴스가 등록되면 바로 알려드립니다.\n"
                    "(동명이인 기사는 자동으로 제외됩니다)"
                )
            else:
                send_message(chat_id, "이미 구독 중입니다. ✅")

        elif text == "/unsubscribe":
            if chat_id in chat_ids:
                chat_ids.remove(chat_id)
                send_message(chat_id, "구독이 취소되었습니다.")
            else:
                send_message(chat_id, "현재 구독 중이 아닙니다.")

        elif text == "/status":
            status = "구독 중 ✅" if chat_id in chat_ids else "미구독 ❌"
            send_message(chat_id, f"상태: {status}\n전체 구독자: {len(chat_ids)}명")

    data["offset"] = offset
    data["chat_ids"] = chat_ids
    return data


# ── 뉴스 수집 ────────────────────────────
def is_target_person(text: str) -> bool:
    if TARGET_NAME not in text:
        return False
    if not any(kw in text for kw in REQUIRED_KEYWORDS):
        return False
    if any(kw in text for kw in EXCLUDE_CONTEXTS) and not any(kw in text for kw in ["세종", "세종시"]):
        return False
    return True


def fetch_naver_news() -> list[dict]:
    articles = []
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)
    seen_keys: set[str] = set()  # 이번 수집 내 중복 방지용
    for query in ["유인호 세종특별자치시의원", "유인호 세종 후보"]:
        url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(query)}&display=20&sort=date"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            title = html.unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))
            # 표시용 URL: 원본 그대로 (쿼리파라미터 유지)
            display_url = item.get("originallink") or item.get("link", "")
            # dedup용 key: 쿼리파라미터 제거한 정규화 URL
            key_link = normalize_url(item.get("link", ""))
            key_orig = normalize_url(item.get("originallink", ""))
            desc = html.unescape(re.sub(r"<[^>]+>", "", item.get("description", "")))

            # 날짜 파싱 실패 기사 제외
            try:
                pub_dt = parsedate_to_datetime(item.get("pubDate", ""))
                date_str = pub_dt.strftime("%Y년 %m월 %d일 %H:%M")
            except Exception:
                continue

            # 최근 15시간 이내 기사만 허용
            if pub_dt < cutoff:
                continue

            # 이번 수집 내 중복 제거
            if key_link in seen_keys or key_orig in seen_keys:
                continue
            if not is_target_person(title + " " + desc):
                continue

            seen_keys.add(key_link)
            seen_keys.add(key_orig)
            articles.append({
                "title": title,
                "url": display_url,       # 전송용 원본 URL
                "key_link": key_link,     # seen_urls 저장용
                "key_orig": key_orig,     # seen_urls 저장용
                "summary": desc,
                "date": date_str,
            })
    return articles


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


# ── 메인 ─────────────────────────────────
def main():
    # 1. 구독자 로드 & 커맨드 처리
    data = load_subscribers()
    data = process_updates(data)
    chat_ids = data["chat_ids"]
    save_subscribers(data)
    print(f"구독자 {len(chat_ids)}명")

    # 2. 뉴스 수집 (최근 15시간 이내)
    seen = load_seen()
    articles = fetch_naver_news()
    # dedup key 기준으로 이미 보낸 기사 제외
    new_articles = [
        a for a in articles
        if a["key_link"] not in seen and a["key_orig"] not in seen
    ][:10]
    print(f"수집 {len(articles)}건 / 신규 {len(new_articles)}건")

    # 3. 새 기사 전송
    if not new_articles:
        now_kst = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M")
        for chat_id in chat_ids:
            send_message(chat_id, f"📭 {now_kst} 기준\n현재까지 최신기사는 없습니다.")
        print("새 기사 없음 메시지 전송")
    else:
        for article in new_articles:
            seen.add(article["key_link"])
            seen.add(article["key_orig"])
            for chat_id in chat_ids:
                send_message(chat_id, format_article(article))
                print(f"  전송 → {chat_id}: {article['title'][:40]}")

    save_seen(seen)


if __name__ == "__main__":
    main()
