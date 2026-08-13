"""
大众点评差评采集 - 原生文本直读脚本
适用:App 原生页面(uiautomator 可直接读取文本节点)
不依赖 PaddleOCR,无需 GPU,启动快

用法:
    python collect_native.py --shop "丰裕（淮海店）" --org-code "080501"
    python collect_native.py --shop "丰裕（淮海店）" --org-code "080501" --scroll 10
    python collect_native.py --shop "丰裕（淮海店）" --org-code "080501" --device 127.0.0.1:16384

配置文件:
    dianping_config.json 存放环境相关参数(adb路径/输出目录等),
    同事按本机环境修改即可,命令行参数优先级高于配置文件。

前置条件:
    1. 安卓设备已连接(USB 真机或 MuMu 模拟器),大众点评 App 已登录
    2. 已进入目标店铺差评列表页(筛选好差评)
    3. ADB 端口已连通(adb connect 127.0.0.1:16384)
"""
import sys
import os
import re
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.adb_helper import ADBHelper
from utils.review_parser import ReviewParser
from utils.review_summarizer import ReviewSummarizer
from utils.csv_exporter import CSVExporter

# 配置文件路径(与本脚本同目录)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dianping_config.json")


def load_config() -> dict:
    """加载 dianping_config.json,文件不存在则用内置默认值"""
    defaults = {
        "adb_path": "adb",
        "device": "",
        "swipe_mode": "auto",
        "scroll": 0,
        "ratio": 0.38,
        "output_dir": "reports/dianping",
        "captcha_screenshot_dir": "screenshots/captcha",
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            defaults.update(cfg)
        except Exception as e:
            print(f"[配置] 读取 {CONFIG_PATH} 失败({e}),使用内置默认值")
    return defaults


def parse_args():
    cfg = load_config()
    p = argparse.ArgumentParser(description="大众点评差评采集 - 原生文本直读")
    p.add_argument("--shop", required=True, help="店铺名(必填,用于报告文件名 + store_name 列)")
    p.add_argument("--org-code", required=True, dest="org_code",
                   help="机构编码(必填,写入 org_code 列,与 store_name 一致)")
    p.add_argument("--scroll", type=int, default=cfg["scroll"],
                   help=f"滑动采集屏数(0=无限滑动,到底则停),默认 {cfg['scroll']}")
    p.add_argument("--device", default=cfg["device"],
                   help="ADB 设备地址(留空=自动选首个 USB 真机;MuMu 填 127.0.0.1:16384)")
    p.add_argument("--adb-path", default=cfg["adb_path"], help="adb.exe 路径")
    p.add_argument("--ratio", type=float, default=cfg["ratio"],
                   help=f"滑动距离比例,默认 {cfg['ratio']}")
    p.add_argument("--swipe-mode", default=cfg["swipe_mode"],
                   help="滑动方式:auto(根据设备自动) | swipe(真机) | roll(MuMu)")
    p.add_argument("--output-dir", default=cfg["output_dir"],
                   help="CSV/JSON 报告输出目录")
    return p.parse_args()


def main():
    args = parse_args()
    # 去除命令行参数首尾可能误带的引号(如 --org-code "080501" 不影响,但 "080501" 会带引号)
    args.shop = args.shop.strip().strip('"').strip("'")
    args.org_code = args.org_code.strip().strip('"').strip("'")

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

    # CSV 增量写入:每采集到一条新评论立即写入文件并 flush,Ctrl+C 中断不丢数据
    csv_exporter = CSVExporter()
    csv_path = csv_exporter.open_incremental(
        shop_name=args.shop,
        org_code=args.org_code,
        output_dir=args.output_dir,
    )
    print(f"CSV 文件: {csv_path}(增量写入)")

    # 无限模式:通过识别"已折叠部分评价"文本判定到底;固定模式:按 args.scroll 滑
    FOLD_HINT = "已折叠部分评价"  # 评论列表到底时点评显示的提示文本
    screen_idx = 0
    while True:
        # 固定模式:达到屏数上限则停
        if not infinite and screen_idx >= args.scroll:
            break

        print(f"--- 第 {screen_idx + 1}{('屏' if infinite else f'/{args.scroll} 屏')} ---")

        # 1. dump 当前屏(复用于:到底检测 + 验证码检测 + 全文按钮定位)
        xml_str = adb.dump_ui()

        # 1a. 到底检测:评论列表底部出现"依据平台规则,已折叠部分评价"
        if FOLD_HINT in xml_str:
            print(f"  [到底] 识别到'{FOLD_HINT}'提示,评论列表已到底")
            break

        # 1b. 详情页检测:滑动后可能误进评论详情页(网络卡顿/列表项误触)
        #     详情页特征:同时有评论评分 + 店铺星级卡片,复用本次 dump 不额外消耗
        if adb.detect_review_detail_page(xml_str):
            print("  [详情页] 误进评论详情页,执行返回回到列表页")
            adb.back()
            adb.human_delay(1.0, 1.5)
            # 返回后重新 dump 作为后续操作的基准
            xml_str = adb.dump_ui()
            if FOLD_HINT in xml_str:
                print(f"  [到底] 返回后识别到'{FOLD_HINT}'提示,评论列表已到底")
                break

        # 1b. 滑动验证码检测(反扒随机弹出,复用本次 dump 不额外消耗)
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

        # 1c. 展开全文(长评论折叠处理)
        #     每次点击后重新 dump 获取最新坐标(展开后下方元素位置下移)
        #     筛选已排除"收起全文"(展开后按钮文本),不会重复点击同一按钮
        #     个别"全文"按钮可能点击无效(点评App bug),连续3次无效则放弃,继续下滑
        #     坐标校验:1.排除顶部15%安全区(防误触导航栏/Tab导致跳顶)
        #              2.Y坐标偏差过大则停止展开(页面可能已跳转至详情页)
        safe_y_min = int(h * 0.15)           # 顶部15%安全区下边界
        y_drift_max = int(h * 0.25)          # Y坐标偏差阈值(超过则判定页面跳转)
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
            # 排除顶部15%安全区内的按钮(防止误触导航栏/Tab导致列表跳顶)
            btns = [b for b in btns if b["center"][1] > safe_y_min]
            if not btns:
                break
            first_y = btns[0]["center"][1]
            if last_first_y is not None:
                y_diff = abs(first_y - last_first_y)
                # Y坐标偏差过大:页面可能已跳转(如误进详情页),停止展开
                if y_diff > y_drift_max:
                    print(f"  [展开] 按钮Y坐标偏差过大({y_diff}px > {y_drift_max}px),可能已离开列表,停止展开")
                    break
                # Y坐标偏差过小:按钮位置没变,点击无效(点评bug)
                if y_diff < 80:
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
            adb.tap(x, y, human=False)
            adb.human_delay(1.0, 2.0)
            expanded += 1
            # 重新 dump:展开后坐标全变,必须刷新
            xml_str = adb.dump_ui()
            # 检测是否误进评论详情页(小屏设备双击/坐标偏差导致)
            # 详情页特征:同时有评论卡片(评分) + 店铺卡片(星级)
            if adb.detect_review_detail_page(xml_str):
                print("  [展开] 误进评论详情页,执行返回回到列表页")
                adb.back()
                adb.human_delay(1.0, 1.5)
                xml_str = adb.dump_ui()
                break  # 本屏展开结束,用返回后的列表页 xml 继续提取

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

        # 3b. 商家回复日期获取:有商家回复的卡片,点击进入详情页获取回复日期
        #     与误入详情页检测不冲突:本步骤主动进入→获取→back()退出,下一屏循环开始时已在列表页
        #     点击策略:优先点"XX(商家)"标签节点(小而明确,位于回复块顶部,稳定可点击);
        #              标签找不到时回退到回复内容前缀匹配;多候选按 Y 升序依次尝试
        #     Y 上限保护:过滤屏幕底部 10%(避免误点"回复"按钮区域)
        #     失败容错:未进入详情页不调用 back()(仍在列表页),换下一个候选重试
        cards_with_reply = [c for c in cards if c.get("merchant_reply", "").strip()]
        if cards_with_reply:
            print(f"  [商家回复] {len(cards_with_reply)} 条有商家回复,进入详情页获取回复日期")
            _, screen_h = adb.get_screen_size()
            y_max = int(screen_h * 0.90)  # 底部 10% 为"回复"按钮区域,过滤
            for card in cards_with_reply:
                reply_text = card["merchant_reply"]
                prefix_m = re.match(r'^(?:商家回复|.+?\(商家\)|商家)\s*[:：]\s*', reply_text)
                # 1. 优先:搜索"XX(商家)"标签节点(小而明确,点击稳定)
                candidates = [b for b in adb.find_elements_by_text(xml_str, "商家")
                              if ("（商家）" in b["text"] or "(商家)" in b["text"])
                              and b["center"][1] < y_max]
                # 2. 回退:用回复内容前15字符匹配节点(标签找不到时)
                if not candidates and prefix_m:
                    search_text = reply_text[prefix_m.end():prefix_m.end() + 15]
                    candidates = [b for b in adb.find_elements_by_text(xml_str, search_text)
                                  if b["center"][1] < y_max]
                if not candidates:
                    print(f"  [商家回复] 未在XML中找到可点击的回复元素,跳过")
                    continue
                # 按 Y 升序:标签节点位于回复块顶部,优先点击
                candidates.sort(key=lambda b: b["center"][1])
                entered = False
                for btn in candidates:
                    x, y = btn["center"]
                    print(f"  [商家回复] 尝试点击 @ ({x}, {y}) text=[{btn['text'][:15]}]")
                    adb.tap(x, y, human=False)
                    adb.human_delay(1.5, 2.5)
                    detail_xml = adb.dump_ui()
                    if adb.detect_review_detail_page(detail_xml):
                        entered = True
                        reply_date = adb.extract_merchant_reply_date(detail_xml)
                        if reply_date:
                            card["merchant_reply_date"] = reply_date
                            print(f"  [商家回复] 回复日期: {reply_date}")
                        else:
                            print(f"  [商家回复] 已进入详情页,但未找到回复日期")
                        adb.back()  # 已进入详情页,返回列表页
                        adb.human_delay(1.0, 1.5)
                        break
                    # 未进入详情页:仍在列表页,不调用 back(),换下一个候选重试
                    print(f"  [商家回复] 本次点击未进入详情页,尝试下一个候选")
                    adb.human_delay(0.5, 1.0)
                if not entered:
                    print(f"  [商家回复] 所有候选均未进入详情页,跳过本条")

        before = len(summarizer.cards)
        result = {"cards": cards, "review_count": len(cards), "source": "native"}
        summarizer.add(result)
        after = len(summarizer.cards)
        new_count = after - before
        print(f"  [评价] 本屏 {len(cards)} 条,新增 {new_count} 条")
        # 增量写入:新增的卡片立即写入 CSV(去重后的新卡片在 summarizer.cards[before:after])
        for card in summarizer.cards[before:after]:
            csv_exporter.write_card(card)
        for card in cards:
            price_tag = f" 人均¥{card['avg_price']}" if card.get("avg_price") else ""
            print(f"    [{card['user']}] {card['date']} {card['score']}{price_tag}")
            print(f"      内容: {card['content'][:60]}...")
            if card["merchant_reply"]:
                print(f"      商家: {card['merchant_reply'][:60]}...")

        # 4. 上滑翻屏(到底判定已在第1步通过"已折叠部分评价"文本完成)
        screen_idx += 1
        # 固定模式最后一屏不滑
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

    # 关闭 CSV 文件(增量写入已在循环中完成)
    csv_exporter.close()
    print(f"CSV 报告: {csv_path}")

    # JSON 报告
    json_path = summarizer.save(shop_name=args.shop, output_dir=args.output_dir)
    print(f"JSON 报告: {json_path}")

    print("\n采集完成")


if __name__ == "__main__":
    main()
