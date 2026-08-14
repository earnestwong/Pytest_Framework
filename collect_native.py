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

    def get_reply_date_from_detail(candidates, target_user=""):
        """
        点击候选元素进入详情页,提取商家回复日期(含下滑重试)
        详情页回复日期可能在屏幕下方,首次提取为空时下滑一次重试
        :param target_user: 目标评论者用户名(非空时校验详情页评论者,
                            点错详情页则返回列表继续尝试下一个候选)
        :return (是否成功取得回复日期, 回复日期, 详情页评论者用户名)
        """
        for btn in candidates:
            x, y = btn["center"]
            print(f"  [商家回复] 尝试点击 @ ({x}, {y}) text=[{btn['text'][:15]}]")
            adb.tap(x, y, human=False)
            adb.human_delay(1.5, 2.5)
            detail_xml = adb.dump_ui()
            if not adb.detect_review_detail_page(detail_xml):
                print(f"  [商家回复] 本次点击未进入详情页,尝试下一个候选")
                adb.human_delay(0.5, 1.0)
                continue
            # 已进入详情页:记录评论者用户名(供调用方校验点进的是否目标评论)
            detail_user = adb.extract_detail_user(detail_xml)
            # 点错详情页(评论者不符):返回列表继续尝试其他候选,避免直接放弃
            if target_user and detail_user and not _user_match(detail_user, target_user):
                print(f"  [商家回复] 详情页评论者[{detail_user}]与目标[{target_user}]不符,返回继续尝试")
                adb.back()
                adb.human_delay(2.0, 3.0)
                continue
            # 提取回复日期;长详情页(多条用户评论)回复日期在下方,最多下滑3次重试
            reply_date = adb.extract_merchant_reply_date(detail_xml)
            w, h = adb.get_screen_size()
            for _ in range(3):
                if reply_date:
                    break
                print(f"  [商家回复] 详情页未找到回复日期,下滑重试")
                adb.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.3), duration_ms=700, human=False)
                adb.human_delay(1.0, 1.5)
                detail_xml = adb.dump_ui()
                reply_date = adb.extract_merchant_reply_date(detail_xml)
                if not detail_user:
                    detail_user = adb.extract_detail_user(detail_xml)
            adb.back()  # 返回列表页
            adb.human_delay(3.0, 4.0)  # 等待页面加载稳定,避免过渡动画误判
            # 验证是否已回到评论列表:用 ensure_on_review_list 完整校验
            # 仅靠 strict 判定(星级卡片+回复按钮)无法区分"商店主页"和"评论列表",
            # back() 过度时可能落到商店主页/全部评价列表,strict 返回 False 误放行,
            # 导致列表筛选丢失后继续采集混入好评。
            check_xml = adb.dump_ui()
            list_ok, check_xml = ensure_on_review_list(check_xml)
            if not list_ok:
                print(f"  [商家回复] back()后评论列表状态丢失,恢复失败")
            return True, reply_date, detail_user
        return False, "", ""

    def find_reply_candidates(xml, reply_text, y_max):
        """
        在XML中找商家回复的可点击候选元素,按Y升序返回
        策略:优先用回复内容前缀精确匹配(避免多条回复误匹配),
             匹配不到时回退到"XX(商家)"标签节点
        顶部(y<16%)为导航/搜索区,点击无效,排除
        """
        _, screen_h = adb.get_screen_size()
        y_min = int(screen_h * 0.16)
        prefix_m = re.match(r'^(?:商家回复|.+?[（(]商家[）)]|商家)\s*[:：]\s*', reply_text)
        # 1. 优先:用回复内容前15字符精确匹配(定位到具体哪条回复)
        if prefix_m:
            search_text = reply_text[prefix_m.end():prefix_m.end() + 15]
            candidates = [b for b in adb.find_elements_by_text(xml, search_text)
                          if y_min < b["center"][1] < y_max]
            if candidates:
                candidates.sort(key=lambda b: b["center"][1])
                return candidates
        # 2. 回退:搜索"XX(商家)"标签节点(列表页合并节点如"丰裕(商家): xxx")
        candidates = [b for b in adb.find_elements_by_text(xml, reply_text[:20])
                      if y_min < b["center"][1] < y_max]
        if candidates:
            candidates.sort(key=lambda b: b["center"][1])
            return candidates
        # 3. 最终回退:搜索"商家"关键词
        candidates = [b for b in adb.find_elements_by_text(xml, "商家")
                      if ("（商家）" in b["text"] or "(商家)" in b["text"])
                      and y_min < b["center"][1] < y_max]
        candidates.sort(key=lambda b: b["center"][1])
        return candidates

    def _reply_fingerprint(text: str) -> str:
        """
        规范化商家回复内容指纹:去空白后取前8字
        用于跨屏去重:同一条回复在不同屏可能被节点截断成长度不同的文本,
        用固定前缀作key,配合 _reply_same 的包含式比较兼容截断差异
        """
        return re.sub(r"\s+", "", text or "")[:8]

    def _reply_same(a: str, b: str) -> bool:
        """两个回复指纹是否同源(兼容截断:较短者是较长者的前缀)"""
        if not a or not b:
            return False
        return a in b or b in a

    def _user_match(a: str, b: str) -> bool:
        """两个用户名是否同源(互相包含,兼容细节差异);任一为空则不做校验"""
        a, b = (a or "").strip(), (b or "").strip()
        if not a or not b:
            return True
        return a in b or b in a

    def ensure_on_review_list(xml_str, max_back=2):
        """
        确认当前页面仍在评论列表,不在则尝试返回恢复
        - 严格详情页(星级卡片+回复按钮/商家标签):back()返回
        - 商店主页(星级卡片+评价(N)tab,无评论筛选栏):back()尝试恢复
        :return (是否确认在列表, 最新xml)
        """
        for _ in range(max_back + 1):
            if adb.detect_review_detail_page_strict(xml_str):
                print(f"  [定位] 仍处于评论详情页,执行返回回到列表")
                adb.back()
                adb.human_delay(3.0, 4.0)
                xml_str = adb.dump_ui()
                continue
            # 商店主页判定:星级卡片 + "评价(N)" tab,且无评论筛选栏(全部/差评/中评/好评)
            if adb.detect_review_detail_page(xml_str):
                items = adb.extract_all_text(xml_str, min_len=1)
                has_eval_tab = any(re.match(r"^评价\s*\(\d+\)$", it["text"]) for it in items)
                has_filter = any(it["text"] in ("全部", "差评", "中评", "好评") for it in items)
                if has_eval_tab and not has_filter:
                    print(f"  [定位] 已跳出到商店主页,执行返回恢复")
                    adb.back()
                    adb.human_delay(3.0, 4.0)
                    xml_str = adb.dump_ui()
                    continue
            return True, xml_str
        return False, xml_str

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
        #     注意:不能用 strict 判定(星级+回复按钮/商家标签)——差评列表页本身
        #     就含"丰裕(商家)"标签,strict 会把列表页误判为详情页,导致误 back() 退出列表。
        #     用宽松判定(星级卡片等任一特征),back()后还需二次确认避免过渡动画误判
        if adb.detect_review_detail_page(xml_str):
            # 二次确认:等待页面加载稳定后再检测,排除过渡动画干扰
            adb.human_delay(1.5, 2.0)
            xml_str = adb.dump_ui()
            if adb.detect_review_detail_page(xml_str):
                print("  [详情页] 误进评论详情页,执行返回回到列表页")
                adb.back()
                adb.human_delay(2.0, 3.0)
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
            # 注意:不能用 strict 判定(列表页含"丰裕(商家)"标签会误判详情页),
            # 用宽松判定 + 二次确认
            if adb.detect_review_detail_page(xml_str):
                adb.human_delay(1.5, 2.0)
                xml_str = adb.dump_ui()
                if adb.detect_review_detail_page(xml_str):
                    print("  [展开] 误进评论详情页,执行返回回到列表页")
                    adb.back()
                    adb.human_delay(2.0, 3.0)
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

        # 3a. 差评筛选状态守卫:差评列表的评分只可能是负面档位,
        #     若出现好评/中性评分,说明已跳出差评筛选(如误触Tab/过度back()),
        #     列表混入全部评价,需尝试恢复筛选,恢复失败则终止采集
        POSITIVE_SCORES = {"一般", "好评", "很好", "满意", "超赞",
                           "还行", "不错", "非常满意", "超预期", "很棒", "还可以"}
        stray_scores = [c for c in cards if c.get("score") in POSITIVE_SCORES]
        if stray_scores:
            stray_users = "、".join(f"{c['user'] or '?'}({c['score']})" for c in stray_scores[:3])
            print(f"  [守卫] 检测到非差评评分[{stray_users}],疑似已跳出差评列表")
            # 恢复策略(不盲目 back(),逐级判断页面层级):
            #   1. 仍在评论列表(有筛选栏):点击"差评"Tab 恢复筛选
            #   2. 在详情页/商店主页:用 ensure_on_review_list back() 回列表后再试
            #   3. 页面已无评论特征(如搜索页/首页):不可恢复,直接终止
            restored = False
            for attempt in range(2):
                # 先尝试点击顶部筛选栏"差评"Tab 恢复(筛选栏在顶部 y<320 区域)
                tab_items = [it for it in adb.extract_all_text(xml_str, min_len=1)
                             if it["text"] == "差评" and it["bounds"][1] < 320]
                if tab_items:
                    bx = tab_items[0]["bounds"]
                    cx, cy = (bx[0] + bx[2]) // 2, (bx[1] + bx[3]) // 2
                    print(f"  [守卫] 点击'差评'Tab @ ({cx}, {cy}),第{attempt+1}次尝试")
                    adb.tap(cx, cy, human=False)
                    adb.human_delay(3.0, 4.0)
                    xml_str = adb.dump_ui()
                    new_cards = parser.parse(adb.extract_all_text(xml_str, min_len=1))
                    new_stray = [c for c in new_cards if c.get("score") in POSITIVE_SCORES]
                    if not new_stray:
                        restored = True
                        cards = new_cards
                        print(f"  [守卫] 差评筛选已恢复,继续采集")
                        break
                    print(f"  [守卫] 点击'差评'Tab后仍有非差评评分,继续下一级恢复")
                    continue
                # 无筛选栏:可能是详情页/商店主页(back()可回到列表)或搜索页(不可恢复)
                print(f"  [守卫] 页面无'差评'Tab,调用列表状态恢复(只处理详情页/商店主页)")
                list_ok, xml_str = ensure_on_review_list(xml_str)
                if not list_ok:
                    print(f"  [守卫] 页面已无评论列表特征,不可恢复")
                    break
                # 恢复后重新解析评分检查
                new_cards = parser.parse(adb.extract_all_text(xml_str, min_len=1))
                new_stray = [c for c in new_cards if c.get("score") in POSITIVE_SCORES]
                if not new_stray:
                    restored = True
                    cards = new_cards
                    print(f"  [守卫] 已回到差评列表,继续采集")
                    break
                print(f"  [守卫] 恢复后仍有非差评评分,继续尝试")
            if not restored:
                print(f"  [守卫] 无法恢复差评筛选,终止采集(防止混入好评)")
                break

        # 3b. 孤立商家回复处理:第一个日期锚点之前的商家回复,属于上一屏最后一条评论
        #     场景:上一条评论全文展开后很长,上划后日期/用户名移出屏幕,
        #     屏幕上只剩内容残余+图片+商家回复,然后是下一条评论。
        #     这条商家回复无法被parser归入当前卡片(无对应日期锚点),
        #     点击进入详情页获取回复日期,补到上一屏最后一条匹配的卡片上
        # 去重:仅用"已归属到任意卡片"判断(回复内容前8字指纹,包含式兼容截断);
        #     不做"已点击过"记录,避免同前缀的不同回复被误拦(不同评论的回复常以"亲爱的顾客"开头)
        if hasattr(parser, "leading_replies") and parser.leading_replies:
            print(f"  [商家回复] 检测到 {len(parser.leading_replies)} 条孤立商家回复(属于上一屏评论)")
            _, screen_h = adb.get_screen_size()
            y_max = int(screen_h * 0.90)
            lead_items = getattr(parser, "leading_reply_items", None) or []
            for k, reply_text in enumerate(parser.leading_replies):
                # 回复内容指纹(去空白取前8字)
                prefix_m = re.match(r'^(?:商家回复|.+?[（(]商家[）)]|商家)\s*[:：]\s*', reply_text)
                content = reply_text[prefix_m.end():] if prefix_m else reply_text
                fp = _reply_fingerprint(content)
                if not fp:
                    continue
                # 该回复已归属到任意卡片则跳过(防跨屏残留被误补到别的卡片)
                if any(_reply_same(fp, _reply_fingerprint(c.get("merchant_reply") or ""))
                       for c in summarizer.cards):
                    print(f"  [商家回复] 该孤立回复已补全到评论,跳过")
                    continue
                # 精确定位:优先用解析器记录的回复节点坐标(避免模糊匹配误点其他回复)
                candidates = []
                if k < len(lead_items):
                    bx1, by1, bx2, by2 = lead_items[k]["bounds"]
                    cy = (by1 + by2) // 2
                    if cy > int(h * 0.16):  # 顶部导航/搜索区点击无效,放弃精确定位
                        candidates = [{"text": lead_items[k]["text"],
                                       "center": ((bx1 + bx2) // 2, cy)}]
                if not candidates:
                    candidates = find_reply_candidates(xml_str, reply_text, y_max)
                if not candidates:
                    print(f"  [商家回复] 未在XML中找到孤立回复元素,跳过")
                    continue
                entered, reply_date, detail_user = get_reply_date_from_detail(candidates)
                if not entered:
                    print(f"  [商家回复] 孤立回复所有候选均未进入详情页,跳过")
                    continue
                if not reply_date:
                    print(f"  [商家回复] 孤立回复已进入详情页,但未找到回复日期")
                    continue
                # 补到上一屏最后一条还没有商家回复的卡片(孤立回复必定紧跟该卡片内容)
                target = None
                for c in reversed(summarizer.cards):
                    if not (c.get("merchant_reply") or "").strip():
                        target = c
                        break
                if target is None:
                    print(f"  [商家回复] 无上一屏卡片可补全,跳过")
                    continue
                # 详情页评论者校验:点进的详情页须与目标卡片同人,防止把别的回复误补过来
                if detail_user and not _user_match(detail_user, target.get("user")):
                    print(f"  [商家回复] 详情页评论者[{detail_user}]与目标[{target.get('user')}]不符,放弃补全")
                    continue
                target["merchant_reply"] = reply_text
                target["merchant_reply_date"] = reply_date
                print(f"  [商家回复] 补全上一屏评论: {reply_date} -> [{target['user']}] {target['date']}")
            # 处理孤立回复后确认仍在评论列表(详情页back()可能改变页面状态)
            list_ok, xml_str = ensure_on_review_list(xml_str)
            if not list_ok:
                print(f"  [定位] 评论列表状态丢失,恢复失败,终止采集")
                break

        # 3c. 用户名缺失补救:滑动过快可能导致用户名滚出上边界未被抓到
        #     强制多次小幅下滑,直到获取用户名或达到最大重试次数(不保存空用户名)
        missing_user = [c for c in cards if not c.get("user", "").strip()]
        if missing_user:
            max_retries = 5  # 最大重试次数,每次下滑 ratio 0.08
            w, h = adb.get_screen_size()
            for retry in range(max_retries):
                still_missing = [c for c in missing_user if not c.get("user", "").strip()]
                if not still_missing:
                    break
                print(f"  [补救] 第{retry+1}/{max_retries}次: {len(still_missing)} 条评论用户名缺失,往下滑重新抓取")
                y1 = int(h * 0.5)
                y2 = int(h * 0.58)  # 往下滑 8%,把上方滚出的用户名露出来
                adb.swipe(w // 2, y1, w // 2, y2, duration_ms=800, human=False)
                adb.human_delay(1.0, 1.5)
                # 重新 dump + 提取 + 解析
                retry_xml = adb.dump_ui()
                new_items = adb.extract_all_text(retry_xml, min_len=1)
                new_cards = parser.parse(new_items)
                # 按 date + content前20 匹配,补全用户名
                for old_card in still_missing:
                    if old_card.get("user", "").strip():
                        continue
                    old_key = f"{old_card.get('date', '')}|{old_card.get('content', '')[:20]}"
                    for new_card in new_cards:
                        new_key = f"{new_card.get('date', '')}|{new_card.get('content', '')[:20]}"
                        if new_key == old_key and new_card.get("user", "").strip():
                            old_card["user"] = new_card["user"]
                            print(f"  [补救] 补全用户名: [{new_card['user']}] {old_card['date']}")
                            break
            # 重试结束后仍缺失的,打印警告(不保存空用户名,跳过该条)
            final_missing = [c for c in missing_user if not c.get("user", "").strip()]
            if final_missing:
                print(f"  [补救] {len(final_missing)} 条评论经{max_retries}次重试仍未获取用户名,跳过(不保存空用户名)")
                for c in final_missing:
                    c["_skip_empty_user"] = True  # 标记跳过

        # 3d. 商家回复日期获取:有商家回复的卡片,点击进入详情页获取回复日期
        #     点击策略:优先用解析器记录的回复节点坐标(精确定位);回退到模糊匹配
        #     详情页回复日期可能在屏幕下方,首次提取为空时下滑一次重试
        #     注意:本地cards的修改不会自动同步到summarizer.cards(去重时本地副本被丢弃),
        #     需通过dedup_key找到summarizer中的对应卡片同步修改
        #     已取到回复日期的卡片跳过,避免同一条回复跨屏反复点击
        cards_with_reply = [
            c for c in cards
            if c.get("merchant_reply", "").strip()
            and not (c.get("merchant_reply_date") or "").strip()
        ]
        if cards_with_reply:
            print(f"  [商家回复] {len(cards_with_reply)} 条有商家回复,进入详情页获取回复日期")
            _, screen_h = adb.get_screen_size()
            y_max = int(screen_h * 0.90)
            for card in cards_with_reply:
                # 汇总中该卡片已有回复日期则跳过(3a补全或上一屏已获取)
                dedup_key = summarizer._dedup_key(card)
                if any(sc.get("merchant_reply_date")
                       and summarizer._dedup_key(sc) == dedup_key
                       for sc in summarizer.cards):
                    print(f"  [商家回复] 该卡片回复日期已获取,跳过")
                    continue
                # 精确定位:优先用解析器记录的回复节点坐标
                candidates = []
                r_bounds = card.get("merchant_reply_bounds")
                if r_bounds:
                    bx1, by1, bx2, by2 = r_bounds
                    cy = (by1 + by2) // 2
                    if cy < y_max and cy > int(h * 0.16):
                        candidates = [{"text": card["merchant_reply"],
                                       "center": ((bx1 + bx2) // 2, cy)}]
                if not candidates:
                    candidates = find_reply_candidates(xml_str, card["merchant_reply"], y_max)
                if not candidates:
                    print(f"  [商家回复] 未在XML中找到可点击的回复元素,跳过")
                    continue
                entered, reply_date, detail_user = get_reply_date_from_detail(candidates, card.get("user"))
                if not entered:
                    print(f"  [商家回复] 所有候选均未进入详情页,跳过本条")
                    continue
                # 详情页评论者校验:点进的详情页须与卡片同人,防止取到别的回复的日期
                if detail_user and not _user_match(detail_user, card.get("user")):
                    print(f"  [商家回复] 详情页评论者[{detail_user}]与卡片[{card.get('user')}]不符,放弃日期")
                    continue
                if not reply_date:
                    print(f"  [商家回复] 已进入详情页,但未找到回复日期")
                    continue
                card["merchant_reply_date"] = reply_date
                print(f"  [商家回复] 回复日期: {reply_date}")
                # 同步到summarizer.cards中的对应卡片(去重后保留的是旧卡片,需同步修改)
                for sc in summarizer.cards:
                    if summarizer._dedup_key(sc) == dedup_key:
                        sc["merchant_reply"] = card["merchant_reply"]
                        sc["merchant_reply_date"] = reply_date
                        break
            # 处理回复后确认仍在评论列表(详情页back()可能改变页面状态)
            list_ok, xml_str = ensure_on_review_list(xml_str)
            if not list_ok:
                print(f"  [定位] 评论列表状态丢失,恢复失败,终止采集")
                break

        # 过滤掉标记跳过的卡片(空用户名且重试失败)
        valid_cards = [c for c in cards if not c.get("_skip_empty_user")]
        # 回复节点坐标仅用于点击取日期,不写入报告
        for c in valid_cards:
            c.pop("merchant_reply_bounds", None)
        skipped = len(cards) - len(valid_cards)
        before = len(summarizer.cards)
        result = {"cards": valid_cards, "review_count": len(valid_cards), "source": "native"}
        summarizer.add(result)
        after = len(summarizer.cards)
        new_count = after - before
        print(f"  [评价] 本屏 {len(valid_cards)} 条,新增 {new_count} 条" + (f"(跳过{skipped}条空用户名)" if skipped else ""))
        # 每屏结束用 summarizer.cards 重写 CSV(确保3a/3c补全的回复日期写入,中断不丢数据)
        csv_exporter.rewrite_all(summarizer.cards)
        for card in valid_cards:
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

    # 用 summarizer.cards 重写 CSV(补全孤立商家回复日期等后续获取的字段)
    csv_exporter.rewrite_all(summarizer.cards)
    csv_exporter.close()
    print(f"CSV 报告: {csv_path}(已更新含回复日期补全)")

    # JSON 报告
    json_path = summarizer.save(shop_name=args.shop, output_dir=args.output_dir)
    print(f"JSON 报告: {json_path}")

    print("\n采集完成")


if __name__ == "__main__":
    main()
