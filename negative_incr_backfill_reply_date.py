# -*- coding: utf-8 -*-
"""
杭州差评【回复日期定点补采回填】脚本
================================================
背景: 批量补采(negative_incr_all_60day)偶发"有商家回复内容但 store_feedback_date 为空"
      的记录。根因是详情页取回复日期时, 直点回复节点进详情页会定位到回复区、
      评分文本("很糟糕/较差")被误当作用户名引发身份误拒, 或懒加载未触发日期。
      utils/adb_helper.extract_detail_user 已修复(纳入 _DETAIL_SCORE_TEXTS);
      CSS 已修复, 本脚本针对已采集入库的存量缺失记录逐条回填。

对每条目标(按 org_code+username+hash):
  1. 导航到该店差评列表顶部
  2. 滚动定位目标评论卡片(username+review_date 精确匹配)
  3. 点卡片内"商家回复"节点进入详情页, 耐心下滑/点"点击重试"触发懒加载,
     用 extract_merchant_reply_date 取回复日期
  4. 回写:  UPDATE store_reviews_negative.store_feedback_date (按 hash)
           更新当日 CSV(按 hash 列)
验证码: 遇验证码暂停 60s 等人工处理(与批量脚本策略一致), 再重试。

用法:
    python negative_incr_backfill_reply_date.py          # 回填所有存量缺失
    python negative_incr_backfill_reply_date.py <org>    # 仅回填指定 org
"""
import sys
import os
import re
import time
import glob
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav_search_negative as nav
from utils.review_parser import ReviewParser
from utils.csv_exporter import CSVExporter
from _test_nav_all import SHOPS  # (org_code, store_name, kw)

import pymysql

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dianping_config.json")
DAY = "20260908"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "dianping")

BOUNDARY_MAX_SCREENS = 30   # 搜索目标卡片最多下滚屏数(目标均在最近60天,足够)
BACK_MAX_SCREENS = 8        # 边界越界后最多向上回滚的屏数(补回向下滚时跳过的目标卡)
DATE_WAIT = 1.2
WAIT = 2.5


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json_load(f)


def json_load(f):
    import json
    return json.load(f)


def _norm_date(s):
    return CSVExporter._normalize_date((s or "").strip())


# ---------------- DB ----------------

def db_connect(cfg):
    db = cfg.get("db", {})
    conn = pymysql.connect(host=db.get("host"), port=int(db.get("port", 3306)),
                           user=db.get("user"), password=db.get("password", ""),
                           database=db.get("database"), charset="utf8mb4",
                           connect_timeout=5, autocommit=True)
    return conn, db.get("table", "store_reviews_negative")


