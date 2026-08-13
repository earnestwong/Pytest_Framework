"""
PaddleOCR 封装:截图文本识别 + H5 渲染内容提取
用于 H5 页面(uiautomator 抓不到文本)的兜底识别
"""
from typing import List, Dict


class OCRHelper:
    """PaddleOCR 封装,提供截图文本识别"""

    # 差评关键词词表(H5 OCR 通道用,可按需扩展)
    NEGATIVE_KEYWORDS = [
        # 口味
        "难吃", "不好吃", "味道差", "没味道", "太咸", "太淡", "太油", "油腻",
        "不新鲜", "变质", "腥", "柴", "老", "硬", "夹生", "没熟",
        # 服务
        "服务差", "态度差", "态度恶劣", "不理人", "不耐烦", "催", "上错",
        "漏单", "不接待", "赶人", "黑脸", "鄙视", "甩脸色",
        # 环境
        "脏", "乱", "差", "臭", "虫", "老鼠", "蟑螂", "苍蝇", "不干净",
        "吵", "嘈杂", "闷", "挤", "破", "旧",
        # 性价比
        "贵", "坑", "宰客", "不值", "性价比低", "割韭菜", "智商税",
        # 综合
        "差评", "恶心", "失望", "后悔", "再也不会", "不会再来", "踩雷",
        "避雷", "劝退", "无语", "吐槽", "垃圾", "骗子",
    ]

    def __init__(self, lang: str = "ch", use_gpu: bool = False):
        """
        :param lang: PaddleOCR 语言,ch 为中英文混合
        :param use_gpu: 是否使用 GPU,无 CUDA 时 False
        """
        self._ocr = None
        self.lang = lang
        self.use_gpu = use_gpu

    @property
    def ocr(self):
        """懒加载 PaddleOCR 实例(首次调用耗时较长,需下载模型)"""
        if self._ocr is None:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(
                use_angle_cls=True,  # 方向分类,处理倾斜文本
                lang=self.lang,
                use_gpu=self.use_gpu,
                show_log=False,
            )
        return self._ocr

    def recognize(self, image_path: str) -> List[Dict]:
        """
        识别图片中的文本
        :return [{'text': str, 'confidence': float, 'box': [[x1,y1],...,[x4,y4]]}, ...]
        """
        result = self.ocr.ocr(image_path, cls=True)
        items = []
        if not result or not result[0]:
            return items
        for line in result[0]:
            box, (text, conf) = line
            items.append(
                {
                    "text": text.strip(),
                    "confidence": float(conf),
                    "box": box,
                }
            )
        return items

    def recognize_full_text(self, image_path: str, min_conf: float = 0.6) -> str:
        """识别并拼接整张图的所有文本"""
        items = self.recognize(image_path)
        texts = [it["text"] for it in items if it["confidence"] >= min_conf]
        return "\n".join(texts)

    def filter_negative_reviews(self, items: List[Dict], min_conf: float = 0.6) -> List[Dict]:
        """
        从 OCR 结果中筛选包含差评关键词的条目
        :return 过滤后的条目,附带 matched_keywords 字段
        """
        negative = []
        for item in items:
            if item["confidence"] < min_conf:
                continue
            text = item["text"]
            matched = [kw for kw in self.NEGATIVE_KEYWORDS if kw in text]
            if matched:
                negative.append({**item, "matched_keywords": matched})
        return negative

    def extract_reviews_from_screenshot(
        self,
        image_path: str,
        min_conf: float = 0.6,
    ) -> Dict:
        """
        从整页截图提取差评(OCR 通道)
        H5 渲染内容 uiautomator 抓不到,依赖此方法
        :return {
            'all_text': str,
            'negative_reviews': [...],   # 含 matched_keywords
            'total_segments': int,
            'negative_count': int,
            'source': 'ocr',
        }
        """
        items = self.recognize(image_path)
        negatives = self.filter_negative_reviews(items, min_conf)
        for n in negatives:
            n["source"] = "ocr"
        return {
            "all_text": "\n".join(
                it["text"] for it in items if it["confidence"] >= min_conf
            ),
            "negative_reviews": negatives,
            "total_segments": len(items),
            "negative_count": len(negatives),
            "source": "ocr",
        }
