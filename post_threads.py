"""
post_threads.py v3 — Japan Travel Base Threads Auto-Poster
Rebuilt 2026-04-23. Sequential cycling prevents duplicate posts.

Schedule (system clock = JST):
  JST  8:00 → Morning: experience post
  JST 12:00 → Lunch:   article (2 of 3 runs) or klook activity (1 of 3)
  JST 19:00 → Evening: spot_highlight / food_experience (alternating)

State: threads_post_state.json tracks index into each theme list.
"""

import json
import os
import re
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import anthropic

load_dotenv(dotenv_path=".env")

# ── Safety check: abort if posting is disabled ─────────────────────
_STOP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "THREADS_POSTING_DISABLED")
if os.path.exists(_STOP_FILE) and "--test" not in sys.argv:
    print(f"[BLOCKED] THREADS_POSTING_DISABLED file exists. Remove it to re-enable posting.")
    sys.exit(0)

THREADS_USER_ID      = os.getenv("THREADS_USER_ID", "")
THREADS_TOKEN        = os.getenv("THREADS_ACCESS_TOKEN", "")
WP_URL               = os.getenv("WP_URL", "https://japantravelbase.com")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
PEXELS_API_KEY       = os.getenv("PEXELS_API_KEY", "")
TRAVELPAYOUTS_MARKER = os.getenv("TRAVELPAYOUTS_MARKER", "710125")
KLOOK_PARTNER_ID     = "4166"
THREADS_API_BASE     = "https://graph.threads.net/v1.0"
JST                  = timezone(timedelta(hours=9))
BASE_DIR             = os.path.dirname(os.path.abspath(__file__))
STATE_FILE           = os.path.join(BASE_DIR, "threads_post_state.json")


def klook_affiliate_url(url: str) -> str:
    import urllib.parse
    return f"https://tp.media/r?marker={TRAVELPAYOUTS_MARKER}&p={KLOOK_PARTNER_ID}&u={urllib.parse.quote(url, safe='')}"


# ── Theme lists ────────────────────────────────────────────────────

EXPERIENCE_THEMES = [
    "The one thing you should do every morning in Kyoto before the crowds arrive",
    "Japan's ryokan experience — what actually happens and why it's worth it",
    "The best street food you have to try in Osaka's Dotonbori",
    "Why visiting a Japanese onsen changes how you think about relaxation",
    "Fushimi Inari at dawn: what it's like to have 10,000 torii gates to yourself",
    "Japan's izakaya culture — how to spend a perfect evening like a local",
    "Arashiyama bamboo grove: the time of day that actually makes it magical",
    "The tea ceremony experience in Kyoto — what to expect and why it stays with you",
    "Japan's train culture: the small rituals that make every journey feel different",
    "What to do on your first night in Tokyo to fall in love with the city",
    "The Nara deer park experience — approaching the deer at Todai-ji for the first time",
    "How to spend a perfect day in Hiroshima that you'll remember forever",
    "Japan's convenience stores: the surprisingly great food you should actually try",
    "The best neighbourhoods in Tokyo to wander with no plan",
    "Autumn in Japan: why the koyo season rivals cherry blossom season",
    "How to experience a real sento (public bath) in Tokyo like a local",
    "The hidden side of Kyoto that most visitors walk straight past",
    "What a traditional kaiseki dinner in Kyoto is actually like",
    "Mt. Fuji: the experience of seeing it for the first time from the Shinkansen",
    "Exploring Yanaka — Tokyo's last old-town neighbourhood",
    "What makes Hokkaido different from the rest of Japan — and why you should go",
    "The Philosopher's Path in Kyoto: when and how to walk it",
    "Tsukiji outer market in the morning — what to eat and why it matters",
    "The moment every Japan traveler has when they realise they want to come back",
    "Japan's seasonal festivals that are worth planning an entire trip around",
    "Things to do in Japan that you genuinely can't do anywhere else on earth",
    "The best views in Japan that aren't Mt. Fuji",
    "Japan's ryokan breakfast: the meal that changes how you feel about mornings",
]

