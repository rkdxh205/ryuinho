import asyncio
import sqlite3
import logging
import re
import html
import os
from datetime import datetime
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

# ─────────────────────────────────────────
#  설정값
# ─────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

# 검색 대상: 유인호 세종특별자치시의원/후보
TARGET_NAME = "유인호"

# 동명이인 구별 필수 키워드 (하나 이상 포함돼야 함)
REQUIRED_KEYWORDS = ["세종", "세종시", "세종특별자치시", "세종시의원", "세종특별자치시의원", "세종시의회", "세종후보", "세종특별자치시후보", "세종 후보"]

# 동명이인 의심 키워드 (이 단어만 있고 세종 관련 없으면 제외)
EXCLUDE_CONTEXTS = ["배우", "가수", "감독", "작가", "교수", "부산", "대구", "인천", "광주", "대전", "울산", "수원", "성남", "고양"]

# 뉴스 확인 주기 (초), 기본 1시간
CHECK_INTERVAL = 3600

DB_PATH = "subscribers.db"
# ─────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── DB ──────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                subscribed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_articles (
                url TEXT PRIMARY KEY,
                title TEXT,
                seen_at TEXT
            )
        """)
        conn.commit()


def get_subscribers():
    with sqlite3.connect(DB_PATH) as conn:
        return [r[0] for r in conn.execute("SELECT chat_id FROM subscribers")]


def add_subscriber(chat_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscribers VALUES (?, ?)",
            (chat_id, datetime.now().isoformat()),
        )
        conn.commit()


def remove_subscriber(chat_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        conn.commit()


def is_subscriber(chat_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT 1 FROM subscribers WHERE chat_id = ?", (chat_id,)
        ).fetchone() is not None


def is_seen(url: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT 1 FROM seen_articles WHERE url = ?", (url,)
        ).fetchone() is not None


def mark_seen(url: str, title: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_articles VALUES (?, ?, ?)",
            (url, title, datetime.now().isoformat()),
        )
        conn.commit()


# ── 동명이인 필터 ────────────────────────
def is_target_person(text: str) -> bool:
    """세종특별자치시의원·후보 유인호인지 판별"""
    if TARGET_NAME not in text:
        return False

    # 필수 키워드 중 하나 이상 포함
    has_required = any(kw in text for kw in REQUIRED_KEYWORDS)
    if not has_required:
        return False

    # 동명이인 맥락 키워드가 있으면서 세종 키워드가 없으면 제외
    has_exclude = any(kw in text for kw in EXCLUDE_CONTEXTS)
    has_sejong = any(kw in text for kw in ["세종", "세종시"])
    if has_exclude and not has_sejong:
        return False

    return True


# ── 뉴스 수집 ────────────────────────────
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
        try:
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
                    articles.append({"title": title, "url": link, "summary": desc, "date": date_str, "source": "네이버 뉴스"})
        except Exception as e:
            logger.error(f"Naver News API 오류: {e}")
    return articles


def fetch_all_news() -> list[dict]:
    return fetch_naver_news()


# ── 메시지 포맷 ──────────────────────────
def format_article(article: dict, new: bool = True) -> str:
    prefix = "🔔 새 뉴스 알림" if new else "📰 최신 뉴스"
    summary = article["summary"][:250] + ("…" if len(article["summary"]) > 250 else "")
    return (
        f"{prefix}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📅 {article.get('date', '날짜 미상')}\n\n"
        f"📌 {article['title']}\n\n"
        f"📝 {summary}\n\n"
        f"🔗 {article['url']}"
    )


# ── 핸들러 ───────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "안녕하세요! 👋\n"
        "유인호 세종특별자치시의원·후보 뉴스 알림 봇입니다.\n\n"
        "📋 명령어\n"
        "/subscribe  — 뉴스 알림 구독\n"
        "/unsubscribe — 구독 취소\n"
        "/status    — 구독 상태 확인\n"
        "/news      — 지금 바로 뉴스 확인"
    )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_subscriber(chat_id):
        await update.message.reply_text("이미 구독 중입니다. ✅")
        return
    add_subscriber(chat_id)
    await update.message.reply_text(
        "구독 완료! ✅\n"
        "유인호 세종특별자치시의원·후보 관련 새 뉴스가 등록되면 바로 알려드립니다.\n"
        "(동명이인 기사는 자동으로 제외됩니다)"
    )


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_subscriber(chat_id):
        await update.message.reply_text("현재 구독 중이 아닙니다.")
        return
    remove_subscriber(chat_id)
    await update.message.reply_text("구독이 취소되었습니다.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    status = "구독 중 ✅" if is_subscriber(chat_id) else "미구독 ❌"
    total = len(get_subscribers())
    await update.message.reply_text(
        f"상태: {status}\n"
        f"전체 구독자: {total}명\n"
        f"뉴스 확인 주기: {CHECK_INTERVAL // 60}분"
    )


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 뉴스 검색 중…")
    articles = fetch_all_news()
    if not articles:
        await update.message.reply_text("현재 관련 뉴스를 찾을 수 없습니다.")
        return
    for article in articles[:5]:
        await update.message.reply_text(format_article(article, new=False))


# ── 주기적 뉴스 체크 잡 ──────────────────
async def news_check_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("뉴스 확인 중…")
    articles = fetch_all_news()
    new_articles = []
    for article in articles:
        if not is_seen(article["url"]):
            new_articles.append(article)
            mark_seen(article["url"], article["title"])

    if not new_articles:
        logger.info("새 뉴스 없음")
        return

    logger.info(f"새 뉴스 {len(new_articles)}건 발송")
    subscribers = get_subscribers()
    for chat_id in subscribers:
        for article in new_articles:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=format_article(article, new=True),
                    disable_web_page_preview=False,
                )
            except Exception as e:
                logger.error(f"발송 실패 (chat_id={chat_id}): {e}")


# ── 메인 ─────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("news", cmd_news))

    # 봇 시작 10초 후 첫 확인, 이후 매 CHECK_INTERVAL 초마다 반복
    app.job_queue.run_repeating(news_check_job, interval=CHECK_INTERVAL, first=10)

    logger.info("봇 시작!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
