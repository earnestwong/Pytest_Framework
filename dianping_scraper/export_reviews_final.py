#!/usr/bin/env python3
"""Export all reviews with original format + recommends only, with DB sync."""
import json, csv, os, re
import hashlib
from datetime import datetime, timedelta, date

try:
    import pymysql
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "pymysql"], check=True)
    import pymysql

BASE = r"C:\Users\Arlene\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6a7edcee69fcf2eae1cb1259"

# 数据库配置
DB_CONFIG = {
    "host": "192.168.6.71",
    "port": 3307,
    "user": "root",
    "password": "HHg@2026",
    "database": "dp_data",
    "charset": "utf8mb4",
}


def _clean_content_for_hash(content: str) -> str:
    """清洗评价内容用于hash计算:
    1. 移除以"推荐："或"推荐:"开头的行
    2. 移除非数字、字母、中文字符
    """
    if not content:
        return ""
    cleaned = re.sub(r'(^|[\r\n])\s*推荐[：:][^\r\n]*', '', content, flags=re.IGNORECASE)
    cleaned = re.sub(r'[^0-9a-zA-Z\u4e00-\u9fff]', '', cleaned)
    return cleaned


def calc_hash(org_code: str, username: str, review_date: str, content: str) -> str:
    """计算记录唯一标识hash，与MySQL公式一致"""
    cleaned_content = _clean_content_for_hash(content)
    s = "{}|{}|{}|{}".format(
        (org_code or "").strip(),
        (username or "").strip(),
        (review_date or "").strip(),
        cleaned_content,
    )
    return hashlib.md5(s.encode("utf-8")).hexdigest()

STORES = [
    {"file": "dianping_capture.jsonl", "org_code": "0801", "store_name": "光明村大酒家（淮海店）", "prefix": "qB4r137879a70eba8fe368a2"},
    {"file": "fengyu_capture.jsonl",   "org_code": "080501", "store_name": "丰裕（淮海店）", "prefix": ""},
    {"file": "0504_capture.jsonl",     "org_code": "0504",  "store_name": "沧浪亭（重庆店）", "prefix": ""},
    {"file": "0513_capture.jsonl",     "org_code": "0513",  "store_name": "老人和（打浦路店）", "prefix": ""},
    {"file": "990007_capture.jsonl",   "org_code": "990007", "store_name": "990007门店", "prefix": ""},
]

