#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在 GitHub Actions 里运行：把最新数据压成一份给助理读取的小摘要。
- 输出 data/summary.json（目标 < 4KB）
- 输出 reports/YYYY-MM-DD_auto_report.md（人类可读完整报告）
不依赖任何第三方库。
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BRAND = re.compile(r"liangcai|梁才", re.I)
CTR_MIN_IMPRESSIONS = 100
CTR_MAX_POSITION = 15
CTR_TARGET = 2.0


def short(u):
    return u.replace("https://www.liangcaieyewear.com", "").replace("http://www.liangcaieyewear.com", "") or "/"


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE / "data" / "latest.json"
    if not src.exists():
        print("[ERROR] data/latest.json 不存在")
        return 1
    d = json.loads(src.read_text(encoding="utf-8"))
    gsc = d.get("gsc", {}) or {}
    ga4 = d.get("ga4", {}) or {}
    known = {"generated_at": d.get("generated_at"), "range_days": d.get("range_days")}

    # ---------- GSC ----------
    rows = gsc.get("queries", []) or gsc.get("queries_only", []) or []
    pages_only = gsc.get("pages_only", []) or []
    total_c = gsc.get("totals", {}).get("clicks", 0)
    total_i = gsc.get("totals", {}).get("impressions", 0)
    visible_i = sum(r.get("impressions", 0) for r in rows)
    brand_i = sum(r.get("impressions", 0) for r in rows if BRAND.search(r["keys"][0]))
    page_rows = [{
        "p": short(p["keys"][0]), "i": p.get("impressions", 0),
        "c": p.get("clicks", 0), "pos": p.get("position", 0),
        "ctr": p.get("ctr", 0),
    } for p in pages_only]
    page_rows.sort(key=lambda x: -x["i"])
    zero_pages = [p["p"] for p in page_rows if p["i"] == 0]
    title_cand = [p for p in page_rows
                  if p["i"] >= CTR_MIN_IMPRESSIONS and p["pos"] <= CTR_MAX_POSITION and p["ctr"] < CTR_TARGET]

    gsc_sum = {
        "clicks": total_c, "impressions": total_i,
        "ctr": gsc.get("totals", {}).get("ctr", 0),
        "pages_with_data": len(page_rows),
        "page_impressions_sum": sum(p["i"] for p in page_rows),
        "impression_mismatch": (abs(sum(p["i"] for p in page_rows) - total_i) / total_i > 0.1) if total_i else False,
        "visible_impressions": visible_i,
        "hidden_impressions": max(total_i - visible_i, 0),
        "brand_impressions": brand_i,
        "brand_share_of_visible": round(brand_i / visible_i * 100, 1) if visible_i else 0,
        "top_pages": page_rows[:12],
        "top_queries": [
            {"q": r["keys"][0], "i": r.get("impressions", 0), "c": r.get("clicks", 0), "pos": r.get("position", 0)}
            for r in sorted(gsc.get("queries_only", []) or [], key=lambda x: -x.get("impressions", 0))[:12]
        ],
        "pages_no_impressions": zero_pages,
        "title_candidates": title_cand,
        "countries": [{"c": x["keys"][0], "i": x.get("impressions", 0), "cl": x.get("clicks", 0)}
                      for x in sorted(gsc.get("countries", []) or [], key=lambda x: -x.get("impressions", 0))[:8]],
        "devices": [{"d": x["keys"][0], "i": x.get("impressions", 0), "cl": x.get("clicks", 0)}
                    for x in (gsc.get("devices", []) or [])],
    }

    # ---------- GA4 ----------
    ga4_sum = {}
    if ga4.get("error"):
        ga4_sum = {"error": ga4["error"]}
    elif ga4.get("skipped"):
        ga4_sum = {"skipped": ga4["skipped"]}
    else:
        daily = ga4.get("daily", []) or []
        ch = ga4.get("by_channel", []) or []
        tp = ga4.get("top_pages", []) or []
        sessions = sum(r["metrics"].get("sessions", 0) for r in daily)
        views = sum(r["metrics"].get("screenPageViews", 0) for r in daily)
        conv = sum(r["metrics"].get("conversions", 0) for r in daily)
        half = len(daily) // 2
        avg1 = round(sum(r["metrics"].get("sessions", 0) for r in daily[:half]) / max(half, 1), 1)
        avg2 = round(sum(r["metrics"].get("sessions", 0) for r in daily[half:]) / max(len(daily) - half, 1), 1)
        ga4_sum = {
            "days_covered": len(daily),
            "first_day": daily[0]["dimensions"][0] if daily else None,
            "last_day": daily[-1]["dimensions"][0] if daily else None,
            "sessions": sessions,
            "pageviews": views,
            "conversions": conv,
            "avg_sessions_1st_half": avg1,
            "avg_sessions_2nd_half": avg2,
            "last7_sessions": round(sum(r["metrics"].get("sessions", 0) for r in daily[-7:]), 0),
            "channels": [{"name": c["dimensions"][0], "sessions": c["metrics"].get("sessions", 0),
                          "users": c["metrics"].get("totalUsers", 0),
                          "conv": c["metrics"].get("conversions", 0),
                          "eng": round(c["metrics"].get("engagementRate", 0) * 100, 1),
                          "dur": round(c["metrics"].get("averageSessionDuration", 0), 0)}
                         for c in ch],
            "top_pages": [{"p": p["dimensions"][0], "s": p["metrics"].get("sessions", 0),
                           "v": p["metrics"].get("screenPageViews", 0),
                           "conv": p["metrics"].get("conversions", 0),
                           "eng": round(p["metrics"].get("engagementRate", 0) * 100, 1)}
                          for p in tp[:15]],
            "conversion_pages": [{"p": p["dimensions"][0], "conv": p["metrics"].get("conversions", 0)}
                                 for p in tp if p["metrics"].get("conversions", 0) > 0],
            "zero_engagement_days": [r["dimensions"][0] for r in daily if r["metrics"].get("engagementRate", 0) < 0.15],
        }

    summary = dict(known)
    summary["gsc"] = gsc_sum
    if gsc_sum.get("impression_mismatch"):
        summary["warnings"] = [
            "页面级曝光合计 %s 与站点总量 %s 对不上，需检查取数口径（是否混入 video/image 搜索类型）"
            % (gsc_sum["page_impressions_sum"], gsc_sum["impressions"])
        ]
    summary["ga4"] = ga4_sum
    summary["built_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    (BASE / "data").mkdir(exist_ok=True)
    (BASE / "data" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---------- Markdown ----------
    L = []
    a = L.append
    a("# SEO 数据日报（CI 自动生成）")
    a("")
    a(f"- 数据时间：{d.get('generated_at')}（窗口 {d.get('range_days')} 天）")
    a(f"- GSC：{gsc.get('start_date')} ~ {gsc.get('end_date')}")
    if ga4_sum.get("days_covered"):
        a(f"- GA4：{ga4_sum['first_day']} ~ {ga4_sum['last_day']}（{ga4_sum['days_covered']} 天有数据）")
    a("")
    a("## GSC")
    a("")
    a(f"- 点击 {total_c} / 曝光 {total_i} / CTR {gsc.get('totals', {}).get('ctr', 0)}%")
    a(f"- 有曝光页面 {len(page_rows)}；可归因曝光 {visible_i}；被隐藏 {max(total_i - visible_i, 0)}")
    a(f"- 品牌词占可归因曝光 {gsc_sum['brand_share_of_visible']}%")
    a("")
    a("| 页面 | 曝光 | 点击 | 排名 |")
    a("|---|---|---|---|")
    for p in page_rows[:15]:
        a(f"| {p['p']} | {p['i']} | {p['c']} | {p['pos']} |")
    a("")
    a("## GA4")
    a("")
    if ga4_sum.get("error"):
        a(f"- 未取到：{ga4_sum['error']}")
    elif ga4_sum.get("skipped"):
        a(f"- 跳过：{ga4_sum['skipped']}")
    else:
        a(f"- 会话 {ga4_sum['sessions']} / 浏览 {ga4_sum['pageviews']} / 转化 {ga4_sum['conversions']}")
        a(f"- 前半段日均 {ga4_sum['avg_sessions_1st_half']} 会话，后半段日均 {ga4_sum['avg_sessions_2nd_half']}")
        a("")
        a("| 渠道 | 会话 | 用户 | 转化 | 参与率 |")
        a("|---|---|---|---|---|")
        for c in ga4_sum["channels"]:
            a(f"| {c['name']} | {round(c['sessions'])} | {round(c['users'])} | {round(c['conv'])} | {c['eng']}% |")
        a("")
        a("| 落地页 | 会话 | 浏览 | 转化 |")
        a("|---|---|---|---|")
        for p in ga4_sum["top_pages"]:
            a(f"| {p['p']} | {round(p['s'])} | {round(p['v'])} | {round(p['conv'])} |")
    a("")
    (BASE / "reports").mkdir(exist_ok=True)
    out = BASE / "reports" / f"{datetime.now().strftime('%Y-%m-%d')}_auto_report.md"
    out.write_text("\n".join(L), encoding="utf-8")

    print(f"[OK] summary.json {len(json.dumps(summary, ensure_ascii=False).encode('utf-8'))} bytes")
    print(f"[OK] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