SPOT_THEMES = [
    ("Fushimi Inari", "Kyoto", "the torii gates stretching up the mountain, the quiet atmosphere in the early morning"),
    ("Arashiyama bamboo grove", "Kyoto", "the sound of wind through bamboo, the way the light filters through"),
    ("Shibuya crossing", "Tokyo", "standing in the middle of the crossing as hundreds of people flow around you"),
    ("teamLab Borderless", "Tokyo", "walking through rooms of moving light that react to your touch"),
    ("Nara deer park", "Nara", "deer wandering freely among the visitors, some following you around hoping for food"),
    ("Dotonbori canal", "Osaka", "the neon lights reflecting off the water at night, the energy of the street"),
    ("Philosopher's Path", "Kyoto", "a quiet canal-side walk lined with cherry trees and small cafes"),
    ("Senso-ji temple", "Tokyo", "the giant lantern at the gate, the smell of incense, the wooden temple behind the crowds"),
    ("Kenroku-en garden", "Kanazawa", "one of Japan's most beautiful gardens, different in every season"),
    ("Kurashiki Bikan district", "Kurashiki", "white-walled storehouses along a willow-lined canal, completely preserved from the Edo era"),
    ("Gion district at dusk", "Kyoto", "stone-paved lanes, lantern-lit facades, the chance of spotting a geiko heading to work"),
    ("Hakone Open Air Museum", "Hakone", "sculptures set against the mountains, the outdoor baths you can use between galleries"),
    ("Miyajima island", "Hiroshima", "the floating torii gate at high tide, deer wandering the shrine grounds"),
    ("Yanaka neighbourhood", "Tokyo", "old shitamachi streets, tiny temples, the feeling that time slowed down here"),
    ("Ine Funaya", "Kyoto prefecture", "floating boat houses built directly over the sea, one of Japan's most photogenic fishing villages"),
]

FOOD_THEMES = [
    ("ramen", "Tokyo", "sitting at a tiny counter, the rich broth arriving in a bowl bigger than expected"),
    ("matcha soft serve", "Kyoto", "the intensity of the matcha flavour, the colour so green it barely looks real"),
    ("takoyaki", "Osaka", "fresh from the griddle, slightly crispy outside and molten inside"),
    ("sushi at Tsukiji outer market", "Tokyo", "the freshness of tuna and salmon eaten standing up at a market stall"),
    ("convenience store breakfast", "Tokyo", "onigiri, hot coffee from the machine, tamago sando — the ritual of a Japanese morning"),
    ("kaiseki dinner", "Kyoto", "course after course of beautiful small dishes, each one a different texture and season"),
    ("yakiniku grilled meat", "Tokyo", "grilling your own wagyu over charcoal at the table, the smell filling the whole room"),
    ("okonomiyaki", "Osaka", "watching it being made on the griddle in front of you, sweet and savoury and completely satisfying"),
    ("wagashi and matcha in a tea house", "Kyoto", "the sweetness of the wagashi against the bitterness of freshly whisked matcha"),
    ("ramen vending machine shop", "Tokyo", "ordering from a ticket machine, sitting alone at a counter with a curtain between you and the chef"),
    ("fresh uni (sea urchin)", "Hokkaido", "the briny, creamy flavour unlike anything you've tasted, eaten straight from the shell"),
    ("taiyaki from a street stall", "Kyoto", "fish-shaped pastry filled with warm red bean paste, eaten while walking"),
]

