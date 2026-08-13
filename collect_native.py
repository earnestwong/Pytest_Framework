"""
大众点评差评采集 - 原生文本直读脚本
适用:App 原生页面(uiautomator 可直接读取文本节点)
不依赖 PaddleOCR,无需 GPU,启动快

用法:
    python collect_native.py
    python collect_native.py --shop 丰裕生煎 --scroll 10
    python collect_native.py --device 127.0.0.1:16384 --scroll 5

前置条件:
    1. MuMu 模拟器已启动,大众点评 App 已登录
    2. 已进入目标店铺差评列表页(筛选好差评)
    3. ADB 端口已连通(adb connect 127.0.0.1:16384)
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.adb_helper import ADBHelper
from utils.review_parser import ReviewParser
from utils.review_summarizer import ReviewSummarizer
from utils.csv_exporter import CSVExporter

# 默认配置(可通过命令行参数覆盖)
# adb 路径:同事使用时,请把此变量改为本机 adb.exe 的完整路径
# (例如 MuMu 自带 adb:C:\Program Files\Netease\MuMu\nx_main\adb.exe)
# 或将 adb 加入系统 PATH 后保持 "adb" 不变
DEFAULT_ADB_PATH = r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"
# 留空=自动选首个 USB 真机;MuMu 模拟器填 127.0.0.1:16384
DEFAULT_DEVICE = ""
DEFAULT_SCROLL = 0  # 0=无限滑动,连续3屏无新评价则停
DEFAULT_RATIO = 0.38  # 滑动比例(配合 700ms 慢速滑动)


def parse_args():
    p = argparse.ArgumentParser(description="大众点评差评采集 - 原生文本直读")
    p.add_argument("--shop", required=True, help="店铺名(必填,用于报告文件名 + store_name 列)")
    p.add_argument("--org-code", required=True, dest="org_code",
                   help="机构编码(必填,写入 org_code 列,与 store_name 一致)")
    p.add_argument("--scroll", type=int, default=DEFAULT_SCROLL,
                   help="滑动采集屏数(0=无限滑动,连续3屏无新评价则停)")
    p.add_argument("--device", default=DEFAULT_DEVICE,
                   help="ADB 设备地址(留空=自动选首个 USB 真机;MuMu 填 127.0.0.1:16384)")
    p.add_argument("--adb-path", default=DEFAULT_ADB_PATH, help="adb.exe 路径")
    p.add_argument("--ratio", type=float, default=DEFAULT_RATIO, help="滑动距离比例")
    p.add_argument("--swipe-mode", default="auto",
                   help="滑动方式:auto(根据设备自动) | swipe(真机) | roll(MuMu)")
    return p.parse_args()


def main():
    args = parse_args()

    # 初始化(原生通道无需 OCR)
    # 设备地址留空=USB 真机(直连,无需 adb connect);填 IP:Port=模拟器(需 adb connect)
    if args.device and ":" in args.device:
        adb = ADBHelper(device_serial=args.device, adb_path=args.adb_path)
        adb.connect(args.device)
    else:
        # USB 真机:不传 device_serial,自动选首个设备
        adb = ADBHelper(device_serial=args.device or None, adb_path=args.adb_path)
    parser = ReviewParser()
    summarizer = ReviewSummarizer()

    print("=" * 60)
    print("大众点评差评采集 - 原生文本直读")
    print("=" * 60)
    w, h = adb.get_screen_size()
    print(f"设备: {args.device or '(USB 自动)'}  分辨率: {w}x{h}")
    print(f"前台: {adb.get_current_package()}")
    # 滑动方式:含:的是模拟器(roll),否则真机(swipe)
    if args.swipe_mode == "auto":
        swipe_mode = "roll" if args.device and ":" in args.device else "swipe"
    else:
        swipe_mode = args.swipe_mode
    infinite = (args.scroll == 0)
    total_screens = "无限(到底)" if infinite else str(args.scroll)
    print(f"店铺: {args.shop}  org_code: {args.org_code}  采集屏数: {total_screens}  滑动比例: {args.ratio}  滑动方式: {swipe_mode}")
    print()

    # 无限模式:连续 5 屏无新评价则停;固定模式:按 args.scroll 滑
    MAX_IDLE = 5  # 连续无新增的屏数阈值(应对单条超长评论占满整屏)
    idle_count = 0
    screen_idx = 0
    while True:
        # 固定模式:达到屏数上限则停
        if not infinite and screen_idx >= args.scroll:
            break

        print(f"--- 第 {screen_idx + 1}{('屏' if infinite else f'/{args.scroll} 屏')} ---")

        # 1. dump 当前屏(复用于:验证码检测 + 全文按钮定位)
        xml_str = adb.dump_ui()

        # 1a. 滑动验证码检测(反扒随机弹出,复用本次 dump 不额外消耗)
        try:
            captcha = adb.detect_slide_captcha(xml_str)
            if captcha:
                adb.solve_slide_captcha(captcha)
                # 处理后重新 dump 确认是否消除,并作为后续操作的基准
                xml_str = adb.dump_ui()
                if adb.detect_slide_captcha(xml_str):
                    print("  [验证] 滑动后仍存在验证弹窗,可能需要人工介入")
                else:
                    print("  [验证] 验证弹窗已消除,继续采集")
                    adb.human_delay(1.5, 2.5)
        except Exception as e:
            print(f"  [验证] 检测异常(忽略): {e}")

        # 1b. 展开全文(长评论折叠处理)
        #     每次点击后重新 dump 获取最新坐标(展开后下方元素位置下移)
        #     筛选已排除"收起全文"(展开后按钮文本),不会重复点击同一按钮
        #     个别"全文"按钮可能点击无效(点评App bug),连续3次无效则放弃,继续下滑
        max_expand = 15  # 单屏最多展开次数,防止死循环
        max_fail = 3     # 连续点击无效次数上限
        expanded = 0
        fail_count = 0
        last_first_y = None
        while expanded < max_expand:
            all_btns = adb.find_elements_by_text(xml_str, "全文")
            # 排除"查看全文"(导航链接)和"收起全文"(收起按钮)
            btns = [b for b in all_btns
                    if "查看" not in b["text"] and "收起" not in b["text"]]
            if not btns:
                break
            first_y = btns[0]["center"][1]
            # 若第一个按钮仍是上次那个位置,说明点击无效(点评bug)
            if last_first_y is not None and abs(first_y - last_first_y) < 80:
                fail_count += 1
                if fail_count >= max_fail:
                    print(f"  [展开] 连续{max_fail}次点击无效,跳过本屏展开继续下滑")
                    break
            else:
                fail_count = 0
            last_first_y = first_y
            btn = btns[0]
            x, y = btn["center"]
            print(f"  [展开] 点击「全文」@ ({x}, {y}) text=[{btn['text'][:20]}]")
            adb.tap(x, y)
            adb.human_delay(1.0, 2.0)
            expanded += 1
            # 重新 dump:展开后坐标全变,必须刷新
            xml_str = adb.dump_ui()

        if expanded > 0:
            print(f"  [展开] 本屏共展开 {expanded} 条全文")

        # 2. 提取文本(不做事前页面跳转检测:超长评论展开后整屏可能只有1条评论,
        #    日期节点<2是正常现象,不能作为离开列表的判据)
        text_items = adb.extract_all_text(xml_str, min_len=1)
        total_chars = sum(len(it["text"]) for it in text_items)
        print(f"  [提取] 文本节点 {len(text_items)} 个,共 {total_chars} 字")

        # 3. 结构化解析(记录新增数,用于判断是否滑到底)
        cards = parser.parse(text_items)

        # 3a. 用户名缺失补救:滑动过快可能导致用户名滚出上边界未被抓到
        #     若有空用户名的卡片,往下滑一点(ratio 0.1, 800ms)重新抓取用户名
        missing_user = [c for c in cards if not c.get("user", "").strip()]
        if missing_user:
            print(f"  [补救] {len(missing_user)} 条评论用户名缺失,往下滑重新抓取")
            w, h = adb.get_screen_size()
            y1 = int(h * 0.5)
            y2 = int(h * 0.6)  # 往下滑 10%,把上方滚出的用户名露出来
            adb.swipe(w // 2, y1, w // 2, y2, duration_ms=800, human=False)
            adb.human_delay(1.0, 1.5)
            # 重新 dump + 提取 + 解析
            xml_str = adb.dump_ui()
            new_items = adb.extract_all_text(xml_str, min_len=1)
            new_cards = parser.parse(new_items)
            # 按 date + content前20 匹配,补全用户名
            for old_card in missing_user:
                old_key = f"{old_card.get('date', '')}|{old_card.get('content', '')[:20]}"
                for new_card in new_cards:
                    new_key = f"{new_card.get('date', '')}|{new_card.get('content', '')[:20]}"
                    if new_key == old_key and new_card.get("user", "").strip():
                        old_card["user"] = new_card["user"]
                        print(f"  [补救] 补全用户名: [{new_card['user']}] {old_card['date']}")
                        break

        before = len(summarizer.cards)
        result = {"cards": cards, "review_count": len(cards), "source": "native"}
        summarizer.add(result)
        after = len(summarizer.cards)
        new_count = after - before
        print(f"  [评价] 本屏 {len(cards)} 条,新增 {new_count} 条")
        for card in cards:
            price_tag = f" 人均¥{card['avg_price']}" if card.get("avg_price") else ""
            print(f"    [{card['user']}] {card['date']} {card['score']}{price_tag}")
            print(f"      内容: {card['content'][:60]}...")
            if card["merchant_reply"]:
                print(f"      商家: {card['merchant_reply'][:60]}...")

        # 4. 无限模式:判断是否滑到底
        if infinite:
            if new_count == 0:
                idle_count += 1
                print(f"  [到底检测] 连续 {idle_count}/{MAX_IDLE} 屏无新增")
                if idle_count >= MAX_IDLE:
                    print("  [到底] 已连续 5 屏无新评价,停止滑动")
                    break
            else:
                idle_count = 0

        # 5. 上滑翻屏
        screen_idx += 1
        # 固定模式最后一屏不滑;无限模式判断完到底才滑
        if not infinite and screen_idx >= args.scroll:
            break
        adb.swipe_up(ratio=args.ratio, mode=swipe_mode)
        adb.random_sleep(2.0, 4.0)
        # 反扒:每 3 屏插一次长延时
        if screen_idx % 3 == 0:
            print("  [反扒] 长延时休息...")
            adb.random_sleep(5.0, 8.0)

    # 汇总输出
    print("\n" + "=" * 60)
    print("采集汇总")
    print("=" * 60)
    summary = summarizer.to_dict()
    print(f"评价总数: {summary['total_reviews']}(去重后)")
    print(f"采集通道: 原生 {summary['source_stats']['native']} 屏")

    # JSON 报告
    json_path = summarizer.save(shop_name=args.shop)
    print(f"\nJSON 报告: {json_path}")

    # CSV 导出
    csv_exporter = CSVExporter()
    csv_path = csv_exporter.export(
        cards=summarizer.cards,
        shop_name=args.shop,
        org_code=args.org_code,
    )
    print(f"CSV 报告: {csv_path}")

    print("\n采集完成")


if __name__ == "__main__":
    main()
