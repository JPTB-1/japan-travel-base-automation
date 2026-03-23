"""
analyze_gsc.py
Google Search Console データ分析スクリプト

使い方:
    python3 analyze_gsc.py          # GSCデータ取得・分析
    python3 analyze_gsc.py --report # レポートのみ表示

出力:
    gsc_insights.json  — 強化すべき記事リスト（generate_article.py が参照）

cron: 毎日JST 7:00 (UTC 22:00) に実行
    0 22 * * * cd "/path/to" && /usr/bin/python3 analyze_gsc.py >> cron_gsc.log 2>&1
"""

import os
import json
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv(dotenv_path=".env")

CREDENTIALS_FILE = os.getenv("GOOGLE_GSC_CREDENTIALS", "gsc_credentials.json")
SITE_URL         = os.getenv("GSC_SITE_URL", "https://japantravelbase.com/")
OUTPUT_FILE      = "gsc_insights.json"
SCOPES           = ["https://www.googleapis.com/auth/webmasters.readonly"]


def get_gsc_service():
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES
    )
    return build("searchconsole", "v1", credentials=creds)


def fetch_search_analytics(service, start_date: str, end_date: str, row_limit=500):
    """クエリ×ページのデータを取得"""
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["page", "query"],
        "rowLimit": row_limit,
        "dataState": "final",
    }
    res = service.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
    return res.get("rows", [])


def analyze(rows_recent, rows_prev):
    """インプレッションが伸びている記事を検出"""

    # ページ単位で集計
    def aggregate_by_page(rows):
        pages = {}
        for row in rows:
            page  = row["keys"][0]
            query = row["keys"][1]
            if page not in pages:
                pages[page] = {"impressions": 0, "clicks": 0, "position_sum": 0, "count": 0, "queries": []}
            pages[page]["impressions"]   += row.get("impressions", 0)
            pages[page]["clicks"]        += row.get("clicks", 0)
            pages[page]["position_sum"]  += row.get("position", 0)
            pages[page]["count"]         += 1
            pages[page]["queries"].append({
                "query":       query,
                "impressions": row.get("impressions", 0),
                "clicks":      row.get("clicks", 0),
                "position":    round(row.get("position", 0), 1),
            })
        # 平均順位を計算
        for p in pages.values():
            p["avg_position"] = round(p["position_sum"] / p["count"], 1) if p["count"] else 0
            p["queries"] = sorted(p["queries"], key=lambda x: x["impressions"], reverse=True)[:10]
        return pages

    recent = aggregate_by_page(rows_recent)
    prev   = aggregate_by_page(rows_prev)

    insights = []
    for page, data in recent.items():
        prev_data    = prev.get(page, {})
        prev_impr    = prev_data.get("impressions", 0)
        curr_impr    = data["impressions"]
        impr_growth  = curr_impr - prev_impr
        impr_growth_pct = (impr_growth / prev_impr * 100) if prev_impr > 0 else 100

        # 強化候補の条件:
        # 1. インプレッションが増加している
        # 2. CTRが低い（クリックされていない）
        # 3. 平均順位が11〜50位（圏外から圏内に入り始め）
        ctr      = data["clicks"] / curr_impr if curr_impr > 0 else 0
        position = data["avg_position"]

        score = 0
        reasons = []

        if impr_growth > 5:
            score += 2
            reasons.append(f"impressions +{impr_growth} vs prev period")
        if impr_growth_pct > 30:
            score += 1
            reasons.append(f"growth {impr_growth_pct:.0f}%")
        if ctr < 0.03 and curr_impr > 10:
            score += 2
            reasons.append(f"low CTR {ctr:.1%}")
        if 10 < position <= 30:
            score += 3
            reasons.append(f"position {position} (page 2-3, improvable)")
        if 30 < position <= 50:
            score += 1
            reasons.append(f"position {position} (entering index)")

        if score >= 3:
            insights.append({
                "url":          page,
                "score":        score,
                "impressions":  curr_impr,
                "clicks":       data["clicks"],
                "ctr":          round(ctr * 100, 2),
                "avg_position": position,
                "impr_growth":  impr_growth,
                "reasons":      reasons,
                "top_queries":  data["queries"][:5],
            })

    # スコア順にソート
    insights.sort(key=lambda x: x["score"], reverse=True)
    return insights[:20]  # 上位20記事


def main():
    report_only = "--report" in sys.argv

    print("=== GSC データ分析 ===")

    if report_only:
        if os.path.exists(OUTPUT_FILE):
            with open(OUTPUT_FILE) as f:
                data = json.load(f)
            print(f"最終更新: {data.get('updated_at', 'unknown')}")
            for i, item in enumerate(data.get("priority_pages", [])[:10], 1):
                print(f"\n{i}. {item['url']}")
                print(f"   順位: {item['avg_position']} | インプレッション: {item['impressions']} | CTR: {item['ctr']}%")
                print(f"   理由: {', '.join(item['reasons'])}")
                print(f"   主要クエリ: {', '.join(q['query'] for q in item['top_queries'][:3])}")
        else:
            print("gsc_insights.json が見つかりません。先に python3 analyze_gsc.py を実行してください。")
        return

    service = get_gsc_service()

    today = datetime.now(timezone.utc).date()
    # 直近7日
    end_recent   = today - timedelta(days=3)  # GSCは3日遅延
    start_recent = end_recent - timedelta(days=7)
    # 前の7日
    end_prev     = start_recent - timedelta(days=1)
    start_prev   = end_prev - timedelta(days=7)

    print(f"  直近期間: {start_recent} 〜 {end_recent}")
    print(f"  比較期間: {start_prev} 〜 {end_prev}")
    print("  データ取得中…")

    rows_recent = fetch_search_analytics(service, str(start_recent), str(end_recent))
    rows_prev   = fetch_search_analytics(service, str(start_prev), str(end_prev))

    print(f"  直近: {len(rows_recent)}行 / 前期間: {len(rows_prev)}行")

    insights = analyze(rows_recent, rows_prev)
    print(f"  強化候補: {len(insights)}記事")

    output = {
        "updated_at":    datetime.now().isoformat(),
        "period":        f"{start_recent} to {end_recent}",
        "priority_pages": insights,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  ✓ {OUTPUT_FILE} に保存しました")

    # 上位5件を表示
    for i, item in enumerate(insights[:5], 1):
        print(f"\n  {i}. {item['url']}")
        print(f"     順位: {item['avg_position']} | インプレッション: {item['impressions']} | CTR: {item['ctr']}%")
        print(f"     理由: {', '.join(item['reasons'])}")


if __name__ == "__main__":
    main()