KLOOK_ACTIVITIES = [
    ("teamLab Borderless Digital Art Museum", "Tokyo",
     "Immersive digital art world with 50+ installations. One of Japan's most viral experiences — book ahead.",
     "https://www.klook.com/en-US/search/?query=teamlab+borderless+tokyo"),
    ("Mt. Fuji Day Trip from Tokyo", "Tokyo",
     "Guided day trip to Mt. Fuji + Lake Kawaguchi with English guide. Hotel pickup included.",
     "https://www.klook.com/en-US/search/?query=mt+fuji+day+trip+tokyo"),
    ("Kyoto Tea Ceremony Experience", "Kyoto",
     "Hands-on matcha tea ceremony in a traditional machiya townhouse. English instruction included.",
     "https://www.klook.com/en-US/search/?query=kyoto+tea+ceremony"),
    ("Arashiyama Rickshaw Ride", "Kyoto",
     "Human-pulled rickshaw through Arashiyama bamboo grove with English-speaking guide.",
     "https://www.klook.com/en-US/search/?query=arashiyama+rickshaw+kyoto"),
    ("Osaka Dotonbori Food Tour", "Osaka",
     "Evening street food walk through Dotonbori with a local guide. Takoyaki, kushikatsu, okonomiyaki included.",
     "https://www.klook.com/en-US/search/?query=osaka+dotonbori+food+tour"),
    ("Universal Studios Japan Express Pass", "Osaka",
     "Skip-the-line access to USJ's top attractions including Super Nintendo World.",
     "https://www.klook.com/en-US/search/?query=universal+studios+japan+express+pass"),
    ("Tokyo Skytree + Asakusa Tour", "Tokyo",
     "Tokyo Skytree observation deck + guided Senso-ji temple walk in Asakusa. Skip-the-line entry.",
     "https://www.klook.com/en-US/search/?query=tokyo+skytree+asakusa+tour"),
    ("Hiroshima & Miyajima Day Trip from Osaka", "Osaka",
     "Full-day tour to Hiroshima Peace Memorial + Miyajima floating torii gate. Guide included.",
     "https://www.klook.com/en-US/search/?query=hiroshima+miyajima+day+trip+osaka"),
    ("Tokyo Shibuya Night Tour", "Tokyo",
     "Evening walk through Shibuya crossing, Golden Gai, and Shinjuku kabukicho.",
     "https://www.klook.com/en-US/search/?query=tokyo+shibuya+night+tour"),
    ("Nara Deer Park & Todai-ji Tour", "Nara",
     "Half-day tour to Nara from Osaka. Feed wild deer, visit the world's largest wooden building.",
     "https://www.klook.com/en-US/search/?query=nara+deer+park+temple+tour"),
    ("Tokyo Sushi Making Class", "Tokyo",
     "Learn nigiri and maki from a professional sushi chef. Small group, eat what you make.",
     "https://www.klook.com/en-US/search/?query=tokyo+sushi+making+class"),
    ("Hakone Mt. Fuji View Day Trip", "Hakone",
     "Hakone Open Air Museum + Lake Ashi cruise + ropeway over volcanic valley.",
     "https://www.klook.com/en-US/search/?query=hakone+day+trip+tokyo"),
]


# ── State management ───────────────────────────────────────────────

_SLOT_COOLDOWN_HOURS = 6   # same slot may not post more than once per N hours

def load_state() -> dict:
    defaults = {
        "experience_idx":    0,
        "spot_idx":          0,
        "food_idx":          0,
        "klook_idx":         0,
        "lunch_cycle":       0,   # mod 3: 0,1=article; 2=klook
        "evening_parity":    0,   # 0=spot, 1=food; toggles each evening
        "recent_article_ids": [],
        "last_slot_times":   {},  # slot → ISO timestamp of last successful post
    }
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        for k, v in defaults.items():
            state.setdefault(k, v)
        return state
    except Exception:
        return defaults


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def slot_recently_posted(state: dict, slot: str, force: bool = False) -> bool:
    """Return True (and skip) if this slot already posted within cooldown window."""
    if force:
        return False
    last = state.get("last_slot_times", {}).get(slot)
    if not last:
        return False
    try:
        from datetime import datetime, timezone, timedelta
        last_dt = datetime.fromisoformat(last)
        age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        if age_hours < _SLOT_COOLDOWN_HOURS:
            print(f"  [SKIP] {slot} slot posted {age_hours:.1f}h ago (cooldown: {_SLOT_COOLDOWN_HOURS}h). Exiting.")
            return True
    except Exception:
        pass
    return False


def mark_slot_posted(state: dict, slot: str):
    """Record that this slot just successfully posted."""
    from datetime import datetime, timezone
    state.setdefault("last_slot_times", {})[slot] = datetime.now(timezone.utc).isoformat()


# ── Claude helpers ─────────────────────────────────────────────────

