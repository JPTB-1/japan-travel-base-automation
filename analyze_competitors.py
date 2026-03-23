"""
analyze_competitors.py
競合上位記事分析スクリプト

使い方:
    python3 analyze_competitors.py "best things to do in Tokyo"
    python3 analyze_competitors.py --from-gsc   # GSCの主要クエリを自動分析

出力:
    competitor_insights.json — カバーされていないニッチなアングル
"""

import os
import sys
import json
import time
import re
import requests
from dotenv import load_dotenv
import anthropic

load_dotenv(dotenv_path=".env")

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
GSC_INSIGHTS   = "gsc_insights.json"
OUTPUT_FILE    = "competitor_insights.json"


def search_top_results(query: str, num=10) -> list[dict]:
    """Serper.dev APIでGoogle上位記事を取得"""
    r = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num, "gl": "us", "hl": "en"},
        timeout=15,
    )
    if not r.ok:
        print(f"[ERROR] Serper API失敗: {r.status_code} {r.text[:200]}")
        return []
    results = []
    for item in r.json().get("organic", []):
        results.append({
            "title":   item.get("title", ""),
            "url":     item.get("link", ""),
            "snippet": item.get("snippet", ""),
        })
    return results[:num]


def fetch_article_content(url: str, max_chars=3000) -> str:
    """記事の本文を取得"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; JTB-Research-Bot/1.0)"}
        r = requests.get(url, headers=headers, timeout=10)
        if not r.ok:
            return ""
        # HTMLタグを除去
        text = re.sub(r"<style[^>]*>.*?</style>", "", r.text, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def analyze_gaps(query: str, competitor_articles: list[dict]) -> dict:
    """Claudeで競合のギャップを分析"""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

    articles_text = ""
    for i, article in enumerate(competitor_articles, 1):
        articles_text += f"\n--- Article {i}: {article['title']} ---\n"
        articles_text += f"URL: {article['url']}\n"
        articles_text += f"Content summary: {article.get('content', article.get('snippet', ''))[:500]}\n"

    prompt = f"""You are an SEO content strategist. Analyze these top-ranking articles for the search query "{query}" and identify content gaps.

TOP RANKING ARTICLES:
{articles_text}

Please provide:
1. **Common angles covered** (what all/most articles cover)
2. **Underserved niches** (specific angles NOT well covered by competitors)
3. **Unique content opportunities** (3-5 specific article angles that could rank well)
4. **Recommended focus keywords** (long-tail keywords competitors miss)
5. **Content suggestions for Japan Travel Base** (specific actionable ideas)

Format as JSON with these keys:
- common_angles: list of strings
- underserved_niches: list of strings
- content_opportunities: list of objects with "title", "angle", "why_it_ranks"
- recommended_keywords: list of strings
- jtb_suggestions: list of strings

Output ONLY valid JSON, no other text."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    # JSONを抽出
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    return json.loads(text)


def get_queries_from_gsc(top_n=5) -> list[str]:
    """GSCの主要クエリを取得"""
    if not os.path.exists(GSC_INSIGHTS):
        print("[WARN] gsc_insights.json が見つかりません。先に analyze_gsc.py を実行してください。")
        return []

    with open(GSC_INSIGHTS) as f:
        data = json.load(f)

    queries = set()
    for page in data.get("priority_pages", [])[:top_n]:
        for q in page.get("top_queries", [])[:3]:
            queries.add(q["query"])

    return list(queries)[:top_n]


def main():
    from_gsc = "--from-gsc" in sys.argv
    queries = []

    if from_gsc:
        queries = get_queries_from_gsc(5)
        if not queries:
            print("GSCクエリが取得できませんでした。")
            sys.exit(1)
        print(f"GSCから{len(queries)}件のクエリを分析します")
    else:
        # コマンドライン引数からクエリを取得
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        if not args:
            print("使い方: python3 analyze_competitors.py \"検索クエリ\"")
            print("または: python3 analyze_competitors.py --from-gsc")
            sys.exit(1)
        queries = [" ".join(args)]

    all_insights = []

    for query in queries:
        print(f"\n=== 分析: \"{query}\" ===")
        print("  上位記事を検索中…")
        results = search_top_results(query, num=10)
        print(f"  {len(results)}件取得")

        print("  記事コンテンツを取得中…")
        for i, r in enumerate(results[:5]):  # 上位5件だけ詳細取得（API節約）
            print(f"    [{i+1}] {r['url'][:60]}…")
            r["content"] = fetch_article_content(r["url"])
            time.sleep(0.3)

        print("  Claudeでギャップ分析中…")
        try:
            gaps = analyze_gaps(query, results)
            insight = {
                "query":       query,
                "competitors": [{"title": r["title"], "url": r["url"]} for r in results],
                "analysis":    gaps,
            }
            all_insights.append(insight)

            # 結果を表示
            print(f"\n  【カバーされていないニッチ】")
            for niche in gaps.get("underserved_niches", [])[:3]:
                print(f"    • {niche}")
            print(f"\n  【コンテンツ機会】")
            for opp in gaps.get("content_opportunities", [])[:3]:
                print(f"    • {opp.get('title', opp)}")

        except Exception as e:
            print(f"  [ERROR] 分析失敗: {e}")

    # 保存
    output = {
        "updated_at": __import__("datetime").datetime.now().isoformat(),
        "insights":   all_insights,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ {OUTPUT_FILE} に保存しました")


if __name__ == "__main__":
    main()
