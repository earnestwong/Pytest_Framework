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
        "adb_server_port": "",
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
    p.add_argument("--adb-port", dest="adb_port", default=cfg["adb_server_port"],
                   help="adb server 端口(留空=默认 5037;被占用时可指定如 5045)")
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
        adb = ADBHelper(device_serial=args.device, adb_path=args.adb_path,
                        server_port=args.adb_port or None)
        adb.connect(args.device)
    else:
        # USB 真机:不传 device_serial,自动选首个设备
        adb = ADBHelper(device_serial=args.device or None, adb_path=args.adb_path,
                        server_port=args.adb_port or None)
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

    def get_reply_date_from_detail(candidates, card=None):
        """
        点击候选元素进入详情页,提取商家回复日期(含下滑重试)
        规则:点开商家回复后,详情页顶部能抓到该评论的用户名(权威归属)。
        :param card: 目标卡片(用于复合身份校验)。
                     传入时,对每个进入详情页的候选做复合匹配,
                     匹配失败则 back() 继续下一个候选(精确定位坐标可能指错回复,
                     多条回复常以"亲爱的顾客"开头,需逐个尝试找到真正归属的那条);
                     不传时,点开第一个进入详情页的候选即返回
                     (向后兼容3b孤立回复补全场景,该场景在调用方对候选卡片列表做匹配)。
        :return (是否进入详情页, 回复日期, 详情页评论者信息dict)
                 dict = {'user': ..., 'date': ..., 'content_prefix': ...};未进入详情页返回 None
        """
        for btn in candidates:
            x, y = btn["center"]
            # 候选落在屏幕顶部/底部导航区时点击无效(点评底部Tab/顶部状态栏拦截):
            # 该回复可能只露出屏幕边缘一条,中心点在导航栏上,直接 tap 会被拦截。
            # 先小幅滚动把回复露到可点击区,再重新定位点击。
            # 注意:精确匹配 y 范围放宽到 3%~98% 是为了跨屏残留回复能命中,
            # 但导航区候选必须滚动露出后才能点击,否则白白浪费一次候选尝试。
            w0, h0 = adb.get_screen_size()
            if y > int(h0 * 0.90) or y < int(h0 * 0.10):
                print(f"  [商家回复] 候选@({x},{y})位于导航区,小幅滚动露出后重新定位")
                if y > int(h0 * 0.90):
                    # 底部:下滑(内容上移)露出底部回复
                    adb.swipe(w0 // 2, int(h0 * 0.80), w0 // 2, int(h0 * 0.55), duration_ms=600, human=False)
                else:
                    # 顶部:上滑(内容下移)露出顶部回复
                    adb.swipe(w0 // 2, int(h0 * 0.45), w0 // 2, int(h0 * 0.70), duration_ms=600, human=False)
                adb.human_delay(1.0, 1.5)
                detail_xml = adb.dump_ui()
                # 用候选文本前40字重新定位(与find_reply_candidates精确匹配一致,
                # 避免同屏多条相似前缀回复时误选其他评论的回复)
                reloc = [b for b in adb.find_elements_by_text(detail_xml, btn["text"][:40])
                         if int(h0 * 0.10) < b["center"][1] < int(h0 * 0.90)]
                if not reloc:
                    print(f"  [商家回复] 滚动后未找到该候选,尝试下一个候选")
                    continue
                x, y = reloc[0]["center"]
                print(f"  [商家回复] 滚动后重新定位 @ ({x}, {y}) text=[{btn['text'][:15]}]")
            print(f"  [商家回复] 尝试点击 @ ({x}, {y}) text=[{btn['text'][:15]}]")
            adb.tap(x, y, human=False)
            adb.human_delay(1.5, 2.5)
            detail_xml = adb.dump_ui()
            if not adb.detect_review_detail_page(detail_xml):
                print(f"  [商家回复] 本次点击未进入详情页,尝试下一个候选")
                adb.human_delay(0.5, 1.0)
                continue
            w, h = adb.get_screen_size()
            # 详情页进入时可能自动滚动到最下方商家回复处(评论者头部在屏外)。
            # 先提取回复相关字段(此刻回复区可见,不依赖滚动位置),再处理身份信息。
            # 注意:商家回复区是滚动触发的懒加载——长评论详情页(带图/语音/超长内容)
            # 进入时不会自动滚到回复处,此时步骤1 reply_date 提取为空属正常,
            # 后续步骤7(下滑重试)会触发懒加载并兜底提取,无需在步骤1前轮询等待。
            reply_date = adb.extract_merchant_reply_date(detail_xml)
            detail_info = adb.extract_detail_comment_info(detail_xml)
            detail_info["merchant_reply"] = adb.extract_detail_merchant_reply(detail_xml)
            # 身份信息缺失(user 为空,通常因详情页已滚至回复区)时,
            # 向上滚动(内容下移)露出评论者头部再提取。
            # 注意:上滚后回复区可能滚出屏幕,但回复字段已在上方提取,不受影响
            # 长评论详情页(内容+回复接近整屏)进入时自动滚到回复区底部,
            # 一次上滚只露出发布时间,用户名仍在屏外,需循环上滚直到露出用户名。
            # 循环条件分场景:
            # - card is not None(常规复合匹配):只看 user。date 缺失不影响复合匹配
            #   (回复全文/内容前缀都能通过),仅为 date 触发上滑会让商家回复区
            #   滚出屏幕,导致 reply_date 提取失败时永久丢失 merchant_reply_date。
            # - card is None(孤立回复场景):检查 user+date+score 三者齐全。
            #   孤立回复拼回卡片需要完整身份信息(用户名+发布日期+评分)才能跨屏补回,
            #   任一缺失都会导致拼回失败;且此时 reply_date 已在上方提取保存,
            #   上滑使回复区滚出屏幕不影响 merchant_reply_date。
            for _scroll in range(3):
                _has_user = (detail_info.get("user") or "").strip()
                if card is not None:
                    if _has_user:
                        break
                else:
                    if _has_user \
                            and (detail_info.get("date") or "").strip() \
                            and (detail_info.get("score") or "").strip():
                        break
                # 守卫:上滑前已不在详情页(可能已滚回列表页),身份信息不可能再补全,
                # 停止上滑,避免下次上滑后 user 被列表页噪声(如"规则")污染。
                # 孤立场景三者齐全的强条件可能把页面滚出详情页,此守卫兜底。
                if not adb.detect_review_detail_page(detail_xml):
                    print(f"  [商家回复] 已不在详情页,停止上滑并保留已有身份信息")
                    break
                print(f"  [商家回复] 详情页用户名缺失(可能已滚至回复区),向上滚动露出评论者头部({_scroll + 1}/3)")
                # 上滚(内容下移)露出评论者头部:小步慢速,避免惯性滚动把头部又甩出屏幕
                adb.swipe(w // 2, int(h * 0.40), w // 2, int(h * 0.80), duration_ms=900, human=False)
                adb.human_delay(1.0, 1.5)
                detail_xml = adb.dump_ui()
                # 上滑后若已滚出详情页(回到列表页),丢弃这次滚出后的提取结果,
                # 保留进入详情页时/上次上滑后的合法身份信息,防止被列表噪声污染
                if not adb.detect_review_detail_page(detail_xml):
                    print(f"  [商家回复] 上滑后已滚出详情页,停止上滑并保留已有身份信息")
                    break
                new_info = adb.extract_detail_comment_info(detail_xml)
                detail_info = {
                    "user": new_info.get("user", "") or detail_info.get("user", ""),
                    "date": new_info.get("date", "") or detail_info.get("date", ""),
                    "score": new_info.get("score", "") or detail_info.get("score", ""),
                    "content_prefix": new_info.get("content_prefix", "")
                    or detail_info.get("content_prefix", ""),
                    "merchant_reply": detail_info.get("merchant_reply")
                    or adb.extract_detail_merchant_reply(detail_xml),
                }
            # 复合身份校验(传了card时):匹配失败直接back继续下一个候选,
            # 不浪费下滑重试(用户名都不对,找日期无意义)
            if card is not None:
                ok, reason = _composite_match(detail_info, card)
                if not ok:
                    print(f"  [商家回复] 详情页与卡片身份不符: {reason},尝试下一个候选")
                    adb.back()
                    adb.human_delay(3.0, 4.0)
                    check_xml = adb.dump_ui()
                    list_ok, check_xml = ensure_on_review_list(check_xml)
                    if not list_ok:
                        print(f"  [商家回复] back()后评论列表状态丢失,恢复失败")
                    continue
            if not reply_date:
                reply_date = adb.extract_merchant_reply_date(detail_xml)
            w, h = adb.get_screen_size()
            # 0. 商家回复区加载失败(详情页显示"点击重试"占位)时,先点击重试重新加载
            #    否则日期/回复段都为空,会误判为"点错"而丢失本可拿到的日期
            for _ in range(2):
                if reply_date:
                    break
                retry_btns = [b for b in adb.find_elements_by_text(detail_xml, "点击重试")
                              if int(h * 0.15) < b["center"][1] < int(h * 0.9)]
                if not retry_btns:
                    break
                print(f"  [商家回复] 详情页回复区加载失败,点击'点击重试'重新加载")
                adb.tap(*retry_btns[0]["center"], human=False)
                adb.human_delay(2.0, 3.0)
                detail_xml = adb.dump_ui()
                reply_date = adb.extract_merchant_reply_date(detail_xml)
                if not detail_info.get("merchant_reply"):
                    detail_info["merchant_reply"] = adb.extract_detail_merchant_reply(detail_xml)
            # 1. 详情页确实无商家回复段(非加载失败:点中评论图片进大图页,或该评论无回复):
            #    继续下滑重试无意义,直接back尝试下一个候选
            if not reply_date and not detail_info.get("merchant_reply") \
                    and "（商家）" not in detail_xml and "(商家)" not in detail_xml \
                    and "点击重试" not in detail_xml:
                print(f"  [商家回复] 详情页无商家回复段(疑似点错),尝试下一个候选")
                adb.back()
                adb.human_delay(3.0, 4.0)
                check_xml = adb.dump_ui()
                list_ok, check_xml = ensure_on_review_list(check_xml)
                if not list_ok:
                    print(f"  [商家回复] back()后评论列表状态丢失,恢复失败")
                continue
            for _ in range(3):
                if reply_date:
                    break
                print(f"  [商家回复] 详情页未找到回复日期,下滑重试")
                adb.swipe(w // 2, int(h * 0.7), w // 2, int(h * 0.3), duration_ms=700, human=False)
                adb.human_delay(1.0, 1.5)
                detail_xml = adb.dump_ui()
                reply_date = adb.extract_merchant_reply_date(detail_xml)
                # 下滑后用户名/内容前缀可能滚出屏幕,只在原值缺失时重新提取
                if not detail_info.get("user") or not detail_info.get("content_prefix"):
                    new_info = adb.extract_detail_comment_info(detail_xml)
                    detail_info = {
                        "user": detail_info.get("user") or new_info.get("user", ""),
                        "date": detail_info.get("date") or new_info.get("date", ""),
                        "score": detail_info.get("score") or new_info.get("score", ""),
                        "content_prefix": detail_info.get("content_prefix") or new_info.get("content_prefix", ""),
                        "merchant_reply": detail_info.get("merchant_reply")
                        or adb.extract_detail_merchant_reply(detail_xml),
                    }
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
            return True, reply_date, detail_info
        return False, "", None

    def find_reply_candidates(xml, reply_text, y_max, near_y=None):
        """
        在XML中找商家回复的可点击候选元素
        策略:优先用回复内容前缀精确匹配(避免多条回复误匹配),
             匹配不到时回退到"XX(商家)"标签节点
        顶部(y<16%)为导航/搜索区,点击无效,排除
        :param near_y: 期望回复所在y坐标(目标卡片内容底部)。
                       多条同前缀回复(如都以"亲爱的顾客"开头)时,
                       优先选空间上紧邻该卡片内容下方的那条
                       (规则:商家回复永远对应它上面紧挨的那条用户评论)
        """
        _, screen_h = adb.get_screen_size()
        y_min = int(screen_h * 0.16)

        def _sort_key(b):
            # 优先按与 near_y 的距离排序(空间紧邻卡片),无 near_y 时按 y 升序
            if near_y is not None:
                return (abs(b["center"][1] - near_y), b["center"][1])
            return (b["center"][1],)

        prefix_m = re.match(r'^(?:商家回复|.+?[（(]商家[）)]|商家)\s*[:：]\s*', reply_text)
        # 1. 优先:用回复内容前40字精确匹配(定位到具体哪条回复)
        #    多个回复常以"亲爱的顾客/尊敬的顾客"开头,15字不足以区分,
        #    40字后不同回复内容差异明显,能精确定位到正确回复节点
        #    注意:长评论(内容+图片+回复接近一屏)跨屏时,回复可能残留在屏幕
        #    顶部(y<16%)或底部(y>90%),此时严格 y_min/y_max 过滤会把该回复排除,
        #    导致精确匹配0候选,回退模糊匹配时误点到同屏其他评论的回复。
        #    精确匹配用的搜索词是回复全文前40字(如"我们非常重视您的意见"),
        #    导航/搜索/底部操作栏都不会出现该文本,故 y 范围放宽到 3%~98%
        #    (仅排除状态栏与底部导航),允许跨屏残留的回复命中。
        if prefix_m:
            search_text = reply_text[prefix_m.end():prefix_m.end() + 40]
            y_lo = int(screen_h * 0.03)
            y_hi = int(screen_h * 0.98)
            candidates = [b for b in adb.find_elements_by_text(xml, search_text)
                          if y_lo < b["center"][1] < y_hi
                          and "语音评价" not in b["text"]
                          and "图片" not in b["text"] and "播放" not in b["text"]]
            if candidates:
                candidates.sort(key=_sort_key)
                return candidates
        # 2. 回退:搜索"XX(商家)"标签节点(列表页合并节点如"丰裕(商家): xxx")
        candidates = [b for b in adb.find_elements_by_text(xml, reply_text[:20])
                      if y_min < b["center"][1] < y_max
                      and "语音评价" not in b["text"]
                      and "图片" not in b["text"] and "播放" not in b["text"]]
        if candidates:
            candidates.sort(key=_sort_key)
            return candidates
        # 3. 最终回退:搜索"商家"关键词
        candidates = [b for b in adb.find_elements_by_text(xml, "商家")
                      if ("（商家）" in b["text"] or "(商家)" in b["text"])
                      and "语音评价" not in b["text"]
                      and "图片" not in b["text"] and "播放" not in b["text"]
                      and y_min < b["center"][1] < y_max]
        candidates.sort(key=_sort_key)
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

    def _composite_match(detail_info, card):
        """
        复合身份匹配:精确匹配 用户名 + 评论时间 + 商家回复全文,判断详情页与列表页卡片是否同人

        简化原则(按需求):正常采集路径卡片都有商家回复内容,
          商家回复全文是最强归属标识——详情页回复内容与卡片回复内容一致即确认点对;
          全文不匹配则拒绝,避免"仅日期相同"导致误配(同一屏相邻评论日期常相同)。
          3b孤立回复补全场景卡片无回复内容时,退回 用户名+评论日期+内容前缀 判断。

        匹配规则(优先级从高到低):
          1. 用户名明确不同(双非匿名) → 直接拒绝
          2. 商家回复全文匹配 → 通过(详情页回复提取成功时强校验,不一致即拒绝)
          3. 内容前缀匹配 → 通过(仅卡片无回复/详情页回复提取失败时使用)
          4. 日期匹配 → 仅当用户名关系可接受(双匿名/同名/详情页用户名缺失)时通过;
             匿名 vs 非匿名不同名时即使日期相同也拒绝(防误配)
          5. 匿名同名 → 拒绝(宁缺毋滥)
          6. 非匿名同名 → 放行

        :param detail_info: 详情页提取的 {'user', 'date', 'content_prefix', 'merchant_reply'}
        :param card: 列表页卡片 {'user', 'date', 'content', 'merchant_reply', ...}
        :return (是否匹配, 不匹配原因)
        """
        if not detail_info:
            return True, ""  # 详情页信息缺失,放行(向后兼容)

        d_user = (detail_info.get("user") or "").strip()
        d_date = (detail_info.get("date") or "").strip()
        d_content = (detail_info.get("content_prefix") or "").strip()
        d_reply = (detail_info.get("merchant_reply") or "").strip()
        c_user = (card.get("user") or "").strip()
        c_date = (card.get("date") or "").strip()
        c_content = (card.get("content") or "").strip()
        c_reply = (card.get("merchant_reply") or "").strip()

        def _is_anon(name):
            """匿名用户:空串或含"匿名"字样(大众点评匿名用户名恒为"匿名用户")"""
            return not name or "匿名" in name

        _strip_reply_label = re.compile(r'^(?:商家回复|.+?[（(]商家[）)]|商家)\s*[:：]\s*')

        # 1. 用户名明确不同(非匿名) → 直接拒绝
        if (d_user and c_user
                and not _is_anon(d_user) and not _is_anon(c_user)
                and d_user != c_user
                and d_user not in c_user and c_user not in d_user):
            return False, f"用户名不同[{d_user}≠{c_user}]"

        # 2. 商家回复全文匹配(卡片有回复内容时) → 通过
        #    去前缀标签("XX(商家):")与空白/占位符后做包含匹配,容忍列表页截断差异
        if c_reply:
            norm_d = re.sub(r"[\s\uFFFC\uFFFD]+", "", _strip_reply_label.sub("", d_reply))
            norm_c = re.sub(r"[\s\uFFFC\uFFFD]+", "", _strip_reply_label.sub("", c_reply))
            if norm_d:
                # 详情页回复提取成功 → 强校验,全文不一致即拒绝(宁缺毋滥)
                if len(norm_d) >= 10 and len(norm_c) >= 10 \
                        and (norm_d in norm_c or norm_c in norm_d):
                    return True, ""
                return False, f"商家回复全文不匹配(详情[{norm_d[:15]}]vs卡片[{norm_c[:15]}])"
            # norm_d 为空:详情页回复提取失败,降级到规则3/4(用户名+日期+内容前缀)

        # 3. 内容前缀匹配 → 通过(去空白/占位符取前15字比较,容忍截断/省略号差异)
        #    注意:"￼"(U+FFFC,图片/emoji占位符)在详情页提取时可能被去掉、
        #    卡片解析时保留,导致[:15]截断后字符错位,必须先去除再比较
        if d_content and c_content:
            norm_d = re.sub(r"[\s￼]+", "", d_content)[:15]
            norm_c = re.sub(r"[\s￼]+", "", c_content)[:15]
            if norm_d and norm_c and (norm_d in norm_c or norm_c in norm_d):
                return True, ""

        # 4. 日期匹配 → 仅当用户名关系可接受时通过("发布于"前缀已在提取时去除)
        if d_date and c_date:
            if d_date == c_date or d_date in c_date or c_date in d_date:
                if (not d_user  # 详情页用户名缺失
                        or (_is_anon(d_user) and _is_anon(c_user))  # 双匿名
                        or (d_user and c_user and d_user == c_user)):  # 同名(含非匿名)
                    return True, ""
                return False, f"用户名不一致[{d_user}≠{c_user}]但日期相同,拒绝(防误配)"
            # 日期明确不同 → 拒绝。日期是评论的强标识(同屏相邻评论日期也可能不同),
            # 详情页日期与卡片日期不同即非同一条评论,直接拒绝防止跨屏误归属
            # (如把上一条评论的孤立回复错补到下一张卡片上)
            return False, f"日期不同[{d_date}≠{c_date}],拒绝(防误配)"

        # 5. 匿名同名 → 拒绝(用户名恒为"匿名用户",无内容/日期/回复佐证即无法区分归属)
        #    详情页信息不完整(如只有匿名用户名)时也在此拒绝,宁缺毋滥——
        #    否则会误接受其他评论详情页的回复日期(如把下一条评论的日期归属到本卡片)
        if _is_anon(d_user) and _is_anon(c_user):
            return False, f"匿名同名但内容/日期/回复均不匹配(内容[{d_content[:10]}]vs[{c_content[:10]}],日期[{d_date}]vs[{c_date}])"

        # 6. 非匿名同名无辅助信息 → 放行
        return True, ""

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
            # 排除"查看全文"(导航链接)、"收起全文"(收起按钮)和"语音评价"(标签干扰)
            btns = [b for b in all_btns
                    if "查看" not in b["text"] and "收起" not in b["text"]
                    and "语音评价" not in b["text"]]
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
                entered, reply_date, detail_info = get_reply_date_from_detail(candidates)
                if not entered:
                    print(f"  [商家回复] 孤立回复所有候选均未进入详情页,跳过")
                    continue
                if not reply_date:
                    print(f"  [商家回复] 孤立回复已进入详情页,但未找到回复日期")
                    continue
                # 补到上一屏最后一条还没有商家回复的卡片(孤立回复必定紧跟该卡片内容)
                # 匿名用户同名场景:倒序遍历最近N条无回复卡片,用复合身份匹配定位正确归属
                target = None
                for c in reversed(summarizer.cards[-30:]):  # 最近30条内搜索,避免全量遍历
                    if (c.get("merchant_reply") or "").strip():
                        continue
                    ok, reason = _composite_match(detail_info, c)
                    if ok:
                        target = c
                        break
                    # 调试日志:记录为何不匹配(便于排查匿名重名场景)
                    print(f"  [商家回复] 候选卡片[{c.get('user')}]{c.get('date')}不匹配: {reason}")
                if target is None:
                    # 跨屏长评论场景:该评论内容+图片+回复接近一屏,两屏都无法形成
                    # 完整卡片(上一屏只有用户名露底,下一屏只有内容露顶)。
                    # 用详情页提取的权威身份信息(用户名+评论日期)+ 屏顶露出的
                    # 孤立内容 + 本条回复,拼成一张完整卡片补回,避免整条丢失。
                    d_user = (detail_info or {}).get("user", "").strip()
                    d_date = (detail_info or {}).get("date", "").strip()
                    lead_text = "".join(getattr(parser, "leading_content", []) or []).strip()
                    if d_user and d_date and (lead_text or (detail_info or {}).get("content_prefix", "")):
                        # 先去重:该用户该日期的卡片已存在(可能是跨屏多次拼回/已正常解析)则跳过
                        if any((c.get("user") or "").strip() == d_user
                               and (c.get("date") or "").strip() == d_date
                               for c in summarizer.cards):
                            print(f"  [商家回复] 跨屏评论[{d_user}]{d_date}已存在,跳过拼回")
                        else:
                            new_card = {
                                "user": d_user,
                                "date": d_date,
                                "score": (detail_info or {}).get("score", ""),
                                "content": lead_text or (detail_info or {}).get("content_prefix", ""),
                                "avg_price": "",
                                "merchant_reply": reply_text,
                                "merchant_reply_date": reply_date,
                            }
                            summarizer.cards.append(new_card)
                            print(f"  [商家回复] 跨屏长评论卡片缺失,已用详情页信息拼回: {d_date} -> [{d_user}]")
                            csv_exporter.rewrite_all(summarizer.cards)
                    else:
                        print(f"  [商家回复] 无上一屏卡片通过复合身份校验,跳过(可能已归属或匿名重名未匹配)")
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

        # 3c2. 商家回复缺失补救:评论内容抓到但商家回复没抓到,且评论在屏幕底部
        #      (被上一条超长回复压到屏底,回复滚出屏幕未dump到),
        #      小幅下滑露出回复并重新解析补全回复内容。
        #      注意:不是每条差评都有商家回复,下滑后仍无回复则放弃,不无限重试;
        #      且只对内容底部接近屏幕底部的卡片生效,避免下滑把屏幕中间的评论滚出。
        missing_reply = [
            c for c in cards
            if c.get("content", "").strip()
            and not (c.get("merchant_reply") or "").strip()
        ]
        if missing_reply:
            w, h = adb.get_screen_size()
            # 只对"评论整体在屏幕下半部分"的卡片生效:
            #   内容顶部 > 0.45h(评论起点在屏幕下半) 且 内容底部 > 0.70h(内容延伸到屏底),
            #   避免把"长评论占据整屏"误判为屏底评论而下滑滚出顶部。
            bottom_cards = [
                c for c in missing_reply
                if c.get("content_bounds")
                and c["content_bounds"][1] > int(h * 0.45)
                and c["content_bounds"][3] > int(h * 0.70)
            ]
            if bottom_cards:
                print(f"  [补救] {len(bottom_cards)} 条评论在屏幕底部但回复缺失,小幅下滑露出回复")
                y1 = int(h * 0.75)
                # 下滑 200px 露出底部回复:足够把屏底评论的回复节点拉进屏幕,
                # 又不会把顶部评论的回复挤出屏幕顶部/导航区(400px会导致顶部回复
                # 滚入 y<16% 导航区,被 find_reply_candidates 过滤,3d 定位不到)
                y2 = y1 - 200
                adb.swipe(w // 2, y1, w // 2, y2, duration_ms=600, human=False)
                adb.human_delay(1.0, 1.5)
                retry_xml = adb.dump_ui()
                new_items = adb.extract_all_text(retry_xml, min_len=1)
                new_cards = parser.parse(new_items)
                for old_card in cards:
                    old_key = f"{old_card.get('date', '')}|{(old_card.get('content') or '')[:20]}"
                    for new_card in new_cards:
                        new_key = f"{new_card.get('date', '')}|{(new_card.get('content') or '')[:20]}"
                        if new_key == old_key:
                            # 下滑后坐标已变:同步所有卡片到新坐标。
                            # 若不更新,3d 仍用下滑前的旧坐标点击,顶部评论的回复
                            # 下滑后滚入导航区(y<16%)或屏幕外,旧坐标点击会点到错误位置。
                            old_card["content_bounds"] = new_card.get("content_bounds") or old_card.get("content_bounds")
                            if (new_card.get("merchant_reply") or "").strip():
                                old_card["merchant_reply"] = new_card["merchant_reply"]
                                old_card["merchant_reply_bounds"] = new_card.get("merchant_reply_bounds") or old_card.get("merchant_reply_bounds")
                                print(f"  [补救] 补全商家回复: [{old_card.get('user') or '?'}] {old_card.get('date')}")
                            break
                # 下滑后坐标已变,更新 xml_str 供后续 3d 定位使用
                xml_str = retry_xml

        # 3c3. 评分缺失补救:user+date+content 都抓到但 score 缺失,
        #      通常因前一条超长评论(全文展开)挤占屏幕导致本条评分节点滚出屏外未 dump 到。
        #      小幅下滑让评分节点露出再重新解析补全。
        #      注意:差评筛选列表的评分节点文本必为负面档位(很差/较差/很糟糕/糟糕),
        #      位置在用户名+日期下方约 60-150px。下滑 200px 足以让屏外的评分节点露出,
        #      又不会把屏幕中间评论的头部滚出顶部。仅对评论头部(date_bounds)在屏幕
        #      下半部分(>0.45h)的卡片生效,避免对顶部评论无意义下滑挤出底部内容。
        missing_score = [
            c for c in cards
            if c.get("user", "").strip()
            and c.get("date", "").strip()
            and c.get("content", "").strip()
            and not (c.get("score") or "").strip()
        ]
        if missing_score:
            w, h = adb.get_screen_size()
            bottom_cards = [
                c for c in missing_score
                if c.get("date_bounds")
                and c["date_bounds"][1] > int(h * 0.45)
            ]
            if bottom_cards:
                print(f"  [补救] {len(bottom_cards)} 条评论评分缺失,小幅下滑露出评分节点")
                y1 = int(h * 0.75)
                y2 = y1 - 200  # 下滑 200px,与3c2一致
                adb.swipe(w // 2, y1, w // 2, y2, duration_ms=600, human=False)
                adb.human_delay(1.0, 1.5)
                retry_xml = adb.dump_ui()
                new_items = adb.extract_all_text(retry_xml, min_len=1)
                new_cards = parser.parse(new_items)
                for old_card in cards:
                    if (old_card.get("score") or "").strip():
                        continue
                    old_key = f"{old_card.get('date', '')}|{(old_card.get('content') or '')[:20]}"
                    for new_card in new_cards:
                        new_key = f"{new_card.get('date', '')}|{(new_card.get('content') or '')[:20]}"
                        if new_key == old_key:
                            # 同步坐标,3d 定位使用最新坐标
                            old_card["content_bounds"] = new_card.get("content_bounds") or old_card.get("content_bounds")
                            old_card["date_bounds"] = new_card.get("date_bounds") or old_card.get("date_bounds")
                            if (new_card.get("score") or "").strip():
                                old_card["score"] = new_card["score"]
                                # 同步到 summarizer.cards(类似3d的merchant_reply同步)
                                # 否则补救补全的评分会因summarizer去重时丢弃本地副本而丢失,
                                # 导致 CSV rating 字段为空
                                dedup_key = summarizer._dedup_key(old_card)
                                for sc in summarizer.cards:
                                    if summarizer._dedup_key(sc) == dedup_key:
                                        sc["score"] = old_card["score"]
                                        break
                                print(f"  [补救] 补全评分: [{old_card.get('user') or '?'}] {old_card.get('date')} → {old_card['score']}")
                            break
                # 下滑后坐标已变,更新 xml_str 供后续 3d 定位使用
                xml_str = retry_xml

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
                dedup_key = summarizer._dedup_key(card)
                # 先把 merchant_reply 内容同步到汇总卡片(不管日期能否取到),
                # 否则 3c2 补救补全的回复内容会因取日期失败而丢失,导致 CSV 回复为空
                for sc in summarizer.cards:
                    if summarizer._dedup_key(sc) == dedup_key:
                        sc["merchant_reply"] = card["merchant_reply"]
                        break
                # 汇总中该卡片已有回复日期则跳过(3a补全或上一屏已获取)
                if any(sc.get("merchant_reply_date")
                       and summarizer._dedup_key(sc) == dedup_key
                       for sc in summarizer.cards):
                    print(f"  [商家回复] 该卡片回复日期已获取,跳过")
                    continue
                # 定位并点击:最多尝试3种屏幕状态(当前/反向/正向滚动)。
                # 前一张卡片处理时可能滚动过(详情页back、重试滚动),列表位置已变,
                # 旧坐标(merchant_reply_bounds)失效,因此每种状态都重新dump定位。
                # 滚动方向场景:
                #   反向(内容下移):回复被3c2补救下滑挤出屏幕顶部/导航区(y<16%,被过滤)时拉回
                #   正向(内容上移):回复在屏幕底部下方(屏幕外)时露出
                w2, h2 = adb.get_screen_size()
                entered = False
                reply_date = ""
                detail_info = None
                for attempt, scroll in enumerate([None, "backward", "forward"]):
                    if attempt > 0:
                        # 状态滚动:小步慢速(距离≤35%屏、时长≥900ms),避免快速滑动
                        # 触发惯性滚动(惯性可能一次滚近一整屏,把目标卡片甩出屏幕)。
                        # backward=内容下移(往回滚),forward=内容上移(向前滚)
                        if scroll == "backward":
                            adb.swipe(w2 // 2, int(h2 * 0.45), w2 // 2, int(h2 * 0.80), duration_ms=900, human=False)
                        else:
                            adb.swipe(w2 // 2, int(h2 * 0.70), w2 // 2, int(h2 * 0.40), duration_ms=900, human=False)
                        adb.human_delay(1.0, 1.5)
                    fresh_xml = adb.dump_ui()
                    fresh_items = adb.extract_all_text(fresh_xml, min_len=1)
                    fresh_cards = parser.parse(fresh_items)
                    # 用当前屏幕刷新near_y(内容底部y):按 date+内容前20 匹配本卡片
                    near_y = None
                    card_key = f"{card.get('date', '')}|{(card.get('content') or '')[:20]}"
                    for fc in fresh_cards:
                        fc_key = f"{fc.get('date', '')}|{(fc.get('content') or '')[:20]}"
                        if fc_key == card_key:
                            if fc.get("content_bounds"):
                                near_y = fc["content_bounds"][3]
                            break
                    candidates = find_reply_candidates(
                        fresh_xml, card["merchant_reply"], y_max, near_y=near_y)
                    state_name = "当前" if scroll is None else ("反向" if scroll == "backward" else "正向")
                    if not candidates:
                        print(f"  [商家回复] {state_name}屏幕未找到回复元素,尝试滚动")
                        continue
                    # 传card:函数内部对每个候选做复合匹配,失败则自动back继续下一个候选,
                    # 直到找到身份匹配的回复详情页(解决多条回复同前缀时定位到错误回复的问题)
                    entered, reply_date, detail_info = get_reply_date_from_detail(
                        candidates, card=card)
                    if entered:
                        break
                if not entered:
                    print(f"  [商家回复] 多状态滚动重试后仍无匹配,跳过本条")
                    continue
                if not reply_date:
                    print(f"  [商家回复] 已进入详情页,但未找到回复日期")
                    continue
                card["merchant_reply_date"] = reply_date
                print(f"  [商家回复] 回复日期: {reply_date}")
                # 同步 merchant_reply_date 到汇总卡片
                for sc in summarizer.cards:
                    if summarizer._dedup_key(sc) == dedup_key:
                        sc["merchant_reply_date"] = reply_date
                        break
            # 处理回复后确认仍在评论列表(详情页back()可能改变页面状态)
            list_ok, xml_str = ensure_on_review_list(xml_str)
            if not list_ok:
                print(f"  [定位] 评论列表状态丢失,恢复失败,终止采集")
                break

        # 过滤掉标记跳过的卡片(空用户名且重试失败)
        valid_cards = [c for c in cards if not c.get("_skip_empty_user")]
        # 回复节点坐标/内容坐标仅用于点击取日期和屏底判断,不写入报告
        for c in valid_cards:
            c.pop("merchant_reply_bounds", None)
            c.pop("content_bounds", None)
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