def call_claude(prompt: str) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    for attempt, model in enumerate(["claude-opus-4-6", "claude-sonnet-4-6"], 1):
        try:
            msg = client.messages.create(
                model=model, max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            if "overloaded" in str(e).lower() and attempt == 1:
                print("  Opus過負荷 → Sonnetにフォールバック...")
                time.sleep(5)
                continue
            raise


_FORBIDDEN_OPENERS = (
    "nobody tells you",
    "what nobody tells you",
    "nobody talks about",
    "what no one tells you",
    "no one tells you",
    "they don't tell you",
    "what they don't tell you",
    "japan is actually",
    "the truth about",
    "the secret to",
    "skip the ",
    "avoid the ",
    "hot take:",
    "hot take —",
    "scam",
)

def is_forbidden(text: str) -> bool:
    """Return True if the post starts with a banned opener."""
    first = text.strip().lower()[:60]
    return any(first.startswith(phrase) for phrase in _FORBIDDEN_OPENERS)


def safe_call_claude(prompt: str, retries: int = 2) -> str:
    """Call Claude and retry if the output starts with a forbidden opener."""
    for attempt in range(retries + 1):
        text = call_claude(prompt)
        if not is_forbidden(text):
            return text
        print(f"  [RETRY {attempt+1}] Forbidden opener detected — regenerating...")
        # Strengthen the instruction on retry
        prompt = prompt.rstrip() + (
            "\n\nIMPORTANT: Your previous response started with a forbidden phrase. "
            "Do NOT start with 'Nobody', 'No one', 'What nobody', 'Japan is actually', "
            "or any advice/tip framing. Start with 'I' or 'We' — a personal memory."
        )
    # Last-resort fallback: plain first-person opener
    print("  [WARN] All retries had forbidden openers. Using safe fallback.")
    return "I still think about Japan every day. Have you been? #JapanTravel #VisitJapan"


def trim_to_limit(text: str, limit: int = 450) -> str:
    def char_len(s):
        return sum(2 if ord(c) > 0xFFFF else 1 for c in s)
    if char_len(text) <= limit:
        return text
    lines, trimmed, total = text.splitlines(), [], 0
    for line in lines:
        ll = char_len(line)
        if total + ll + 1 > limit:
            break
        trimmed.append(line)
        total += ll + 1
    result = "\n".join(trimmed) if trimmed else lines[0][:440]
    print(f"  [TRIM] {char_len(text)} → {char_len(result)} chars")
    return result


# ── Post generators ────────────────────────────────────────────────

def generate_experience_post(theme: str) -> str:
    return safe_call_claude(f"""You write Threads posts for @japantravelbase, a Japan travel account.

THEME: {theme}

Write a first-person travel memory post.

RULES:
- 220–360 characters total
- First-person only ("I", "we") — a personal memory, not advice
- Sensory details: what you saw, smelled, tasted, heard, felt
- No prices, statistics, distances, or specific times
- No factual claims that could be disputed
- No restaurant/hotel/shop names
- End with a simple question: "Have you been?" or "What was yours?" or similar
- Final line: 2–3 hashtags (#JapanTravel #VisitJapan + one relevant tag)

FORBIDDEN OPENERS — never start the post with any of these phrases:
"Nobody tells you", "What nobody tells you", "Nobody talks about",
"What no one tells you", "No one tells you", "They don't tell you",
"What they don't tell you", "The truth about", "The secret to",
"The thing about Japan", "Here's what Japan won't tell you",
"Japan is actually", "Skip the [X]", "Avoid the [X]"

Output ONLY the post text. No quotation marks.""")


def generate_article_post(article: dict) -> str:
    title   = re.sub(r"<[^>]+>", "", article.get("title", {}).get("rendered", ""))
    excerpt = re.sub(r"<[^>]+>", "", article.get("excerpt", {}).get("rendered", "")).strip()[:200]
    is_yt   = "youtube.com" in article.get("link", "") or "youtu.be" in article.get("link", "")
    cta     = "Watch now →" if is_yt else "Full guide →"
    return safe_call_claude(f"""You write Threads posts for @japantravelbase, a Japan travel account.

ARTICLE TITLE: {title}
EXCERPT: {excerpt}

Write a short post that makes followers want to read this.

RULES:
- 220–360 characters total (NO URL — goes in reply separately)
- Open with a personal travel feeling or moment related to the topic
- One line on what makes the article useful
- Final line: "{cta}" + 2–3 hashtags (#JapanTravel #Japan2026 + one relevant tag)
- First-person ("I", "we"), warm and personal tone
- No prices or factual claims

FORBIDDEN OPENERS — never start the post with any of these phrases:
"Nobody tells you", "What nobody tells you", "Nobody talks about",
"What no one tells you", "No one tells you", "They don't tell you",
"Japan is actually", "Skip the [X]", "Avoid the [X]"

Output ONLY the post text. No quotation marks.""")


def generate_spot_post(name: str, city: str, description: str) -> str:
    return safe_call_claude(f"""You write Threads posts for @japantravelbase, a Japan travel account.

SPOT: {name}, {city}
WHAT IT'S LIKE: {description}

Write a first-person post recommending this place.

RULES:
- 220–360 characters total
- Open with "If you go to Japan, visit {name}" or a similar hook
- Describe the sensory experience using ONLY the details provided above
- No opening hours, entrance fees, visitor numbers, or statistics
- No place names or details not given above
- Genuine recommendation tone, like telling a friend
- Final line: 2–3 hashtags (#JapanTravel #VisitJapan + one city/spot tag)

Output ONLY the post text. No quotation marks.""")


def generate_food_post(name: str, city: str, description: str) -> str:
    return safe_call_claude(f"""You write Threads posts for @japantravelbase, a Japan travel account.

FOOD: {name} in {city}, Japan
EXPERIENCE: {description}

Write a first-person post about eating this food in Japan.

RULES:
- 210–350 characters total
- Open with the sensory moment of eating it
- Focus on taste, smell, texture, atmosphere — ONLY from details above
- No restaurant names, prices, or addresses
- End with: "Try this when you're in Japan" or "Don't skip this" or similar
- Final line: 2–3 hashtags (#JapanFood #JapanTravel + one city tag)

Output ONLY the post text. No quotation marks.""")


def generate_klook_post(name: str, city: str, description: str) -> str:
    return safe_call_claude(f"""You write Threads posts for @japantravelbase, a Japan travel account.

ACTIVITY: {name} in {city}, Japan
WHAT IT'S LIKE: {description}

Write a first-person post that makes followers want to book this.

RULES:
- 220–360 characters total (NO URL — goes in reply separately)
- Open with a personal experience moment from this activity
- One line on what makes it memorable
- End with: "Link in replies to book" or "Details in the comments"
- No prices, "sells out fast", or unverifiable claims
- Final line: 2–3 hashtags (#KlookJapan #JapanTravel + one city tag)

FORBIDDEN OPENERS — never start the post with any of these phrases:
"Nobody tells you", "What nobody tells you", "Nobody talks about",
"What no one tells you", "No one tells you", "Japan is actually",
"Skip the [X]", "Avoid the [X]"

Output ONLY the post text. No quotation marks.""")


# ── Pexels ─────────────────────────────────────────────────────────

def fetch_pexels_photo(query: str) -> str | None:
    if not PEXELS_API_KEY:
        return None
    try:
        import random
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query + " Japan", "per_page": 15, "orientation": "landscape"},
            timeout=10,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        return random.choice(photos[:10])["src"]["large2x"] if photos else None
    except Exception:
        return None


