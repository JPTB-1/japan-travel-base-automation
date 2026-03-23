"""
post_threads.py
Threads 自動投稿スクリプト

使い方:
    python3 post_threads.py          # 通常投稿（時間帯で内容を自動判定）
    python3 post_threads.py --test   # テスト投稿（実際には投稿しない）
    python3 post_threads.py --article  # 記事紹介を強制
    python3 post_threads.py --tip      # 旅行ライフハックを強制

cron (JST):
    0 23 * * *   python3 /path/to/post_threads.py   # 8:00 JST
    0 3  * * *   python3 /path/to/post_threads.py   # 12:00 JST
    0 10 * * *   python3 /path/to/post_threads.py   # 19:00 JST
"""

import os
import sys
import json
import random
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import anthropic

load_dotenv(dotenv_path=".env")

# ── 設定 ─────────────────────────────────────────────────────────
THREADS_USER_ID    = os.getenv("THREADS_USER_ID", "")
THREADS_TOKEN      = os.getenv("THREADS_ACCESS_TOKEN", "")
WP_URL             = os.getenv("WP_URL", "https://japantravelbase.com")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
THREADS_API_BASE   = "https://graph.threads.net/v1.0"

JST = timezone(timedelta(hours=9))

# ── 旅行ライフハックのテーマ一覧 ──────────────────────────────────
TRAVEL_TIP_THEMES = [
    "IC card (Suica/Pasmo) tips for getting around Japan cheaply",
    "How to use Japan's convenience stores (konbini) like a local",
    "Best apps for traveling in Japan (Google Maps, Google Translate, Hyperdia)",
    "How to find cheap flights to Japan",
    "Japan's 100-yen shops and what to buy there",
    "How to use Japan's vending machines and what's unique about them",
    "Etiquette tips every Japan traveler should know",
    "How to find budget accommodation in Japan (hostels, capsule hotels, business hotels)",
    "Best times to visit Japan to avoid crowds",
    "How to get from airports to city centers in Japan cheaply",
    "Japan's food allergy and dietary restriction tips for travelers",
    "How to use Japan's coin lockers at train stations",
    "Free WiFi spots in Japan and how to get a pocket WiFi",
    "How to save money on food in Japan (standing bars, set meals, supermarket discounts)",
    "Day trip ideas from Tokyo under 2 hours by train",
    "Day trip ideas from Osaka under 2 hours by train",
    "How to read Japanese train maps and signs",
    "Japan's onsen etiquette for first-timers",
    "Hidden gems and lesser-known spots in Japan",
    "How to use Japan Post and shipping luggage between cities (takuhaibin)",
    "Best Japan Rail Pass alternatives for different itineraries",
    "How to find authentic local restaurants in Japan",
    "Japan's seasonal events and festivals worth planning around",
    "How to tip (or not tip) in Japan",
    "Cherry blossom viewing tips and best spots",
    "Autumn foliage viewing tips and best spots in Japan",
    "How to climb Mt. Fuji — what travelers need to know",
    "Budget-friendly ways to experience Japanese culture",
    "Must-try street foods in Japan",
    "How to use self-checkout and cashless payment in Japan",
]

# ── WordPress から最新記事を取得 ──────────────────────────────────
def get_latest_articles(count=5):
    try:
        r = requests.get(
            f"{WP_URL}/wp-json/wp/v2/posts",
            params={"per_page": count, "status": "publish", "_fields": "title,link,excerpt,categories"},
            timeout=10,
        )
        if r.ok:
            return r.json()
    except Exception as e:
        print(f"[WARN] WP記事取得失敗: {e}")
    return []


