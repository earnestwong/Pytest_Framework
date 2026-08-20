"""
评价汇总器:跨多次采集合并结果,去重,导出 JSON
原生文本通道和 OCR 通道共用此模块
"""
import os
import re
import json
from typing import List, Dict
from datetime import datetime


class ReviewSummarizer:
    """评价汇总器,跨多次截图/XML 合并结果,支持结构化卡片"""

    def __init__(self):
        # 结构化评价卡片(每条含 user/date/score/content/avg_price/merchant_reply)
        self.cards: List[Dict] = []
        # 统计各通道使用次数
        self.source_stats = {"native": 0, "ocr": 0}

    def add(self, result: Dict):
        """追加一次采集结果(原生或 OCR 均可)"""
        source = result.get("source", "unknown")
        if source in self.source_stats:
            self.source_stats[source] += 1

        # 优先消费结构化 cards,回退到扁平 reviews
        cards = result.get("cards", [])
        if cards:
            for card in cards:
                key = self._dedup_key(card)
                if not any(self._dedup_key(c) == key for c in self.cards):
                    self.cards.append(card)
        else:
            # 兼容旧格式:把扁平 reviews 转成最小卡片
            reviews = result.get("reviews") or result.get("negative_reviews", [])
            for rev in reviews:
                card = {
                    "user": "",
                    "date": "",
                    "score": "",
                    "content": rev["text"] if rev.get("type") != "merchant" else "",
                    "merchant_reply": rev["text"] if rev.get("type") == "merchant" else "",
                }
                key = self._dedup_key(card)
                if not any(self._dedup_key(c) == key for c in self.cards):
                    self.cards.append(card)

    @staticmethod
    def _dedup_key(card: Dict) -> str:
        """去重 key:用户名 + 日期 + 内容前20字符(忽略评分前缀)"""
        user = (card.get("user") or "").strip()
        date = (card.get("date") or "").strip()
        content = (card.get("content") or "").strip()
        # 同一评论在列表页解析时 content 以"口味:1.5\n环境:1.5\n服务:1.5\n"
        # 评分前缀开头,而孤立回复跨屏拼回时 content 无此前缀,导致 content[:20]
        # 不同而无法去重。剥离开头连续的多行评分前缀后再取前20字,归一化一致。
        content = re.sub(r'^(?:(?:口味|环境|服务|性价比):\s*[\d.]+\s*\n?)+', '', content)
        content = content[:20]
        return f"{user}|{date}|{content}"

    def to_dict(self) -> Dict:
        """生成汇总字典"""
        return {
            "total_reviews": len(self.cards),
            "source_stats": self.source_stats,
            "reviews": self.cards,
        }

    def save(self, output_dir: str = "reports/dianping", shop_name: str = "shop") -> str:
        """保存汇总结果为 JSON 文件(生成带时间戳的新文件)"""
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(output_dir, f"{shop_name}_reviews_{ts}.json")
        return self.save_to(path)

    def save_to(self, path: str) -> str:
        """
        保存汇总结果为 JSON 到指定固定路径(增量写入复用同一文件)
        与 save 的区别:save 每次都生成新时间戳文件名;save_to 固定覆盖指定 path,
        供采集过程中每屏结束时重写,即使 Ctrl+C/中断/列表丢失退出也不丢已采集数据。
        """
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path
