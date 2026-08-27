"""
CSV 导出模块:把采集到的评价卡片写入 CSV 文件
字段顺序:org_code, store_name, username, review_date, rating, price_per_person, content, sentiment, store_feedback
编码:utf-8-sig(带 BOM,Excel 正确识别中文;MySQL utf8mb4 表导入兼容)
日期格式:统一 YYYY-MM-DD,相对时间(刚刚/N小时前/N天前/昨天/前天)按当前日期换算
"""
import csv
import os
import re
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict


class CSVExporter:
    """把结构化评价卡片导出为 CSV 文件(utf8mb4,适配 MySQL 导入)"""

    # CSV 表头(顺序固定)
    HEADERS = [
        "org_code",            # 机构编码(与 store_name 一致)
        "store_name",          # 店铺名
        "username",            # 用户名
        "review_date",         # 评价日期(YYYY-MM-DD)
        "rating",              # 评分
        "price_per_person",    # 人均单价
        "content",             # 评价内容
        "sentiment",           # 情感(根据 rating 映射:positive/neutral/negative)
        "store_feedback",      # 商家回复
        "store_feedback_date", # 商家回复日期(YYYY-MM-DD)
        "hash_value",          # 记录唯一标识 MD5(org_code|username|review_date|content)
    ]

    # rating 文案 -> sentiment 映射(按大众点评常见评分文案)
    POSITIVE_KEYWORDS = ("很棒", "超预期", "不错")
    NEUTRAL_KEYWORDS = ("还可以", "一般")
    NEGATIVE_KEYWORDS = ("较差", "很糟糕")

    # 相对时间换算:刚刚/N分钟前/N小时前/N天前/昨天/前天
    _REL_PAT = re.compile(r"^(\d+)\s*(分钟|小时|天)前$")
    _NOW_PAT = re.compile(r"^(刚刚|刚刚前)$")

    @staticmethod
    def _content_for_hash(content: str) -> str:
        """按 SQL hash_value 规则清洗内容后再参与 MD5(与 MySQL/SQL Server 同):
          stage1 : 删除以"推荐：/推荐:"开头的整行(含其后中文,避免被保留进哈希)
                  (正则 ^[\\s]*推荐[:：][^\\n]*$ ,MULTILINE 逐行匹配)
          stage2 : 仅保留 ASCII字母数字与 CJK通用汉字(U+4E00–U+9FFF),其余全删
        """
        s = content or ""
        # stage1: remove whole recommendation lines (推荐：/推荐:) incl. trailing text/newline
        cleaned = re.sub(r"(?:^|\r?\n)\s*推荐[：:].*?(?=\r?\n|$)", "", s, flags=re.DOTALL)
        # stage2: keep only ASCII alnum + Chinese CJK ideographs; strip everything else
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", cleaned)

    @staticmethod
    def hash_value(org_code: str, username: str, review_date: str, content: str) -> str:
        """按 SQL hash_value 规则计算唯一标识:
          MD5(CONCAT(org_code,'|',username,'|',review_date,'|',清洗后content))
          小写 hex,utf-8 编码 —— 与 MySQL/SQL Server store_reviews_negative.hash_value 同公式"""
        s = "{}|{}|{}|{}".format(
            (org_code or "").strip(),
            (username or "").strip(),
            (review_date or "").strip(),
            CSVExporter._content_for_hash(content),
        )
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    @classmethod
    def card_hash(cls, org_code: str, card: Dict) -> str:
        """计算卡片唯一标识(日期归一化为 YYYY-MM-DD 后,与 CSV/DB/hash_value 三处一致)"""
        return cls.hash_value(
            org_code,
            card.get("user", ""),
            cls._normalize_date(card.get("date") or ""),
            card.get("content", "") or "",
        )

    @classmethod
    def _rating_to_sentiment(cls, rating: str) -> str:
        """根据 rating 文案映射到 sentiment 标签,未匹配返回空串"""
        if not rating:
            return ""
        for kw in cls.POSITIVE_KEYWORDS:
            if kw in rating:
                return "positive"
        for kw in cls.NEUTRAL_KEYWORDS:
            if kw in rating:
                return "neutral"
        for kw in cls.NEGATIVE_KEYWORDS:
            if kw in rating:
                return "negative"
        return ""

    @classmethod
    def _normalize_date(cls, raw: str) -> str:
        """
        把大众点评的日期文案统一转换为 YYYY-MM-DD
        支持:
          - 2025年8月26日 / 2025-08-26 / 2025/08/26
          - 8月26日          (无年份,按当前年份补,若超过当前日期则补去年)
          - 2025-08         (无日,补01)
          - 昨天/前天/N天前/N小时前/刚刚 (按 datetime.now() 换算)
        无法解析则原样返回(便于人工核对)
        """
        if not raw:
            return ""
        s = raw.strip()

        # 1. 相对时间:刚刚/N小时前/N天前
        if cls._NOW_PAT.match(s):
            return datetime.now().strftime("%Y-%m-%d")
        m = cls._REL_PAT.match(s)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if unit == "分钟":
                delta = timedelta(minutes=n)
            elif unit == "小时":
                delta = timedelta(hours=n)
            else:
                delta = timedelta(days=n)
            return (datetime.now() - delta).strftime("%Y-%m-%d")
        if s == "昨天":
            return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if s == "前天":
            return (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

        # 2. 标准格式:2025年8月26日
        m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$", s)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return f"{y:04d}-{mo:02d}-{d:02d}"

        # 3. 标准格式:8月26日(无年份)
        m = re.match(r"^(\d{1,2})月(\d{1,2})日$", s)
        if m:
            mo, d = int(m.group(1)), int(m.group(2))
            now = datetime.now()
            y = now.year
            # 若该日期在今年还未来到,则归属去年(如当前2026-08,12月25日应为2025-12-25)
            try:
                candidate = datetime(y, mo, d)
                if candidate > now:
                    y = y - 1
            except ValueError:
                pass
            return f"{y:04d}-{mo:02d}-{d:02d}"

        # 4. ISO/斜线:2025-08-26 / 2025/08/26 / 2025-08
        m = re.match(r"^(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?$", s)
        if m:
            y = int(m.group(1))
            mo = int(m.group(2))
            d = int(m.group(3)) if m.group(3) else 1
            return f"{y:04d}-{mo:02d}-{d:02d}"

        # 5. 兜底:原样返回
        return s

    def _card_to_row(self, card: Dict, shop_name: str, org_code: str) -> List:
        """把单张卡片转为 CSV 行"""
        return [
            org_code,
            shop_name,
            card.get("user", ""),
            self._normalize_date(card.get("date", "")),
            card.get("score", ""),
            card.get("avg_price", ""),
            card.get("content", ""),
            self._rating_to_sentiment(card.get("score", "")),
            card.get("merchant_reply", ""),
            self._normalize_date(card.get("merchant_reply_date", "")),
            self.card_hash(org_code, card),
        ]

    def open_incremental(
        self,
        shop_name: str = "shop",
        org_code: str = "",
        output_dir: str = "reports/dianping",
    ) -> str:
        """
        创建 CSV 文件并写入表头,用于增量写入模式
        :return CSV 文件路径(后续 write_card/close 用 self._file/self._writer)
        """
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c for c in shop_name if c.isalnum() or c in "_-")
        path = os.path.join(output_dir, f"{safe_name}_reviews_{ts}.csv")
        self._file = open(path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file, lineterminator="\n")
        self._writer.writerow(self.HEADERS)
        self._file.flush()
        self._shop_name = shop_name
        self._org_code = org_code
        self._path = path
        return path

    def write_card(self, card: Dict):
        """增量写入单条卡片并 flush(防 Ctrl+C 丢数据)"""
        if not hasattr(self, "_writer"):
            raise RuntimeError("需先调用 open_incremental()")
        self._writer.writerow(self._card_to_row(card, self._shop_name, self._org_code))
        self._file.flush()

    def close(self):
        """关闭增量写入的文件句柄"""
        if hasattr(self, "_file") and self._file:
            self._file.close()
            self._file = None
            self._writer = None

    def rewrite_all(self, cards: List[Dict]):
        """用全部卡片覆盖重写CSV(用于后续补全字段如孤立商家回复日期)"""
        if not hasattr(self, "_path"):
            raise RuntimeError("需先调用 open_incremental()")
        if hasattr(self, "_file") and self._file:
            self._file.close()
        self._file = open(self._path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file, lineterminator="\n")
        self._writer.writerow(self.HEADERS)
        for card in cards:
            self._writer.writerow(self._card_to_row(card, self._shop_name, self._org_code))
        self._file.flush()

    def export(
        self,
        cards: List[Dict],
        shop_name: str = "shop",
        org_code: str = "",
        output_dir: str = "reports/dianping",
    ) -> str:
        """
        一次性导出全部评价卡片到 CSV(utf8mb4 编码)
        :param cards: ReviewSummarizer.cards 或 ReviewParser.parse() 的返回值
                      每张卡片含 user/date/score/content/avg_price/merchant_reply
        :param shop_name: 店铺名,写入 store_name 列 + 用于文件名
        :param org_code: 机构编码,写入 org_code 列(与 store_name 一致)
        :param output_dir: 输出目录
        :return CSV 文件路径
        """
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 文件名安全化:去掉特殊字符
        safe_name = "".join(c for c in shop_name if c.isalnum() or c in "_-")
        path = os.path.join(output_dir, f"{safe_name}_reviews_{ts}.csv")

        # utf-8-sig(带 BOM):Excel 正确识别中文,MySQL utf8mb4 表导入兼容
        # 换行符 \n(Linux 风格,避免 \r\n 在某些 MySQL 导入场景被解析为内容)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(self.HEADERS)
            for card in cards:
                writer.writerow(self._card_to_row(card, shop_name, org_code))
        return path