# ── Claude でスレッズ投稿文を生成 ─────────────────────────────────
def generate_post(post_type: str, article=None, tip_theme=None) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if post_type == "article" and article:
        title   = article.get("title", {}).get("rendered", "")
        link    = article.get("link", "")
        excerpt = article.get("excerpt", {}).get("rendered", "")
        # HTMLタグ除去
        import re
        excerpt = re.sub(r"<[^>]+>", "", excerpt).strip()[:300]

        prompt = f"""Write a viral-worthy Threads post in English to promote this Japan travel article.

Article title: {title}
Article URL: {link}
Article excerpt: {excerpt}

Requirements:
- 200-350 characters for the main content (before URL and hashtags)
- Open with a scroll-stopping hook: a surprising fact, bold statement, or "Most tourists don't know this..." style opener
- Use a short punchy sentence structure — no long paragraphs
- Include 1 specific, concrete detail from the article that feels surprising or genuinely useful
- End the main content with a soft CTA like "Full guide →" or "Read before you go →" followed by the URL on a new line
- Add 3-4 hashtags on the final line (e.g. #JapanTravel #VisitJapan #TravelTips #Japan)
- Tone: confident, conversational, like a well-traveled friend sharing insider knowledge — NOT a travel brochure

Output ONLY the post text, nothing else."""

    else:  # tip
        theme = tip_theme or random.choice(TRAVEL_TIP_THEMES)
        prompt = f"""Write a viral-worthy Threads post in English sharing a Japan travel tip.

Topic: {theme}

Requirements:
- 200-350 characters for the main content
- Open with a scroll-stopping hook: a surprising fact, "Nobody tells you this...", a relatable traveler moment, or a bold claim
- Use short punchy lines — break it up for easy reading
- Share 2-3 specific, actionable tips with concrete details (prices, names, exact steps)
- Make it feel like insider knowledge from someone who actually lives in Japan
- End with a light CTA like "Save this for your trip 🗾" or "Tag someone going to Japan"
- Add 3-4 hashtags on the final line (e.g. #JapanTravel #JapanTips #TravelHacks #VisitJapan)
- Do NOT include any URLs

Output ONLY the post text, nothing else."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


# ── Threads に投稿 ────────────────────────────────────────────────
def post_to_threads(text: str, dry_run=False) -> bool:
    if dry_run:
        print(f"\n[DRY RUN] 投稿内容:\n{'-'*50}\n{text}\n{'-'*50}")
        print(f"文字数: {len(text)}")
        return True

    # Step 1: メディアコンテナ作成
    r1 = requests.post(
        f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads",
        data={
            "media_type": "TEXT",
            "text": text,
            "access_token": THREADS_TOKEN,
        },
        timeout=15,
    )
    if not r1.ok:
        print(f"[ERROR] コンテナ作成失敗: {r1.status_code} {r1.text}")
        return False

    creation_id = r1.json().get("id")
    print(f"  コンテナID: {creation_id}")

    # Step 2: 公開
    r2 = requests.post(
        f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads_publish",
        data={
            "creation_id": creation_id,
            "access_token": THREADS_TOKEN,
        },
        timeout=15,
    )
    if not r2.ok:
        print(f"[ERROR] 公開失敗: {r2.status_code} {r2.text}")
        return False

    thread_id = r2.json().get("id")
    print(f"  ✓ 投稿成功 (ID: {thread_id})")
    return True


# ── 時間帯でポストタイプを決定 ────────────────────────────────────
def decide_post_type() -> str:
    hour = datetime.now(JST).hour
    # 8:00 → article, 12:00 → tip, 19:00 → article or tip (交互)
    if hour < 10:
        return "article"
    elif hour < 16:
        return "tip"
    else:
        # 日付の奇偶で交互
        return "article" if datetime.now(JST).day % 2 == 0 else "tip"


# ── メイン ────────────────────────────────────────────────────────
def main():
    dry_run = "--test" in sys.argv
    force_article = "--article" in sys.argv
    force_tip = "--tip" in sys.argv

    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    print(f"=== Threads 自動投稿 [{now_jst}] ===")

    if force_article:
        post_type = "article"
    elif force_tip:
        post_type = "tip"
    else:
        post_type = decide_post_type()

    print(f"  投稿タイプ: {post_type}")

    article = None
    tip_theme = None

    if post_type == "article":
        articles = get_latest_articles(5)
        if articles:
            article = random.choice(articles)
            print(f"  記事: {article.get('title', {}).get('rendered', '')}")
        else:
            print("  [WARN] 記事取得失敗 → tipに切り替え")
            post_type = "tip"

    if post_type == "tip":
        tip_theme = random.choice(TRAVEL_TIP_THEMES)
        print(f"  テーマ: {tip_theme}")

    print("  Claude で文章生成中…")
    text = generate_post(post_type, article=article, tip_theme=tip_theme)

    success = post_to_threads(text, dry_run=dry_run)

    if not success and not dry_run:
        sys.exit(1)


if __name__ == "__main__":
    main()
