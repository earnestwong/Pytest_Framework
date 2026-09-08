# -*- coding: utf-8 -*-
"""
从大众点评首页搜索导航到目标店铺差评列表。
调用入口: navigate_to_negative_list(kw) -> dict
"""
import sys
import os
import re
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.adb_helper import ADBHelper

# 常量配置（遵循用户要求：无随机延时）
DEFAULT_WAIT = 1.5      # 页面切换等待秒数（固定值，无随机）
MAX_INPUT_RETRY = 3      # 中文输入重试上限
MAX_MATCH_RETRY = 4      # 匹配选店重试上限
MIN_BRAND_LEN = 2        # 品牌词最小长度（rank 模式下候选判断）

ADB_PATH = r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"
PACKAGE = "com.dianping.v1"

# 导出给调用方
adb = None
w, h = None, None
last_shop = None

def init(device_serial=None):
    """初始化: 创建 ADBHelper, 获取屏幕尺寸。调用方可提前 init 复用。"""
    global adb, w, h
    adb = ADBHelper(device_serial=device_serial, adb_path=ADB_PATH)
    w, h = adb.get_screen_size()
    return adb, w, h

def _wait(secs=None):
    """固定等待（无随机），用户要求去掉随机延时。"""
    time.sleep(secs if secs is not None else DEFAULT_WAIT)

def _dump(tries: int = 4, inner: int = 5):
    """稳定 dump UI。uiautomator 在页面有动画时会报 'could not get idle state' 返回空，
    此包装在失败后递增等待并重试，规避批量测试中的偶发 dump 失败。
    :param tries: 外层重试(滑动失焦等场景用)
    :param inner: 内层单次 dump_ui 的尝试次数(探测目标醒目时填 1 加快失败返回)"""
    for i in range(tries):
        try:
            return adb.dump_ui(retries=inner)
        except RuntimeError:
            time.sleep(0.6 + 0.4 * i)  # 失败快速重试(动画/瞬时), 不叠加长等待
    raise RuntimeError("dump_ui 连续失败(页面动画未稳定)")

def _center(item: dict):
    """由 text 节点的 bounds [x1,y1,x2,y2] 计算中心点 (cx, cy)。
    find_elements_by_text / extract_all_text 返回的节点只有 bounds，没有 center。"""
    b = item["bounds"]
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2

# 结果页顶部(促销banner/轮播等自绘区域)存在持续动画, 导致 uiautomator dump
# 无法进入 idle 而报 "could not get idle state"(与搜索框光标无关: 搜索框始终
# 固定于顶部, 但下滑越过动画块后 dump 即恢复)。实测下滑约 200~225px 越过动画
# 块后 dump 恢复, 且第一张门店卡标题仍在可视区(cy≈240)。
# 关键: 必须用受控慢速拖动(不打惯性), 快速 swipe 的惯性fling会把第一张门店
# 卡滚出可视区, 导致 dump 到的却是更下方的卡(选错店)。
# 光标聚焦区(搜索框)高度约为屏幕 8% 以下, 扫描区下界取 0.08h 可排除之。
RESULT_TOP_GUARD = 0.08      # 扫描区上界(排除顶部搜索框/筛选行)
PRE_SCROLL_DIST = 250        # 越过顶部动画块的单次最大下滑距离(实测阈值~200-225, 取250留裕量)
DETAIL_PRE_SCROLL = 200      # 详情页循环下滑的单次距离(小幅递减, 越过秒杀等动画块)
DETAIL_MAX_SCROLL_TRIES = 15 # 详情页 dump 失败时最多下滑次数(动画块滚出视口即恢复, 上限兜底)

