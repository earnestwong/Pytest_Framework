"""
大众点评店铺页 Page Object
封装进入店铺、切换点评 tab、滑动加载、截图采集等业务操作
"""
import os
import time
from typing import List, Dict, Optional

from utils.adb_helper import ADBHelper
from utils.ocr_helper import OCRHelper
from utils.review_summarizer import ReviewSummarizer
from utils.review_parser import ReviewParser


class DianpingShopPage:
    """大众点评店铺详情页"""

    # 大众点评包名
    PACKAGE = "com.dianping.v1"

    def __init__(self, adb: ADBHelper, ocr: OCRHelper, screenshot_dir: str = "screenshots/dianping"):
        self.adb = adb
        self.ocr = ocr
        self.parser = ReviewParser()
        self.screenshot_dir = screenshot_dir
        os.makedirs(screenshot_dir, exist_ok=True)
        self._shot_index = 0

    # ---------- 导航 ----------

    def ensure_app_foreground(self) -> bool:
        """确认大众点评在前台,若不在则拉起"""
        pkg = self.adb.get_current_package()
        if pkg == self.PACKAGE:
            return True
        self.adb.shell(f"monkey -p {self.PACKAGE} -c android.intent.category.LAUNCHER 1")
        self.adb.random_sleep(5.0, 8.0)
        return self.adb.get_current_package() == self.PACKAGE

    def enter_shop_by_search(self, shop_name: str):
        """
        通过搜索进入指定店铺
        注意:调用前需保证大众点评已在前台首页
        """
        # 点击首页搜索框(坐标需根据模拟器分辨率调整,以 1080x1920 为例)
        w, h = self.adb.get_screen_size()
        self.adb.tap(w // 2, int(h * 0.08))
        self.adb.human_delay()

        # 输入店铺名(中文需借助 ADBKeyBoard 或剪贴板)
        self._input_chinese(shop_name)
        self.adb.human_delay()

        # 点击搜索按钮 / 键盘确认
        self.adb.tap(int(w * 0.9), int(h * 0.08))
        self.adb.random_sleep(3.0, 5.0)

        # 点击搜索结果第一条
        self.adb.tap(w // 2, int(h * 0.25))
        self.adb.random_sleep(4.0, 6.0)

    def _input_chinese(self, text: str):
        """
        中文输入:通过剪贴板粘贴
        依赖 ADB 自带的 content insert 命令(Android 10+)
        """
        # 方案1:使用 ADBKeyBoard(需预先安装 apk)
        # self.adb.shell(f"am broadcast -a ADB_INPUT_TEXT --es msg '{text}'")
        # 方案2:剪贴板 + 长按粘贴(兼容性更好)
        self.adb.shell(f"input text '{text.encode('utf-8').hex()}'")  # 兜底,部分 ROM 支持
        # 推荐:在 conftest 中预先安装 ADBKeyBoard,这里走广播

    def click_reviews_tab(self):
        """点击「点评」tab,进入评论列表"""
        # uiautomator 定位 "点评" 文本
        xml = self.adb.dump_ui()
        elements = self.adb.find_elements_by_text(xml, "点评")
        if not elements:
            # 兜底:按坐标点击(典型布局:点评 tab 在屏幕中部偏上)
            w, h = self.adb.get_screen_size()
            self.adb.tap(int(w * 0.5), int(h * 0.55))
        else:
            x, y = elements[0]["center"]
            self.adb.tap(x, y)
        self.adb.random_sleep(3.0, 5.0)

    def click_filter_negative(self):
        """
        点击筛选条件 -> 只看差评
        大众点评通常有「全部/好/中/差」筛选,这里点「差」
        """
        xml = self.adb.dump_ui()
        elements = self.adb.find_elements_by_text(xml, "差")
        if elements:
            x, y = elements[0]["center"]
            self.adb.tap(x, y)
            self.adb.random_sleep(2.0, 4.0)

    # ---------- 采集 ----------

    def _next_screenshot_path(self, prefix: str = "review") -> str:
        self._shot_index += 1
        return os.path.join(self.screenshot_dir, f"{prefix}_{self._shot_index:03d}.png")

    def expand_all_fulltext(self):
        """
        点击当前屏所有「全文」按钮,展开被截断的长评论
        大众点评长评论默认折叠,需逐个点击展开
        """
        xml_str = self.adb.dump_ui()
        btns = self.adb.find_elements_by_text(xml_str, "全文")
        for btn in btns:
            x, y = btn["center"]
            self.adb.tap(x, y)
            self.adb.human_delay(1.0, 2.0)
        if btns:
            print(f"  [展开] 点击了 {len(btns)} 个「全文」按钮")

    def collect_reviews(
        self,
        scroll_count: int = 5,
        summarizer: Optional[ReviewSummarizer] = None,
        native_min_chars: int = 20,
        expand_fulltext: bool = True,
    ) -> ReviewSummarizer:
        """
        滑动屏幕逐屏采集差评(原生文本优先,OCR 兜底)
        :param scroll_count: 滑动屏数,控制采集量与反扒风险
        :param summarizer: 外部传入的汇总器(跨店铺复用),不传则新建
        :param native_min_chars: 原生文本字符数阈值,低于此值降级到 OCR
        :param expand_fulltext: 每屏采集前是否点击「全文」展开长评论
        :return ReviewSummarizer
        """
        summarizer = summarizer or ReviewSummarizer()

        for i in range(scroll_count):
            # 先展开当前屏的「全文」
            if expand_fulltext:
                self.expand_all_fulltext()

            result = self._collect_one_screen(i, scroll_count, native_min_chars)
            summarizer.add(result)

            # 上滑加载下一屏
            self.adb.swipe_up(ratio=0.6)

            # 反扒:每滑动几屏后插一次较长延时
            if (i + 1) % 3 == 0:
                self.adb.random_sleep(5.0, 8.0)

        return summarizer

    def _collect_one_screen(
        self,
        index: int,
        total: int,
        native_min_chars: int,
    ) -> Dict:
        """
        采集当前一屏:优先用 uiautomator 提取原生文本,不足时降级到 OCR
        App 已过滤为差评,全量抓取所有评价文本
        :return 采集结果 dict(含 source 字段标识通道)
        """
        # 通道1:uiautomator dump 原生文本
        xml_str = self.adb.dump_ui()
        text_items = self.adb.extract_all_text(xml_str)
        total_chars = sum(len(it["text"]) for it in text_items)

        if total_chars >= native_min_chars:
            # 原生文本充足,直接走原生通道
            cards = self.parser.parse(text_items)
            result = {
                "cards": cards,
                "total_segments": len(text_items),
                "review_count": len(cards),
                "source": "native",
            }
            print(f"[采集] 第 {index + 1}/{total} 屏 [原生]:"
                  f"文本 {total_chars} 字,片段 {result['total_segments']},"
                  f"评价 {result['review_count']} 条")
            return result

        # 通道2:降级到 OCR(H5 渲染兜底)
        print(f"[采集] 第 {index + 1}/{total} 屏 原生文本仅 {total_chars} 字,降级 OCR")
        shot_path = self._next_screenshot_path()
        self.adb.screenshot(shot_path)
        result = self.ocr.extract_reviews_from_screenshot(shot_path)
        print(f"[采集] 第 {index + 1}/{total} 屏 [OCR]:"
              f"识别 {result['total_segments']} 段,差评 {result['negative_count']} 条")
        return result

    # ---------- 完整流程 ----------

    def collect_shop_negative_reviews(
        self,
        shop_name: str,
        scroll_count: int = 5,
    ) -> Dict:
        """
        完整流程:搜索店铺 -> 进入 -> 切点评 tab -> 筛差评 -> 滑动采集 -> 汇总
        App 已过滤为差评,全量抓取
        :return 汇总结果 dict
        """
        self.ensure_app_foreground()
        self.enter_shop_by_search(shop_name)
        self.click_reviews_tab()
        self.click_filter_negative()

        summarizer = self.collect_reviews(scroll_count=scroll_count)
        path = summarizer.save(shop_name=shop_name)

        result = summarizer.to_dict()
        result["report_path"] = path
        result["shop_name"] = shop_name
        return result

    def back_to_home(self):
        """返回大众点评首页,供下一次采集使用"""
        for _ in range(3):
            self.adb.back()
            self.adb.human_delay()
            pkg = self.adb.get_current_package()
            if pkg != self.PACKAGE:
                self.ensure_app_foreground()
                break