# ── WordPress ──────────────────────────────────────────────────────

def get_latest_articles(count: int = 15) -> list:
    try:
        r = requests.get(
            f"{WP_URL}/wp-json/wp/v2/posts",
            params={"per_page": count, "status": "publish",
                    "_fields": "id,title,link,excerpt", "context": "view"},
            auth=(os.getenv("WP_USER", ""), os.getenv("WP_APP_PASSWORD", "")),
            timeout=10,
        )
        if r.ok:
            return r.json()
    except Exception as e:
        print(f"[WARN] WP記事取得失敗: {e}")
    return []


# ── Threads API ────────────────────────────────────────────────────

def post_to_threads(text: str, dry_run: bool = False,
                    reply_to_id: str = None, image_url: str = None) -> str | None:
    if dry_run:
        label = "[DRY RUN reply]" if reply_to_id else "[DRY RUN]"
        print(f"\n{label}\n{'-'*50}\n{text}\n{'-'*50}")
        if image_url:
            print(f"Image: {image_url[:80]}...")
        print(f"Length: {len(text)} chars")
        return "dry_run_id"

    data = {
        "media_type": "IMAGE" if image_url else "TEXT",
        "text": text,
        "access_token": THREADS_TOKEN,
    }
    if image_url:
        data["image_url"] = image_url
    if reply_to_id:
        data["reply_to_id"] = reply_to_id

    r1 = requests.post(f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads",
                       data=data, timeout=15)
    if not r1.ok:
        print(f"[ERROR] コンテナ作成失敗: {r1.status_code} {r1.text[:200]}")
        return None

    creation_id = r1.json().get("id")
    time.sleep(8 if image_url else 3)

    r2 = None
    for attempt in range(3):
        r2 = requests.post(
            f"{THREADS_API_BASE}/{THREADS_USER_ID}/threads_publish",
            data={"creation_id": creation_id, "access_token": THREADS_TOKEN},
            timeout=15,
        )
        if r2.ok:
            break
        if r2.json().get("error", {}).get("error_subcode") == 4279009 and attempt < 2:
            print(f"  コンテナ待ち... ({attempt+1}/3)")
            time.sleep(10)
        else:
            break

    if not r2 or not r2.ok:
        print(f"[ERROR] 公開失敗: {r2.status_code} {r2.text[:200]}")
        return None

    post_id = r2.json().get("id")
    print(f"  ✓ 投稿成功 (ID: {post_id})")
    return post_id


