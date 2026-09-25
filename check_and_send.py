import re
import html
import os
import json
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

KST = timezone(timedelta(hours=9))
RECENT_HOURS = 24  # 오후6시 → 다음날 오전8시(14h) + Actions 지연 여유
                   # (실측 지연 약 2h, 중복은 seen_urls로 차단되므로 넉넉히)

BOT_TOKEN = os.environ["BOT_TOKEN"]
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

SEEN_FILE = "seen_urls.txt"
SUBSCRIBERS_FILE = "subscribers.json"
TARGET_NAME = "유인호"
REQUIRED_KEYWORDS = ["더불어민주당", "민주당", "보람동", "부의장", "세종", "세종시", "세종시의원", "세종시의회", "세종특별자치시", "세종특별자치시의원", "세종특별자치시의회", "원내대표", "제1부의장"]
EXCLUDE_CONTEXTS = ["가수", "감독", "고양", "광주", "교수", "대구", "대전", "배우", "부산", "성남", "수원", "울산", "인천", "작가"]

TGAPI = f"https://api.telegram.org/bot{BOT_TOKEN}"


_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "_ga", "_gid", "ref", "referer", "source", "from",
}

def normalize_url(url: str) -> str:
    p = urlparse(url)
    if p.query:
        params = parse_qs(p.query, keep_blank_values=True)
        filtered = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
        new_query = urlencode(filtered, doseq=True) if filtered else ""
    else:
        new_query = ""
    return urlunparse((p.scheme, p.netloc, p.path, "", new_query, ""))


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
        f.write("\n".join(sorted(u for u in seen if u)) + "\n")


# ── HTTP 세션 (텔레그램 + 네이버 공용) ──
HTTP_TIMEOUT = (10, 30)   # (connect, read) 초
HTTP_RETRIES = 4          # 최초 1회 + 재시도 4회 (backoff 2s→4s→8s→16s)


def _make_session() -> requests.Session:
    """네트워크 일시 장애(ReadTimeout, 5xx, 429)에 자동 재시도하는 세션."""
    retry = Retry(
        total=HTTP_RETRIES,
        connect=HTTP_RETRIES,
        read=HTTP_RETRIES,
        status=HTTP_RETRIES,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


SESSION = _make_session()


# ── 텔레그램 ─────────────────────────────
def tg_get(method: str, **params):
    r = SESSION.get(f"{TGAPI}/{method}", params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def send_message(chat_id, text: str) -> bool:
    """전송 성공 여부를 반환. 예외는 내부에서 흡수해
    한 명에게 실패해도 나머지 전송과 seen_urls 저장이 계속되도록 한다."""
    try:
        r = SESSION.post(f"{TGAPI}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        }, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        print(f"  전송 실패 → {chat_id}: {type(e).__name__}: {e}")
        return False

    if not r.ok:
        print(f"  전송 실패 → {chat_id}: HTTP {r.status_code} {r.text[:200]}")
        return False
    return True


# ── 커맨드 처리 ──────────────────────────
def process_updates(data: dict):
    offset = data.get("offset", 0)
    chat_ids: list = data.get("chat_ids", [])

    resp = tg_get("getUpdates", offset=offset, timeout=0)
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
                "유인호 세종특별자치시의원 뉴스 알림 봇입니다.\n\n"
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
                    "유인호 세종특별자치시의원 관련 새 뉴스가 등록되면 바로 알려드립니다.\n"
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
    for query in ["유인호 더불어민주당", "유인호 보람동", "유인호 세종시의회", "유인호 세종특별자치시의원"]:
        url = f"https://openapi.naver.com/v1/search/news.json?query={requests.utils.quote(query)}&display=20&sort=date"
        resp = SESSION.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            title = html.unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))
            # 표시용 URL: 원본 그대로 (쿼리파라미터 유지)
            display_url = item.get("originallink") or item.get("link", "")
            # dedup용 key: 트래킹 파라미터만 제거한 정규화 URL
            # originallink가 비어 있는 기사가 있어 빈 문자열은 key에서 제외
            # (제외하지 않으면 두 번째 기사부터 중복으로 오판되어 누락됨)
            keys = {k for k in (normalize_url(item.get("link", "")),
                                normalize_url(item.get("originallink", ""))) if k}
            desc = html.unescape(re.sub(r"<[^>]+>", "", item.get("description", "")))

            # 날짜 파싱 실패 기사 제외
            try:
                pub_dt = parsedate_to_datetime(item.get("pubDate", ""))
                if pub_dt.tzinfo is None:          # 타임존 없는 응답 방어
                    pub_dt = pub_dt.replace(tzinfo=KST)
                date_str = pub_dt.strftime("%Y년 %m월 %d일 %H:%M")
            except Exception:
                continue

            # 최근 15시간 이내 기사만 허용
            if pub_dt < cutoff:
                continue

            # 이번 수집 내 중복 제거
            if keys & seen_keys:
                continue
            if not is_target_person(title + " " + desc):
                continue

            seen_keys |= keys
            articles.append({
                "title": title,
                "url": display_url,       # 전송용 원본 URL
                "keys": keys,             # seen_urls 저장/조회용
                "summary": desc,
                "date": date_str,
                "pub_dt": pub_dt,         # 정렬용
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
    import traceback

    chat_ids: list = []  # 오류 알림 전송용 fallback

    try:
        # 1. 구독자 로드 (커맨드 처리는 command_handler 전용)
        data = load_subscribers()
        chat_ids = data.get("chat_ids", [])
        print(f"구독자 {len(chat_ids)}명")

        # 2. 뉴스 수집 (최근 15시간 이내)
        seen = load_seen()
        articles = fetch_naver_news()
        # dedup key 기준으로 이미 보낸 기사 제외
        new_articles = sorted(
            [a for a in articles if not (a["keys"] & seen)],
            key=lambda a: a["pub_dt"]
        )[:10]
        print(f"수집 {len(articles)}건 / 신규 {len(new_articles)}건")

        # 3. 새 기사 전송
        if not new_articles:
            now_kst = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M")
            for chat_id in chat_ids:
                send_message(chat_id, f"📭 {now_kst} 기준\n현재까지 최신기사는 없습니다.")
            print("새 기사 없음 메시지 전송")
        else:
            for article in new_articles:
                text = format_article(article)
                results = [send_message(chat_id, text) for chat_id in chat_ids]
                # 한 명이라도 전송에 성공했을 때만 발송 완료로 기록
                # (전원 실패 시 다음 실행에서 재시도)
                if any(results) or not chat_ids:
                    seen.update(article["keys"])
                    print(f"  전송 {sum(results)}/{len(chat_ids)}: {article['title'][:40]}")
                else:
                    print(f"  전송 전원 실패 (다음 실행 재시도): {article['title'][:40]}")

        save_seen(seen)

    except Exception as e:
        print(traceback.format_exc())
        now_kst = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M")
        err_text = (
            f"⚠️ 봇 오류 발생\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now_kst}\n\n"
            f"{type(e).__name__}: {e}"
        )
        for chat_id in chat_ids:
            send_message(chat_id, err_text)


def commands_main():
    import traceback
    try:
        data = load_subscribers()
        data = process_updates(data)
        save_subscribers(data)
        print(f"구독자 {len(data['chat_ids'])}명")
    except Exception as e:
        print(traceback.format_exc())


if __name__ == "__main__":
    import sys
    if "--commands-only" in sys.argv:
        commands_main()
    else:
        main()
