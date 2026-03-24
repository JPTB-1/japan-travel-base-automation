"""
generate_destination.py
Japan Travel Base — Destination article generator with multi-axis categories.

Rotates through ~20 Japanese destinations, posting weekly or bi-weekly.
Each article is tagged with:
  - Area category   (e.g. "Okinawa")
  - Budget category (e.g. "Mid-range")
  - Duration category (e.g. "One Week")

Usage:
    python generate_destination.py              # post next destination in rotation
    python generate_destination.py --update-top # also regenerate TOP page

State file: destination_state.json (tracks rotation index)

Environment (.env):
    ANTHROPIC_API_KEY=
    OPENAI_API_KEY=
    WP_URL=https://japantravelbase.com
    WP_USER=
    WP_APP_PASSWORD=
"""

import argparse
import csv
import json
import logging
import os
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
STATE_FILE     = "destination_state.json"

logging.basicConfig(
    filename=LOG_ERROR_FILE,
    level=logging.ERROR,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---------------------------------------------------------------------------
# Destination definitions
# ---------------------------------------------------------------------------
# budget:   "budget" | "mid-range" | "luxury"
# duration: "day-trip" | "weekend" | "one-week" | "extended"

DESTINATIONS = [
    {
        "name":       "Okinawa",
        "region":     "Okinawa",
        "budget":     "mid-range",
        "duration":   "one-week",
        "shortcodes": ['[jtb_hotel city="Okinawa" area="Naha"]',
                       '[jtb_flight origin="SYD" destination="OKA"]'],
        "image_prompt": (
            "Turquoise crystal-clear waters and white sand beaches of Okinawa Japan, "
            "tropical palm trees, traditional Ryukyuan red-roofed Shuri Castle in the distance."
        ),
        "prompt_context": (
            "Write a comprehensive Okinawa travel guide for international visitors. "
            "Cover Naha city and Shuri Castle, the stunning beaches (Emerald Beach, Manza Beach), "
            "Churaumi Aquarium, Ryukyu culture and food (rafute, champuru), island-hopping to "
            "Ishigaki and Miyakojima, best time to visit (avoiding typhoon season), and how to "
            "get there from major Japanese cities and directly from Australia/USA."
        ),
    },
    {
        "name":       "Hokkaido",
        "region":     "Hokkaido",
        "budget":     "mid-range",
        "duration":   "one-week",
        "shortcodes": ['[jtb_hotel city="Sapporo" area="Susukino"]',
                       '[jtb_flight origin="SYD" destination="CTS"]'],
        "image_prompt": (
            "Hokkaido Japan winter wonderland — snow-covered lavender fields of Furano, "
            "Sapporo snow festival ice sculptures glowing at night, powder skiing at Niseko."
        ),
        "prompt_context": (
            "Write a Hokkaido travel guide covering all seasons. "
            "Winter: Sapporo Snow Festival, Niseko powder skiing, drift ice in Abashiri. "
            "Summer: Furano lavender fields, fresh seafood in Hakodate, Shiretoko National Park. "
            "Food highlights: Sapporo ramen, crab, dairy products, Jingisukan BBQ. "
            "Include transport tips (JR passes, domestic flights from Tokyo)."
        ),
    },
    {
        "name":       "Nara",
        "region":     "Nara",
        "budget":     "budget",
        "duration":   "day-trip",
        "shortcodes": ['[jtb_hotel city="Nara" area="Nara Park"]',
                       '[jtb_flight origin="SYD" destination="KIX"]'],
        "image_prompt": (
            "Sacred deer roaming freely among ancient stone lanterns in Nara Park Japan, "
            "Todai-ji temple's Great Buddha Hall in the background, autumn maple trees."
        ),
        "prompt_context": (
            "Write a Nara day-trip guide from Osaka and Kyoto. "
            "Cover Nara Park and the famous freely-roaming deer, Todai-ji temple (Great Buddha), "
            "Kasuga Taisha shrine, Naramachi historic district, Isuien Garden. "
            "Include how to feed the deer, best photo spots, getting there by train (30-45 min from Osaka), "
            "and combining with a Kyoto day."
        ),
    },
    {
        "name":       "Hiroshima & Miyajima",
        "region":     "Hiroshima",
        "budget":     "budget",
        "duration":   "weekend",
        "shortcodes": ['[jtb_hotel city="Hiroshima" area="Peace Memorial Park"]',
                       '[jtb_flight origin="SYD" destination="HND"]'],
        "image_prompt": (
            "Itsukushima shrine floating torii gate at high tide in Miyajima island Japan, "
            "vermilion red gate reflecting in calm sea water, distant mountains at sunset."
        ),
        "prompt_context": (
            "Write a Hiroshima and Miyajima Island travel guide. "
            "Cover the Peace Memorial Park and Museum (respectful visitor tips), A-Bomb Dome, "
            "Miyajima Island's iconic floating torii gate and Itsukushima Shrine, "
            "Momijidani Park, climbing Mt. Misen, local food (okonomiyaki, oysters, maple leaf manju). "
            "Day-by-day itinerary for a 2-day trip from Osaka or Kyoto via Shinkansen."
        ),
    },
    {
        "name":       "Hakone",
        "region":     "Hakone",
        "budget":     "luxury",
        "duration":   "weekend",
        "shortcodes": ['[jtb_hotel city="Hakone" area="Hakone Yumoto"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "image_prompt": (
            "Traditional Japanese ryokan with outdoor onsen hot spring pool overlooking "
            "snow-capped Mount Fuji through morning mist in Hakone Japan, wooden architecture."
        ),
        "prompt_context": (
            "Write a Hakone luxury weekend guide. "
            "Focus on ryokan stays with onsen (hot spring baths) and kaiseki cuisine, "
            "the Hakone Open Air Museum, Lake Ashi boat cruise with Mount Fuji views, "
            "the famous Hakone Round Course (ropeway, funicular, boat), "
            "Owakudani volcanic valley. Include: Romancecar train from Shinjuku, "
            "best ryokan recommendations by price tier, onsen etiquette for first-timers."
        ),
    },
    {
        "name":       "Kanazawa",
        "region":     "Kanazawa",
        "budget":     "mid-range",
        "duration":   "weekend",
        "shortcodes": ['[jtb_hotel city="Kanazawa" area="Higashichaya"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "image_prompt": (
            "Kenroku-en garden in Kanazawa Japan, snow-covered pine trees with yukitsuri rope "
            "supports, traditional geisha teahouse district Higashichaya in the background."
        ),
        "prompt_context": (
            "Write a Kanazawa travel guide — Japan's hidden gem. "
            "Cover Kenroku-en (one of Japan's top three gardens), Kanazawa Castle, "
            "Higashichaya geisha district, Omicho fresh seafood market, "
            "Nagamachi samurai district, 21st Century Museum of Contemporary Art. "
            "Highlight: why Kanazawa escaped WWII bombing and preserved Edo-period culture. "
            "Include Shinkansen access from Tokyo (2.5 hours)."
        ),
    },
    {
        "name":       "Fukuoka",
        "region":     "Fukuoka",
        "budget":     "budget",
        "duration":   "weekend",
        "shortcodes": ['[jtb_hotel city="Fukuoka" area="Hakata"]',
                       '[jtb_flight origin="SYD" destination="FUK"]'],
        "image_prompt": (
            "Fukuoka Japan yatai open-air food stalls lit up at night along the Nakagawa river, "
            "steaming bowls of Hakata tonkotsu ramen, lively atmosphere with lanterns."
        ),
        "prompt_context": (
            "Write a Fukuoka travel and food guide. "
            "Fukuoka is Japan's ramen capital — cover Hakata tonkotsu ramen, "
            "the famous yatai open-air food stalls, Ohori Park, Dazaifu Tenmangu shrine, "
            "Canal City shopping, proximity to Nagasaki and Kumamoto for day trips. "
            "Highlight: direct international flights make it a perfect gateway to Japan. "
            "Include budget tips — Fukuoka is one of Japan's most affordable major cities."
        ),
    },
    {
        "name":       "Nikko",
        "region":     "Nikko",
        "budget":     "mid-range",
        "duration":   "day-trip",
        "shortcodes": ['[jtb_hotel city="Nikko" area="Nikko"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "image_prompt": (
            "Toshogu shrine in Nikko Japan surrounded by towering cedar trees, "
            "ornate gold and lacquer architecture, autumn red and orange maple foliage."
        ),
        "prompt_context": (
            "Write a Nikko day-trip guide from Tokyo. "
            "Cover Toshogu Shrine (UNESCO World Heritage, Tokugawa Ieyasu's mausoleum), "
            "Rinno-ji temple, Futarasan Shrine, Kegon Falls, Lake Chuzenji. "
            "Seasonal highlights: spring cherry blossoms, autumn foliage (best in Japan). "
            "Include: Nikko Pass value analysis, train options from Tokyo (Tobu Nikko Limited Express), "
            "how to combine with an onsen stay in Kinugawa."
        ),
    },
    {
        "name":       "Kamakura",
        "region":     "Kamakura",
        "budget":     "budget",
        "duration":   "day-trip",
        "shortcodes": ['[jtb_hotel city="Tokyo" area="Shibuya"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "image_prompt": (
            "Great Buddha Kotoku-in bronze statue in Kamakura Japan, "
            "surrounded by lush green hills, clear blue sky, peaceful and majestic."
        ),
        "prompt_context": (
            "Write a Kamakura day-trip guide from Tokyo. "
            "Cover the Great Buddha (Kotoku-in), Tsurugaoka Hachimangu shrine, "
            "Engakuji and Kencho-ji Zen temples, Hase-dera with ocean views, "
            "the scenic Enoden tram line, Enoshima island add-on. "
            "Include: Just 1 hour from Tokyo by train, ideal for combining with Yokohama, "
            "best season (spring for cherry blossoms, hydrangeas in June)."
        ),
    },
    {
        "name":       "Kyoto Deep Dive",
        "region":     "Kyoto",
        "budget":     "luxury",
        "duration":   "one-week",
        "shortcodes": ['[jtb_hotel city="Kyoto" area="Gion"]',
                       '[jtb_flight origin="SYD" destination="KIX"]'],
        "image_prompt": (
            "Gion district Kyoto Japan at dusk, a maiko in colourful kimono walking "
            "along Hanamikoji stone-paved street, traditional machiya townhouses, soft lantern glow."
        ),
        "prompt_context": (
            "Write an in-depth Kyoto one-week itinerary for travelers who want to go beyond the basics. "
            "Cover the must-sees (Fushimi Inari, Arashiyama, Kinkakuji) plus hidden gems: "
            "Fushimi Momoyama, Kurama onsen, Ohara villages, Philosopher's Path in autumn. "
            "Include: ryokan vs hotel, kaiseki dinner experience, tea ceremony, "
            "renting a kimono in Gion, day trips to Nara and Uji (matcha capital)."
        ),
    },
    {
        "name":       "Tokyo Hidden Gems",
        "region":     "Tokyo",
        "budget":     "mid-range",
        "duration":   "one-week",
        "shortcodes": ['[jtb_hotel city="Tokyo" area="Asakusa"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "image_prompt": (
            "Hidden alley in Shimokitazawa Tokyo Japan — vintage boutiques, indie cafes, "
            "fairy lights strung between buildings, young Japanese locals at street food stalls."
        ),
        "prompt_context": (
            "Write a Tokyo Beyond the Basics guide for repeat visitors or those wanting to explore off the beaten path. "
            "Cover: Shimokitazawa vintage and live music scene, Yanaka old town, "
            "Koenji antique markets, Kagurazaka French-Japanese district, "
            "teamLab digital art museums, rooftop bars, Tokyo's best depachika (basement food halls). "
            "Include Sumo tournament tips and how to get day-of tickets."
        ),
    },
    {
        "name":       "Osaka Food & Culture",
        "region":     "Osaka",
        "budget":     "budget",
        "duration":   "weekend",
        "shortcodes": ['[jtb_hotel city="Osaka" area="Namba"]',
                       '[jtb_flight origin="SYD" destination="KIX"]'],
        "image_prompt": (
            "Dotonbori canal Osaka Japan at night, bright neon signs reflecting in water, "
            "giant Glico running man billboard, takoyaki and street food stalls lining the canal."
        ),
        "prompt_context": (
            "Write an Osaka food and culture deep dive. "
            "Osaka is Japan's kitchen — cover Dotonbori, Kuromon Ichiba Market, Shinsekai. "
            "Must-eat: takoyaki (octopus balls), okonomiyaki, kushikatsu, fugu. "
            "Culture: Osaka Castle, Universal Studios Japan, Namba and Shinsaibashi shopping. "
            "Budget tips: Osaka is famously cheaper than Tokyo — include cost comparisons. "
            "Include Osaka Amazing Pass value analysis for tourists."
        ),
    },
    {
        "name":       "Nagasaki & Kyushu",
        "region":     "Nagasaki",
        "budget":     "mid-range",
        "duration":   "weekend",
        "shortcodes": ['[jtb_hotel city="Nagasaki" area="Nagasaki"]',
                       '[jtb_flight origin="SYD" destination="FUK"]'],
        "image_prompt": (
            "Nagasaki Japan panoramic night view from Mt. Inasa — one of Japan's top three night views, "
            "city lights twinkling in the harbor, Nagasaki Peace Park monument silhouette."
        ),
        "prompt_context": (
            "Write a Nagasaki travel guide. "
            "Cover the Peace Park and Atomic Bomb Museum (respectful tone), Glover Garden, "
            "Chinatown (one of Japan's oldest), Nagasaki Lantern Festival (February), "
            "Hashima Island (Battleship Island) day cruise, Huis Ten Bosch Dutch theme park. "
            "Include: Nagasaki's unique multicultural history (Dutch, Chinese, Portuguese), "
            "champon noodles and castella cake, Kyushu rail pass for island-hopping."
        ),
    },
    {
        "name":       "Mount Fuji & Fuji Five Lakes",
        "region":     "Fuji",
        "budget":     "mid-range",
        "duration":   "weekend",
        "shortcodes": ['[jtb_hotel city="Fuji" area="Kawaguchiko"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "image_prompt": (
            "Perfect reflection of snow-capped Mount Fuji in the still waters of Lake Kawaguchi "
            "at dawn, cherry blossom trees lining the shore, pastel pink and blue sky."
        ),
        "prompt_context": (
            "Write a Mount Fuji and Fuji Five Lakes travel guide. "
            "Cover Lake Kawaguchiko as base (best views, accommodation), "
            "climbing Mount Fuji (season July–September, trails, gear, mountain huts), "
            "Chureito Pagoda for iconic photo, Oshino Hakkai springs, "
            "Fuji-Q Highland theme park. "
            "Include: how to get there from Tokyo (Fuji Excursion train or highway bus), "
            "day trip vs overnight stay, what to do if Fuji is hidden in clouds."
        ),
    },
    {
        "name":       "Tohoku & Sendai",
        "region":     "Tohoku",
        "budget":     "budget",
        "duration":   "one-week",
        "shortcodes": ['[jtb_hotel city="Sendai" area="Sendai"]',
                       '[jtb_flight origin="SYD" destination="TYO"]'],
        "image_prompt": (
            "Matsushima Bay Japan — hundreds of pine-covered islands at golden hour, "
            "traditional red torii gate on small islet, wooden bridge, mist rising over the sea."
        ),
        "prompt_context": (
            "Write a Tohoku region travel guide — Japan's undiscovered north. "
            "Cover Sendai (Tanabata Festival in August, gyutan beef tongue), "
            "Matsushima Bay (one of Japan's three views), Yamadera cliff temple, "
            "Aizu-Wakamatsu samurai town, Hirosaki Castle cherry blossoms, "
            "Nyuto Onsen remote hot springs. "
            "Highlight: Tohoku sees far fewer tourists than Kyoto — authentic Japan experience. "
            "Include Tohoku Emotion gourmet train and JR East Pass value."
        ),
    },
]

# ---------------------------------------------------------------------------
# Category axes
# ---------------------------------------------------------------------------

BUDGET_CATEGORIES = {
    "budget":     {"slug": "budget-travel",   "name": "Budget Travel (Under $80/day)"},
    "mid-range":  {"slug": "mid-range-travel", "name": "Mid-Range ($80–200/day)"},
    "luxury":     {"slug": "luxury-travel",    "name": "Luxury Travel ($200+/day)"},
}

DURATION_CATEGORIES = {
    "day-trip":  {"slug": "day-trip",   "name": "Day Trips"},
    "weekend":   {"slug": "weekend",    "name": "Weekend Getaways (2–3 Days)"},
    "one-week":  {"slug": "one-week",   "name": "One Week Itineraries"},
    "extended":  {"slug": "extended",   "name": "Extended Stays (2+ Weeks)"},
}

# ---------------------------------------------------------------------------
# State management (rotation index)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.isfile(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"index": 0, "last_posted": None}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def next_destination(state: dict) -> tuple[dict, int]:
    idx  = state.get("index", 0) % len(DESTINATIONS)
    dest = DESTINATIONS[idx]
    return dest, idx

# ---------------------------------------------------------------------------
# Image generation (DALL-E 3)
# ---------------------------------------------------------------------------

_IMAGE_STYLE = (
    "Professional travel photography, ultra-realistic, golden hour lighting, "
    "vibrant colours, shallow depth of field, 16:9 landscape orientation, "
    "no text, no watermarks, no people's faces."
)


def generate_featured_image(image_prompt: str) -> bytes | None:
    import re as _re_img
    api_key = _re_img.sub(r'\s', '', os.getenv("OPENAI_API_KEY", ""))
    if not api_key:
        print("  [SKIP] OPENAI_API_KEY not set.")
        return None

    prompt = f"{image_prompt} {_IMAGE_STYLE}"
    print("  Generating featured image with DALL-E 3 …")

    resp = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": "dall-e-3", "prompt": prompt, "n": 1,
              "size": "1792x1024", "quality": "standard", "response_format": "url"},
        timeout=60,
    )
    resp.raise_for_status()
    image_url = resp.json()["data"][0]["url"]
    img_resp  = requests.get(image_url, timeout=30)
    img_resp.raise_for_status()
    print(f"  Image generated ({len(img_resp.content) // 1024} KB).")
    return img_resp.content


