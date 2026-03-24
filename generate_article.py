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
import re
import sys
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
    0: {  # Monday
        "theme": "Tokyo",
        "category_slug": "tokyo",
        "category_name": "Tokyo",
        "shortcodes": ['[jtb_hotel city="Tokyo" area="Shinjuku"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "prompt_context": (
            "Focus on area-by-area hotel recommendations and must-see attractions "
            "in Tokyo. Cover areas like Shinjuku, Shibuya, Asakusa, Akihabara, and "
            "Harajuku. Include practical tips for navigating each area."
        ),
    },
    1: {  # Tuesday
        "theme": "Osaka / Kyoto",
        "category_slug": "osaka-kyoto",
        "category_name": "Osaka & Kyoto",
        "shortcodes": ['[jtb_hotel city="Osaka" area="Dotonbori"]',
                       '[jtb_flight origin="SYD" destination="KIX"]'],
        "prompt_context": (
            "Write a comprehensive travel guide covering both Osaka and Kyoto. "
            "Include top food spots in Osaka (Dotonbori, Kuromon Market), "
            "cultural landmarks in Kyoto (Fushimi Inari, Arashiyama, Gion), "
            "and tips for day-tripping between the two cities."
        ),
    },
    2: {  # Wednesday
        "theme": "Transport in Japan",
        "category_slug": "transport",
        "category_name": "Transport",
        "shortcodes": ['[jtb_flight origin="SYD" destination="TYO"]'],
        "prompt_context": (
            "Explain Japan's transport system for first-time visitors: "
            "JR Pass value analysis, Shinkansen tips, IC cards (Suica/Pasmo), "
            "local subway navigation, airport transfers (Narita vs Haneda), "
            "and budget airline options within Japan."
        ),
    },
    3: {  # Thursday — seasonal (auto-detected from current month)
        "theme": "Seasonal Travel",
        "category_slug": "seasonal",
        "category_name": "Seasonal Travel",
        "shortcodes": ['[jtb_hotel city="Tokyo" area="Ueno"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "prompt_context": None,  # built dynamically below
    },
    4: {  # Friday
        "theme": "Travel Tips",
        "category_slug": "travel-tips",
        "category_name": "Travel Tips",
        "shortcodes": ['[jtb_esim]'],
        "prompt_context": (
            "Cover essential Japan travel tips for foreigners: cash vs card culture, "
            "currency exchange (airport vs 7-Eleven ATM vs Wise), phone/SIM options, "
            "etiquette (shoes, chopsticks, train manners), tipping culture, "
            "and useful apps (Google Maps, Google Translate, Japan Official Travel App)."
        ),
    },
    5: {  # Saturday
        "theme": "Japan Itineraries",
        "category_slug": "itineraries",
        "category_name": "Itineraries",
        "shortcodes": ['[jtb_hotel city="Tokyo" area="Shinjuku"]',
                       '[jtb_hotel city="Kyoto" area="Gion"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "prompt_context": (
            "Create a detailed Japan itinerary guide with three options: "
            "3-day Tokyo blitz, 5-day Tokyo + Kyoto classic, and 7-day Golden Route "
            "(Tokyo → Nikko or Hakone → Mt. Fuji → Kyoto → Osaka). "
            "Include day-by-day breakdowns, budget estimates, and booking tips."
        ),
    },
    6: {  # Sunday
        "theme": "eSIM & Wi-Fi in Japan",
        "category_slug": "esim-wifi",
        "category_name": "eSIM & Wi-Fi",
        "shortcodes": ['[jtb_esim]'],
        "prompt_context": (
            "Compare all connectivity options for Japan visitors: pocket Wi-Fi rental "
            "(pros/cons, cost), physical SIM cards (IIJmio, Mobal), eSIM providers "
            "(Airalo, Holafly, Ubigi), and free Wi-Fi spots. "
            "Include a clear recommendation for each traveler type."
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

Always structure the article with:
1. An engaging introduction (hook + what the article covers)
2. Multiple H2 sections with H3 sub-sections where appropriate
3. Bullet lists or numbered lists for tips/steps
4. Relevant shortcodes inserted naturally in the flow (not at the very end)
5. A final H2 section titled "## Plan Your Japan Trip Today" with a CTA

Return ONLY raw JSON — no markdown fences, no extra text — in this exact shape:
{{
  "title": "<SEO title, max 65 chars>",
  "meta_description": "<155 char max meta description>",
  "content": "<full HTML article body, using <h2>/<h3>/<p>/<ul>/<ol>/<li> tags>"
}}

Important for shortcode placement:
- Insert shortcodes as plain text inside <p> tags or on their own line between paragraphs.
- Example: <p>[jtb_flight origin="SYD" destination="TYO"]</p>
"""


def build_user_prompt(day_config: dict, seo_insights: dict | None = None) -> str:
    theme = day_config["theme"]
    context = day_config["prompt_context"] or get_seasonal_context()
    shortcodes = day_config["shortcodes"]

    # 競合分析のインサイトを追加
    competitor_context = ""
    if seo_insights and seo_insights.get("competitors"):
        competitor_context = build_competitor_context(seo_insights["competitors"], theme)

    shortcode_instructions = "\n".join(
        f'- Insert the shortcode {sc} once, in a contextually appropriate place.'
        for sc in shortcodes
    )

    return f"""Write a Japan travel article on the theme: **{theme}**

Context / angle:
{context}{competitor_context}

Shortcodes to include (insert them naturally in the HTML content):
{shortcode_instructions}

Remember: respond with raw JSON only (no ```json fences).
"""


def generate_article(day_config: dict, seo_insights: dict | None = None) -> dict:
    """Call Claude API and return parsed {title, meta_description, content}."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set in .env")

    client = anthropic.Anthropic(api_key=api_key)

    print(f"  Calling Claude API (streaming) for theme: {day_config['theme']} …")

    full_text = ""
    with client.messages.stream(
        model="claude-sonnet-4-0",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": build_user_prompt(day_config, seo_insights)}
        ],
    ) as stream:
        for text_chunk in stream.text_stream:
            full_text += text_chunk
            print(text_chunk, end="", flush=True)

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

        # Try direct parse first
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Fallback: extract fields with regex (handles unescaped newlines in content)
        title = _re.search(r'"title"\s*:\s*"(.*?)"(?=\s*,\s*"meta_description")', cleaned, _re.S)
        meta  = _re.search(r'"meta_description"\s*:\s*"(.*?)"(?=\s*,\s*"content")', cleaned, _re.S)
        cont  = _re.search(r'"content"\s*:\s*"(.*?)"\s*\}', cleaned, _re.S)
        if title and meta and cont:
            return {
                "title":            title.group(1).replace('\\"', '"'),
                "meta_description": meta.group(1).replace('\\"', '"'),
                "content":          cont.group(1).replace('\\"', '"').replace("\\n", "\n"),
            }

        raise json.JSONDecodeError("Could not parse response", cleaned, 0)

    try:
        article = _extract_article(full_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON response: {exc}\n---\n{full_text[:500]}") from exc

    required_keys = {"title", "meta_description", "content"}
    missing = required_keys - article.keys()
    if missing:
        raise ValueError(f"Claude response missing keys: {missing}")

    return article


# ---------------------------------------------------------------------------
# Featured image: generate with DALL-E 3, upload to WordPress
# ---------------------------------------------------------------------------

# Photographic style prompt appended to every image request.
_IMAGE_STYLE = (
    "Professional travel photography, ultra-realistic, golden hour lighting, "
    "vibrant colours, shallow depth of field, 16:9 landscape orientation, "
    "no text, no watermarks, no people's faces."
)

# Per-theme visual descriptions for DALL-E 3.
IMAGE_PROMPTS = {
    "Tokyo":              "Iconic Tokyo skyline at sunset with Tokyo Tower and Mount Fuji silhouette in the background, neon-lit Shinjuku streets below.",
    "Osaka / Kyoto":      "Fushimi Inari shrine vermilion torii gates winding through a misty cedar forest in Kyoto, Japan.",
    "Transport in Japan": "A sleek white Shinkansen bullet train speeding past snow-capped Mount Fuji under a clear blue sky.",
    "Seasonal Travel":    "Thousands of cherry blossom trees in full bloom lining a canal in Tokyo, soft pink petals floating in the breeze.",
    "Travel Tips":        "A traveler's flat-lay on a wooden table: Japanese yen coins, IC card, Japan map, passport, and green tea.",
    "Japan Itineraries":  "Aerial view of the classic Japan Golden Route — Tokyo tower, Kyoto temple, and Osaka castle composite.",
    "eSIM & Wi-Fi in Japan": "Close-up of a smartphone showing a Japan map with location pins, held above a blurred Tokyo street scene.",
}


def generate_featured_image(theme: str) -> bytes | None:
    """Call DALL-E 3 and return raw PNG bytes, or None on failure."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("  [SKIP] OPENAI_API_KEY not set — skipping image generation.")
        return None

    visual = IMAGE_PROMPTS.get(theme, f"Beautiful travel scene representing {theme} in Japan.")
    prompt = f"{visual} {_IMAGE_STYLE}"

    print(f"  Generating featured image with DALL-E 3 …")

    resp = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model":   "dall-e-3",
            "prompt":  prompt,
            "n":       1,
            "size":    "1792x1024",
            "quality": "standard",
            "response_format": "url",
        },
        timeout=60,
    )
    resp.raise_for_status()
    image_url = resp.json()["data"][0]["url"]

    # Download the generated image.
    img_resp = requests.get(image_url, timeout=30)
    img_resp.raise_for_status()
    print(f"  Image generated ({len(img_resp.content) // 1024} KB).")
    return img_resp.content


def upload_image_to_wp(image_bytes: bytes, title: str, auth: tuple) -> int | None:
    """Upload image bytes to WordPress Media Library. Returns attachment ID."""
    wp_url  = os.getenv("WP_URL", "").rstrip("/")
    endpoint = f"{wp_url}/wp-json/wp/v2/media"

    # Build a filesystem-safe filename from the article title.
    safe_title = title.lower()
    for ch in " /\\:*?\"<>|'":
        safe_title = safe_title.replace(ch, "-")
    safe_title = safe_title[:60].strip("-")
    filename = f"{safe_title}-{datetime.now().strftime('%Y%m%d')}.png"

    print(f"  Uploading image to WordPress as '{filename}' …")

    resp = requests.post(
        endpoint,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "image/png",
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
        "status":     "draft",
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
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
            day_config = SCHEDULE[weekday]
    else:
        day_config = SCHEDULE[weekday]

    print("=" * 60)
    print(f"  Japan Travel Base — Article Generator")
    print(f"  Date    : {datetime.now().strftime('%Y-%m-%d %A')}")
    print(f"  Theme   : {day_config['theme']}")
    print(f"  Category: {day_config['category_name']}")
    if seo_insights.get("competitors"):
        print(f"  SEO     : 競合分析インサイトあり ✓")
    print("=" * 60)

    # 1. Generate article
    try:
        article = generate_article(day_config, seo_insights)
    except (EnvironmentError, ValueError, anthropic.APIError) as exc:
        log_error(f"Article generation failed — {exc}")
        sys.exit(1)

    print(f"\n  Title   : {article['title']}")
    print(f"  Meta    : {article['meta_description']}")
    print(f"  Words   : ~{len(article['content'].split())} (HTML)")

    # 2. Generate featured image
    image_b64 = None
    try:
        image_bytes = generate_featured_image(day_config["theme"])
        if image_bytes:
            import base64
            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            print(f"  ✓ Featured image generated ({len(image_bytes)//1024}KB)")
    except Exception as exc:
        print(f"  [WARN] Image generation failed: {exc}")

    # 3. Save article to pending_articles/ for WordPress to fetch
    os.makedirs("pending_articles", exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    article_filename = f"article_{timestamp}.json"
    article_path = os.path.join("pending_articles", article_filename)

    meta_comment = f'<!-- meta_description: {article["meta_description"]} -->\n'
    pending_payload = {
        "title":            article["title"],
        "content":          meta_comment + article["content"],
        "meta_description": article["meta_description"],
        "category_slug":    day_config["category_slug"],
        "category_name":    day_config["category_name"],
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "featured_image_b64": image_b64,
    }
    with open(article_path, "w", encoding="utf-8") as f:
        json.dump(pending_payload, f, ensure_ascii=False, indent=2)

    # Update index.json
    index_path = "pending_articles/index.json"
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)
    else:
        index_data = {"files": []}
    index_data["files"].append(article_filename)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2)

    print(f"\n  ✓ Article saved to {article_path}")
    print(f"  ✓ WordPress will fetch and create draft automatically")
    print("=" * 60)


if __name__ == "__main__":
    main()
