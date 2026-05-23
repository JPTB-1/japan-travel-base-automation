"""
generate_article.py
Japan Travel Base — automated English article generator + WordPress draft poster.

Usage:
    python generate_article.py

Dependencies:
    pip install -r requirements.txt

Environment (.env):
    ANTHROPIC_API_KEY=
    OPENAI_API_KEY=
    WP_URL=https://japantravelbase.com
    WP_USER=
    WP_APP_PASSWORD=
"""

import csv
import io
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

import anthropic
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

LOG_ERROR_FILE = "article_errors.log"
LOG_CSV_FILE   = "article_log.csv"

logging.basicConfig(
    filename=LOG_ERROR_FILE,
    level=logging.ERROR,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---------------------------------------------------------------------------
# Day-of-week schedule
# ---------------------------------------------------------------------------

# weekday() → 0=Mon … 6=Sun
SCHEDULE = {
    0: {  # Monday — 低競合: JR Pass vs IC card 具体比較
        "theme": "JR Pass vs IC Card: Which Suits Your Trip",
        "category_slug": "transport",
        "category_name": "Transport",
        "shortcodes": ['[jtb_flight origin="SYD" destination="TYO"]'],
        "prompt_context": (
            "Answer the specific question: 'Should I get a JR Pass or just use an IC card for Japan?' "
            "Frame the answer around trip style and itinerary, not saving money. "
            "Cover: JR Pass is ideal for multi-city trips (Tokyo → Kyoto → Osaka → Hiroshima), "
            "IC card (Suica/Pasmo) is ideal for exploring a single city in depth or short regional trips. "
            "Include Shinkansen fares as practical reference points (Tokyo–Kyoto ¥13,870, Tokyo–Osaka ¥14,720), "
            "and JR Pass price range (7-day ~$350 USD, 14-day ~$560 USD) so travelers can match to their itinerary. "
            "Explain what experience each option unlocks — JR Pass = freedom to hop between cities spontaneously; "
            "IC card = seamless tap-in tap-out for city metro, buses, convenience stores. "
            "Target query: 'japan rail pass or ic card which do i need' — answer it by itinerary type in the first 100 words. "
            "Somewhere natural in the article, include a CTA like: "
            "'<p>Not sure if the JR Pass suits your specific itinerary? "
            "<a href=\"https://japantravelbase.com/tools/jr-pass-calculator/\">Use our free JR Pass Calculator</a>"
            "— enter your planned routes and get an instant verdict.</p>'"
        ),
    },
    1: {  # Tuesday — 低競合: 初めての訪日者向け箱根ガイド
        "theme": "Hakone First-Timer Guide",
        "category_slug": "hakone",
        "category_name": "Hakone",
        "shortcodes": ['[jtb_hotel city="Hakone" area="Hakone"]'],
        "prompt_context": (
            "Write a specific, practical guide for first-time visitors to Hakone. "
            "Target query: 'hakone day trip from tokyo what to do'. "
            "Cover: Hakone Free Pass (worth it or not, exact price ¥6,000), "
            "the Romancecar train from Shinjuku (60 min, book ahead), "
            "Owakudani volcanic valley (check if open — sometimes closed for eruption risk), "
            "Lake Ashi boat cruise, Hakone Open Air Museum (¥1,600 entry). "
            "Include a realistic single-day itinerary with timings. "
            "Mention ryokan with private onsen as a reason to stay overnight vs day trip. "
            "Affiliate angle: Hakone ryokans on Booking.com (high nightly rate = high commission)."
        ),
    },
    2: {  # Wednesday — SEO強化 or 低競合: 奈良日帰り具体ガイド
        "theme": "Nara Day Trip from Osaka/Kyoto",
        "category_slug": "nara",
        "category_name": "Nara",
        "shortcodes": ['[jtb_hotel city="Nara" area="Nara"]',
                       '[jtb_flight origin="SYD" destination="KIX"]'],
        "prompt_context": (
            "Answer the specific question: 'Is Nara worth a day trip from Kyoto or Osaka?' "
            "Target queries: 'nara day trip from kyoto how long', 'nara from osaka day trip'. "
            "Cover: exact train times and fares (Kintetsu Nara from Osaka: 40 min ¥680; "
            "JR from Kyoto: 45 min ¥720), what to do in 4-6 hours, "
            "Todai-ji Temple (¥600 entry, giant Buddha), Kasuga Taisha shrine (free outer, ¥500 inner), "
            "Nara deer park (deer crackers ¥200, best morning interactions), "
            "Naramachi historic district for lunch. "
            "Give a definitive answer: YES worth it, here's the best half-day plan. "
            "Affiliate: Klook Nara day tour from Osaka/Kyoto for those who want a guide."
        ),
    },
    3: {  # Thursday — 低競合: 具体的な日本旅行プランニングガイド
        "theme": "Planning Your Japan Trip Budget: What to Expect",
        "category_slug": "travel-tips",
        "category_name": "Travel Tips",
        "shortcodes": ['[jtb_hotel city="Tokyo" area="Shinjuku"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "prompt_context": (
            "Write a practical planning guide for what a Japan trip actually looks and costs, "
            "framed around what experiences you get at each level — not around minimizing spend. "
            "Target query: 'how much does a trip to japan cost from australia 2026'. "
            "Give REAL numbers as reference points, always tied to the experience: "
            "Flights SYD–TYO: $800–1,400 return depending on airline and flexibility. "
            "Accommodation: capsule/hostel ($30–50/night, social and central), "
            "business hotel ($100–180/night, comfortable, great locations), "
            "ryokan with kaiseki dinner and private onsen ($250–450/night, a must-do at least one night). "
            "Food: convenience store breakfast ($5–8, surprisingly good and part of the Japan experience), "
            "ramen or soba lunch ($10–15), omakase or specialty dinner ($60–120, worth it once or twice). "
            "Transport: IC card covers all city transit seamlessly; JR Pass unlocks multi-city travel. "
            "Attractions: most temples/shrines free or under ¥1,000; teamLab ($30), Disneyland (¥9,400). "
            "Frame totals by experience level: 'a comfortable 7-day trip runs $3,500–5,500 AUD all-in, "
            "with the biggest variable being accommodation and dining choices.' "
            "Affiliate: Booking.com hotels at each tier, Klook experiences. "
            "Somewhere natural in the article, include a CTA like: "
            "'<p>Want a personalised estimate for your specific trip? "
            "<a href=\"https://japantravelbase.com/tools/japan-trip-budget-calculator/\">Try our free Japan Budget Calculator</a>"
            "— choose your travel style and duration to get a breakdown in seconds.</p>'"
        ),
    },
    4: {  # Friday — 低競合: 訪日前に知るべき実用Tips（具体的）
        "theme": "Japan First-Timer Mistakes to Avoid",
        "category_slug": "travel-tips",
        "category_name": "Travel Tips",
        "shortcodes": ['[jtb_esim]'],
        "prompt_context": (
            "Write about the 10 most common mistakes first-time Japan visitors make, "
            "with specific solutions for each. "
            "Target query: 'mistakes to avoid in japan first time'. "
            "Cover real, specific mistakes: "
            "1) Not carrying cash (ATMs: 7-Eleven/Japan Post only reliably accept foreign cards), "
            "2) Buying a JR Pass without checking if it actually covers your itinerary (a Tokyo-only trip doesn't need one), "
            "3) Booking non-refundable hotels before confirming visa, "
            "4) Not getting a pocket WiFi or eSIM (roaming costs $15–30/day), "
            "5) Wearing shoes that lace up tightly (remove shoes 20x/day at temples), "
            "6) Packing too much (coin lockers ¥400–700/day, luggage forwarding service), "
            "7) Skipping Nara/Hakone/Kamakura because 'staying in Tokyo', "
            "8) Eating at tourist-trap restaurants near Senso-ji, "
            "9) Not downloading Google Maps offline, "
            "10) Missing the last train (typically 11:30pm–midnight). "
            "Affiliate: eSIM for Japan, Klook day tours."
        ),
    },
    5: {  # Saturday — 低競合: 具体的な滞在エリア比較（Airbnb/ホテル）
        "theme": "Tokyo Neighborhood Guide for Tourists",
        "category_slug": "tokyo",
        "category_name": "Tokyo",
        "shortcodes": ['[jtb_hotel city="Tokyo" area="Shinjuku"]'],
        "prompt_context": (
            "Compare Tokyo's top neighborhoods for tourist stays with honest pros/cons. "
            "Target query: 'best area to stay in tokyo for first time tourist'. "
            "Cover 5 specific areas with REAL details: "
            "Shinjuku (transport hub, JR lines to Fuji/Nikko, nightlife, ¥12,000–25,000/night hotels), "
            "Shibuya (crossing, Harajuku walk, younger vibe, slightly pricier), "
            "Asakusa (traditional feel, near Senso-ji, cheaper hotels ¥8,000–15,000, older area), "
            "Ginza (luxury, 5-min walk to Tokyo Station, expensive ¥20,000+), "
            "Akihabara (niche: anime/electronics fans only). "
            "Give a DIRECT recommendation: 'For most first-timers, Shinjuku wins because...' "
            "Affiliate: Booking.com Shinjuku hotels."
        ),
    },
    6: {  # Sunday — 低競合: 北海道vs沖縄 どちらを選ぶか
        "theme": "Hokkaido vs Okinawa — Which to Choose",
        "category_slug": "itineraries",
        "category_name": "Itineraries",
        "shortcodes": ['[jtb_hotel city="Sapporo" area="Sapporo"]',
                       '[jtb_hotel city="Naha" area="Naha"]',
                       '[jtb_flight origin="SYD" destination="CTS"]'],
        "prompt_context": (
            "Answer the specific question: 'Should I visit Hokkaido or Okinawa?' "
            "Target query: 'hokkaido vs okinawa which is better'. "
            "Structure as a direct comparison: "
            "Best season each (Hokkaido: June–Aug lavender, Feb snow festival; Okinawa: Apr–Jun, Oct–Nov), "
            "Getting there (both need domestic flight from Tokyo: ~$80–150 AUD one-way), "
            "Cost difference (Hokkaido slightly cheaper, Okinawa resort prices), "
            "Activities (Hokkaido: nature/food/skiing; Okinawa: beaches/diving/Ryukyu culture), "
            "Food (Hokkaido: dairy, crab, ramen; Okinawa: goya champuru, Awamori, sea grapes), "
            "Give a clear recommendation matrix: 'Choose Hokkaido if... Choose Okinawa if...' "
            "Affiliate: Booking.com for each, Klook activities (lavender farm tours, snorkeling)."
        ),
    },
}

SEASONAL_CONTEXTS = {
    1:  "Winter Japan travel — snow festivals (Sapporo Snow Festival in February), "
        "skiing in Niseko/Hakuba, kotatsu culture, and onsen towns.",
    2:  "Late winter into spring — plum blossoms (ume) as cherry blossom precursors, "
        "best early-spring destinations like Atami and Mito.",
    3:  "Cherry blossom (sakura) season — top hanami spots, forecast tracking, "
        "best parks in Tokyo/Kyoto/Osaka, picnic etiquette.",
    4:  "Golden Week (late April–early May) — how to navigate crowds, "
        "alternative destinations, booking strategies.",
    5:  "Early summer — sando festivals, firefly watching, avoiding rainy-season (tsuyu) blues.",
    6:  "Summer in Japan — Gion Matsuri in Kyoto, Obon, fireworks festivals (hanabi), "
        "beach destinations like Okinawa.",
    7:  "Peak summer — beating the heat tips, yukata and summer festivals, "
        "Hiroshima Peace Memorial Day (Aug 6), Awa Odori in Tokushima.",
    8:  "Late summer / early autumn — typhoon season awareness, "
        "early autumn foliage in Hokkaido, food harvest season.",
    9:  "Autumn foliage (koyo) — best momiji spots, peak timing by region, "
        "Nikko, Kyoto's Eikan-do and Tofuku-ji temples.",
    10: "Deep autumn colours and harvest festivals — Shichi-Go-San, "
        "autumn food (matsutake mushrooms, new rice), late koyo in Tokyo.",
    11: "Early winter — illuminations and winter illumination events "
        "(Nabana no Sato, Ashikaga Flower Park), hot pot culture.",
    12: "Christmas in Japan (a unique cultural experience), "
        "New Year (oshogatsu) traditions, hatsumode (first shrine visit), "
        "osechi ryori, and best spots to ring in the New Year.",
}


def get_seasonal_context() -> str:
    month = datetime.now().month
    return (
        "Write about seasonal Japan travel for this time of year. "
        + SEASONAL_CONTEXTS.get(month, "General seasonal travel in Japan.")
    )


# ---------------------------------------------------------------------------
# SEO Insights loader (GSC + Competitor analysis)
# ---------------------------------------------------------------------------

def load_seo_insights() -> dict:
    """GSCと競合分析のインサイトを読み込む"""
    insights = {"gsc": None, "competitors": None}

    if os.path.exists("gsc_insights.json"):
        try:
            with open("gsc_insights.json", encoding="utf-8") as f:
                insights["gsc"] = json.load(f)
        except Exception:
            pass

    if os.path.exists("competitor_insights.json"):
        try:
            with open("competitor_insights.json", encoding="utf-8") as f:
                insights["competitors"] = json.load(f)
        except Exception:
            pass

    return insights


def get_strengthen_config(gsc_data: dict) -> dict | None:
    """GSCデータから強化すべき記事のテーマを生成"""
    if not gsc_data:
        return None
    pages = gsc_data.get("priority_pages", [])
    if not pages:
        return None

    top = pages[0]
    url = top.get("url", "")
    queries = [q["query"] for q in top.get("top_queries", [])[:3]]
    position = top.get("avg_position", 0)
    slug = url.rstrip("/").split("/")[-1].replace("-", " ")

    return {
        "theme": f"SEO Strengthen: {slug}",
        "category_slug": "travel-tips",
        "category_name": "Travel Tips",
        "shortcodes": ['[jtb_hotel city="Tokyo" area="Shinjuku"]'],
        "prompt_context": (
            f"Write an improved, comprehensive article targeting these search queries: {', '.join(queries)}. "
            f"The existing page ranks at position {position:.0f} and needs a stronger, more detailed version. "
            f"Cover the topic more thoroughly than existing results by including specific details, "
            f"current 2026 information, and practical advice that competitors miss."
        ),
        "_gsc_url": url,
        "_gsc_queries": queries,
    }


def build_competitor_context(competitor_data: dict, theme: str) -> str:
    """競合分析からニッチなアングルを抽出してプロンプトに追加"""
    if not competitor_data:
        return ""

    insights = competitor_data.get("insights", [])
    if not insights:
        return ""

    niches = []
    opportunities = []
    for insight in insights[:2]:
        analysis = insight.get("analysis", {})
        niches.extend(analysis.get("underserved_niches", [])[:2])
        for opp in analysis.get("content_opportunities", [])[:2]:
            if isinstance(opp, dict):
                opportunities.append(opp.get("title", ""))
            else:
                opportunities.append(str(opp))

    if not niches and not opportunities:
        return ""

    parts = ["\n\nSEO Intelligence (use these insights to differentiate from competitors):"]
    if niches:
        parts.append("Underserved angles competitors miss: " + "; ".join(niches[:3]))
    if opportunities:
        parts.append("Content opportunities: " + "; ".join(filter(None, opportunities[:3])))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal link helpers
# ---------------------------------------------------------------------------

def fetch_existing_posts(limit: int = 50) -> list[dict]:
    """Fetch recent published posts from WP for internal linking context."""
    wp_url  = os.getenv("WP_URL", "").rstrip("/")
    wp_user = os.getenv("WP_USER", "").strip()
    wp_pass = os.getenv("WP_APP_PASSWORD", "").strip()
    if not all([wp_url, wp_user, wp_pass]):
        return []
    try:
        resp = requests.get(
            f"{wp_url}/wp-json/wp/v2/posts",
            params={"status": "publish", "per_page": limit, "_fields": "title,link"},
            auth=(wp_user, wp_pass),
            timeout=15,
        )
        if not resp.ok:
            return []
        return [
            {"title": p["title"]["rendered"], "url": p["link"]}
            for p in resp.json()
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Schema.org JSON-LD builder
# ---------------------------------------------------------------------------

def build_jsonld_block(article: dict, theme: str) -> str:
    """Return <script> tags with FAQPage + TravelAction JSON-LD."""
    faq_items = article.get("faq_items", [])
    blocks = []

    if faq_items:
        faq_ld = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": item["question"],
                    "acceptedAnswer": {"@type": "Answer", "text": item["answer"]},
                }
                for item in faq_items
            ],
        }
        blocks.append(
            '<script type="application/ld+json">\n'
            + json.dumps(faq_ld, ensure_ascii=False, indent=2)
            + "\n</script>"
        )

    travel_ld = {
        "@context": "https://schema.org",
        "@type": "TravelAction",
        "name": f"Travel to Japan — {theme}",
        "description": article["meta_description"],
        "toLocation": {
            "@type": "Country",
            "name": "Japan",
        },
    }
    blocks.append(
        '<script type="application/ld+json">\n'
        + json.dumps(travel_ld, ensure_ascii=False, indent=2)
        + "\n</script>"
    )

    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Article generation
