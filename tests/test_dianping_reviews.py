"""
大众点评店铺差评采集测试用例
通过 ADB 驱动 MuMu 模拟器,OCR 识别 H5 差评内容
"""
import pytest
import os
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestDianpingReviews:
    """大众点评差评采集测试"""

    @pytest.mark.smoke
    def test_adb_device_connected(self, adb_device):
        """验证 MuMu 模拟器 ADB 连接正常"""
        size = adb_device.get_screen_size()
        assert size[0] > 0 and size[1] > 0, f"屏幕分辨率异常: {size}"
        print(f"[OK] 设备分辨率: {size[0]}x{size[1]}")

    @pytest.mark.smoke
    def test_dianping_app_foreground(self, dianping_page):
        """验证大众点评 App 已启动并可切换到前台"""
        assert dianping_page.ensure_app_foreground(), "大众点评 App 未在前台"
        print("[OK] 大众点评 App 已在前台")

    @pytest.mark.regression
    def test_native_text_extraction(self, adb_device, dianping_page):
        """验证原生文本提取通道可正常工作"""
        dianping_page.ensure_app_foreground()
        xml_str = adb_device.dump_ui()
        text_items = adb_device.extract_all_text(xml_str)

        assert text_items, "uiautomator dump 未提取到任何原生文本节点"
        total_chars = sum(len(it["text"]) for it in text_items)
        print(f"[OK] 原生文本提取: {len(text_items)} 个节点,共 {total_chars} 字")
        print(f"  样例: {text_items[0]['text'][:30]}")

    @pytest.mark.regression
    def test_ocr_basic(self, ocr_engine, adb_device):
        """验证 OCR 引擎可正常识别当前屏幕文本(兜底通道)"""
        shot_path = "screenshots/dianping/ocr_test.png"
        os.makedirs("screenshots/dianping", exist_ok=True)
        adb_device.screenshot(shot_path)

        text = ocr_engine.recognize_full_text(shot_path)
        assert text, "OCR 未识别到任何文本,请检查截图或 OCR 配置"
        print(f"[OK] OCR 识别文本片段数: {len(text.splitlines())}")

    @pytest.mark.regression
    def test_dual_channel_fallback(self, dianping_page, adb_device):
        """
        验证双通道降级逻辑:原生文本充足时走原生,不足时降级 OCR
        通过人为传入极小的 native_min_chars 阈值强制走原生通道
        """
        dianping_page.ensure_app_foreground()

        # 强制走原生通道(阈值设为 0)
        result_native = dianping_page._collect_one_screen(0, 1, native_min_chars=0)
        assert result_native["source"] == "native", "应走原生通道"
        print(f"[OK] 原生通道: {result_native['total_segments']} 段文本")

        # 强制走 OCR 通道(阈值设极大)
        result_ocr = dianping_page._collect_one_screen(0, 1, native_min_chars=999999)
        assert result_ocr["source"] == "ocr", "应降级到 OCR 通道"
        print(f"[OK] OCR 兜底: {result_ocr['total_segments']} 段文本")

    @pytest.mark.regression
    def test_collect_single_shop_reviews(self, dianping_page):
        """
        采集单个店铺的差评内容(原生优先 + OCR 兜底)
        默认滑动 5 屏,可按需调整 scroll_count
        """
        shop_name = os.getenv("SHOP_NAME", "海底捞火锅")
        scroll_count = int(os.getenv("SCROLL_COUNT", "5"))

        result = dianping_page.collect_shop_negative_reviews(
            shop_name=shop_name,
            scroll_count=scroll_count,
        )

        # 断言:至少识别到 1 条差评
        assert result["total_negative_reviews"] >= 1, "未采集到任何差评内容"
        assert os.path.exists(result["report_path"]), "差评汇总报告未生成"

        print(f"\n========== 差评汇总 ==========")
        print(f"店铺: {result['shop_name']}")
        print(f"差评总数: {result['total_negative_reviews']}")
        print(f"采集通道: 原生 {result['source_stats']['native']} 屏, "
              f"OCR {result['source_stats']['ocr']} 屏")
        print(f"报告路径: {result['report_path']}")
        print("\n关键词统计(Top 5):")
        for kw in result["keyword_stats"][:5]:
            print(f"  {kw['keyword']}: {kw['count']} 次")
        print("================================\n")

    @pytest.mark.regression
    def test_collect_multiple_shops_reviews(self, dianping_page):
        """
        批量采集多个店铺差评
        通过环境变量 SHOPS 传入逗号分隔的店铺名
        """
        shops_env = os.getenv("SHOPS", "海底捞火锅,西贝莜面村,呷哺呷哺")
        shop_list = [s.strip() for s in shops_env.split(",") if s.strip()]
        scroll_count = int(os.getenv("SCROLL_COUNT", "3"))

        all_results = []
        for shop in shop_list:
            print(f"\n>>> 开始采集: {shop}")
            try:
                result = dianping_page.collect_shop_negative_reviews(
                    shop_name=shop,
                    scroll_count=scroll_count,
                )
                all_results.append(result)
                print(f"<<< {shop} 采集完成,差评 {result['total_negative_reviews']} 条")
            except Exception as e:
                print(f"<<< {shop} 采集失败: {e}")
            finally:
                # 返回首页,准备下一个店铺
                dianping_page.back_to_home()

        # 汇总断言:至少有一个店铺采集到差评
        success_count = sum(1 for r in all_results if r["total_negative_reviews"] > 0)
        assert success_count >= 1, f"所有 {len(shop_list)} 家店铺均未采集到差评"

        print(f"\n========== 批量汇总 ==========")
        print(f"目标店铺: {len(shop_list)} 家")
        print(f"成功采集: {success_count} 家")
        for r in all_results:
            native_screens = r["source_stats"]["native"]
            ocr_screens = r["source_stats"]["ocr"]
            print(f"  {r['shop_name']}: {r['total_negative_reviews']} 条差评"
                  f"(原生 {native_screens} 屏/OCR {ocr_screens} 屏)")
        print("================================\n")
