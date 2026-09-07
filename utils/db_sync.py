"""
MySQL 同步模块:把采集到的评价卡片同步写入 store_reviews_negative 表
去重逻辑(按 hash_value,公式见 CSVExporter.hash_value):
  - 表中无此 hash → INSERT 全字段
  - 表中有此 hash → 看新记录 merchant_reply(store_feedback):
      无值 → skip(不清空已有 feedback)
      有值 → 与已有行比 (store_feedback, store_feedback_date):完全相同 → skip;任一不同 → UPDATE
数据库不可用(如外网跑脚本) → 自动禁用同步,不抛异常,不影响 CSV/JSON 输出
"""
import re
from typing import Dict, List, Tuple

import pymysql

from utils.csv_exporter import CSVExporter

# YYYY-MM-DD 校验(Match 后直接作为 DATE 传给 MySQL)
_DATE_PAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_date(s: str):
    """YYYY-MM-DD 字符串直接传(MySQL DATE 接受),无法解析传 None"""
    s = (s or "").strip()
    return s if _DATE_PAT.match(s) else None


def _to_int_price(s: str):
    """人均价格字符串 → int,失败 → None"""
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


class ReviewDBSync:
    """store_reviews_negative 同步器(best-effort,任何错误不抛异常)"""

    def __init__(self, db_cfg: dict):
        self.cfg = db_cfg
        self.enabled = False
        self._conn = None

    def _connect(self) -> bool:
        """惰性建立连接;失败打印原因(外网场景),标记禁用"""
        if self._conn is not None:
            return True
        try:
            self._conn = pymysql.connect(
                host=self.cfg.get("host"),
                port=int(self.cfg.get("port", 3306)),
                user=self.cfg.get("user"),
                password=self.cfg.get("password", ""),
                database=self.cfg.get("database"),
                charset="utf8mb4",
                connect_timeout=int(self.cfg.get("connect_timeout", 5)),
                autocommit=True,
            )
            self.enabled = True
            return True
        except Exception as e:
            print(f"[DB] 数据库连接失败(外网?) {e},本次仅输出 CSV/JSON")
            self.enabled = False
            return False

    def is_available(self) -> bool:
        """试连接;可用 True,不可用 False(已标记禁用)"""
        return self._connect()

    def get_latest_review_date(self, org_code: str):
        """
        查询 org_code 下 review_date 倒序最新的一条记录日期(YYYY-MM-DD)。
        用于增量采集:采集开始前取库内最新评论日期,边界停止日期 = 该值 - 1 天。
        表内无记录或查询失败返回 None(调用方应回退为全量采集)。
        """
        if not self._connect():
            return None
        table = self.cfg.get("table", "store_reviews_negative")
        try:
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT review_date FROM {table} "
                "WHERE org_code=%s AND review_date IS NOT NULL "
                "ORDER BY review_date DESC LIMIT 1",
                (org_code,),
            )
            row = cur.fetchone()
            if row and row[0]:
                return row[0].strftime("%Y-%m-%d") if hasattr(row[0], "strftime") else str(row[0])
            return None
        except Exception as e:
            print(f"[DB] 查询 {org_code} 最新 review_date 失败: {e}")
            return None

    def sync_cards(self, cards: List[Dict], org_code: str, shop_name: str) -> Tuple[int, int, int]:
        """
        批量同步卡片:新记录插入,feedback 变化的重复记录更新
        :param cards: ReviewSummarizer.cards(全量,幂等同步)
        :param org_code: 机构编码(hash 与 CSV org_code 列一致)
        :param shop_name: 店铺名(DB store_name 列,NOT NULL)
        :return (inserted, updated, skipped)
        """
        if not self._connect():
            return (0, 0, 0)
        if not cards:
            return (0, 0, 0)
        table = self.cfg.get("table", "store_reviews_negative")
        inserted = updated = skipped = 0
        try:
            cur = self._conn.cursor()
            # 1. 本批去重:同 hash 只保留一张(合并 feedback)
            merged = {}
            for c in cards:
                user = (c.get("user") or "").strip()
                if not user:  # 防御:username NOT NULL
                    continue
                h = CSVExporter.card_hash(org_code, c)
                if h in merged:
                    if not merged[h].get("merchant_reply") and c.get("merchant_reply"):
                        merged[h]["merchant_reply"] = c.get("merchant_reply")
                        merged[h]["merchant_reply_date"] = c.get("merchant_reply_date")
                    continue
                merged[h] = c
            valid = list(merged.items())

            # 2. 批量查已有 hash(分组 IN 查询防参数超限)
            hashes = [h for h, _ in valid]
            existing = {}
            for i in range(0, len(hashes), 500):
                chunk = hashes[i:i + 500]
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(
                    "SELECT hash_value, store_feedback, store_feedback_date "
                    f"FROM {table} WHERE hash_value IN ({ph})",
                    chunk,
                )
                for row in cur.fetchall():
                    existing.setdefault(row[0], []).append(row)

            for h, c in valid:
                new_fb = (c.get("merchant_reply") or "").strip()
                new_d = _to_date(CSVExporter._normalize_date((c.get("merchant_reply_date") or "") or ""))
                rows = existing.get(h, [])
                if not rows:
                    # 防重兜底(跨批次):同 (org_code, username, review_date) 且正文互为子串
                    # 时,说明两者是同一评论的"折叠残片/完整版",hash 因内容完整度不同而不一致
                    # (折叠版结尾常带省略号+“推荐:xx行”,清洗后与完整版正文不同)。
                    # 旧行内容更长=已存完整版 → 本次残片跳过,防止重复入库;
                    # 本次内容更长=疑似完整版 → 删除旧残片再插入,收敛为一条并保留最新完整正文。
                    _u = (c.get("user") or "").strip()
                    _d = _to_date(CSVExporter._normalize_date((c.get("date") or "") or ""))
                    if _u and _d:
                        _new_c = (c.get("content") or "")
                        try:
                            cur.execute(
                                "SELECT hash_value, content FROM " + table +
                                " WHERE org_code=%s AND username=%s AND review_date=%s "
                                "AND hash_value<>%s LIMIT 1",
                                [org_code, _u, _d, h],
                            )
                            _row = cur.fetchone()
                            if _row is not None:
                                _oc = (_row[1] or "")
                                # 用清洗后内容(去"推荐:xx"行/符号,CJK+字母数字)比较互为子串:
                                # 折叠残片清洗后=正文前N字,完整版清洗后=全文,
                                # 残片清洗必为完整版清洗的子串(原始文本因省略号/推荐行不是子串)。
                                _nc2 = CSVExporter._content_for_hash(_new_c)
                                _ocl = CSVExporter._content_for_hash(_oc)
                                if _ocl and _nc2 and (_ocl in _nc2 or _nc2 in _ocl):
                                    # 互为子串 → 同一评论不同完整度
                                    if len(_oc) >= len(_new_c):
                                        # 库中已是完整版(更长),本次残片跳过
                                        print(f"  [DB] 折叠残片跳过(库中已有完整版): user={_u} date={_d} len {len(_new_c)}->{len(_oc)}")
                                        skipped += 1
                                        continue
                                    # 本次是完整版,旧残片删除后插入新完整版
                                    cur.execute(
                                        "DELETE FROM " + table + " WHERE hash_value=%s",
                                        [_row[0]],
                                    )
                                    print(f"  [DB] 删除旧折叠残片,改用本次完整版: user={_u} date={_d} len {len(_oc)}->{len(_new_c)}")
                        except Exception:
                            pass  # 防重兜底失败不影响主流程
                    cur.execute(
                        "INSERT INTO " + table +
                        " (org_code, store_name, username, review_date, rating, "
                        "price_per_person, content, sentiment, store_feedback, "
                        "store_feedback_date, hash_value) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [
                            org_code, shop_name, (c.get("user") or "").strip(),
                            _to_date(CSVExporter._normalize_date((c.get("date") or "") or "")),
                            c.get("score", "") or "",
                            _to_int_price(c.get("avg_price", "")),
                            c.get("content", "") or "",
                            CSVExporter._rating_to_sentiment(c.get("score", "") or "") or None,
                            new_fb or None, new_d, h,
                        ],
                    )
                    inserted += 1
                    continue
                # 重复 hash:新记录无 feedback → skip
                if not new_fb:
                    skipped += 1
                    continue
                # 有 feedback → 与已有行比 (store_feedback, store_feedback_date)
                # 保护:本次未取到新日期(new_d 为空)时,沿用已有行的日期,避免覆盖清空已补采结果
                keep_date = None
                for row in rows:
                    d = row[2].strftime("%Y-%m-%d") if row[2] else ""
                    if d:
                        keep_date = d
                        break
                new_d_eff = new_d or keep_date
                need_update = False
                for row in rows:
                    row_fb = (row[1] or "").strip()
                    row_d = row[2].strftime("%Y-%m-%d") if row[2] else ""
                    if row_fb != new_fb or row_d != new_d_eff:
                        need_update = True
                        break
                if need_update:
                    cur.execute(
                        f"UPDATE {table} SET store_feedback=%s, store_feedback_date=%s "
                        "WHERE hash_value=%s",
                        [new_fb, new_d_eff, h],
                    )
                    updated += 1
                else:
                    skipped += 1
            if inserted or updated:
                print(f"  [DB] 同步: 新增 {inserted} / 更新 {updated} / 跳过 {skipped}")
            return (inserted, updated, skipped)
        except Exception as e:
            print(f"[DB] 同步失败,禁用同步: {e}")
            self.enabled = False
            return (0, 0, 0)