# ---------------------------------------------------------------------------

CURRENT_YEAR = 2026

SYSTEM_PROMPT = f"""You are a professional travel writer for Japan Travel Base (japantravelbase.com),
a leading English-language travel blog targeting foreign visitors to Japan —
primarily from Australia, the USA, the UK, and Southeast Asia.

IMPORTANT: Today is {CURRENT_YEAR}. All information must be current as of {CURRENT_YEAR}:
- Use up-to-date prices in USD/AUD (e.g. JR Pass prices updated Oct 2023, now ~$400-700)
- Reference current Japan entry rules (visa-free for most Western passport holders)
- Mention cashless payment expansion (IC cards, PayPay, credit cards now widely accepted)
- Note any {CURRENT_YEAR} travel trends (record tourist numbers, overtourism measures)
- Write years explicitly as "{CURRENT_YEAR}" where helpful — never use vague "recently"

Your writing style:
- Friendly, knowledgeable, enthusiastic yet practical
- Written in clear, accessible English for non-native speakers too
- SEO-optimised: natural keyword use in headings and first paragraphs
- Length: 1000–1500 words of body content (not counting shortcodes)

LONG-TAIL KEYWORD FOCUS (critical for SEO):
- Target a specific, long-tail angle (e.g. "how to use the JR Pass on Shinkansen without a reservation"
  NOT "JR Pass guide")
- Identify ONE primary question this article answers and state it in the first sentence
  (e.g. "Confused about whether the JR Pass covers reserved Shinkansen seats?")
- Give a direct 2–3 sentence answer to that question within the first 100 words
  (this format is picked up by Google's AI Overview)
- Every H2 heading should address a specific sub-question a real traveler would search for

Always structure the article with:
1. Intro: primary question → direct answer → expand with context
2. Multiple H2 sections with H3 sub-sections (each H2 = a real traveler sub-question)
3. Bullet lists or numbered lists for tips/steps
4. Relevant shortcodes inserted naturally in the flow (not at the very end)
5. FAQ section (H2: "Frequently Asked Questions") with 5–7 Q&As covering long-tail queries
6. Final H2: "Plan Your Japan Trip Today" with CTA

Return ONLY raw JSON — no markdown fences, no extra text — in this exact shape:
{{
  "title": "<SEO title — rules below>",
  "meta_description": "<meta description — rules below>",
  "focus_keyword": "<primary long-tail keyword phrase, 3-5 words>",
  "primary_question": "<the one specific question this entire article answers>",
  "faq_items": [
    {{"question": "...", "answer": "..."}},
    {{"question": "...", "answer": "..."}}
  ],
  "content": "<full HTML article body, using <h2>/<h3>/<p>/<ul>/<ol>/<li> tags — include FAQ as HTML>"
}}

TITLE RULES (critical for click-through rate):
- Max 60 characters
- Start with the target keyword (first 2–3 words = keyword)
- Be specific: answer WHAT the reader gets ("Hakone Day Trip: Exact Route & Timings")
- FORBIDDEN phrases: "Complete Guide", "Ultimate Guide", "Everything You Need", "Your Guide"
- FORBIDDEN opener: "Japan Travel Base:" prefix — never start with the site name
- Include year ({CURRENT_YEAR}) only if it fits naturally
- Good examples: "Hakone Day Trip: What to Do, See & Skip (2026)", "JR Pass Worth It? Honest Answer by Trip Length"

META DESCRIPTION RULES (critical for click-through rate):
- 140–155 characters exactly
- Open with a direct answer to the primary question — not a question, not "Learn how to..."
- Include one specific concrete detail (a place name, a number, a price range)
- Close with an action phrase: "Here's exactly what to plan", "Use this to decide", "Find out below"
- FORBIDDEN openers: "Japan Travel Base", "Discover", "Explore", "In this guide", "Are you..."
- Good example: "The JR Pass pays off on trips covering Tokyo–Kyoto–Osaka and beyond. Here's the exact calculation for your itinerary."

The "faq_items" array must have 5–7 items. The FAQ section in "content" should be plain HTML
(no JSON-LD there — structured data is injected separately).
Insert shortcodes as plain text inside <p> tags or on their own line between paragraphs.
Example: <p>[jtb_flight origin="SYD" destination="TYO"]</p>
"""