def post_url_reply(post_id: str, cta: str, url: str,
                   fallback_url: str = None, dry_run: bool = False):
    time.sleep(5)
    reply_id = post_to_threads(f"{cta}\n{url}", dry_run=dry_run, reply_to_id=post_id)
    if not reply_id and not dry_run and fallback_url and fallback_url != url:
        print("  アフィリエイトURL失敗 → 直接URLで再試行...")
        reply_id = post_to_threads(f"{cta}\n{fallback_url}",
                                   dry_run=dry_run, reply_to_id=post_id)
    if not reply_id and not dry_run:
        print(f"  [WARN] URLリプライ失敗 (メイン投稿 {post_id} は成功済み)")


# ── Slot detection ─────────────────────────────────────────────────

def get_slot() -> str:
    hour = datetime.now(JST).hour
    if hour < 11:    # catches JST 8:00
        return "morning"
    elif hour < 16:  # catches JST 12:00
        return "lunch"
    else:            # catches JST 19:00
        return "evening"


# ── Main ───────────────────────────────────────────────────────────

def main():
    dry_run = "--test" in sys.argv

    # Force overrides (don't save state when forced)
    if   "--article"    in sys.argv: slot, force = "lunch",   "article"
    elif "--klook"      in sys.argv: slot, force = "lunch",   "klook"
    elif "--experience" in sys.argv: slot, force = "morning", "experience"
    elif "--spot"       in sys.argv: slot, force = "evening", "spot"
    elif "--food"       in sys.argv: slot, force = "evening", "food"
    else:                            slot, force = get_slot(), None

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    print(f"=== Threads [{now_str}] | slot={slot}{' | force=' + force if force else ''} ===")

    state = load_state()

    # ── Cooldown guard: skip if this slot already ran recently ───
    if not dry_run and slot_recently_posted(state, slot, force=bool(force)):
        sys.exit(0)

    # ── Morning: experience ──────────────────────────────────────
    if slot == "morning":
        idx   = state["experience_idx"] % len(EXPERIENCE_THEMES)
        theme = EXPERIENCE_THEMES[idx]
        print(f"  Experience [{idx+1}/{len(EXPERIENCE_THEMES)}]: {theme[:65]}")
        print("  Claude生成中...")
        text  = trim_to_limit(generate_experience_post(theme))
        photo = fetch_pexels_photo(theme[:50])
        print(f"  Pexels: {'✓' if photo else '×'}")
        post_id = post_to_threads(text, dry_run=dry_run, image_url=photo)
        if (post_id or dry_run) and not force:
            state["experience_idx"] = idx + 1

    # ── Lunch: article (2/3) or klook (1/3) ─────────────────────
    elif slot == "lunch":
        cycle     = state["lunch_cycle"] % 3
        use_klook = (cycle == 2) if not force else (force == "klook")

        if not use_klook:
            articles    = get_latest_articles(15)
            recent_ids  = set(state["recent_article_ids"][-12:])
            fresh       = [a for a in articles if a.get("id") not in recent_ids]
            article     = (fresh or articles)[0] if (fresh or articles) else None

            if not article:
                print("  [WARN] 記事なし → klookに切り替え")
                use_klook = True

        if not use_klook and article:
            title = re.sub(r"<[^>]+>", "", article.get("title", {}).get("rendered", ""))
            print(f"  Article (cycle {cycle+1}/3): {title[:60]}")
            print("  Claude生成中...")
            text  = trim_to_limit(generate_article_post(article))
            photo = fetch_pexels_photo(title[:50] or "Japan travel")
            print(f"  Pexels: {'✓' if photo else '×'}")
            post_id = post_to_threads(text, dry_run=dry_run, image_url=photo)
            if (post_id or dry_run) and not force:
                state["recent_article_ids"] = (state["recent_article_ids"] + [article["id"]])[-15:]
                state["lunch_cycle"] += 1
            link = article.get("link", "")
            if link and (post_id or dry_run):
                post_url_reply(post_id, "Full guide →", link, dry_run=dry_run)

        if use_klook:
            idx  = state["klook_idx"] % len(KLOOK_ACTIVITIES)
            name, city, desc, klook_url = KLOOK_ACTIVITIES[idx]
            print(f"  Klook [{idx+1}/{len(KLOOK_ACTIVITIES)}]: {name}")
            print("  Claude生成中...")
            text  = trim_to_limit(generate_klook_post(name, city, desc))
            photo = fetch_pexels_photo(f"{name} {city} Japan")
            print(f"  Pexels: {'✓' if photo else '×'}")
            post_id = post_to_threads(text, dry_run=dry_run, image_url=photo)
            if (post_id or dry_run) and not force:
                state["klook_idx"]    = idx + 1
                state["lunch_cycle"] += 1
            if post_id or dry_run:
                aff_url = klook_affiliate_url(klook_url)
                post_url_reply(post_id, "Book on Klook →", aff_url,
                               fallback_url=klook_url, dry_run=dry_run)

    # ── Evening: spot or food (alternating) ─────────────────────
    elif slot == "evening":
        parity = state["evening_parity"]
        if force == "spot":  parity = 0
        if force == "food":  parity = 1

        if parity == 0:
            idx         = state["spot_idx"] % len(SPOT_THEMES)
            name, city, desc = SPOT_THEMES[idx]
            print(f"  Spot [{idx+1}/{len(SPOT_THEMES)}]: {name}, {city}")
            print("  Claude生成中...")
            text  = trim_to_limit(generate_spot_post(name, city, desc))
            photo = fetch_pexels_photo(f"{name} {city} Japan")
            print(f"  Pexels: {'✓' if photo else '×'}")
            post_id = post_to_threads(text, dry_run=dry_run, image_url=photo)
            if (post_id or dry_run) and not force:
                state["spot_idx"]       = idx + 1
                state["evening_parity"] = 1
        else:
            idx         = state["food_idx"] % len(FOOD_THEMES)
            name, city, desc = FOOD_THEMES[idx]
            print(f"  Food [{idx+1}/{len(FOOD_THEMES)}]: {name}, {city}")
            print("  Claude生成中...")
            text  = trim_to_limit(generate_food_post(name, city, desc))
            photo = fetch_pexels_photo(f"{name} Japan food")
            print(f"  Pexels: {'✓' if photo else '×'}")
            post_id = post_to_threads(text, dry_run=dry_run, image_url=photo)
            if (post_id or dry_run) and not force:
                state["food_idx"]       = idx + 1
                state["evening_parity"] = 0

    if not dry_run and not force:
        mark_slot_posted(state, slot)
        save_state(state)
        print("  状態保存 ✓")
    elif dry_run:
        print("\n[DRY RUN] 状態ファイルは更新されていません")


if __name__ == "__main__":
    main()
