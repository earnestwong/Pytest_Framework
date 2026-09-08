# -*- coding: utf-8 -*-
"""生成《批量补采60天差评》Excel 报告。
读取 reports/dianping 下当日 CSV + _negative_incr_all_results.csv, 输出:
  Sheet1  总览(元信息+关键指标)
  Sheet2  各店采集量
  Sheet3  差评明细(含回复及回复日期)
"""
import os
import glob
import csv
import sys
from datetime import datetime

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BASE = r"d:\TraeProject\Pytest_Framework"
REPORT_DIR = os.path.join(BASE, "reports", "dianping")
RESULTS_CSV = os.path.join(BASE, "_negative_incr_all_results.csv")
DAY = "20260908"
OUT_XLSX = os.path.join(REPORT_DIR, f"批量补采60天差评报告_{DAY}.xlsx")

TH = Font(bold=True, color="FFFFFF")
HEADFILL = PatternFill("solid", fgColor="2F5496")
SUBFILL = PatternFill("solid", fgColor="D9E2F3")
THIN = Border(*[Side(style="thin", color="BFBFBF")]*4)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def style_sheet(ws, widths, last_row):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for cell in ws[1]:
        cell.font, cell.alignment = TH, CENTER
        cell.fill = HEADFILL
    for row in ws.iter_rows(min_row=1, max_row=last_row):
        for c in row:
            c.border = THIN


def load_results():
    rows = []
    with open(RESULTS_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def load_details():
    """合并当日所有店 CSV 明细。列: org_code,store_name,username,review_date,
    rating,price_per_person,content,sentiment,store_feedback,store_feedback_date,
    hash_value"""
    details = []
    for fp in sorted(glob.glob(os.path.join(REPORT_DIR, f"*_reviews_{DAY}_*.csv"))):
        with open(fp, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r["_file"] = os.path.basename(fp)
                details.append(r)
    return details


def main():
    results = load_results()
    details = load_details()
    by_store = {}
    for r in results:
        by_store[r["org_code"]] = r["store_name"]
    # 各店数量
    store_count = {}
    for d in details:
        o = d["org_code"]
        store_count[o] = store_count.get(o, 0) + 1
    total = len(details)
    with_reply = sum(1 for d in details if d.get("store_feedback"))
    with_date = sum(1 for d in details if d.get("store_feedback_date"))
    ok = sum(1 for r in results if r.get("status") == "DONE")
    fail = len(results) - ok

    wb = openpyxl.Workbook()

    # Sheet1 总览
    ws = wb.active
    ws.title = "总览"
    meta = [
        ("报告名称", "店铺自动导航+增量差评 批量补采60天报告"),
        ("执行日期", "2026-09-08"),
        ("调度脚本", "negative_incr_all_60day.py"),
        ("采集窗口", "最近 60 天(停止边界 review_date < 2026-07-10)"),
        ("差评去重口径", "按 hash_value(MD5) 去重"),
        ("数据入库", "store_reviews_negative 表"),
        ("", ""),
        ("目标店铺数", len(results)),
        ("采集成功", ok),
        ("采集失败", fail),
        ("差评总数(去重)", total),
        ("含商家回复", with_reply),
        ("含回复日期", with_date),
    ]
    ws.append(["字段", "值"])
    for k, v in meta:
        ws.append([k, v])
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 1).fill = SUBFILL
        ws.cell(r, 1).alignment = CENTER
    style_sheet(ws, [22, 60], 2)

    # Sheet2 各店采集量
    ws2 = wb.create_sheet("各店采集量")
    ws2.append(["序号", "org_code", "店铺名", "差评数", "含商家回复", "含回复日期"])
    order = [r["org_code"] for r in results]
    cnt_reply = {}
    cnt_date = {}
    for d in details:
        o = d["org_code"]
        if d.get("store_feedback"):
            cnt_reply[o] = cnt_reply.get(o, 0) + 1
        if d.get("store_feedback_date"):
            cnt_date[o] = cnt_date.get(o, 0) + 1
    for i, r in enumerate(results, 1):
        o = r["org_code"]
        ws2.append([i, o, r["store_name"], store_count.get(o, 0),
                    cnt_reply.get(o, 0), cnt_date.get(o, 0)])
    ws2.append(["", "", "合计", sum(store_count.values()),
                sum(cnt_reply.values()), sum(cnt_date.values())])
    for c in ws2[ws2.max_row]:
        c.font = TH
    style_sheet(ws2, [6, 10, 24, 10, 12, 12], ws2.max_row)

    # Sheet3 差评明细
    ws3 = wb.create_sheet("差评明细")
    ws3.append(["序号", "org_code", "店铺名", "用户名", "review_date", "评分",
                "人均", "sentiment", "评论内容", "商家回复", "回复日期", "hash_value"])
    for i, d in enumerate(details, 1):
        ws3.append([i, d.get("org_code"), d.get("store_name"), d.get("username"),
                    d.get("review_date"), d.get("rating"), d.get("price_per_person"),
                    d.get("sentiment"), d.get("content"), d.get("store_feedback"),
                    d.get("store_feedback_date"), d.get("hash_value")])
    style_sheet(ws3, [6, 9, 20, 14, 12, 8, 8, 10, 60, 60, 12, 4], ws3.max_row)
    ws3.freeze_panes = "A2"

    wb.save(OUT_XLSX)
    print("已生成:", OUT_XLSX)
    print(f"店铺 {len(results)} 家 / 明细 {len(details)} 条 / "
          f"含回复 {with_reply} / 含回复日期 {with_date}")


if __name__ == "__main__":
    main()