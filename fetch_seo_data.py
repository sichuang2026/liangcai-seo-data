#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GSC (Search Console) 近30天搜索词 + GA4 近30天流量 → JSON
环境变量:
  GOOGLE_SERVICE_ACCOUNT_JSON  service account 密钥 JSON 全文
  SEARCH_CONSOLE_SITE_URL      GSC 资源，如 sc-domain:example.com 或 https://example.com/
  GA4_PROPERTY_ID              可选，GA4 数字 property id；缺省则跳过 GA4
输出:
  data/seo-data-YYYY-MM-DD.json   当日快照
  data/latest.json                最新快照（覆盖写）
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    OrderBy,
    RunReportRequest,
)

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]
DAYS = int(os.environ.get("LOOKBACK_DAYS", "90"))
OUT_DIR = Path("data")
GSC_ROW_LIMIT = 25000


def fail(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def load_credentials():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        fail("缺少环境变量 GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        fail("GOOGLE_SERVICE_ACCOUNT_JSON 不是合法 JSON（检查是否有多余换行/引号）")
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def date_window(days=DAYS):
    # GSC 数据通常 T+2，取完整已结算区间更稳
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


# ---------------- GSC ----------------
def gsc_query(svc, site, start, end, dimensions):
    rows, start_row = [], 0
    while True:
        body = {
            "startDate": start,
            "endDate": end,
            "dimensions": dimensions,
            "rowLimit": GSC_ROW_LIMIT,
            "startRow": start_row,
            "dataState": "final",
        }
        resp = svc.searchanalytics().query(siteUrl=site, body=body).execute()
        batch = resp.get("rows", [])
        rows.extend(batch)
        if len(batch) < GSC_ROW_LIMIT:
            break
        start_row += GSC_ROW_LIMIT
    out = []
    for r in rows:
        keys = r.get("keys", [])
        out.append(
            {
                "keys": keys,
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": round(r.get("ctr", 0) * 100, 2),
                "position": round(r.get("position", 0), 1),
            }
        )
    return out


def fetch_gsc(creds, site, start, end):
    svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    print(f"[GSC] {site} {start} ~ {end}")
    # 三份报表分工：query 单独看词量；page 单独看单页 CTR；query+page 看落地匹配
    queries_only = gsc_query(svc, site, start, end, ["query"])
    pages_only = gsc_query(svc, site, start, end, ["page"])
    queries = gsc_query(svc, site, start, end, ["query", "page"])
    countries = gsc_query(svc, site, start, end, ["country"])
    devices = gsc_query(svc, site, start, end, ["device"])
    total_clicks = sum(q["clicks"] for q in countries)
    total_impr = sum(q["impressions"] for q in countries)
    return {
        "site": site,
        "start_date": start,
        "end_date": end,
        "totals": {
            "clicks": total_clicks,
            "impressions": total_impr,
            "ctr": round(total_clicks / total_impr * 100, 2) if total_impr else 0,
        },
        "query_page_count": len(queries),
        "query_count": len(queries_only),
        "page_count": len(pages_only),
        "queries_only": queries_only,
        "pages_only": pages_only,
        "queries": queries,
        "countries": countries,
        "devices": devices,
    }


# ---------------- GA4 ----------------
def ga4_report(client, prop, start, end, dimensions, metrics, limit=100, order_metric=None, order_dim=None):
    req = RunReportRequest(
        property=f"properties/{prop}",
        date_ranges=[DateRange(start_date=start, end_date=end)],
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        limit=limit,
    )
    if order_dim:
        # 日期是维度不是指标，必须走 DimensionOrderBy，按时间升序
        req.order_bys = [
            OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name=order_dim), desc=False)
        ]
    elif order_metric:
        req.order_bys = [
            OrderBy(metric=OrderBy.MetricOrderBy(metric_name=order_metric), desc=True)
        ]
    resp = client.run_report(req)
    rows = []
    for row in resp.rows:
        item = {"dimensions": [d.value for d in row.dimension_values]}
        item["metrics"] = {}
        for i, m in enumerate(metrics):
            v = row.metric_values[i].value
            try:
                item["metrics"][m] = float(v)
            except ValueError:
                item["metrics"][m] = v
        rows.append(item)
    return rows


def fetch_ga4(creds, prop, start, end):
    client = BetaAnalyticsDataClient(credentials=creds)
    print(f"[GA4] property {prop} {start} ~ {end}")
    core = ["sessions", "totalUsers", "screenPageViews", "engagementRate",
            "averageSessionDuration", "conversions"]
    daily = ga4_report(client, prop, start, end, ["date"], core, limit=DAYS + 5, order_dim="date")
    channels = ga4_report(client, prop, start, end, ["sessionDefaultChannelGroup"], core,
                          limit=20, order_metric="sessions")
    pages = ga4_report(client, prop, start, end, ["pagePath"], core, limit=50, order_metric="screenPageViews")
    source_medium = ga4_report(client, prop, start, end, ["sessionSourceMedium"], core,
                               limit=50, order_metric="sessions")
    return {
        "property_id": prop,
        "start_date": start,
        "end_date": end,
        "daily": daily,
        "by_channel": channels,
        "top_pages": pages,
        "by_source_medium": source_medium,
    }


def main():
    creds = load_credentials()
    site = os.environ.get("SEARCH_CONSOLE_SITE_URL", "").strip()
    prop = os.environ.get("GA4_PROPERTY_ID", "").strip()

    if not site:
        fail("缺少环境变量 SEARCH_CONSOLE_SITE_URL")

    start, end = date_window()
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone(
            timezone(timedelta(hours=8))).isoformat(),
        "range_days": DAYS,
    }

    errors = []
    try:
        payload["gsc"] = fetch_gsc(creds, site, start, end)
    except Exception as e:  # noqa: BLE001
        errors.append(f"GSC: {type(e).__name__}: {e}")
        payload["gsc"] = {"error": str(e)}

    if prop:
        try:
            payload["ga4"] = fetch_ga4(creds, prop, start, end)
        except Exception as e:  # noqa: BLE001
            errors.append(f"GA4: {type(e).__name__}: {e}")
            payload["ga4"] = {"error": str(e)}
    else:
        payload["ga4"] = {"skipped": "GA4_PROPERTY_ID 未配置"}
        print("[WARN] GA4_PROPERTY_ID 未配置，跳过 GA4")

    if errors:
        payload["errors"] = errors

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today_path = OUT_DIR / f"seo-data-{date.today().isoformat()}.json"
    for p in (today_path, OUT_DIR / "latest.json"):
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 写入 {p}")

    # 数据源全挂则让 Actions 失败，避免静默空跑
    if errors and not payload.get("gsc", {}).get("queries") and not payload.get("ga4", {}).get("daily"):
        fail("所有数据源均失败：" + " | ".join(errors))


if __name__ == "__main__":
    main()