def upload_image_to_wp(image_bytes: bytes, title: str, auth: tuple) -> int | None:
    wp_url   = os.getenv("WP_URL", "").rstrip("/")
    safe     = "".join(c if c.isalnum() else "-" for c in title.lower())[:60].strip("-")
    filename = f"{safe}-{datetime.now().strftime('%Y%m%d')}.png"
    print(f"  Uploading image as '{filename}' …")
    resp = requests.post(
        f"{wp_url}/wp-json/wp/v2/media",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "Content-Type": "image/png"},
        data=image_bytes, auth=auth, timeout=60,
    )
    resp.raise_for_status()
    aid = resp.json()["id"]
    print(f"  Image uploaded — attachment ID: {aid}")
    return aid


def set_featured_image(post_id: int, attachment_id: int, auth: tuple) -> None:
    wp_url = os.getenv("WP_URL", "").rstrip("/")
    resp   = requests.post(
        f"{wp_url}/wp-json/wp/v2/posts/{post_id}",
        json={"featured_media": attachment_id}, auth=auth, timeout=15,
    )
    resp.raise_for_status()
    print(f"  Featured image set on post {post_id}.")

# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------

def get_or_create_category(wp_url: str, auth: tuple, slug: str, name: str,
                            parent_id: int = 0) -> int:
    endpoint = f"{wp_url}/wp-json/wp/v2/categories"
    resp = requests.get(endpoint, params={"slug": slug}, auth=auth, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data:
        return data[0]["id"]
    payload = {"name": name, "slug": slug}
    if parent_id:
        payload["parent"] = parent_id
    resp = requests.post(endpoint, json=payload, auth=auth, timeout=15)
    resp.raise_for_status()
    return resp.json()["id"]


def resolve_categories(wp_url: str, auth: tuple, dest: dict) -> list[int]:
    ids = []

    # Parent categories (created once)
    area_parent     = get_or_create_category(wp_url, auth, "areas",     "Search by Area")
    budget_parent   = get_or_create_category(wp_url, auth, "budget",    "Search by Budget")
    duration_parent = get_or_create_category(wp_url, auth, "duration",  "Search by Duration")

    # Area child
    area_slug = dest["region"].lower().replace(" ", "-").replace("&", "and")
    area_id   = get_or_create_category(wp_url, auth, area_slug, dest["region"], area_parent)
    ids.append(area_id)

    # Budget child
    b = BUDGET_CATEGORIES[dest["budget"]]
    ids.append(get_or_create_category(wp_url, auth, b["slug"], b["name"], budget_parent))

    # Duration child
    d = DURATION_CATEGORIES[dest["duration"]]
    ids.append(get_or_create_category(wp_url, auth, d["slug"], d["name"], duration_parent))

    return ids

# ---------------------------------------------------------------------------
# Article generation (Claude)
# ---------------------------------------------------------------------------

CURRENT_YEAR = 2026

SYSTEM_PROMPT = f"""You are a professional travel writer for Japan Travel Base (japantravelbase.com),
targeting foreign visitors to Japan — primarily from Australia, the USA, and the UK.

IMPORTANT: Today is {CURRENT_YEAR}. All information must reflect {CURRENT_YEAR} reality:
- Use current prices in USD/AUD (as of {CURRENT_YEAR})
- Reference current transport options (e.g. new Shinkansen routes, updated JR Pass rules)
- Note any post-COVID changes still in effect (mask guidance, cashless payments, etc.)
- Mention current entry requirements if relevant (visa-free for most Western countries)
- Use "{CURRENT_YEAR}" or "as of {CURRENT_YEAR}" where helpful, not outdated years

Style: friendly, knowledgeable, practical, enthusiastic. Clear English, accessible to non-natives.
Length: 1200–1600 words of body content (not counting shortcodes).
SEO: natural keyword use in headings and first paragraph.

Structure:
1. Engaging introduction (hook + what the article covers)
2. Multiple H2 sections with H3 sub-sections
3. Bullet/numbered lists for tips
4. Shortcodes inserted naturally in the flow
5. Final H2: "## Plan Your Japan Trip Today" with CTA

Return ONLY raw JSON — no markdown fences — in this exact shape:
{{
  "title": "<SEO title, max 65 chars>",
  "meta_description": "<max 155 chars>",
  "content": "<full HTML using <h2>/<h3>/<p>/<ul>/<ol>/<li> tags>"
}}

Insert shortcodes as plain text on their own line between paragraphs, e.g.:
<p>[jtb_hotel city="Okinawa" area="Naha"]</p>
"""


def generate_article(dest: dict) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    shortcode_lines = "\n".join(
        f'- Insert {sc} once, naturally in context.' for sc in dest["shortcodes"]
    )

    user_msg = (
        f"Write a Japan travel article about: **{dest['name']}**\n\n"
        f"Context / angle:\n{dest['prompt_context']}\n\n"
        f"Shortcodes to embed (insert naturally in HTML content):\n{shortcode_lines}\n\n"
        "Respond with raw JSON only (no ```json fences)."
    )

    print(f"  Calling Claude API (streaming) for: {dest['name']} …")
    full_text = ""
    with client.messages.stream(
        model="claude-sonnet-4-0",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        for chunk in stream.text_stream:
            full_text += chunk
            print(chunk, end="", flush=True)
    print()

    cleaned = full_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    # Remove control characters that break JSON parsing
    import re as _re
    cleaned = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)

    try:
        article = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: use json5-tolerant loading via ast
        import ast
        article = ast.literal_eval(cleaned)

    for key in ("title", "meta_description", "content"):
        if key not in article:
            raise ValueError(f"Missing key in Claude response: {key}")
    return article

# ---------------------------------------------------------------------------
# WordPress post
# ---------------------------------------------------------------------------

def post_draft(article: dict, category_ids: list[int], auth: tuple) -> int:
    wp_url    = os.getenv("WP_URL", "").rstrip("/")
    meta_html = f'<!-- meta_description: {article["meta_description"]} -->\n'
    payload   = {
        "title":      article["title"],
        "content":    meta_html + article["content"],
        "status":     "draft",
        "categories": category_ids,
        "meta": {
            "_yoast_wpseo_metadesc": article["meta_description"],
            "_aioseo_description":   article["meta_description"],
            "rank_math_description": article["meta_description"],
        },
    }
    resp = requests.post(f"{wp_url}/wp-json/wp/v2/posts",
                         json=payload, auth=auth, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]

# ---------------------------------------------------------------------------
# TOP page update
# ---------------------------------------------------------------------------

# Reference sites to research for TOP page inspiration
REFERENCE_SITES = [
    ("japan.travel",      "https://www.japan.travel/en/us/"),
    ("lonelyplanet.com",  "https://www.lonelyplanet.com/japan"),
    ("timeout.com",       "https://www.timeout.com/tokyo"),
]


def _fetch_site_text(url: str, max_chars: int = 6000) -> str:
    """Fetch a URL and return stripped text content."""
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (compatible; JTB-bot/1.0)"
        })
        if not r.ok:
            return ""
        # Strip HTML tags crudely for token efficiency
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", " ", r.text, flags=re.S)
        text = re.sub(r"<style[^>]*>.*?</style>",  " ", text,   flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def _research_and_generate_top_html(client: anthropic.Anthropic,
                                     updated: str) -> str:
    """
    1. Fetch reference travel sites.
    2. Ask Claude to extract UX/content ideas.
    3. Ask Claude to generate improved TOP page HTML.
    """
    # Step 1: collect reference snippets
    print("  Fetching reference travel sites …")
    site_excerpts = []
    for name, url in REFERENCE_SITES:
        text = _fetch_site_text(url)
        if text:
            site_excerpts.append(f"=== {name} ===\n{text[:2000]}")
            print(f"    ✓ {name} ({len(text)} chars)")
        else:
            print(f"    ✗ {name} (failed)")

    references_block = "\n\n".join(site_excerpts) if site_excerpts else "(no references fetched)"

    # Step 2 + 3: single Claude call — analyse + generate
    prompt = f"""You are a senior UX designer and front-end developer for Japan Travel Base (japantravelbase.com).

Today is {CURRENT_YEAR}. Analyze the following excerpts from top travel sites and identify:
- Effective homepage sections and layouts
- Engaging copywriting patterns
- Navigation/discovery UX patterns worth borrowing

Then generate a complete, self-contained WordPress page HTML block for the Japan Travel Base homepage.

REQUIREMENTS for the HTML:
- Wrap everything in <!-- wp:html --> ... <!-- /wp:html -->
- All CSS must be inline <style> at the top (no external dependencies)
- Sections to include (inspired by what works on the reference sites):
  1. Hero banner: gradient background, headline, sub-headline, 4 quick-link badges
  2. Short intro paragraph about the site
  3. "Browse by Area" — photo-card grid with 12 Japanese destinations
     (use Unsplash image URLs: https://images.unsplash.com/photo-XXXXXX?w=400&q=75)
  4. Two-column filter row: "Search by Budget" (3 pills) + "Search by Duration" (4 pills)
  5. "Popular Topics" — 6 theme cards
  6. Small "Last updated: {updated}" footer line
- Design: modern, clean, mobile-responsive (grid + flexbox), blue/white palette (#0057b8 accent)
- All internal links use relative paths e.g. /category/areas/tokyo/
- No JavaScript required
- Incorporate at least 2 specific UX ideas you noticed from the reference sites

REFERENCE SITE EXCERPTS:
{references_block}

Return ONLY the raw HTML block — no explanation, no markdown fences.
"""

    print("  Asking Claude to analyse references and generate TOP page HTML …")
    full_html = ""
    with client.messages.stream(
        model="claude-sonnet-4-0",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for chunk in stream.text_stream:
            full_html += chunk
            print(chunk, end="", flush=True)
    print()

    # Strip accidental markdown fences
    html = full_html.strip()
    if html.startswith("```"):
        html = html.split("```", 2)[1]
        if html.startswith("html"):
            html = html[4:]
        html = html.rsplit("```", 1)[0].strip()

    return html


def update_top_page(auth: tuple) -> None:
    wp_url = os.getenv("WP_URL", "").rstrip("/")
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    print("  Looking for front page …")

    # Resolve front page ID
    front_page_id = None
    settings_resp = requests.get(f"{wp_url}/wp-json/wp/v2/settings",
                                  auth=auth, timeout=15)
    if settings_resp.ok:
        front_page_id = settings_resp.json().get("page_on_front")

    if not front_page_id:
        for term in ("Home", "Top", "Welcome"):
            r = requests.get(f"{wp_url}/wp-json/wp/v2/pages",
                             params={"search": term, "per_page": 1},
                             auth=auth, timeout=15)
            if r.ok and r.json():
                front_page_id = r.json()[0]["id"]
                break

    if not front_page_id:
        print("  [SKIP] Could not find front page.")
        return

    updated = datetime.now().strftime("%B %d, %Y")

    if api_key:
        client = anthropic.Anthropic(api_key=api_key)
        try:
            nav_html = _research_and_generate_top_html(client, updated)
        except Exception as exc:
            print(f"  [WARN] Research-based generation failed ({exc}), using static template.")
            nav_html = _static_top_html(updated)
    else:
        nav_html = _static_top_html(updated)

    resp = requests.post(
        f"{wp_url}/wp-json/wp/v2/pages/{front_page_id}",
        json={"content": nav_html},
        auth=auth,
        timeout=30,
    )
    resp.raise_for_status()
    print(f"\n  TOP page (ID: {front_page_id}) updated successfully.")


def _static_top_html(updated: str) -> str:
    """Fallback static template used when Claude is unavailable."""
    return f"""<!-- wp:html -->
<style>
.jtb-home{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1a1a1a}}
.jtb-hero{{position:relative;width:100%;min-height:460px;background:linear-gradient(135deg,#001f5b 0%,#0057b8 55%,#0096d6 100%);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:60px 24px;border-radius:12px;overflow:hidden;margin-bottom:44px}}
.jtb-hero::before{{content:"";position:absolute;inset:0;background:url('https://images.unsplash.com/photo-1490806843957-31f4c9a91c65?w=1400&q=80') center/cover;opacity:.32}}
.jtb-hero-content{{position:relative;z-index:1}}
.jtb-hero h1{{font-size:clamp(2em,5vw,3em);font-weight:900;color:#fff;margin:0 0 14px;text-shadow:0 2px 12px rgba(0,0,0,.4)}}
.jtb-hero-sub{{font-size:1.1em;color:rgba(255,255,255,.9);max-width:580px;margin:0 auto 28px;line-height:1.65;text-shadow:0 1px 6px rgba(0,0,0,.3)}}
.jtb-badges{{display:flex;flex-wrap:wrap;gap:10px;justify-content:center}}
.jtb-badge{{background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.38);color:#fff;padding:8px 18px;border-radius:24px;font-size:.88em;font-weight:600;text-decoration:none!important}}
.jtb-badge:hover{{background:rgba(255,255,255,.32);color:#fff!important}}
.jtb-section{{margin-bottom:44px}}
.jtb-sh{{display:flex;align-items:center;gap:10px;margin-bottom:18px;padding-bottom:10px;border-bottom:2px solid #e0eaf8}}
.jtb-sh h2{{font-size:1.25em;font-weight:800;color:#003580;margin:0}}
.jtb-sh p{{font-size:.84em;color:#777;margin:3px 0 0}}
.jtb-area-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:12px}}
.jtb-ac{{position:relative;border-radius:10px;overflow:hidden;aspect-ratio:4/3;text-decoration:none!important;display:block}}
.jtb-ac img{{width:100%;height:100%;object-fit:cover;transition:transform .35s}}
.jtb-ac:hover img{{transform:scale(1.07)}}
.jtb-ac-label{{position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent,rgba(0,0,0,.7));color:#fff!important;font-weight:700;font-size:.88em;padding:22px 10px 8px}}
.jtb-filter-row{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:44px}}
.jtb-fb{{background:#f0f6ff;border:1px solid #cce0ff;border-radius:10px;padding:18px}}
.jtb-fb h3{{font-size:.95em;font-weight:800;color:#003580;margin:0 0 12px;display:flex;align-items:center;gap:7px}}
.jtb-pills{{display:flex;flex-direction:column;gap:8px}}
.jtb-pill{{display:flex;align-items:center;gap:10px;padding:10px 13px;background:#fff;border:1px solid #cce0ff;border-radius:8px;text-decoration:none!important;color:#003580!important;font-weight:600;font-size:.88em;transition:all .2s}}
.jtb-pill:hover{{background:#0057b8;color:#fff!important;border-color:#0057b8}}
.fp-d{{font-size:.76em;font-weight:400;color:#999;margin-top:1px}}
.jtb-pill:hover .fp-d{{color:rgba(255,255,255,.75)}}
.jtb-theme-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:12px}}
.jtb-tc{{background:#fff;border:1px solid #e0eaf5;border-radius:10px;padding:16px 12px;text-align:center;text-decoration:none!important;color:#003580!important;font-weight:700;font-size:.9em;transition:all .2s;display:flex;flex-direction:column;align-items:center;gap:7px;box-shadow:0 2px 8px rgba(0,53,128,.06)}}
.jtb-tc:hover{{background:#0057b8;color:#fff!important;border-color:#0057b8;transform:translateY(-2px)}}
.jtb-ti{{font-size:1.9em;line-height:1}}
.jtb-td{{font-size:.75em;font-weight:400;color:#999;margin-top:-3px}}
.jtb-tc:hover .jtb-td{{color:rgba(255,255,255,.75)}}
.jtb-upd{{font-size:.74em;color:#bbb;text-align:right;margin-top:36px}}
@media(max-width:620px){{.jtb-filter-row{{grid-template-columns:1fr}}.jtb-area-grid,.jtb-theme-grid{{grid-template-columns:repeat(2,1fr)}}}}
</style>
<div class="jtb-home">
<div class="jtb-hero"><div class="jtb-hero-content">
<h1>Discover Japan. Your Way.</h1>
<p class="jtb-hero-sub">Honest guides, real {updated[:4]} prices, and practical advice for first-timers and repeat visitors alike — from flights and hotels to eSIM and day trips.</p>
<div class="jtb-badges">
<a class="jtb-badge" href="/category/areas/">🗾 Browse by Area</a>
<a class="jtb-badge" href="/category/itineraries/">🗺️ Itineraries</a>
<a class="jtb-badge" href="/category/budget/">💰 By Budget</a>
<a class="jtb-badge" href="/category/esim-wifi/">📶 eSIM Guide</a>
</div></div></div>
<div class="jtb-section"><div class="jtb-sh"><span style="font-size:1.4em">🗾</span><div><h2>Browse by Area</h2><p>Choose your destination and start exploring</p></div></div>
<div class="jtb-area-grid">
<a class="jtb-ac" href="/category/areas/tokyo/"><img src="https://images.unsplash.com/photo-1540959733332-eab4deabeeaf?w=400&q=75" alt="Tokyo" loading="lazy"><span class="jtb-ac-label">🗼 Tokyo</span></a>
<a class="jtb-ac" href="/category/areas/osaka/"><img src="https://images.unsplash.com/photo-1590559899731-a382839e5549?w=400&q=75" alt="Osaka" loading="lazy"><span class="jtb-ac-label">🏯 Osaka</span></a>
<a class="jtb-ac" href="/category/areas/kyoto/"><img src="https://images.unsplash.com/photo-1493976040374-85c8e12f0c0e?w=400&q=75" alt="Kyoto" loading="lazy"><span class="jtb-ac-label">⛩️ Kyoto</span></a>
<a class="jtb-ac" href="/category/areas/hokkaido/"><img src="https://images.unsplash.com/photo-1578469645742-46cae010e5d4?w=400&q=75" alt="Hokkaido" loading="lazy"><span class="jtb-ac-label">❄️ Hokkaido</span></a>
<a class="jtb-ac" href="/category/areas/okinawa/"><img src="https://images.unsplash.com/photo-1551641506-ee5bf4cb45f1?w=400&q=75" alt="Okinawa" loading="lazy"><span class="jtb-ac-label">🏝️ Okinawa</span></a>
<a class="jtb-ac" href="/category/areas/nara/"><img src="https://images.unsplash.com/photo-1528360983277-13d401cdc186?w=400&q=75" alt="Nara" loading="lazy"><span class="jtb-ac-label">🦌 Nara</span></a>
<a class="jtb-ac" href="/category/areas/hiroshima/"><img src="https://images.unsplash.com/photo-1524413840807-0c3cb6fa808d?w=400&q=75" alt="Hiroshima" loading="lazy"><span class="jtb-ac-label">🕊️ Hiroshima</span></a>
<a class="jtb-ac" href="/category/areas/hakone/"><img src="https://images.unsplash.com/photo-1570459027562-4a916cc6113f?w=400&q=75" alt="Hakone" loading="lazy"><span class="jtb-ac-label">🗻 Hakone</span></a>
<a class="jtb-ac" href="/category/areas/fukuoka/"><img src="https://images.unsplash.com/photo-1576675784201-0e142b423952?w=400&q=75" alt="Fukuoka" loading="lazy"><span class="jtb-ac-label">🍜 Fukuoka</span></a>
<a class="jtb-ac" href="/category/areas/kanazawa/"><img src="https://images.unsplash.com/photo-1600618528240-fb9fc964b853?w=400&q=75" alt="Kanazawa" loading="lazy"><span class="jtb-ac-label">🌸 Kanazawa</span></a>
<a class="jtb-ac" href="/category/areas/nikko/"><img src="https://images.unsplash.com/photo-1536098561742-ca998e48cbcc?w=400&q=75" alt="Nikko" loading="lazy"><span class="jtb-ac-label">🎋 Nikko</span></a>
<a class="jtb-ac" href="/category/areas/"><img src="https://images.unsplash.com/photo-1480796927426-f609979314bd?w=400&q=75" alt="More" loading="lazy"><span class="jtb-ac-label">➕ More Areas</span></a>
</div></div>
<div class="jtb-filter-row">
<div class="jtb-fb"><h3>💰 Search by Budget</h3><div class="jtb-pills">
<a class="jtb-pill" href="/category/budget/budget-travel/"><span>🪙</span><div><div>Budget Travel</div><div class="fp-d">Under $80/day</div></div></a>
<a class="jtb-pill" href="/category/budget/mid-range-travel/"><span>💳</span><div><div>Mid-Range</div><div class="fp-d">$80–$200/day</div></div></a>
<a class="jtb-pill" href="/category/budget/luxury-travel/"><span>💎</span><div><div>Luxury</div><div class="fp-d">$200+/day</div></div></a>
</div></div>
<div class="jtb-fb"><h3>📅 Search by Duration</h3><div class="jtb-pills">
<a class="jtb-pill" href="/category/duration/day-trip/"><span>☀️</span><div><div>Day Trips</div><div class="fp-d">Out and back in one day</div></div></a>
<a class="jtb-pill" href="/category/duration/weekend/"><span>🏃</span><div><div>Weekend (2–3 Days)</div><div class="fp-d">Short break itineraries</div></div></a>
<a class="jtb-pill" href="/category/duration/one-week/"><span>📆</span><div><div>One Week</div><div class="fp-d">The classic Japan trip</div></div></a>
<a class="jtb-pill" href="/category/duration/extended/"><span>✈️</span><div><div>Two Weeks+</div><div class="fp-d">Deep-dive extended stays</div></div></a>
</div></div></div>
<div class="jtb-section"><div class="jtb-sh"><span style="font-size:1.4em">🔥</span><div><h2>Popular Topics</h2><p>Guides, tips and tools for your Japan trip</p></div></div>
<div class="jtb-theme-grid">
<a class="jtb-tc" href="/category/transport/"><span class="jtb-ti">🚄</span><div>Transport &amp; JR Pass</div><div class="jtb-td">Shinkansen, IC cards, airports</div></a>
<a class="jtb-tc" href="/category/esim-wifi/"><span class="jtb-ti">📶</span><div>eSIM &amp; Wi-Fi</div><div class="jtb-td">Best SIM cards for Japan</div></a>
<a class="jtb-tc" href="/category/seasonal-travel/"><span class="jtb-ti">🌸</span><div>Seasonal Travel</div><div class="jtb-td">Sakura, koyo, snow festivals</div></a>
<a class="jtb-tc" href="/category/itineraries/"><span class="jtb-ti">🗺️</span><div>Itineraries</div><div class="jtb-td">Day-by-day Japan trip plans</div></a>
<a class="jtb-tc" href="/category/travel-tips/"><span class="jtb-ti">💡</span><div>Travel Tips</div><div class="jtb-td">Etiquette, money &amp; more</div></a>
<a class="jtb-tc" href="/category/areas/tokyo/"><span class="jtb-ti">🌆</span><div>Tokyo Guides</div><div class="jtb-td">Hotels, areas, hidden gems</div></a>
</div></div>
<p class="jtb-upd">Last updated: {updated}</p>
</div>
<!-- /wp:html -->"""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_success(title: str, region: str, post_id: int | None) -> None:
    exists = os.path.isfile(LOG_CSV_FILE)
    with open(LOG_CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["datetime", "title", "category", "wp_post_id"])
        writer.writerow([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            title, region, post_id,
        ])


def log_error(message: str) -> None:
    logging.error(message)
    print(f"\n[ERROR] {message}", file=sys.stderr)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-top", action="store_true",
                        help="Also regenerate the TOP page navigation.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate article content but do not post to WordPress.")
    args = parser.parse_args()

    state       = load_state()
    dest, idx   = next_destination(state)

    print("=" * 60)
    print(f"  Japan Travel Base — Destination Article Generator")
    print(f"  Date        : {datetime.now().strftime('%Y-%m-%d %A')}")
    print(f"  Destination : {dest['name']}  [{idx + 1}/{len(DESTINATIONS)}]")
    print(f"  Budget tier : {dest['budget']}  |  Duration: {dest['duration']}")
    print("=" * 60)

    # 1. Generate article
    try:
        article = generate_article(dest)
    except Exception as exc:
        log_error(f"Article generation failed — {exc}")
        sys.exit(1)

    print(f"\n  Title : {article['title']}")
    print(f"  Meta  : {article['meta_description']}")
    print(f"  Words : ~{len(article['content'].split())} (HTML)")

    if args.dry_run:
        print("\n  [DRY RUN] Skipping WordPress post.")
        return

    # 2. Generate featured image
    import re as _re3
    openai_key = _re3.sub(r'\s', '', os.getenv("OPENAI_API_KEY", ""))
    image_filename = None
    try:
        img = generate_featured_image(dest["image_prompt"])
        if img:
            os.makedirs("pending_articles", exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            image_filename = f"image_{timestamp}.png"
            with open(os.path.join("pending_articles", image_filename), "wb") as f:
                f.write(img)
            print(f"  ✓ Featured image saved ({len(img)//1024}KB)")
    except Exception as exc:
        print(f"  [WARN] Image generation failed: {exc}")

    # 3. Save to pending_articles/
    os.makedirs("pending_articles", exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    article_filename = f"article_{timestamp}.json"
    article_path = os.path.join("pending_articles", article_filename)

    pending_payload = {
        "title":            article["title"],
        "content":          article["content"],
        "meta_description": article["meta_description"],
        "category_slug":    dest.get("region", "destinations").lower().replace(" ", "-"),
        "category_name":    dest.get("region", "Destinations"),
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "image_filename":   image_filename,
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

    print(f"  ✓ Article saved to {article_path}")

    # 4. Update state → advance rotation
    state["index"]       = (idx + 1) % len(DESTINATIONS)
    state["last_posted"] = {
        "destination": dest["name"],
        "post_id":     None,
        "date":        datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    # 6. Log
    log_success(article["title"], dest["region"], None)

    print(f"\n  ✓ Article saved to pending_articles/")
    print(f"  ✓ Next destination: {DESTINATIONS[state['index']]['name']}")
    print(f"  ✓ Logged to {LOG_CSV_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