def parse_time(t):
    if not t:
        return ""
    t = str(t).strip()
    now = datetime.now()
    if "天前" in t:
        m = re.search(r"(\d+)天前", t)
        if m: return (now - timedelta(days=int(m.group(1)))).date().isoformat()
    if "小时前" in t or "分钟前" in t:
        return now.date().isoformat()
    if "昨天" in t: return (now - timedelta(days=1)).date().isoformat()
    if "前天" in t: return (now - timedelta(days=2)).date().isoformat()
    if "今天" in t: return now.date().isoformat()
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", t)
    if m: return f"{int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})月(\d{1,2})日", t)
    if m:
        y = now.year; mo = int(m.group(1)); d = int(m.group(2))
        if mo > now.month: y -= 1
        return f"{y}-{mo:02d}-{d:02d}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m: return f"{int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""

def classify_sentiment(rating):
    try:
        r = float(rating)
    except: return ""
    if r >= 4.0: return "positive"
    elif r >= 3.0: return "neutral"
    else: return "negative"

def extract_store_feedback(item):
    comments = item.get("comments") or []
    parts = []
    for c in comments:
        if not isinstance(c, dict): continue
        fu = c.get("fromUser") or {}
        if fu.get("userType") == 10:
            ctext = c.get("content", "") or ""
            uname = fu.get("userName", "") or ""
            if ctext: parts.append(f"[{uname}]{ctext}")
    return "\n".join(parts) if parts else ""

def extract_reviews(file_path, store):
    org_code = store["org_code"]
    store_name = store["store_name"]
    prefix = store["prefix"]
    reviews = {}
    
    with open(file_path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except: continue
            if e.get("type") != "response": continue
            url = e.get("url", "")
            if "outsidesiftedreviewlist" not in url and "outsideshopreviewlist" not in url:
                continue
            body = e.get("body", "")
            if not body: continue
            try:
                data = json.loads(body)
            except: continue
            if not isinstance(data, dict): continue
            
            result = data.get("result")
            result_reviews = []
            if isinstance(result, dict):
                result_reviews = result.get("reviewList", [])
            lst = data.get("list") or result_reviews or []
            
            for item in lst:
                if not isinstance(item, dict): continue
                mid = str(item.get("mainId", ""))
                if not mid or mid in reviews: continue
                
                sid = item.get("shopidencrypt", "")
                if prefix and sid and not sid.startswith(prefix): continue
                
                feed_user = item.get("feedUser") or {}
                username = feed_user.get("userName", "") or item.get("userName", "") or ""
                raw_rating = item.get("star") or item.get("score") or ""
                try:
                    rating = f"{float(raw_rating) / 10.0:.1f}"
                except: rating = ""
                time_str = item.get("time", "") or ""
                content = item.get("content", "") or item.get("reviewBody", "") or ""
                price_val = item.get("avgPrice") or item.get("price") or item.get("avgPriceText")
                price = None
                if price_val:
                    m = re.search(r"(\d+)", str(price_val))
                    price = int(m.group(1)) if m else ""
                
                store_feedback = extract_store_feedback(item)
                recommends = item.get("recommends") or []
                
                review_date = parse_time(time_str)
                # 计算hash值
                hash_value = calc_hash(org_code, username, review_date, content)
                
                reviews[mid] = {
                    "org_code": org_code, "store_name": store_name,
                    "username": username[:50],
                    "review_date": review_date,
                    "rating": rating,
                    "price_per_person": price if price is not None else "",
                    "content": content,
                    "sentiment": classify_sentiment(rating),
                    "store_feedback": store_feedback,
                    "recommends": json.dumps(recommends, ensure_ascii=False),
                    "hash_value": hash_value,
                }
    
    return list(reviews.values())

def sync_to_db(reviews: list) -> tuple:
    """同步评论到数据库:已存在的更新菜品字段,新记录插入"""
    if not reviews:
        return (0, 0, 0)

    try:
        conn = pymysql.connect(
            host=DB_CONFIG["host"],
            port=int(DB_CONFIG["port"]),
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            database=DB_CONFIG["database"],
            charset=DB_CONFIG["charset"],
            autocommit=True,
        )
    except Exception as e:
        print(f"  [DB] 连接失败: {e}")
        return (0, 0, 0)

    cur = conn.cursor()
    table = "store_reviews_negative"
    inserted = updated = skipped = 0

    try:
        # 按hash批量查询已有记录
        hashes = [r["hash_value"] for r in reviews]
        existing = {}
        for i in range(0, len(hashes), 500):
            chunk = hashes[i:i + 500]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"SELECT hash_value, recommends FROM {table} WHERE hash_value IN ({ph})",
                chunk,
            )
            for row in cur.fetchall():
                existing[row[0]] = {"recommends": row[1]}

        for r in reviews:
            h = r["hash_value"]
            new_rec = r.get("recommends", "[]")

            if h in existing:
                # 已存在:检查是否需要更新菜品字段
                old_rec = existing[h].get("recommends") or "[]"
                # 如果新记录有菜品且与旧记录不同,则更新
                if new_rec != "[]" and old_rec != new_rec:
                    cur.execute(
                        f"UPDATE {table} SET recommends=%s WHERE hash_value=%s",
                        [new_rec, h],
                    )
                    updated += 1
                else:
                    skipped += 1
            else:
                # 新记录:插入
                cur.execute(
                    f"""INSERT INTO {table}
                        (org_code, store_name, username, review_date, rating,
                         price_per_person, content, sentiment, store_feedback,
                         recommends, hash_value)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        r["org_code"], r["store_name"], r["username"],
                        r["review_date"], r["rating"],
                        r["price_per_person"], r["content"], r["sentiment"],
                        r["store_feedback"], new_rec, h,
                    ],
                )
                inserted += 1

        print(f"  [DB] 同步: 新增 {inserted} / 更新 {updated} / 跳过 {skipped}")
        return (inserted, updated, skipped)

    except Exception as e:
        print(f"  [DB] 同步失败: {e}")
        return (inserted, updated, skipped)
    finally:
        cur.close()
        conn.close()


def main():
    FIELDS = [
        "org_code", "store_name", "username", "review_date",
        "rating", "price_per_person", "content", "sentiment",
        "store_feedback", "recommends", "hash_value",
    ]

    output_file = os.path.join(BASE, "all_stores_reviews_with_recommends.csv")
    all_reviews = []

    print("Processing all stores...")
    for store in STORES:
        file_path = os.path.join(BASE, store["file"])
        if not os.path.exists(file_path):
            print(f"  {store['store_name']}: file not found, skipping")
            continue
        reviews = extract_reviews(file_path, store)
        print(f"  {store['store_name']}: {len(reviews)} reviews")
        all_reviews.extend(reviews)

    print(f"\nTotal: {len(all_reviews)} reviews")

    # 写入CSV
    with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_reviews)

    size = os.path.getsize(output_file)
    print(f"Output: {output_file} ({size:,} bytes)")

    has_rec = sum(1 for r in all_reviews if r["recommends"] != "[]")
    print(f"\nWith recommends: {has_rec}/{len(all_reviews)} ({has_rec/len(all_reviews)*100:.1f}%)")

    # 同步到数据库
    print("\nSyncing to database...")
    sync_to_db(all_reviews)


if __name__ == "__main__":
    main()