def build_user_prompt(
    day_config: dict,
    seo_insights: dict | None = None,
    existing_posts: list[dict] | None = None,
) -> str:
    theme = day_config["theme"]
    context = day_config["prompt_context"] or get_seasonal_context()
    shortcodes = day_config["shortcodes"]

    # 競合分析のインサイトを追加
    competitor_context = ""
    if seo_insights and seo_insights.get("competitors"):
        competitor_context = build_competitor_context(seo_insights["competitors"], theme)

    # Internal links block
    internal_link_block = ""
    if existing_posts:
        link_list = "\n".join(
            f"- {p['title']} → {p['url']}" for p in existing_posts[:20]
        )
        internal_link_block = (
            "\n\nINTERNAL LINKS — naturally embed 2–3 of these existing articles as "
            '<a href="URL">anchor text</a> links where contextually relevant '
            "(do NOT force them — only include where they genuinely add value):\n"
            + link_list
        )

    shortcode_instructions = "\n".join(
        f'- Insert the shortcode {sc} once, in a contextually appropriate place.'
        for sc in shortcodes
    )

    return f"""Write a Japan travel article on the theme: **{theme}**

Context / angle:
{context}{competitor_context}{internal_link_block}

Shortcodes to include (insert them naturally in the HTML content):
{shortcode_instructions}

Remember: respond with raw JSON only (no ```json fences).
"""


