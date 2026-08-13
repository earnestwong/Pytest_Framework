"""
大众点评评价采集 - 半自动模式(真机未 root,输入注入被拦时的兜底方案)
自动:dump UI -> 提取文本 -> 结构化解析 -> 去重 -> CSV 导出
手动:点「全文」展开 + 上滑翻屏(脚本会提示,操作完按回车继续)

用法:
    py collect_semiauto.py
    py collect_semiauto.py --shop 丰裕生煎 --scroll 5
    py collect_semiauto.py --device 127.0.0.1:16384 --scroll 5   # 模拟器(可自动点击)

前置条件:
    1. 手机已开启 USB 调试,大众点评 App 已登录
    2. 已停在目标店铺评价列表页(已筛选差评或全部评价)
    3. 已关闭开发者选项中的三项动画(否则 uiautomator dump 会失败)
"""
import sys
import os
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.adb_helper import ADBHelper
from utils.review_parser import ReviewParser
from utils.review_summarizer import ReviewSummarizer
from utils.csv_exporter import CSVExporter

# 默认配置(可通过命令行参数覆盖)
# adb 路径:默认 "adb" 走系统 PATH;MuMu 用户可用 --adb-path 指定其自带 adb.exe
DEFAULT_ADB_PATH = "adb"
# 留空=自动选首个 USB 真机;MuMu 模拟器填 127.0.0.1:16384
DEFAULT_DEVICE = ""
DEFAULT_SHOP = "丰裕生煎"
DEFAULT_SCROLL = 5


def parse_args():
    p = argparse.ArgumentParser(description="大众点评评价采集 - 半自动模式")
    p.add_argument("--shop", default=DEFAULT_SHOP, help="店铺名(用于报告文件名)")
    p.add_argument("--scroll", type=int, default=DEFAULT_SCROLL, help="滑动采集屏数")
    p.add_argument("--device", default=DEFAULT_DEVICE,
                   help="ADB 设备地址(留空=自动选首个 USB 真机;MuMu 填 127.0.0.1:16384)")
    p.add_argument("--adb-path", default=DEFAULT_ADB_PATH, help="adb.exe 路径")
    return p.parse_args()


def init_adb(args):
    """初始化 ADB:模拟器走 connect,USB 真机直连"""
    if args.device and ":" in args.device:
        adb = ADBHelper(device_serial=args.device, adb_path=args.adb_path)
        adb.connect(args.device)
    else:
        adb = ADBHelper(device_serial=args.device or None, adb_path=args.adb_path)
    return adb


def pause(prompt: str):
    """Windows 下用 cmd /c pause 暂停(不依赖 stdin 重定向)"""
    print(prompt)
    # cmd pause 输出"请按任意键继续. . ." 然后等待按键
    subprocess.call("pause", shell=True)


def main():
    args = parse_args()
    adb = init_adb(args)
    parser = ReviewParser()
    summarizer = ReviewSummarizer()

    print("=" * 60)
    print("大众点评评价采集 - 半自动模式")
    print("=" * 60)
    w, h = adb.get_screen_size()
    print(f"设备: {args.device or '(USB 自动)'}  分辨率: {w}x{h}")
    print(f"前台: {adb.get_current_package()}")
    print(f"店铺: {args.shop}  采集屏数: {args.scroll}")
    print()
    print("说明:输入注入被系统拦截,采用半自动模式")
    print("  - 脚本自动:dump UI -> 解析文本 -> 去重 -> 导出 CSV")
    print("  - 你需手动:1) 点击屏幕上的「全文」展开长评论  2) 上滑加载下一屏")
    print("  - 每屏操作完按回车继续")
    print()

    i = 0
    while i < args.scroll:
        print(f"=== 第 {i + 1}/{args.scroll} 屏 ===")

        # 提示用户先展开全文(若有)
        pause("[手动] 请在手机上点击当前屏所有「全文」按钮展开长评论,完成后按任意键 dump...")

        # dump UI
        print("  [自动] 正在 dump UI...")
        try:
            xml_str = adb.dump_ui()
        except RuntimeError as e:
            print(f"  [错误] dump 失败: {e}")
            print(f"  [提示] 请确认:1)大众点评在前台 2)三项动画已关闭 3)页面已停止加载")
            pause("  按任意键重试本屏(Ctrl+C 退出)...")
            continue  # 不递增 i,重试本屏

        # 提取文本
        text_items = adb.extract_all_text(xml_str, min_len=1)
        total_chars = sum(len(it["text"]) for it in text_items)
        print(f"  [提取] 文本节点 {len(text_items)} 个,共 {total_chars} 字")

        if total_chars == 0:
            print("  [警告] 未提取到任何文本,可能是 H5 页面或 dump 异常")
            pause("  按任意键继续下一屏...")

        # 结构化解析
        cards = parser.parse(text_items)
        result = {"cards": cards, "review_count": len(cards), "source": "native"}
        summarizer.add(result)
        print(f"  [评价] 本屏 {len(cards)} 条")
        for card in cards:
            price_tag = f" 人均¥{card['avg_price']}" if card.get("avg_price") else ""
            print(f"    [{card['user']}] {card['date']} {card['score']}{price_tag}")
            print(f"      内容: {card['content'][:60]}...")
            if card["merchant_reply"]:
                print(f"      商家: {card['merchant_reply'][:60]}...")

        # 提示上滑(非最后一屏)
        if i < args.scroll - 1:
            pause("\n[手动] 请在手机上向上滑动加载下一屏,完成后按任意键继续...")

        i += 1

    # 汇总输出
    print("\n" + "=" * 60)
    print("采集汇总")
    print("=" * 60)
    summary = summarizer.to_dict()
    print(f"评价总数: {summary['total_reviews']}(去重后)")
    print(f"采集通道: 原生 {summary['source_stats'].get('native', 0)} 屏")

    # JSON 报告
    json_path = summarizer.save(shop_name=args.shop)
    print(f"\nJSON 报告: {json_path}")

    # CSV 导出
    csv_exporter = CSVExporter()
    csv_path = csv_exporter.export(
        cards=summarizer.cards,
        shop_name=args.shop,
    )
    print(f"CSV 报告: {csv_path}")

    print("\n采集完成")


if __name__ == "__main__":
    main()
