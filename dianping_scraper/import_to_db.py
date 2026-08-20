import json
import re
import sys
from datetime import datetime, date

try:
    import pymysql
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pymysql", "-q"])
    import pymysql

DB_CONFIG = {
    "host": "192.168.6.71",
    "port": 3307,
    "user": "systemid",
    "password": "HHg@2026",
    "database": "dp_data",
    "charset": "utf8mb4",
}

BASE = r'C:\Users\Arlene\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a7edcee69fcf2eae1cb1259'

STORES = [
    {"file": "dianping_capture.jsonl", "org_code": "0801", "store_name": "光明村大酒家（淮海店）"},
    {"file": "fengyu_capture.jsonl",   "org_code": "080501", "store_name": "丰裕（淮海店）"},
]

def parse_time(t):
    if not t:
        return None
    t = str(t).strip()
    now = datetime.now()
    if "天前" in t:
        m = re.search(r"(\d+)天前", t)
        if m:
            return (now - timedelta(days=int(m.group(1)))).date()
    if "小时前" in t:
        m = re.search(r"(\d+)小时前", t)
        if m:
            return (now - timedelta(hours=int(m.group(1)))).date()
    if "分钟前" in t:
        return now.date()
    if "昨天" in t:
        return (now - timedelta(days=1)).date()
    if "前天" in t:
        return (now - timedelta(days=2)).date()
    if "今天" in t:
        return now.date()
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})月(\d{1,2})日", t)
    if m:
        y = now.year
        mo = int(m.group(1))
        d = int(m.group(2))
        if mo > now.month:
            y -= 1
        return date(y, mo, d)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None

from datetime import timedelta

def classify_sentiment(rating):
    try:
        r = float(rating)
    except (ValueError, TypeError):
        return None
    if r >= 4.0:
        return "好评"
    elif r >= 3.0:
        return "中评"
    else:
        return "差评"

def extract_reviews(file_path, org_code, store_name):
    reviews = {}
    with open(file_path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") != "response":
                continue
            if "outsidesiftedreviewlist" not in e.get("url", ""):
                continue
            body = e.get("body", "")
            if not body:
                continue
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            lst = data.get("list", []) or []
            for item in lst:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("mainId", ""))
                if not mid or mid in reviews:
                    continue
                username = item.get("feedUser", {}).get("userName", "") or item.get("userName", "") or ""
                rating = item.get("star") or item.get("score") or ""
                try:
                    rating10 = float(rating) / 10.0
                    rating_val = f"{rating10:.1f}"
                except (TypeError, ValueError):
                    rating_val = None
                time_str = item.get("time", "") or ""
                content = item.get("content", "") or item.get("reviewBody", "") or ""
                price = item.get("avgPrice") or item.get("price") or item.get("avgPriceText")
                if price:
                    m = re.search(r"(\d+)", str(price))
                    price = int(m.group(1)) if m else None
                else:
                    price = None
                comments = item.get("comments", []) or []
                store_feedback_parts = []
                for c in comments:
                    if not isinstance(c, dict):
                        continue
                    fu = c.get("fromUser", {}) or {}
                    if fu.get("userType") == 10:
                        ctext = c.get("content", "") or ""
                        uname = fu.get("userName", "") or ""
                        if ctext:
                            store_feedback_parts.append(f"[{uname}]{ctext}")
                store_feedback = "\n".join(store_feedback_parts) if store_feedback_parts else None
                reviews[mid] = (
                    org_code,
                    store_name,
                    username[:50],
                    parse_time(time_str),
                    rating_val,
                    price,
                    content,
                    classify_sentiment(rating_val),
                    store_feedback,
                )
    return list(reviews.values())

def main():
    all_reviews = []
    for store in STORES:
        fp = f"{BASE}\\{store['file']}"
        print(f"解析 {store['store_name']} ({store['file']})...")
        reviews = extract_reviews(fp, store["org_code"], store["store_name"])
        print(f"  去重后: {len(reviews)} 条")
        all_reviews.extend(reviews)

    print(f"\n总计: {len(all_reviews)} 条评论待插入")

    print(f"\n连接数据库 {DB_CONFIG['host']}:{DB_CONFIG['port']}...")
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM store_reviews")
    before = cursor.fetchone()[0]
    print(f"当前表中已有: {before} 条")

    sql = """INSERT INTO store_reviews
             (org_code, store_name, username, review_date, rating, price_per_person, content, sentiment, store_feedback)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""

    batch = 100
    inserted = 0
    for i in range(0, len(all_reviews), batch):
        batch_data = all_reviews[i:i+batch]
        try:
            cursor.executemany(sql, batch_data)
            conn.commit()
            inserted += len(batch_data)
            print(f"  已插入 {inserted}/{len(all_reviews)}")
        except Exception as ex:
            print(f"  批次 {i//batch} 出错: {ex}")
            conn.rollback()

    cursor.execute("SELECT COUNT(*) FROM store_reviews")
    after = cursor.fetchone()[0]
    print(f"\n导入完成! 表中现有: {after} 条 (新增 {after - before} 条)")

    cursor.execute("SELECT org_code, store_name, COUNT(*) FROM store_reviews GROUP BY org_code, store_name")
    for row in cursor.fetchall():
        print(f"  {row[0]} {row[1]}: {row[2]} 条")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