def generate_article(
    day_config: dict,
    seo_insights: dict | None = None,
    existing_posts: list[dict] | None = None,
) -> dict:
    """Call Claude API and return parsed article dict."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set in .env")

    client = anthropic.Anthropic(api_key=api_key)

    print(f"  Calling Claude API (streaming) for theme: {day_config['theme']} …")

    RETRY_WAITS = [30, 60, 120]
    full_text = ""
    for attempt, wait in enumerate([0] + RETRY_WAITS):
        if wait:
            print(f"  [RETRY {attempt}/{len(RETRY_WAITS)}] overloaded — {wait}秒後に再試行…")
            time.sleep(wait)
        try:
            full_text = ""
            with client.messages.stream(
                model="claude-sonnet-4-0",
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": build_user_prompt(day_config, seo_insights, existing_posts)}
                ],
            ) as stream:
                for text_chunk in stream.text_stream:
                    full_text += text_chunk
                    print(text_chunk, end="", flush=True)
            # 空レスポンスはリトライ
            if not full_text.strip():
                if attempt < len(RETRY_WAITS):
                    print(f"\n  [WARN] Empty response from Claude, retrying…")
                    continue
                raise ValueError("Claude returned empty response after all retries")
            break  # 成功
        except (anthropic.InternalServerError, anthropic.RateLimitError) as exc:
            if attempt < len(RETRY_WAITS):
                continue
            raise

    print()  # newline after streaming

    # Parse JSON response
    import re as _re

    def _extract_article(text: str) -> dict:
        # Strip backtick fences
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.rsplit("```", 1)[0].strip()

        # Remove non-printable control chars except \n \r \t
        cleaned = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)

        if not cleaned:
            raise json.JSONDecodeError("Empty response from Claude", "", 0)

        def _extract_field(t: str, key: str) -> str:
            pattern = rf'"{_re.escape(key)}"\s*:\s*"(.*?)(?<!\\)"(?=\s*[,}}])'
            m = _re.search(pattern, t, _re.DOTALL)
            return m.group(1) if m else ""

        # Attempt 1: direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Attempt 2: escape literal newlines inside JSON string values, then retry
        try:
            fixed = _re.sub(
                r'("(?:[^"\\]|\\.)*")',
                lambda m: m.group(0).replace('\n', '\\n').replace('\r', '\\r'),
                cleaned,
                flags=_re.DOTALL,
            )
            return json.loads(fixed)
        except (ValueError, json.JSONDecodeError):
            pass

        # Attempt 3: manual regex extraction of each field
        title       = _extract_field(cleaned, "title")
        meta        = _extract_field(cleaned, "meta_description")
        keyword     = _extract_field(cleaned, "focus_keyword")
        question    = _extract_field(cleaned, "primary_question")
        content_raw = _extract_field(cleaned, "content")
        if not title or not content_raw:
            raise json.JSONDecodeError("Could not parse response", cleaned, 0)

        faq_match = _re.search(r'"faq_items"\s*:\s*(\[.*?\])', cleaned, _re.DOTALL)
        faq_items = []
        if faq_match:
            try:
                faq_items = json.loads(faq_match.group(1))
            except Exception:
                pass

        return {
            "title":            title.replace('\\"', '"'),
            "meta_description": meta.replace('\\"', '"'),
            "focus_keyword":    keyword,
            "primary_question": question,
            "faq_items":        faq_items,
            "content":          content_raw.replace('\\"', '"').replace("\\n", "\n"),
        }

    try:
        article = _extract_article(full_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON response: {exc}\n---\n{full_text[:500]}") from exc

    required_keys = {"title", "meta_description", "content"}
    missing = required_keys - article.keys()
    if missing:
        raise ValueError(f"Claude response missing keys: {missing}")

    # Ensure faq_items is a list (fallback to empty)
    if not isinstance(article.get("faq_items"), list):
        article["faq_items"] = []

    return article


# ---------------------------------------------------------------------------
# Featured image: Pexels（本物の旅行写真）→ WordPress
# ---------------------------------------------------------------------------

# Per-theme Pexels search queries.
PEXELS_QUERIES = {
    "Tokyo":                 "Tokyo Japan skyline night",
    "Osaka / Kyoto":         "Kyoto Japan torii gates temple",
    "Transport in Japan":    "Shinkansen bullet train Japan Mount Fuji",
    "Seasonal Travel":       "Japan spring green foliage golden week",
    "Travel Tips":           "Japan travel passport map",
    "Japan Itineraries":     "Japan travel golden route Kyoto Tokyo",
    "eSIM & Wi-Fi in Japan": "smartphone Japan city travel",
}


def generate_featured_image(theme: str) -> bytes | None:
    """Pexelsから本物の旅行写真を取得してJPEGバイトを返す。失敗時はNone。"""
    pexels_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not pexels_key:
        print("  [SKIP] PEXELS_API_KEY not set — skipping image fetch.")
        return None

    # themeからPexelsクエリを決定（未知のテーマはfallback）
    query = PEXELS_QUERIES.get(theme, f"{theme} Japan travel")
    print(f"  Pexelsで写真取得中: {query} …")

    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": pexels_key},
            params={"query": query, "per_page": 15, "orientation": "landscape"},
            timeout=15,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])

        # 結果がなければ汎用クエリでリトライ
        if not photos:
            print(f"  [WARN] '{query}' で結果なし — 'Japan travel' で再試行")
            resp2 = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": pexels_key},
                params={"query": "Japan travel", "per_page": 15, "orientation": "landscape"},
                timeout=15,
            )
            resp2.raise_for_status()
            photos = resp2.json().get("photos", [])

        if not photos:
            print("  [SKIP] Pexels: 写真が見つかりませんでした")
            return None

        photo_url = random.choice(photos[:10])["src"]["large2x"]
        img_resp = requests.get(photo_url, timeout=30)
        img_resp.raise_for_status()
        print(f"  ✓ Pexels写真取得完了 ({len(img_resp.content) // 1024} KB)")
        return img_resp.content

    except Exception as exc:
        print(f"  [WARN] Pexels取得失敗: {exc}")
        return None


def upload_image_to_wp(image_bytes: bytes, title: str, auth: tuple) -> int | None:
    """Upload image bytes to WordPress Media Library. Returns attachment ID."""
    wp_url  = os.getenv("WP_URL", "").rstrip("/")
    endpoint = f"{wp_url}/wp-json/wp/v2/media"

    # Build a filesystem-safe filename from the article title.
    safe_title = title.lower()
    for ch in " /\\:*?\"<>|'":
        safe_title = safe_title.replace(ch, "-")
    safe_title = safe_title[:60].strip("-")
    filename = f"{safe_title}-{datetime.now().strftime('%Y%m%d')}.jpg"

    print(f"  Uploading image to WordPress as '{filename}' …")

    resp = requests.post(
        endpoint,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "image/jpeg",
        },
        data=image_bytes,
        auth=auth,
        timeout=60,
    )
    resp.raise_for_status()
    attachment_id = resp.json()["id"]
    print(f"  Image uploaded — attachment ID: {attachment_id}")
    return attachment_id


def set_featured_image(post_id: int, attachment_id: int, auth: tuple) -> None:
    """Set the featured image (thumbnail) on a WordPress post."""
    wp_url = os.getenv("WP_URL", "").rstrip("/")
    resp = requests.post(
        f"{wp_url}/wp-json/wp/v2/posts/{post_id}",
        json={"featured_media": attachment_id},
        auth=auth,
        timeout=15,
    )
    resp.raise_for_status()
    print(f"  Featured image set on post {post_id}.")


# ---------------------------------------------------------------------------
# WordPress REST API
# ---------------------------------------------------------------------------

def get_or_create_category(wp_url: str, auth: tuple, slug: str, name: str) -> int | None:
    """Return WP category ID, creating it if it doesn't exist. Returns None on failure."""
    endpoint = f"{wp_url}/wp-json/wp/v2/categories"

    try:
        # Check if category exists
        resp = requests.get(endpoint, params={"slug": slug}, auth=auth, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data:
            return data[0]["id"]

        # Create it
        resp = requests.post(
            endpoint,
            json={"name": name, "slug": slug},
            auth=auth,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception as e:
        print(f"  [WARN] Category lookup/create failed ({e}), posting without category")


def post_draft(article: dict, day_config: dict) -> int:
    """Post article as a WordPress draft. Returns the new post ID."""
    wp_url  = os.getenv("WP_URL", "").rstrip("/")
    wp_user = os.getenv("WP_USER", "").strip()
    wp_pass = os.getenv("WP_APP_PASSWORD", "").strip()

    for var, val in [("WP_URL", wp_url), ("WP_USER", wp_user), ("WP_APP_PASSWORD", wp_pass)]:
        if not val:
            raise EnvironmentError(f"{var} is not set in .env")

    auth     = (wp_user, wp_pass)
    endpoint = f"{wp_url}/wp-json/wp/v2/posts"

    category_id = get_or_create_category(
        wp_url, auth,
        day_config["category_slug"],
        day_config["category_name"],
    )

    # Build post body
    # Prepend meta description as an HTML comment (readable by SEO plugins)
    meta_comment = f'<!-- meta_description: {article["meta_description"]} -->\n'
    full_content = meta_comment + article["content"]

    payload = {
        "title":      article["title"],
        "content":    full_content,
        "status":     "publish",
        **({"categories": [category_id]} if category_id else {}),
        "meta": {
            "_yoast_wpseo_metadesc":     article["meta_description"],
            "_aioseo_description":       article["meta_description"],
            "rank_math_description":     article["meta_description"],
        },
    }

    resp = requests.post(endpoint, json=payload, auth=auth, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def log_success(title: str, category: str, post_id: int) -> None:
    file_exists = os.path.isfile(LOG_CSV_FILE)
    with open(LOG_CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["datetime", "title", "category", "wp_post_id"])
        writer.writerow([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            title,
            category,
            post_id,
        ])


def log_error(message: str) -> None:
    logging.error(message)
    print(f"\n[ERROR] {message}", file=sys.stderr)


def count_today_posts() -> int:
    """Count how many articles were posted today (UTC date) in article_log.csv."""
    if not os.path.isfile(LOG_CSV_FILE):
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    try:
        with open(LOG_CSV_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("datetime", "").startswith(today):
                    count += 1
    except Exception:
        pass
    return count


def is_theme_recently_published(theme: str, days: int = 90) -> bool:
    """90日以内に同テーマが投稿済みか確認（重複防止）。"""
    if not os.path.isfile(LOG_CSV_FILE):
        return False
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    # テーマから意味のある単語を抽出（ストップワードを除く）
    stop = {"a", "the", "in", "to", "of", "for", "and", "or", "vs", "your", "how"}
    theme_words = {w.lower() for w in theme.split() if w.lower() not in stop and len(w) > 2}
    try:
        with open(LOG_CSV_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    row_dt = datetime.fromisoformat(
                        row["datetime"].replace(" UTC", "+00:00")
                    )
                except Exception:
                    continue
                if row_dt < cutoff:
                    continue
                row_title = row.get("title", "").lower()
                matches = sum(1 for w in theme_words if w in row_title)
                if matches >= 2:
                    return True
    except Exception:
        pass
    return False


def find_fresh_day_config(current_weekday: int) -> dict:
    """直近90日に投稿済みでないテーマを今日から探して返す。"""
    for offset in range(len(SCHEDULE)):
        candidate = (current_weekday + offset) % len(SCHEDULE)
        config = SCHEDULE[candidate]
        if not is_theme_recently_published(config["theme"]):
            if offset > 0:
                print(
                    f"  [SKIP] '{SCHEDULE[current_weekday]['theme']}' は直近90日内に投稿済 → "
                    f"'{config['theme']}' に変更"
                )
            return config
    # 全テーマ投稿済みの場合はスキップ（重複生成を防ぐ）
    print("  [SKIP] 全テーマが直近90日内に投稿済。本日は記事生成をスキップします。")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DAILY_POST_LIMIT = 2

def main() -> None:
    today_count = count_today_posts()
    if today_count >= DAILY_POST_LIMIT:
        print(f"  ⏸  本日の投稿上限 ({DAILY_POST_LIMIT}本) に達しています ({today_count}本投稿済)。スキップします。")
        sys.exit(0)

    strengthen_mode = "--strengthen" in sys.argv
    weekday = datetime.now().weekday()  # 0=Mon … 6=Sun

    # SEOインサイトを読み込む
    seo_insights = load_seo_insights()

    # --strengthen モード or 水曜日はGSC強化記事を生成
    if strengthen_mode or (weekday == 2 and seo_insights.get("gsc")):
        strengthen_config = get_strengthen_config(seo_insights.get("gsc"))
        if strengthen_config:
            day_config = strengthen_config
            print(f"  [SEO Mode] GSC強化記事を生成: {strengthen_config.get('_gsc_url', '')}")
        else:
            day_config = find_fresh_day_config(weekday)
    else:
        day_config = find_fresh_day_config(weekday)

    print("=" * 60)
    print(f"  Japan Travel Base — Article Generator")
    print(f"  Date    : {datetime.now().strftime('%Y-%m-%d %A')}")
    print(f"  Theme   : {day_config['theme']}")
    print(f"  Category: {day_config['category_name']}")
    if seo_insights.get("competitors"):
        print(f"  SEO     : 競合分析インサイトあり ✓")
    print("=" * 60)

    # Fetch existing posts for internal linking
    existing_posts = fetch_existing_posts()
    if existing_posts:
        print(f"  Internal links: {len(existing_posts)} existing posts loaded ✓")

    # 1. Generate article
    try:
        article = generate_article(day_config, seo_insights, existing_posts)
    except (EnvironmentError, ValueError, anthropic.APIError) as exc:
        log_error(f"Article generation failed — {exc}")
        sys.exit(1)

    print(f"\n  Title   : {article['title']}")
    print(f"  Meta    : {article['meta_description']}")
    print(f"  Words   : ~{len(article['content'].split())} (HTML)")

    # 2. Save article to pending_articles/ for WordPress to fetch
    os.makedirs("pending_articles", exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    article_filename = f"article_{timestamp}.json"
    article_path = os.path.join("pending_articles", article_filename)

    # 3. Generate featured image and save as separate file
    image_filename = None
    try:
        image_bytes = generate_featured_image(day_config["theme"])
        if image_bytes:
            image_filename = f"image_{timestamp}.jpg"
            image_path = os.path.join("pending_articles", image_filename)
            with open(image_path, "wb") as f:
                f.write(image_bytes)
            print(f"  ✓ Featured image saved ({len(image_bytes)//1024}KB)")
    except Exception as exc:
        print(f"  [WARN] Image generation failed: {exc}")

    # Build JSON-LD and prepend to content
    jsonld = build_jsonld_block(article, day_config["theme"])
    meta_comment = f'<!-- meta_description: {article["meta_description"]} -->\n'
    full_content = jsonld + "\n" + meta_comment + article["content"]

    pending_payload = {
        "title":            article["title"],
        "content":          full_content,
        "meta_description": article["meta_description"],
        "focus_keyword":    article.get("focus_keyword", ""),
        "primary_question": article.get("primary_question", ""),
        "category_slug":    day_config["category_slug"],
        "category_name":    day_config["category_name"],
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "image_filename":   image_filename,
    }
    with open(article_path, "w", encoding="utf-8") as f:
        json.dump(pending_payload, f, ensure_ascii=False, indent=2)

    # Update index.json (retry on EDEADLK / transient OS errors)
    index_path = "pending_articles/index.json"
    for _attempt in range(5):
        try:
            if os.path.exists(index_path):
                with open(index_path, "r", encoding="utf-8") as f:
                    index_data = json.load(f)
            else:
                index_data = {"files": []}
            index_data["files"].append(article_filename)
            _tmp = index_path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(index_data, f, indent=2)
            os.replace(_tmp, index_path)
            break
        except OSError as _e:
            import time as _time
            print(f"  [WARN] index.json write attempt {_attempt+1} failed: {_e} — retrying...")
            _time.sleep(0.5)
    else:
        print("  [ERROR] index.json update failed after 5 attempts — article saved but not indexed")

    print(f"\n  ✓ Article saved to {article_path}")
    print(f"  ✓ WordPress will fetch and create draft automatically")
    print("=" * 60)


if __name__ == "__main__":
    main()