def load_targets(cfg, only_org=None):
    """目标源: 今日(DAY)导出的 CSV 中 store_feedback 非空但 store_feedback_date 为空的记录。
    即只处理当天批量这才产生的缺失(实测为 7 条), 不回填历史欠账。
    返回 [{'org_code','store_name','username','review_date','store_feedback','hash_value'}]"""
    targets = []
    seen = set()
    for fp in _shop_csv_files():
        with open(fp, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            if not (r.get("store_feedback") or "").strip():
                continue
            if (r.get("store_feedback_date") or "").strip():
                continue
            h = (r.get("hash_value") or "").strip()
            if not h or h in seen:
                continue
            org = (r.get("org_code") or "").strip()
            if only_org and org != only_org:
                continue
            seen.add(h)
            targets.append({
                "org_code": org,
                "store_name": (r.get("store_name") or "").strip(),
                "username": (r.get("username") or "").strip(),
                "review_date": (r.get("review_date") or "").strip(),
                "store_feedback": r.get("store_feedback") or "",
                "hash_value": h,
            })
    # 按 org_code 分组顺序稳定(按首现顺序)
    return targets


def update_db_date(cfg, table, hash_value, date_str):
    conn = pymysql.connect(host=cfg["db"]["host"], port=int(cfg["db"]["port"]),
                           user=cfg["db"]["user"], password=cfg["db"]["password"],
                           database=cfg["db"]["database"], charset="utf8mb4",
                           connect_timeout=5, autocommit=True)
    cur = conn.cursor()
    cur.execute(f"UPDATE {table} SET store_feedback_date=%s WHERE hash_value=%s",
                (date_str, hash_value))
    n = cur.rowcount
    conn.close()
    return n


# ---------------- CSV ----------------

def _shop_csv_files():
    return sorted(glob.glob(os.path.join(OUT_DIR, f"*_reviews_{DAY}_*.csv")))


def patch_csv_reply_date(hash_value, date_str):
    """在所有当日 CSV 中按 hash_value 更新 store_feedback_date 列"""
    headers = None
    for fp in _shop_csv_files():
        with open(fp, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        headers = list(rows[0].keys()) if rows else None
        changed = False
        for r in rows:
            if r.get("hash_value") == hash_value:
                r["store_feedback_date"] = date_str
                changed = True
        if changed:
            with open(fp, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=headers)
                w.writeheader()
                w.writerows(rows)
            print(f"    [CSV] 已更新 {os.path.basename(fp)} hash={hash_value}")
    return headers


# ---------------- 定位与取日期 ----------------

def _captcha_pause(adb):
    try:
        xml = adb.dump_ui(retries=1)
    except RuntimeError:
        return False
    if "请依次点击" in xml or "依次点击下图" in xml or "身份核实" in xml:
        print("  !!! 检测到验证码, 请在设备上人工处理; 60 秒后重试", flush=True)
        time.sleep(60)
        return True
    return False


def fetch_cards(adb, parser):
    xml = adb.dump_ui()
    items = adb.extract_all_text(xml, min_len=1)
    cards = parser.parse(items)
    return xml, cards


def find_card(cards, username, review_date):
    """在解析出的卡片中精确匹配 user + review_date"""
    rv = _norm_date(review_date)
    for c in cards:
        u = (c.get("user") or "").strip()
        d = _norm_date(c.get("date"))
        if u == username and d and d == rv:
            return c
    return None


def _date_diff_days(d, target):
    """规范化后的日期差(天), 任一无效则返回很大的数"""
    try:
        yd, md, dd = map(int, d.split("-"))
        yt, mt, dt = map(int, target.split("-"))
        import datetime
        return abs((datetime.date(yd, md, dd) - datetime.date(yt, mt, dt)).days)
    except Exception:
        return 10 ** 9


def _locate_target(cards, username, review_date):
    """定位目标卡。先 user+date 精确; 若失败:
    - 非匿名(用户名店内唯一): 用户名匹配 + 日期在 ±2 天内
    - 匿名: 仅当该屏 ±2 天内恰有唯一一条带商家回复的匿名卡时命中(否则有歧义, 放行)
    """
    c = find_card(cards, username, review_date)
    if c is not None:
        return c
    rv = _norm_date(review_date)
    if not rv or not re.match(r"^\d{4}-\d{2}-\d{2}$", rv):
        return None
    if username and username != "匿名用户":
        for cc in cards:
            if (cc.get("user") or "").strip() == username:
                d = _norm_date(cc.get("date"))
                if d and _date_diff_days(d, rv) <= 2:
                    return cc
    elif username == "匿名用户":
        # 匿名卡: 用户字段可能为空串或"匿名用户"; 限 ±2 天 + 必须带回复 + 窗口内唯一
        cands = []
        for cc in cards:
            u = (cc.get("user") or "").strip()
            if u in ("", "匿名用户"):
                d = _norm_date(cc.get("date"))
                if d and _date_diff_days(d, rv) <= 2 and (cc.get("merchant_reply") or "").strip():
                    cands.append(cc)
        if len(cands) == 1:
            return cands[0]
    return None


def _reply_center(c, xml):
    """取卡片商家回复节点中心坐标;失败返回 None"""
    mrb = c.get("merchant_reply_bounds")
    if mrb:
        bx1, by1, bx2, by2 = mrb
        if bx2 > bx1 and by2 > by1:
            return (bx1 + bx2) // 2, (by1 + by2) // 2
    return None


def extract_reply_date_by_tap(adb, w, h, c, xml):
    """点目标卡片的商家回复节点进详情页, 耐心取回复日期。返回 (日期原始文本, 成功进过详情页)"""
    center = _reply_center(c, xml)
    if center is None:
        print("    [回复] 卡片无回复坐标, 跳过")
        return "", False
    adb.tap(*center, human=False)
    time.sleep(WAIT)
    detail_xml = adb.dump_ui()
    if not adb.detect_review_detail_page(detail_xml):
        print("    [回复] 点击未进入详情页, 尝试恢复列表")
        return "", False
    rd = adb.extract_merchant_reply_date(detail_xml)
    # 耐心触发懒加载: 点"点击重试" + 多次下滑
    for i in range(6):
        if rd:
            break
        retry = [b for b in adb.find_elements_by_text(detail_xml, "点击重试")]
        if retry:
            adb.tap(*((retry[0]["bounds"][0] + retry[0]["bounds"][2]) // 2,
                      (retry[0]["bounds"][1] + retry[0]["bounds"][3]) // 2), human=False)
            time.sleep(2.5)
            detail_xml = adb.dump_ui()
            rd = adb.extract_merchant_reply_date(detail_xml)
            continue
        adb.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.3), duration_ms=700, human=False)
        time.sleep(1.0)
        detail_xml = adb.dump_ui()
        rd = adb.extract_merchant_reply_date(detail_xml)
        if rd:
            break
    # 回列表
    adb.back()
    time.sleep(3.0)
    return rd, True


def _try_screen(adb, w, h, parser, cards, xml, username, review_date):
    """在当前屏 cards 中定位目标卡并取回复日期。
    返回 (原始日期, 状态) 或 None(本屏未命中目标)。状态: ok / no_reply / no_date"""
    c = _locate_target(cards, username, review_date)
    if c is None:
        return None
    if not (c.get("merchant_reply") or "").strip():
        # 对齐主程序 3c2"商家回复缺失补救": 长文/多图把商家回复挤到屏底下方,
        # 当前 dump 只看到卡片头部、没渲染出回复。此时在"列表页小幅下滑"把回复
        # 露进屏幕再重新解析, 而不是直接判 no_reply(否则长评论卡的回复被漏判)。
        # 只对目标卡做, 不进详情页, 也不影响其它评论。
        revealed = False
        c2 = c
        for _step in range(6):  # 每步下滑约一屏高的1/6, 足够露出卡内回复
            print(f"    [露出] 卡片回复未渲染, 小幅下滑露出回复({_step + 1}/6)", flush=True)
            adb.swipe(w // 2, int(h * 0.78), w // 2, int(h * 0.62), duration_ms=600, human=False)
            time.sleep(1.1)
            try:
                xml, cards = fetch_cards(adb, parser)
            except RuntimeError:
                break
            # 下滑后卡片头部可能已滚出, 用 lenient 定位器(匿名保持精确 date)
            c2 = _locate_target(cards, username, review_date)
            if c2 is None:
                break
            if (c2.get("merchant_reply") or "").strip():
                revealed = True
                break
        if not revealed:
            return "", "no_reply"
        c = c2
    rd, _entered = extract_reply_date_by_tap(adb, w, h, c, xml)
    if rd:
        return rd, "ok"
    return "", "no_date"


def locate_and_fetch(adb, w, h, parser, username, review_date):
    """从差评列表当前顶部开始下滚, 定位目标卡片并取回复日期。
    返回 (原始日期, 状态)。状态: ok / not_found / no_reply / no_date"""
    # 列表按时间倒序(顶部最新→向下越老)。目标在最近60天内, 从顶部向下滚即可。
    target_date = _norm_date(review_date)
    # 边界退出: 一旦本屏可见评论的“最新日期”已早于目标日期, 说明目标已被翻过
    # (上一屏已包含它), 继续下翻只会越过越老(曾一路滚到2025年)。
    # 此时本屏没有目标 → 停止下翻, 向上回滚几屏找(向下滚可能因屏幅错位跳过目标卡)。
    for screen in range(BOUNDARY_MAX_SCREENS):
        if _captcha_pause(adb):
            continue
        try:
            xml, cards = fetch_cards(adb, parser)
        except RuntimeError:
            time.sleep(0.5)
            continue
        if not cards:
            continue  # 本屏解析为空(动画/顶部区), 不据此判定边界, 继续下滚
        r = _try_screen(adb, w, h, parser, cards, xml, username, review_date)
        if r is not None:
            return r
        # 边界判定: 本屏可见卡片的最新日期
        newest = max(
            (_norm_date(cc.get("date")) for cc in cards
             if re.match(r"^\d{4}-\d{2}-\d{2}$", _norm_date(cc.get("date")))),
            default=None,
        )
        if newest and newest < target_date:
            # 目标已被翻过但向下滚时因屏幅错位跳过了它 → 向上回滚几屏再找。
            print(f"    [边界] 目标 {target_date} 已被翻过(本屏最新可见 {newest}), 向上回滚再找", flush=True)
            for _back in range(BACK_MAX_SCREENS):
                # 反向手势(手指向下)让内容回滚到更「新」的一侧
                adb.swipe(w // 2, int(h * 0.40), w // 2, int(h * 0.72), duration_ms=900, human=False)
                time.sleep(1.0)
                if _captcha_pause(adb):
                    continue
                try:
                    xml, cards = fetch_cards(adb, parser)
                except RuntimeError:
                    continue
                if not cards:
                    continue
                r = _try_screen(adb, w, h, parser, cards, xml, username, review_date)
                if r is not None:
                    return r
                # 回滚到本屏最新日期等于/晚于目标, 说明已回到目标所在附近, 再滚可能越过
                newest2 = max(
                    (_norm_date(cc.get("date")) for cc in cards
                     if re.match(r"^\d{4}-\d{2}-\d{2}$", _norm_date(cc.get("date")))),
                    default=None,
                )
                if newest2 and newest2 >= target_date:
                    # 已回到含目标日期的区间, 但未见 → 再滚上去也未必命中, 放弃
                    break
            return "", "passed"
        adb.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.40), duration_ms=900, human=False)
        time.sleep(1.0)
    return "", "not_found"


def load_targets_by_hashes(cfg, hashes):
    """按 hash 清单从 DB 拉取目标记录(用于回填今日 CSV 之外的历史遗留)。
    返回与 load_targets 相同的结构。"""
    if not hashes:
        return []
    db = cfg.get("db", {})
    conn = pymysql.connect(host=db.get("host"), port=int(db.get("port", 3306)),
                           user=db.get("user"), password=db.get("password", ""),
                           database=db.get("database"), charset="utf8mb4",
                           connect_timeout=5, autocommit=True)
    tbl = db.get("table", "store_reviews_negative")
    cur = conn.cursor()
    ph = ",".join(["%s"] * len(hashes))
    cur.execute(
        f"SELECT org_code, store_name, username, review_date, store_feedback, hash_value "
        f"FROM {tbl} WHERE hash_value IN ({ph})", list(hashes))
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        if hasattr(d["review_date"], "strftime"):
            d["review_date"] = d["review_date"].strftime("%Y-%m-%d")
        else:
            d["review_date"] = str(d.get("review_date") or "")[:10]
        out.append(d)
    return out


def main():
    import json
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    # 参数: [only_org] 或 [--hashes h1,h2,...]
    only_org = hashes = None
    if len(sys.argv) > 1:
        if sys.argv[1] == "--hashes":
            hashes = [x.strip() for x in sys.argv[2].split(",") if x.strip()]
        else:
            only_org = sys.argv[1]

    nav.init()
    adb, w, h = nav.adb, nav.w, nav.h
    parser = ReviewParser()

    try:
        if hashes:
            targets = load_targets_by_hashes(cfg, hashes)
        else:
            targets = load_targets(cfg, only_org)
    except Exception as e:
        print(f"[DB] 查询目标失败: {e}")
        return
    if not targets:
        print("无缺失回复日期记录, 结束")
        return

    # org -> kw
    org_kw = {o: kw for o, _, kw in SHOPS}
    # 按 org 分组
    by_org = {}
    for t in targets:
        by_org.setdefault(t["org_code"], []).append(t)

    print(f"待回填店数 {len(by_org)} 家, 记录 {len(targets)} 条")
    total_ok = total_fail = 0
    for org, ts in by_org.items():
        kw = org_kw.get(org)
        if not kw:
            print(f"[{org}] 无店铺关键词, 跳过其 {len(ts)} 条")
            continue
        print(f"\n##### [{org}] {ts[0].get('store_name')} kw={kw}: {len(ts)} 条 #####", flush=True)
        # 导航到差评列表顶部(允许验证码暂停重试)
        ok = False
        for attempt in range(3):
            try:
                r = nav.navigate_to_negative_list(kw, do_init=False, verbose=False)
            except Exception as e:
                r = {"ok": False, "err": f"导航异常 {e!r}"}
            if r.get("ok"):
                ok = True
                break
            if "验证码" in r.get("err", ""):
                print("  [nav] 验证码, 人工处理后重试", flush=True)
                time.sleep(60)
                continue
            print(f"  [nav] 导航失败: {r.get('err')}", flush=True)
            break
        if not ok:
            print(f"  [{org}] 导航失败, 跳过 {len(ts)} 条")
            total_fail += len(ts)
            continue

        for t in ts:
            username = t["username"] or ""
            review_date = (t["review_date"] or "")
            if hasattr(review_date, "strftime"):
                review_date = review_date.strftime("%Y-%m-%d")
            else:
                review_date = str(review_date)[:10]
            print(f"  -> [{username}] {review_date}", flush=True)
            rd_raw, status = locate_and_fetch(adb, w, h, parser, username, review_date)
            if status == "ok":
                date_str = _norm_date(rd_raw)
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                    print(f"     [日期异常] {rd_raw!r} -> {date_str!r}, 未回写")
                    total_fail += 1
                    continue
                # DB 回写
                try:
                    n = update_db_date(cfg, cfg["db"]["table"], t["hash_value"], date_str)
                except Exception as e:
                    print(f"     [DB] 更新失败: {e}")
                    n = 0
                # CSV 回写
                try:
                    patch_csv_reply_date(t["hash_value"], date_str)
                except Exception as e:
                    print(f"     [CSV] 更新失败: {e}")
                print(f"     [OK] 回复日期 = {date_str} (db更新{n})", flush=True)
                total_ok += 1
            else:
                reason = {"no_reply": "该卡片当前屏幕无商家回复",
                          "no_date": "进详情页后未取到回复日期",
                          "passed": "已翻过目标日期仍未见(需回滚向上找)",
                          "not_found": "滚动未定位到该评论(可能判定边界/已过期)"}[status]
                print(f"     [FAIL] {reason}", flush=True)
                total_fail += 1

    print(f"\n========== 回填完成 ==========")
    print(f"成功 {total_ok} / 失败 {total_fail}")


if __name__ == "__main__":
    main()