def _ensure_dumpable():
    """在搜索结果页稳定取 UI XML:
    先直接 dump; 失败(顶部动画块)则受控慢速拖动(不打惯性fling, 避免把第一张
    门店卡滚出可视区)下滑 PRE_SCROLL_DIST 越过动画块恢复 idle, dump 即成功;
    仍不够再补拖一段(兜底). 最终失败按默认重试抛异常由调用方 try-except 兜底。"""
    try:
        return _dump(tries=1, inner=1)
    except RuntimeError:
        pass
    for dist in (PRE_SCROLL_DIST, PRE_SCROLL_DIST + 200):
        # 慢速拖动(1200ms, 无惯性fling), 精确下滑 dist px; 快速swipe的fling会
        # 把第一张门店卡滚出可视区, 导致 dump 到的却是更下方的卡(选错店)
        adb.shell(f"input swipe {w // 2} {int(h * 0.72)} "
                  f"{w // 2} {int(h * 0.72) - dist} 1200")
        time.sleep(0.4)  # 短停等滚动稳定即重试, 不叠加长等待
        try:
            return _dump(tries=1, inner=1)
        except RuntimeError:
            continue
    return _dump()

def _ensure_dumpable_detail():
    """详情页稳定取 UI XML。
    详情页存在优惠/促销banner(神券/代金券/到店套餐抢购区)及『秒杀』倒计时等
    持续动画块时, dump 无法进入 idle(与结果页同因)。而这些动画块一定位于页面的
    某个滚动位置, 持续受控小幅下滑即可把它们滚出视口恢复 idle——因此每家店铺
    最终都必然能 dump 成功, 只需循环下滑直到成功, 而非固定下滑固定距离。
    每次下滑约 DETAIL_PRE_SCROLL px, 最多 DETAIL_MAX_SCROLL_TRIES 次; 下滑中越
    过动画块后『评价 (N)』tab 吸顶仍可点击, 恢复位置不影响后续进评价/切差评。"""
    try:
        return _dump(tries=1, inner=1)
    except RuntimeError:
        pass
    for _ in range(DETAIL_MAX_SCROLL_TRIES):
        adb.shell(f"input swipe {w // 2} {int(h * 0.72)} "
                  f"{w // 2} {int(h * 0.72) - DETAIL_PRE_SCROLL} 1200")
        time.sleep(0.4)  # 短停等滚动稳定即重试, 不叠加长等待
        try:
            return adb.dump_ui(retries=1)
        except RuntimeError:
            continue
    return _dump()

