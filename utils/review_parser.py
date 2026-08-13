"""
大众点评评价结构化解析器
从 uiautomator dump 的文本节点中,按 y 坐标分组提取结构化评价记录
字段:用户名、日期、评分、评价内容、商家回复
"""
import re
from typing import List, Dict, Optional


class ReviewParser:
    """解析大众点评评价卡片,提取结构化字段"""

    # 评分文字(大众点评评分档位,与 csv_exporter 的 sentiment 映射对齐)
    SCORE_TEXTS = {
        "很差", "较差", "一般", "好评", "很好", "满意", "超赞",
        "很糟糕", "糟糕", "还行", "不错", "非常满意",
        "超预期", "很棒", "还可以",  # 新增(positive/neutral 档位)
    }

    # 日期正则:3月27日 / 2025年8月26日 / 2024-03-27 / 3天前 / 昨天 / 今天
    DATE_PAT = re.compile(
        r"^\d{1,2}月\d{1,2}日$"
        r"|^\d{4}年\d{1,2}月\d{1,2}日$"
        r"|^\d{4}[-/.]\d{1,2}([-/.]\d{1,2})?$"
        r"|^\d+天前$"
        r"|^(前天|昨天|今天|刚刚)$"
        r"|^\d+小时前$"
    )

    # 商家回复前缀(兼容多种格式:"商家回复:" / "XXX(商家):" / "XXX（商家）：" / "商家:")
    # 括号支持全角（）和半角(),冒号支持全角：和半角:
    MERCHANT_PAT = re.compile(r"^(?:商家回复|.+?[（(]商家[）)]|商家)\s*[:：]")

    # 人均价格(可能出现在评价内容首行)
    PRICE_PAT = re.compile(r"[￥¥]\s*(\d+(?:\.\d+)?)\s*/?\s*人?")

    # 用户签名特征:"发布过X条..."
    USER_SIG_PAT = re.compile(r"^发布过\d+条")

    # UI 噪声关键词(这些是控件文本,非评价内容)
    UI_NOISE = {
        "全部", "最新", "差评", "中评", "好评", "带图/视频", "语音评价",
        "当地人评价", "筛选", "评价", "搜索", "规则", "图片", "播放",
        "有帮助", "评论", "说点什么吧～", "头像", "有帮助按钮", "评论按钮",
        "收起", "展开", "全文",  # 折叠/展开按钮文本
        "打卡后评价", "通过大众点评消费",  # 评价标签
        "已收藏", "打卡", "写评价",  # 底部操作栏
        "查看全部", "顶部查看全部",
        "搜索栏", "返回", "收藏", "分享", "更多",
        "gridView_item_wrapper",
    }

    # UI 噪声正则(匹配"正文N"、"共N条回复"、"图片N"、"X条评论"、菜单(N) 等)
    UI_NOISE_PAT = re.compile(
        r"^正文\d+$|^图片\d+$|^评论\d+$|^共\d+条回复$"
        r"|^\d+条评论$|^\d+小时前有新增评价$"
        r"^(菜单|优惠|推荐菜|评价)\s*\(\d+\)$"  # 菜单(7)/评价(1359)
    )

    def parse(self, text_items: List[Dict]) -> List[Dict]:
        """
        解析文本节点列表,返回结构化评价记录
        以「日期」为锚点切分卡片,兼容两种节点顺序:
          真机:日期 → 用户名 → [人均] → 评分 → 内容
          MuMu:用户名 → [签名] → 日期 → [人均] → 评分 → 内容
        """
        # 第1步:过滤 UI 噪声
        # 注意:单字中文不过滤(可能是用户名如"聪"/"芳"),由 _is_username_candidate 精筛
        valid = []
        for it in text_items:
            text = it["text"].strip()
            if not text or text in self.UI_NOISE:
                continue
            if self.UI_NOISE_PAT.match(text):
                continue
            if not text:
                continue
            if text.isdigit():
                continue
            valid.append({**it, "text": text})

        # 第2步:找所有日期节点索引(卡片锚点)
        date_idxs = [i for i, it in enumerate(valid) if self.DATE_PAT.match(it["text"])]

        # 第2b步:检测第一个日期锚点之前的孤立商家回复(属于上一条评论的残余)
        #   不归入当前卡片(避免数据错误),记录到 orphan_replies 供调用方提示
        self.orphan_replies = []
        if date_idxs:
            first_d = date_idxs[0]
            for i in range(first_d):
                if self.MERCHANT_PAT.match(valid[i]["text"]):
                    self.orphan_replies.append(valid[i]["text"])

        # 第3步:按日期锚点切分并解析每张卡片
        # 注意:MuMu 顺序下「用户名→日期」,下一张的用户名会落在当前卡片范围内,
        # 需检查下一日期锚点前一节点是否为用户名候选,若是则本张 end_idx 前移一位,
        # 把该用户名留给下一张卡片,避免吞进当前 content
        cards = []
        for k, d_idx in enumerate(date_idxs):
            end_idx = date_idxs[k + 1] if k + 1 < len(date_idxs) else len(valid)
            # 若下一张日期前一个节点是用户名候选,把它留给下一张
            if k + 1 < len(date_idxs):
                next_d = date_idxs[k + 1]
                if (next_d - 1 > d_idx
                        and self._is_username_candidate(valid[next_d - 1]["text"])):
                    end_idx = next_d - 1
            card = self._parse_one_card(valid, d_idx, end_idx, k > 0)
            if card:
                cards.append(card)

        # 兜底:无日期锚点时用宽松规则
        if not cards and valid:
            cards = self._parse_loose(valid)

        return cards

    # 搜索建议/链接特征:"搜索"开头 或 含引号搜索词
    SEARCH_LINK_PAT = re.compile(r'^搜索[“"]|^查看更多|^相关推荐|^猜你想找')

    def _is_username_candidate(self, text: str) -> bool:
        """判断文本是否像用户名(非噪声/非评分/非人均/非日期/非商家回复)"""
        if not text or len(text) > 20:
            return False
        if text in self.UI_NOISE or text in self.SCORE_TEXTS:
            return False
        if self.DATE_PAT.match(text) or self.MERCHANT_PAT.match(text):
            return False
        if self.USER_SIG_PAT.match(text) or self.UI_NOISE_PAT.match(text):
            return False
        if self.PRICE_PAT.match(text):
            return False
        # 过滤搜索建议/链接(如 搜索"炸猪排"看真实经验总结)
        if self.SEARCH_LINK_PAT.match(text):
            return False
        return True

    def _parse_one_card(self, items: List[Dict], d_idx: int, end_idx: int,
                        has_prev_card: bool) -> Optional[Dict]:
        """
        解析单个评价卡片(以日期节点为锚点)
        :param d_idx: 日期节点在 items 中的索引
        :param end_idx: 本卡片结束索引(下一个日期锚点,exclusive)
        :param has_prev_card: 是否有上一张卡片(影响用户名候选:日期前一节点可能属上张卡片)
        """
        card = {
            "user": "", "date": items[d_idx]["text"], "score": "",
            "content": "", "avg_price": "", "merchant_reply": "",
        }

        # 1. 找用户名:
        #   真机顺序:日期 → 用户名(取 d_idx+1)
        #   MuMu 顺序:用户名 → [头像/签名] → 日期(日期前可能隔头像等噪声节点)
        user_idx = -1
        if d_idx + 1 < end_idx and self._is_username_candidate(items[d_idx + 1]["text"]):
            # 真机:日期后一节点是用户名
            user_idx = d_idx + 1
            card["user"] = items[user_idx]["text"]
        else:
            # MuMu:向前扫描(最多3个节点),跳过头像/签名等噪声,找用户名候选
            for back in range(1, min(4, d_idx + 1)):
                prev_text = items[d_idx - back]["text"]
                if self._is_username_candidate(prev_text):
                    user_idx = d_idx - back
                    card["user"] = prev_text
                    break
                # 遇到头像/签名等可跳过的噪声,继续向前
                if (prev_text in self.UI_NOISE
                        or self.USER_SIG_PAT.match(prev_text)
                        or self.UI_NOISE_PAT.match(prev_text)):
                    continue
                # 遇到其他非噪声文本(如上一条评论的内容/商家回复),停止
                break

        # 2. 从日期向后扫描找评分/人均/商家回复/内容
        score_found = False
        content_start = -1
        for j in range(d_idx + 1, end_idx):
            if j == user_idx:
                continue
            text = items[j]["text"]

            # 商家回复
            if self.MERCHANT_PAT.match(text):
                card["merchant_reply"] = text
                continue

            # 人均
            price_match = self.PRICE_PAT.match(text)
            if price_match:
                card["avg_price"] = price_match.group(1)
                continue

            # 评分(未找到内容前)
            if not score_found and text in self.SCORE_TEXTS:
                card["score"] = text
                score_found = True
                continue

            # 跳过签名
            if self.USER_SIG_PAT.match(text):
                continue

            # 其余归入内容
            # 边界检查:若当前节点像用户名,且后续3个节点内出现日期(可能隔签名/图片标签),
            # 则当前节点是下一张卡片的用户名,本卡片停止合并
            if self._is_username_candidate(text):
                # 向后看最多3个节点,只要遇到日期就认定是下一卡片锚点
                hit_date = False
                for k in range(1, min(4, len(items) - j)):
                    nxt = items[j + k]["text"]
                    if self.DATE_PAT.match(nxt):
                        hit_date = True
                        break
                    # 遇到非签名/非图片标签的其他用户名候选,放弃(可能是内容里的专有名词)
                    if (not self.USER_SIG_PAT.match(nxt)
                            and not self.UI_NOISE_PAT.match(nxt)
                            and not self.PRICE_PAT.match(nxt)
                            and nxt not in self.SCORE_TEXTS
                            and not self.MERCHANT_PAT.match(nxt)):
                        break
                if hit_date:
                    break
            if card["content"]:
                card["content"] += "\n" + text
            else:
                card["content"] = text

        # MuMu 顺序:用户名在日期前,日期前可能还有签名节点,内容在日期后
        # 上面已处理日期后部分;若用户名在日期前且评分未找到,检查日期前是否有评分(罕见)

        # 无内容且无商家回复则视为无效
        if not card["content"] and not card["merchant_reply"]:
            return None
        return card

    def _parse_loose(self, items: List[Dict]) -> List[Dict]:
        """宽松解析:按日期锚点切分卡片(无用户名签名时兜底)"""
        cards = []
        current = None
        for item in items:
            text = item["text"]
            if self.DATE_PAT.match(text):
                # 新卡片
                if current and current.get("content"):
                    cards.append(current)
                current = {"user": "", "date": text, "score": "",
                           "content": "", "avg_price": "", "merchant_reply": ""}
            elif self.MERCHANT_PAT.match(text) and current:
                current["merchant_reply"] = text
            elif current:
                # 先尝试提取人均
                price_match = self.PRICE_PAT.match(text)
                if price_match:
                    current["avg_price"] = price_match.group(1)
                elif text in self.SCORE_TEXTS:
                    current["score"] = text
                elif text not in self.UI_NOISE and not text.isdigit():
                    if current["content"]:
                        current["content"] += "\n" + text
                    else:
                        current["content"] = text
        if current and current.get("content"):
            cards.append(current)
        return cards
