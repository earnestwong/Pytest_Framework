"""
大众点评差评采集 - H5 OCR 识别脚本
适用:H5 渲染页面(uiautomator 抓不到文本,需截图 + PaddleOCR 识别)
依赖 PaddleOCR + PaddlePaddle,首次运行需下载模型(约 100MB)

用法:
    python collect_ocr.py
    python collect_ocr.py --shop 丰裕生煎 --scroll 10
    python collect_ocr.py --device 127.0.0.1:16384 --scroll 5 --gpu

前置条件:
    1. MuMu 模拟器已启动,大众点评 App 已登录
    2. 已进入目标店铺差评列表页(筛选好差评)
    3. ADB 端口已连通
    4. 已安装依赖:pip install paddleocr paddlepaddle pillow
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.adb_helper import ADBHelper
from utils.ocr_helper import OCRHelper
from utils.review_summarizer import ReviewSummarizer
from utils.csv_exporter import CSVExporter

# 默认配置
# adb 路径:默认 "adb" 走系统 PATH;MuMu 用户可用 --adb-path 指定其自带 adb.exe
DEFAULT_ADB_PATH = "adb"
DEFAULT_DEVICE = "127.0.0.1:16384"
DEFAULT_SHOP = "丰裕生煎"
DEFAULT_SCROLL = 5
SCREENSHOT_DIR = "screenshots/dianping"


def parse_args():
    p = argparse.ArgumentParser(description="大众点评差评采集 - H5 OCR 识别")
    p.add_argument("--shop", default=DEFAULT_SHOP, help="店铺名(用于报告文件名)")
    p.add_argument("--scroll", type=int, default=DEFAULT_SCROLL, help="滑动采集屏数")
    p.add_argument("--device", default=DEFAULT_DEVICE, help="ADB 设备地址")
    p.add_argument("--adb-path", default=DEFAULT_ADB_PATH, help="adb.exe 路径")
    p.add_argument("--ratio", type=float, default=0.8, help="滑动距离比例")
    p.add_argument("--gpu", action="store_true", help="启用 GPU 加速 OCR")
    p.add_argument("--min-conf", type=float, default=0.6, help="OCR 置信度阈值")
    return p.parse_args()


def main():
    args = parse_args()

    # 初始化
    adb = ADBHelper(device_serial=args.device, adb_path=args.adb_path)
    adb.connect(args.device)
    ocr = OCRHelper(use_gpu=args.gpu)
    summarizer = ReviewSummarizer()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    print("=" * 60)
    print("大众点评差评采集 - H5 OCR 识别")
    print("=" * 60)
    w, h = adb.get_screen_size()
    print(f"设备: {args.device}  分辨率: {w}x{h}")
    print(f"前台: {adb.get_current_package()}")
    print(f"店铺: {args.shop}  采集屏数: {args.scroll}  GPU: {args.gpu}")
    print()

    print("[初始化] 加载 PaddleOCR 模型(首次较慢)...")
    # 触发懒加载
    _ = ocr.ocr
    print("[初始化] 模型就绪")
    print()

    for i in range(args.scroll):
        print(f"--- 第 {i + 1}/{args.scroll} 屏 ---")

        # 1. 展开全文(H5 页面可能用「全文」按钮,坐标定位)
        xml_str = adb.dump_ui()
        btns = adb.find_elements_by_text(xml_str, "全文")
        for btn in btns:
            x, y = btn["center"]
            print(f"  [展开] 点击「全文」@ ({x}, {y})")
            adb.tap(x, y)
            adb.human_delay(1.0, 2.0)

        # 2. 截图
        shot_path = os.path.join(SCREENSHOT_DIR, f"ocr_{i + 1:03d}.png")
        adb.screenshot(shot_path)
        print(f"  [截图] {shot_path}")

        # 3. OCR 识别 + 差评筛选
        result = ocr.extract_reviews_from_screenshot(shot_path, min_conf=args.min_conf)
        summarizer.add(result)
        print(f"  [OCR] 识别 {result['total_segments']} 段,差评 {result['negative_count']} 条")
        for rev in result["negative_reviews"]:
            kws = ",".join(rev.get("matched_keywords", []))
            print(f"    [命中:{kws}] {rev['text'][:60]}...")

        # 4. 上滑翻屏
        if i < args.scroll - 1:
            adb.swipe_up(ratio=args.ratio)
            adb.random_sleep(2.0, 4.0)
            if (i + 1) % 3 == 0:
                print("  [反扒] 长延时休息...")
                adb.random_sleep(5.0, 8.0)

    # 汇总输出
    print("\n" + "=" * 60)
    print("采集汇总")
    print("=" * 60)
    summary = summarizer.to_dict()
    print(f"评价总数: {summary['total_reviews']}(去重后)")
    print(f"采集通道: OCR {summary['source_stats']['ocr']} 屏")

    # JSON 报告
    json_path = summarizer.save(shop_name=args.shop)
    print(f"\nJSON 报告: {json_path}")

    # CSV 导出(OCR 通道无结构化字段,仅填 content)
    csv_exporter = CSVExporter()
    csv_path = csv_exporter.export(
        cards=summarizer.cards,
        shop_name=args.shop,
    )
    print(f"CSV 报告: {csv_path}")

    print("\n采集完成")


if __name__ == "__main__":
    main()