def _back_to_homepage(tries=12) -> bool:
    """持续back直到回到首页（标志: 有'关注'+'附近'），tries 上限。
    若无法回到首页，兜底尝试拉起大众点评包。
    注意: 页面有持续动画(如语音评价)时 dump 会一直失败,此时不能抛异常
    中断批量测试,而应继续 back 离开动态页面。"""
    for _ in range(tries):
        try:
            xml = _dump(tries=1, inner=1)  # 快速失败: 动画页 dump 失败立即 back, 不内部反复重试
            if "关注" in xml and "附近" in xml:
                return True
        except RuntimeError:
            pass  # dump 失败(动画未稳定) -> 继续 back 离开动态页
        adb.back()
        time.sleep(0.6)  # 原1.5s: back后转场短停, 缩短加快回首页
    # 兜底: 包拉起
    adb.shell(f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1")
    _wait(4.0)
    xml = _dump()
    return "关注" in xml and "附近" in xml

def _dismiss_ime_consent(xml: str) -> bool:
    """搜狗输入法首次使用会弹『隐私同意』弹窗, 拦截搜索建议页。
    点击右下角『同意』按钮关闭。返回是否处理过弹窗。
    注意: '同意' 是 '不同意' 的子串, 需排除 '不同意' 节点; 按钮在弹窗最右下。"""
    if "搜狗输入法" not in xml:
        return False
    best = None
    for b in adb.find_elements_by_text(xml, "同意"):
        t = (b.get("text") or "").strip()
        if "不同意" in t:
            continue
        cx, cy = _center(b)
        if best is None or cy > best[1]:
            best = (cx, cy)
    if not best:
        return False
    adb.tap(best[0], best[1], human=False)
    _wait(2.0)
    return True

def _open_search_box() -> bool:
    """点击首页顶部搜索栏，进入搜索建议页（出现'历史搜索'标志）。
    兼容搜狗输入法隐私弹窗与滑块验证码弹窗的拦截。"""
    xml = _dump(tries=2, inner=1)  # 首页搜索框探测: 快速失败即可
    box = None
    # 找y<15%、文本含'搜索'、最靠左的节点
    for b in adb.find_elements_by_text(xml, "搜索"):
        cx, cy = _center(b)
        if cy < int(h * 0.15) and (box is None or cx < box[0]):
            box = (cx, cy)
    if not box:
        return False
    adb.tap(box[0], box[1], human=False)
    time.sleep(0.8)  # 点搜索框后等搜索建议页转场(原1.5s, 缩短)
    xml = _dump(tries=2, inner=1)
    # 搜狗输入法隐私弹窗拦截 -> 点同意
    if _dismiss_ime_consent(xml):
        xml = _dump(tries=2, inner=1)
    # 滑块验证码拦截 -> 自动滑动; 图标验证码需人工
    if _check_captcha():
        return False
    ts = [it["text"] for it in adb.extract_all_text(xml, min_len=1)]
    # 搜索建议页标志: 历史搜索/搜索发现 / 最近搜索/店内热搜(新版改版标题) 或
    # search_edit_text 存在(旧版热搜榜布局输入框为更稳标志)。
    return (any(t in ("历史搜索", "搜索发现", "最近搜索", "店内热搜") for t in ts)
            or 'resource-id="com.dianping.v1:id/search_edit_text"' in xml)

def _clear_input_box() -> None:
    """清空输入框: 连发DEL 25次。
    关键: 在设备端 shell 循环内连发(一次 adb 往返), 避免 25 次独立 adb shell
    每次往返 ~0.19s 累计到 4.8s。汉字占 2 字节, 25 次足够清空长关键词。"""
    nav_adb = adb
    nav_adb.shell('for i in $(seq 25); do input keyevent 67; sleep 0.02; done')

IME_ADBKEYBOARD = "com.android.adbkeyboard/.AdbIME"

def _refocus_search_box() -> bool:
    """重新聚焦搜索输入框(ime set 切输入法可能重置焦点)。
    新版搜索建议页输入框 resource-id 可能为空(非 search_edit_text), 因此
    resource-id 匹配与任意 EditText 类节点(取页面最上 EditText)双保险。"""
    for _ in range(3):
        try:
            xml = _dump(tries=1)
        except RuntimeError:
            time.sleep(0.4)
            continue
        m = re.search(
            r'<node[^>]*resource-id="[^"]*search_edit_text"[^>]*'
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if not m:
            m = re.search(
                r'class="android.widget.EditText"[^>]*'
                r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if m:
            cx = (int(m.group(1)) + int(m.group(3))) // 2
            cy = (int(m.group(2)) + int(m.group(4))) // 2
            adb.tap(cx, cy, human=False)
            time.sleep(0.3)
            return True
        time.sleep(0.4)
    return False

def _input_chinese(kw: str) -> bool:
    """用 ADBKeyboard 广播注入中文，dump校验框内命中。重试上限 3 次。
    关键: ADBKeyboard 的 ADB_INPUT_TEXT 广播需其 IME 处于激活态才有注入目标。
    默认 IME 若被搜狗等抢占(隐私弹窗/重启后), 广播会静默失败, 因此必须先
    ime set 到 ADBKeyboard 并恢复搜索框焦点再注入。"""
    for attempt in range(MAX_INPUT_RETRY):
        # 每次重试前确保 ADBKeyboard 为当前 IME(被抢占则切回)并恢复焦点
        adb.shell(f"ime set {IME_ADBKEYBOARD}")
        time.sleep(0.4)  # 原0.8s, 缩短
        _refocus_search_box()
        _clear_input_box()
        time.sleep(0.2)  # 原0.3s, 缩短
        # ADBKeyboard 广播注入
        adb.shell(f'am broadcast -a ADB_INPUT_TEXT --es msg "{kw}"')
        time.sleep(0.5)  # 原0.8s, 缩短
        # 校验: 顶区y<0.08h 文本是否含kw
        xml = _dump()
        has_kw = False
        for it in adb.extract_all_text(xml, min_len=1):
            if it["bounds"][3] < int(h * 0.08) and kw in it["text"]:
                has_kw = True
                break
        if has_kw:
            return True
    return False

def _norm(s: str) -> str:
    """归一化店名: 去掉括号/空格等分隔字符, 便于比较。"""
    return re.sub(r"[（()）\[\]【】\s]", "", s or "")

def _card_match(card_text: str, kw: str, min_len: int = MIN_BRAND_LEN):
    """店名匹配（rank 模式选卡依据）, 两级判定:
      - 'full' : 归一化后 kw 与 card 互为子串(全名/分支级匹配, 如
                 kw='老人和金航城店' vs card='老人和(金航城店)' / '老人和金航城店')
      - 'brand': 仅品牌前缀对齐(如 kw='光明村田林店' vs card='光明村大酒家(汇源广场)'),
                 公共前缀长度>=min_len。作为 full 匹配不到时的兜底。
    返回 None 表示不匹配。"""
    cn, kn = _norm(card_text), _norm(kw)
    if not kn or not cn:
        return None
    if kn in cn or cn in kn:
        return "full"
    n = 0
    for a, b in zip(cn, kn):
        if a != b:
            break
        n += 1
    return "brand" if n >= min_len else None

def _pick_top_brand_card(kw: str, skip_texts=(), min_len: int = MIN_BRAND_LEN):
    """rank-first：自上而下遍历结果页门店卡。
    优先返回第一张 full 级匹配卡(确保进入正确分店)；
    全表扫描完仍无 full 时, 回退到第一张 brand 级匹配卡(品牌兜底)。
    没找到则滚动找下一屏（最多10屏）。
    稳定性: 结果页搜索框光标闪烁导致 dump 挂起时, 先下滑失焦恢复 dump(_ensure_dumpable);
    顶部搜索框(内为 kw)与『“{kw}”相关推荐』分栏标题均不视为门店卡, 显式排除。
    :param skip_texts: 已尝试过且被否决的卡片文本集合, 跳过以免重复点同一张卡。"""
    first_brand = None
    for _ in range(10):
        xml = _ensure_dumpable()
        items = adb.extract_all_text(xml, min_len=2)
        # 按 y 升序（自上而下）
        items.sort(key=lambda it: it["bounds"][1])
        for it in items:
            y0, y1 = it["bounds"][1], it["bounds"][3]
            cy = (y0 + y1) // 2
            # 避开顶部搜索框(y<8%)与底部导航(y>92%)，只扫描门店卡片区
            if not (int(RESULT_TOP_GUARD * h) <= cy <= int(0.92 * h)):
                continue
            text = it["text"]
            # 『“{kw}”相关推荐』分栏标题含完整 kw(会误判 full), 非门店卡, 排除
            if "相关推荐" in text:
                continue
            if text in skip_texts:
                continue
            mtype = _card_match(text, kw, min_len)
            if mtype is None:
                continue
            cx = (it["bounds"][0] + it["bounds"][2]) // 2
            if mtype == "full":
                return cx, cy, text
            if first_brand is None:
                first_brand = (cx, cy, text)
        adb.swipe_up(ratio=0.45, human=False, mode="swipe")
        _wait()
    if first_brand:
        return first_brand
    return None, None, None

def _pick_first_card(kw: str):
    """结果页取列表顶部第一张门店卡(不做品牌匹配、不滚动)。
    只解析真实 text 节点(忽略仅有 content-desc 的布局容器, 如 rootView /
    SearchPagerViewPager 等整屏包围节点会排在最前被误当首卡), 再从 y 升序
    跳过顶部搜索框(y<8%)、底部导航(y>92%)、分类筛选tab与『“kw”相关推荐』
    分栏标题等非门店文本, 取第一张门店卡标题。只在当前屏查找, 找不到返回 None。
    :return (cx, cy, card_text); 找不到返回 (None, None, None)"""
    NOISE = ("相关推荐", "智能排序", "离我最近", "好评优先", "人气最高", "筛选",
             "全城", "分类", "排序",
             # 结果页固定顶部的分类筛选tab标签(非门店卡), 位于扫描带内会先于首卡被选中
             "全部", "商户", "团购", "外卖", "内容", "用户")
    xml = _ensure_dumpable()
    # 仅取 text 属性非空且有 bounds 的节点, 按 [y,x] 升序
    pat = re.compile(r'text="([^"]+)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
    cands = []
    for m in pat.finditer(xml):
        t = m.group(1).strip()
        if not t or len(t) < 2:
            continue
        x1, y1, x2, y2 = map(int, m.groups()[1:])
        cy = (y1 + y2) // 2
        if not (int(RESULT_TOP_GUARD * h) <= cy <= int(0.92 * h)):
            continue
        if any(n in t for n in NOISE):
            continue
        cands.append(((y1, x1), (x1 + x2) // 2, cy, t))
    cands.sort()
    if cands:
        _, cx, cy, text = cands[0]
        return cx, cy, text
    return None, None, None

def _is_detail_page(xml: str) -> bool:
    """是否已进入店铺详情页（而非仍在结果页）。"""
    if "SearchPagerListView" in xml:
        return False
    return any(t in xml for t in ("写评价", "收藏", "元/人", "优惠"))

def _check_detail_title(kw: str, min_len: int = MIN_BRAND_LEN) -> bool:
    """从详情页全屏文本中核对店名匹配。
    优先全名匹配(确认进入正确分店)；无全名时放宽到品牌前缀匹配。
    扫描整页可见文本节点, 任一命中即视为正确进店。"""
    xml = _dump()
    items = adb.extract_all_text(xml, min_len=2)
    for it in items:
        if _card_match(it["text"], kw, min_len):
            return True
    return False

_DETAIL_TITLE_NOISE = ("视频", "相册", "图片", "详情", "联系", "收藏", "分享",
                       "更多", "关注", "优惠", "菜单", "评价", "导航", "地图",
                       # 店铺经营状态/营业信息等非店名文本
                       "营业中", "营业时间", "休息中", "已打烊", "暂停营业",
                       "人均", "地址", "电话", "车位", "招牌", "推荐菜")

def _looks_noise(text: str) -> bool:
    """判断某文本是否为店名噪声(评分/榜单头衔/收录N年/地址等), 用于决定
    _pick_first_card 的结果页首卡标题是否可信。非噪声视为真实店名。"""
    t = (text or "").strip()
    if not t or len(t) < 2:
        return True
    # 纯数字/评分 (3.4 / 4.3 / 4分)
    if re.fullmatch(r"[\d.]+", t) or re.fullmatch(r"[\d.]+分", t):
        return True
    # 收录 + 数字 + 年
    if re.search(r"收录\d+年", t):
        return True
    # 榜单头衔: 含 '榜' + '第N名' 或 '热门榜/销量榜/环境榜' 等
    if "榜" in t and re.search(r"第\s*\d+\s*名", t):
        return True
    if re.search(r"(热门榜|销量榜|环境榜|口味榜|服务榜|评价榜)", t):
        return True
    # 地址: 以 区/路/街/号 结尾或含 '路' + 数字
    if re.search(r"(区|路|街|巷|号)\s*\d*$", t) and re.search(r"\d", t):
        return True
    # 经营状态/星级/评分相关非店名
    if any(k in t for k in ("星级分", "精选", "广告", "合集")):
        return True
    return False


def _detail_shop_name(xml: str) -> str:
    """从详情页中上部提取店铺名标题(用于把结果页仍到卡的"4.3"之类首卡文案换成真实店名)。
    详情页标题区: 相册/视频按钮 → 店名标题。
    先收集扫描带内(0.18h~0.55h)非噪声文本节点, 再按 y 升序(自上而下)取第一个
    —— 避免"营业中"等若与店名同屏却被 extract_all_text 顺序先返而误取。"""
    cands = []
    for it in adb.extract_all_text(xml, min_len=2):
        b = it["bounds"]
        cy = (b[1] + b[3]) // 2
        if not (int(0.18 * h) <= cy <= int(0.55 * h)):
            continue
        t = (it["text"] or "").strip()
        if not t or any(n in t for n in _DETAIL_TITLE_NOISE):
            continue
        if re.search(r"[¥￥]", t) or re.search(r"/\s*人\s*$", t) or re.match(r"^\d", t):
            continue
        cands.append((b[1], b[3], t))
    if not cands:
        return ""
    cands.sort(key=lambda c: (c[0], c[1]))  # 按 y 升序
    return cands[0][2]

def _submit_search() -> bool:
    """点右上搜索按钮或回车，确认进入搜索结果页（SearchPagerListView）。"""
    xml = _dump(tries=2, inner=1)  # 搜索建议页无动画, 快速失败即可
    # 找y<15%、文本含'搜索'、最靠右节点（右上搜索按钮）
    btn = None
    for b in adb.find_elements_by_text(xml, "搜索"):
        cx, cy = _center(b)
        if cy < int(h * 0.15) and (btn is None or cx > btn[0]):
            btn = (cx, cy)
    if btn:
        adb.tap(btn[0], btn[1], human=False)
    else:
        # 找不到按钮回退回车
        adb.shell("input keyevent 66")
    time.sleep(0.6)  # 点搜索后等结果页转场; _ensure_dumpable 已在失败时滑动重试, 无需长等
    xml = _ensure_dumpable()  # 结果页搜索框光标闪烁会挂起 dump, 下滑失焦恢复
    # 搜索结果页标志: SearchPagerListView
    return "SearchPagerListView" in xml

def _is_review_list(xml: str) -> bool:
    """是否已进入评价列表页。
    评价列表页标志：顶部筛选行同时出现『最新』与『中评』。
    （店铺详情页只有『菜单/优惠/推荐菜/评价』tab 与标签云里的『差评 N』，
    不含『最新/中评』，因此不可再用『差评 in xml』判断——详情页会误判。）"""
    return "最新" in xml and "中评" in xml

def _click_evaluation_tab() -> bool:
    """点击详情页「评价」入口，进入评价列表。
    兼容不同店铺布局：从详情页收集候选入口（『评价』tab、『评价 (N)』标题、
    评价区『查看全部』），按从左到右逐个点击，直到检测到评价列表页。"""
    def _collect_cands(xml):
        cands = []
        # 主入口：文本含『评价』，避开『写评价/语音评价/当地人评价』
        for b in adb.find_elements_by_text(xml, "评价"):
            cx, cy = _center(b)
            if not (int(0.05 * h) <= cy <= int(0.60 * h)):
                continue
            t = (b.get("text") or "").strip()
            if any(k in t for k in ("写评价", "语音评价", "当地人评价")):
                continue
            cands.append((cx, cy, t))
        # 兜底入口：评价区『查看全部』
        if not cands:
            for b in adb.find_elements_by_text(xml, "查看全部"):
                cx, cy = _center(b)
                if int(0.05 * h) <= cy <= int(0.50 * h):
                    cands.append((cx, cy, (b.get("text") or "").strip()))
        return cands

    for _ in range(4):
        xml = _ensure_dumpable_detail()
        cands = _collect_cands(xml)
        if not cands:
            _wait()
            continue
        # 从左到右尝试（『评价 (N)』标题通常在最左，实测为有效入口）
        cands.sort(key=lambda c: (c[0], c[1]))
        for cx, cy, _t in cands:
            adb.tap(cx, cy, human=False)
            _wait(2.5)
            xml = _ensure_dumpable_detail()
            if _is_review_list(xml):
                return True
    return False

def _check_negative_selected(xml: str) -> bool:
    """校验「差评」tab处于选中态（不是只存在）。
    无 _parse_xml，改用 XML 字符串判断：
    检查 '差评' 附近是否含 selected="true" / checked="true"；
    若无则回退检查是否出现差评评分词。"""
    flags = ('selected="true"', 'checked="true"')
    for m in re.finditer("差评", xml):
        lo = max(0, m.start() - 150)
        hi = min(len(xml), m.end() + 150)
        window = xml[lo:hi]
        if any(f in window for f in flags):
            return True
    return any(t in xml for t in ("很差", "较差", "很糟糕", "糟糕"))

def _goto_negative_list() -> bool:
    """在评价列表点击「差评」筛选tab，并校验选中。"""
    for _ in range(3):
        xml = _dump()
        cand = None
        # 找y<30%、含'差评'节点，取最上
        for b in adb.find_elements_by_text(xml, "差评"):
            cy = _center(b)[1]
            if cy < int(0.30 * h) and (cand is None or cy < cand[1]):
                cand = _center(b)
        if not cand:
            _wait()
            continue
        adb.tap(cand[0], cand[1], human=False)
        _wait(2.8)
        xml = _dump()
        if _check_negative_selected(xml):
            return True
    return False

def _check_captcha() -> bool:
    """检测当前是否有验证码。
    返回 True=需要人工处理；False=无验证码可继续。
    滑块尝试自动解决；图标点选（'请依次点击'）提示人工并暂停。
    注意: dump 失败(如停在搜索结果页、顶部搜索框光标闪烁)时无法检测验证码,
    直接返回 False 视为无验证码, 由调用方 _back_to_homepage 兜底恢复页面,
    而不是抛异常中断整个导航。"""
    try:
        xml = _dump(tries=1)
    except RuntimeError:
        return False
    joined = "".join(it["text"] for it in adb.extract_all_text(xml, min_len=1))
    # 图标点选验证码(需人工): 特征为'请依次点击/依次点击下图'
    # 注意: '身份核实' 是滑块验证码弹窗标题, 不属于图标验证码特征, 不能误判
    if "请依次点击" in joined or "依次点击下图" in joined:
        print("\n!!! 检测到图标点选验证码，请在设备上人工完成验证后继续。")
        return True  # 需要人工
    cap = adb.detect_slide_captcha(xml)
    if cap:
        print(f"  [验证] 检测到滑块验证码，尝试自动滑动: {cap['hint'][:20]}")
        adb.solve_slide_captcha(cap)
        _wait(3.0)
        xml = _dump()
        joined = "".join(it["text"] for it in adb.extract_all_text(xml, min_len=1))
        if ("身份核实" not in joined and "请依次点击" not in joined
                and not adb.detect_slide_captcha(xml)):
            print("  [验证] 滑块验证通过")
            return False
        print("  [验证] 自动滑动后仍有验证，请人工处理")
        return True
    return False

def navigate_to_negative_list(kw: str, do_init: bool = True, verbose: bool = False) -> dict:
    """
    从首页开始导航，最终到目标店铺差评列表顶部。
    :param kw: 店名关键词（可与点评店名不完全一致）
    :param do_init: 是否在内部做 init，调用方提前 init 过传 False
    :param verbose: 是否打印步骤日志（批量测试定位卡点用）
    :return: {"ok": bool, "shop_name": str, "matched": bool, "err": str}
        ok=True -> 已停在差评列表顶部，调用方可开始采集
        ok=False -> 失败，已尽力回首页，调用方跳过即可
    """
    global last_shop
    def _log(msg):
        if verbose:
            print(f"  [nav] {msg}", flush=True)

    if do_init and adb is None:
        init()

    # 1. 验证码预检（如果当前就在验证码页）
    if _check_captcha():
        return {"ok": False, "shop_name": "", "matched": False,
                "err": "需要人工处理验证码"}

    # 2. 回首页
    if not _back_to_homepage():
        return {"ok": False, "shop_name": "", "matched": False,
                "err": "无法回到大众点评首页"}
    _log("已回首页")
    if _check_captcha():
        return {"ok": False, "shop_name": "", "matched": False,
                "err": "需要人工处理验证码"}

    # 3. 开搜索框
    if not _open_search_box():
        return {"ok": False, "shop_name": "", "matched": False,
                "err": "无法打开搜索框"}
    _log("已打开搜索框")

    # 4. 中文输入
    if not _input_chinese(kw):
        return {"ok": False, "shop_name": "", "matched": False,
                "err": f"ADBKeyboard 输入 '{kw}' 失败，重试上限耗尽"}
    _log(f"已输入 {kw}")

    # 5. 提交搜索
    if not _submit_search():
        return {"ok": False, "shop_name": "", "matched": False,
                "err": "提交搜索后无法进入结果页"}
    _log("已进入搜索结果页")

    # 6. 直接进结果页顶部第一张门店卡(用户要求: 不做品牌匹配、不滚动)
    matched_text = None
    for retry in range(MAX_MATCH_RETRY):
        cx, cy, card_text = _pick_first_card(kw)
        if cx is None:
            break
        adb.tap(cx, cy, human=False)
        time.sleep(1.2)  # 原2.0s: 点卡后等详情页转场; 详情页若有动画由 _ensure_dumpable_detail 滑动兜底
        # 详情页有优惠/促销banner持续动画导致 dump 挂起, 用受控下滑兜底
        xml = _ensure_dumpable_detail()
        if _is_detail_page(xml):
            # 结果页首卡标题即真实店名(最可靠来源)。详情页标题区店名标题多被渲染进
            # 动画块/非text节点, _detail_shop_name 扫到的常是 '收录N年/榜单头衔/地址'
            # 等噪声, 因此以 card_text 为准; 仅在 card_text 明显是噪声(评分/榜单/
            # 收录+数字+年/地址)时回退详情页标题。
            _sn = _detail_shop_name(xml)
            if _looks_noise(card_text) and _sn:
                matched_text = _sn
            else:
                matched_text = card_text
            break
        # 顶部第一张卡未进详情(可能点到筛选/分栏) -> 退回结果页重试取第一张
        adb.back()
        _wait()
    if matched_text is None:
        for _ in range(6):
            try:
                if "SearchPagerListView" in _ensure_dumpable():
                    break
            except RuntimeError:
                pass
            adb.back()
            _wait()
        _back_to_homepage()
        return {"ok": False, "shop_name": "", "matched": False,
                "err": f"无法进入结果列表第一家门店，kw={kw}"}

    # 7. 进评价→切差评
    if not _click_evaluation_tab():
        _back_to_homepage()
        return {"ok": False, "shop_name": matched_text, "matched": True,
                "err": "无法进入评价列表"}
    if not _goto_negative_list():
        _back_to_homepage()
        return {"ok": False, "shop_name": matched_text, "matched": True,
                "err": "无法选中差评tab"}

    # 8. 成功
    last_shop = matched_text
    print(f"[done] 导航成功: {kw} -> {matched_text}，已停在差评列表")
    return {"ok": True, "shop_name": matched_text, "matched": True, "err": ""}


# 供命令行测试: python nav_search_negative.py "店名"
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python nav_search_negative.py <shop_name_keyword>")
        sys.exit(1)
    kw = sys.argv[1]
    init()
    result = navigate_to_negative_list(kw, do_init=False)
    print("\n=== RESULT ===")
    print(result)