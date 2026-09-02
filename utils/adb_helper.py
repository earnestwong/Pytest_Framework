"""
ADB 底层操作封装
提供设备连接、UI 事件模拟、uiautomator dump、截图等基础能力
针对大众点评反扒场景,加入随机延时与人类化操作
"""
import subprocess
import time
import random
import os
import re
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict, Tuple


class ADBHelper:
    """ADB 命令封装,所有 shell 调用统一走此类"""

    def __init__(
        self,
        device_serial: Optional[str] = None,
        adb_path: str = "adb",
        server_port: Optional[str] = None,
    ):
        """
        :param device_serial: 设备序列号,MuMu 默认 127.0.0.1:7555,留空则取首个设备
        :param adb_path: adb 可执行文件路径,已加入 PATH 时用默认值即可
        :param server_port: adb server 监听端口(默认 5037)。本机 5037 可能被其他
                            工具(如 MuMu 自带 adb)抢占导致 daemon 反复被杀,
                            可指定独立端口如 "5045" 规避
        """
        self.device_serial = device_serial
        self.adb_path = adb_path
        self._base_cmd = [adb_path]
        self._screen_size: Optional[Tuple[int, int]] = None  # 屏幕尺寸缓存(分辨率在会话中不变)
        if server_port:
            os.environ["ANDROID_ADB_SERVER_PORT"] = server_port
        if device_serial:
            self._base_cmd += ["-s", device_serial]

    # ---------- 基础命令 ----------

    def run(self, args: List[str], timeout: int = 30) -> str:
        """执行 ADB 命令并返回 stdout"""
        cmd = self._base_cmd + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ADB 命令执行失败: {' '.join(cmd)}\nstderr: {result.stderr}"
            )
        return result.stdout.strip()

    def shell(self, cmd: str, timeout: int = 30) -> str:
        """执行 shell 命令"""
        return self.run(["shell", cmd], timeout=timeout)

    def connect(self, host: str = "127.0.0.1:7555") -> bool:
        """连接 MuMu 模拟器(MuMu 默认 ADB 端口 7555,新版可能为 16384)"""
        out = self.run(["connect", host])
        return "connected" in out

    def get_devices(self) -> List[str]:
        """获取已连接设备列表"""
        out = subprocess.run(
            [self.adb_path, "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        ).stdout
        devices = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if line and "\tdevice" in line:
                devices.append(line.split("\t")[0])
        return devices

    # ---------- 设备信息 ----------

    def get_screen_size(self) -> Tuple[int, int]:
        """获取屏幕分辨率(带缓存,分辨率在会话中不变)"""
        if self._screen_size:
            return self._screen_size
        out = self.shell("wm size")
        # 形如 "Physical size: 1080x1920"
        parts = out.split(":")[-1].strip().split("x")
        self._screen_size = (int(parts[0]), int(parts[1]))
        return self._screen_size

    def get_current_package(self) -> str:
        """
        获取当前前台应用包名
        注意:Android shell 无 grep,在 Python 端解析 dumpsys 输出
        """
        out = self.shell("dumpsys activity activities")
        # 形如 "topResumedActivity=ActivityRecord{xxx u0 com.dianping.v1/...}"
        for line in out.splitlines():
            line = line.strip()
            if "topResumedActivity" in line or "mResumedActivity" in line:
                for token in line.split():
                    if "/" in token and "." in token:
                        return token.split("/")[0]
        return ""

    # ---------- UI 事件(人类化) ----------

    def human_delay(self, min_s: float = 0.8, max_s: float = 2.5):
        """随机延时,模拟人类操作间隔"""
        time.sleep(random.uniform(min_s, max_s))

    def tap(self, x: int, y: int, human: bool = True, offset: int = 8):
        """
        点击坐标,可附加随机偏移(加大随机性降低反扒风险)
        :param offset: 随机偏移量(±offset px),小屏/小按钮用更小值避免偏出可点击区
        """
        if human:
            x += random.randint(-offset, offset)
            y += random.randint(-offset, offset)
        self.shell(f"input tap {x} {y}")
        if human:
            self.human_delay()

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
        human: bool = True,
    ):
        """滑动,duration 控制速度(大众点评需要慢滑避免被识别)"""
        if human:
            duration_ms += random.randint(-50, 100)
            # 轻微抖动模拟手指不稳
            x1 += random.randint(-5, 5)
            x2 += random.randint(-5, 5)
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
        if human:
            self.human_delay(1.0, 2.0)

    def swipe_up(self, ratio: float = 0.38, human: bool = True, mode: str = "swipe"):
        """
        上滑翻屏(列表滚动)
        :param ratio: 滚动距离占屏幕高度的比例基准(0.1~1.0,实际在 ±0.01 内随机)
        :param human: 是否加人类化随机偏移
        :param mode: swipe=真机 input swipe;roll=MuMu input roll
        """
        w, h = self.get_screen_size()
        cx = w // 2 + (random.randint(-30, 30) if human else 0)
        if human:
            self.human_delay(0.5, 1.0)
        # ratio 在基准 ±0.01 内随机,模拟人类滑动幅度不稳定
        ratio = ratio + random.uniform(-0.01, 0.01) if human else ratio
        if mode == "roll":
            # MuMu 模拟器:input swipe 对列表无效,用 input roll
            cy = h // 2
            dy = -int(h * ratio)
            if human:
                dy += random.randint(-50, 50)
            self.shell(f"input roll 0 {dy} {cx} {cy}")
        else:
            # 真机:input swipe(加大随机性:起点 X 偏移、起止 Y 抖动)
            y1 = int(h * (0.7 + (random.uniform(-0.08, 0.08) if human else 0)))
            y2 = int(h * (0.7 - ratio + (random.uniform(-0.08, 0.08) if human else 0)))
            # X 轴也加随机偏移(不总在正中央)
            cx2 = cx + (random.randint(-40, 40) if human else 0)
            # 慢速滑动(690~710ms 随机)减少惯性滚动,避免跳过卡片
            duration = random.randint(690, 710) if human else 700
            self.shell(f"input swipe {cx} {y1} {cx2} {y2} {duration}")
        # 滑动后短等待,让滚动动画停下、页面渲染稳定
        time.sleep(random.uniform(0.01, 0.20))
        if human:
            self.human_delay(1.0, 2.0)

    def input_text(self, text: str):
        """输入文本(仅支持 ASCII,中文需用 ADBKeyBoard 或剪贴板)"""
        self.shell(f'input text "{text}"')
        self.human_delay()

    def back(self):
        """返回键"""
        self.shell("input keyevent 4")
        self.human_delay()

    def home(self):
        """Home 键"""
        self.shell("input keyevent 3")
        self.human_delay()

    # ---------- uiautomator ----------

    def dump_ui(self, save_path: Optional[str] = None, retries: int = 5) -> str:
        """
        dump 当前 UI 层级为 XML
        原生页面文本可直接从 XML 提取;若遇到 WebView/H5 渲染则文本缺失,需走 OCR
        注意:uiautomator dump 常返回非零退出码但实际已成功,此处忽略退出码直接验证文件内容
        关键:每次 dump 前先删除旧文件,避免 dump 失败时 cat 读到上次残留 XML 导致误判
        """
        remote = "/sdcard/ui_dump.xml"
        last_err = None
        for attempt in range(retries):
            # 先删除旧文件,确保 cat 读到的一定是本次 dump 的结果
            # (否则 dump 失败时 cat 会读到上次残留 XML,误判为成功)
            subprocess.run(
                self._base_cmd + ["shell", f"rm -f {remote}"],
                capture_output=True,
                text=True,
                timeout=15,
                encoding="utf-8",
                errors="ignore",
            )
            # dump 命令忽略退出码(uiautomator dump 即使成功也可能返回非零)
            subprocess.run(
                self._base_cmd + ["shell", f"uiautomator dump {remote}"],
                capture_output=True,
                text=True,
                timeout=60,
                encoding="utf-8",
                errors="ignore",
            )
            time.sleep(1.0)
            # 读取文件内容(文件不存在则 cat 返回空,触发重试)
            cat = subprocess.run(
                self._base_cmd + ["shell", f"cat {remote}"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="ignore",
            )
            xml_str = cat.stdout or ""
            if xml_str.startswith("<?xml"):
                if save_path:
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    with open(save_path, "w", encoding="utf-8") as f:
                        f.write(xml_str)
                return xml_str
            last_err = f"dump 返回空或非 XML(stdout_len={len(xml_str)}, stderr={cat.stderr.strip()[:80]})"
            # uiautomator 服务偶发挂起:杀掉进程强制重启后再重试
            try:
                subprocess.run(
                    self._base_cmd + ["shell", "pkill -f uiautomator"],
                    capture_output=True, text=True, timeout=5,
                    encoding="utf-8", errors="ignore",
                )
            except Exception:
                pass
            time.sleep(3.0)
        raise RuntimeError(f"uiautomator dump 失败(重试 {retries} 次): {last_err}")

    def find_elements_by_text(self, xml_str: str, text: str) -> List[Dict]:
        """
        在 UI XML 中按文本模糊匹配元素
        返回 [{'bounds': [x1,y1,x2,y2], 'text': ..., 'class': ...}, ...]
        bounds 中心点即可用于 tap
        """
        root = ET.fromstring(xml_str)
        results = []
        for node in root.iter("node"):
            node_text = node.attrib.get("text", "") + node.attrib.get("content-desc", "")
            if text in node_text:
                bounds = node.attrib.get("bounds", "")
                # 形如 "[x1,y1][x2,y2]"
                coords = bounds.replace("][", ",").strip("[]").split(",")
                if len(coords) == 4:
                    x1, y1, x2, y2 = map(int, coords)
                    # 过滤零面积/无效 bounds(如 [0,0,0,0])
                    if x2 <= x1 or y2 <= y1:
                        continue
                    results.append(
                        {
                            "text": node_text,
                            "bounds": [x1, y1, x2, y2],
                            "center": ((x1 + x2) // 2, (y1 + y2) // 2),
                            "class": node.attrib.get("class", ""),
                        }
                    )
        return results

    def extract_all_text(self, xml_str: str, min_len: int = 2) -> List[Dict]:
        """
        从 UI XML 中提取所有可见文本节点(原生页面文本提取入口)
        :param min_len: 文本最小长度,过滤掉单字噪声
        :return [{'text': str, 'bounds': [x1,y1,x2,y2], 'class': str}, ...]
                按 bounds 的 y 坐标升序(从上到下阅读顺序)
        """
        root = ET.fromstring(xml_str)
        items = []
        for node in root.iter("node"):
            # 优先取 text,其次取 content-desc
            text = node.attrib.get("text", "").strip()
            if not text:
                text = node.attrib.get("content-desc", "").strip()
            if not text or len(text) < min_len:
                continue

            bounds = node.attrib.get("bounds", "")
            coords = bounds.replace("][", ",").strip("[]").split(",")
            if len(coords) != 4:
                continue
            x1, y1, x2, y2 = map(int, coords)
            items.append(
                {
                    "text": text,
                    "bounds": [x1, y1, x2, y2],
                    "class": node.attrib.get("class", ""),
                }
            )

        # 按 y 坐标升序,模拟从上到下阅读顺序
        items.sort(key=lambda it: (it["bounds"][1], it["bounds"][0]))
        return items

    def has_native_review_text(self, xml_str: str, min_chars: int = 20) -> bool:
        """
        判断当前页是否有足够的原生文本(用于决定是否降级到 OCR)
        :param min_chars: 文本总字符数阈值,低于此值认为原生文本不足
        """
        items = self.extract_all_text(xml_str)
        total = sum(len(it["text"]) for it in items)
        return total >= min_chars

    # ---------- 滑动验证码处理 ----------

    def detect_slide_captcha(self, xml_str: str) -> Optional[Dict]:
        """
        检测页面是否出现滑动验证滑块
        大众点评反扒会随机弹出"安全验证"/"拖动滑块完成验证"等弹窗
        :return None=无验证;Dict=检测到,含 hint 文本和滑块可能区域
        """
        root = ET.fromstring(xml_str)
        hit_node = None
        for node in root.iter("node"):
            text = (node.attrib.get("text", "") + node.attrib.get("content-desc", "")).strip()
            if "验证" in text or "拖动滑块" in text:
                hit_node = node
                break
        if hit_node is None:
            return None
        # 找到验证弹窗,尝试定位滑块(下半屏的可拖动元素)
        w, h = self.get_screen_size()
        slider = None
        # 优先: Yoda 滑块(阿里系验证码, WebView 渲染, 节点非 clickable)
        #   yodaMoveingBar=滑块本体, yodaBoxWrapper=轨道区域
        for node in root.iter("node"):
            rid = node.attrib.get("resource-id", "")
            bounds = node.attrib.get("bounds", "")
            if "yodaMoveingBar" not in rid or not bounds:
                continue
            coords = bounds.replace("][", ",").strip("[]").split(",")
            if len(coords) != 4:
                continue
            x1, y1, x2, y2 = map(int, coords)
            slider = {"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                      "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2}
            break
        if slider is None:
            # 常规滑块: 在屏幕中下部(0.45h-0.85h)找可点击/可滚动节点
            slider_y_min = int(h * 0.45)
            slider_y_max = int(h * 0.85)
            for node in root.iter("node"):
                clickable = node.attrib.get("clickable", "")
                scrollable = node.attrib.get("scrollable", "")
                bounds = node.attrib.get("bounds", "")
                if not bounds:
                    continue
                coords = bounds.replace("][", ",").strip("[]").split(",")
                if len(coords) != 4:
                    continue
                x1, y1, x2, y2 = map(int, coords)
                cy = (y1 + y2) // 2
                if slider_y_min <= cy <= slider_y_max and (clickable == "true" or scrollable == "true"):
                    # 选最靠左的(滑块起始于左侧)
                    if slider is None or x1 < slider["x1"]:
                        slider = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "cx": (x1 + x2) // 2, "cy": cy}
        return {
            "hint": (hit_node.attrib.get("text", "") or hit_node.attrib.get("content-desc", "")).strip(),
            "slider": slider,
            "screen": (w, h),
        }

    def solve_slide_captcha(self, info: Dict, save_dir: str = "screenshots/captcha") -> bool:
        """
        尝试解决滑动验证码:从滑块当前位置横向滑到屏幕右侧
        :param info: detect_slide_captcha 返回的 dict
        :return True=已尝试滑动(不保证成功);False=无法定位滑块
        """
        w, h = info["screen"]
        slider = info.get("slider")
        # 截图留证
        os.makedirs(save_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self.screenshot(os.path.join(save_dir, f"captcha_{ts}.png"))

        if slider:
            # 有明确滑块节点:从滑块中心滑到屏幕右侧 85%
            x1 = slider["cx"]
            y = slider["cy"]
            x2 = int(w * 0.85)
        else:
            # 无明确节点:用通用坐标(屏幕左侧 15% 滑到右侧 85%,y 在 70%)
            x1 = int(w * 0.15)
            y = int(h * 0.72)
            x2 = int(w * 0.85)
        # 分多段滑动模拟人类拖动轨迹(快速起步->中段匀速->末端减速停顿),
        # 单次匀速 input swipe 易被风控判定为机器操作
        total_dist = x2 - x1
        # 分段比例: 起步加速 10% 短、中段 60% 快、末端 30% 慢(带停顿)
        segments = [
            (0.05, 120),  # (占全程比例, 该段滑动时长 ms) 起步快
            (0.10, 140),
            (0.25, 260),  # 中段
            (0.25, 240),
            (0.20, 300),  # 末端减速
            (0.15, 380),
        ]
        # 归一化比例(可能不精确等于1)
        seg_sum = sum(s[0] for s in segments)
        cur_x = x1
        cur_y = y + random.randint(-3, 3)
        print(f"  [验证] 检测到验证弹窗[{info['hint'][:20]}],"
              f"分段滑动 ({x1},{y})->({x2},{y})")
        for ratio, dur in segments:
            nx = x1 + int(total_dist * (ratio / seg_sum))
            ny = cur_y + random.randint(-4, 4)  # 每段轻微 Y 抖动
            self.shell(f"input swipe {cur_x} {cur_y} {nx} {ny} {dur}")
            cur_x = nx
            cur_y = ny
        # 末端停顿后释放(模拟按住思考)
        time.sleep(random.uniform(0.3, 0.6))
        # 滑动后等待验证结果
        time.sleep(random.uniform(2.0, 3.5))
        return True

    # ---------- 评论详情页检测 ----------

    # 评论评分文本(与 review_parser.SCORE_TEXTS 对齐)
    _REVIEW_SCORES = {
        "很差", "较差", "一般", "好评", "很好", "满意", "超赞",
        "很糟糕", "糟糕", "还行", "不错", "非常满意",
        "超预期", "很棒", "还可以",
    }
    # 店铺星级卡片特征:"· 3.8 星" / "3.8 星" / "3.8星"(详情页底部店铺卡片独有)
    # 用锚定/带"·"的形式匹配,避免列表页评论文本里的"2星半/3星/半颗星"被误判为详情页。
    # 详情页店铺星级节点文本形如"· 3.7 星"(带前缀圆点"·",列表页无此形态)。
    _SHOP_STAR_PAT = re.compile(r'·\s*\d+(?:\.\d+)?\s*星')

    # 详情页回复日期节点的完整形态(独立节点,如"8月11日 11:08"/"2025年8月11日 11:08")
    # 用锚定匹配:仅当整个文本就是"日期+时间"才算详情页特征,
    # 避免列表页长评论正文里子串含"2025年8月29日 12:15pm..."误判为详情页
    _FULL_REPLY_DATE_PAT = re.compile(
        r'^(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})'
        r'\s+\d{1,2}:\d{2}$'
    )

    # 评论列表筛选栏 tab(列表页聚合视图独有,单个评论详情页不会出现该组合)
    # 反向排除用:命中 >=3 个即确认当前在列表页而非详情页,
    # 兜底防止列表页残留星级卡(_SHOP_STAR_PAT)等被误判为详情页
    _LIST_FILTER_TABS = ("全部", "最新", "差评", "中评")

    def detect_review_detail_page(self, xml_str: str) -> bool:
        """
        检测是否在评论详情页
        详情页标志(任一命中即判定,兼容长详情页星级卡片滚出屏幕的情况):
          - 店铺星级卡片("· 3.3 星"):详情页顶部店铺卡
          - "发条友善评论吧":详情页评论区输入框(列表页为"说点什么吧~",不命中)
          - "这条评价内容有帮助吗":详情页独有
          - "发布于X月X日":详情页发布时间(列表页仅日期无前缀)
          - 独立"日期 时:分"节点:详情页评论/回复带时间(列表页日期无时间;
            注意必须锚定整节点,列表页长评论正文子串含日期时间会误命中)
          - 顶部(屏幕高度13%内)同时有"分享"和"更多":详情页导航(列表页顶部为 规则/评价/搜索)
        反向排除:命中评论列表筛选栏(全部/最新/差评/中评) >=3 个,确认在列表页,
        直接返回 False,防止星级卡等列表页残留特征被误判为详情页。
        :return True=在详情页(需 back 返回);False=不在详情页
        """
        root = ET.fromstring(xml_str)
        try:
            _, screen_h = self.get_screen_size()
        except Exception:
            screen_h = 1920  # adb 不可用时(如离线测试)降级到默认高度
        top_threshold = int(screen_h * 0.13)  # 顶部导航栏区域(自适应:屏幕高度13%)
        texts = []
        top_texts = []
        for node in root.iter("node"):
            text = (node.attrib.get("text", "") + node.attrib.get("content-desc", "")).strip()
            if not text:
                continue
            texts.append(text)
            bounds = node.attrib.get("bounds", "")
            coords = bounds.replace("][", ",").strip("[]").split(",")
            if len(coords) == 4:
                try:
                    if int(coords[1]) < top_threshold:  # 顶部导航栏区域(自适应)
                        top_texts.append(text)
                except ValueError:
                    pass
        # 反向排除:评论列表筛选栏(全部/最新/差评/中评)组合命中 >=2 个 -> 确认在评论列表页,非详情页
        # 用子串匹配:uiautomator 的 text+content-desc 常被拼接成"差评差评"/"中评中评"重复串,
        # 精确匹配(t in LIST_TABS)会失败导致反排失效,从而把列表页误判为详情页后 back() 退出列表。
        tab_hits = sum(1 for t in texts if any(tab in t for tab in self._LIST_FILTER_TABS))
        if tab_hits >= 2:
            return False
        # 详情页特征判断(任一命中即判定)
        if any(self._SHOP_STAR_PAT.search(t) for t in texts):
            return True
        joined = "|".join(texts)
        if "发条友善评论吧" in joined:
            return True
        if "这条评价内容有帮助吗" in joined:
            return True
        if re.search(r"发布于\s*\d{1,2}月\d{1,2}日", joined):
            return True
        # 仅当存在"整个文本=日期+时间"的独立节点才算详情页特征(锚定匹配)
        if any(self._FULL_REPLY_DATE_PAT.match(t) for t in texts):
            return True
        if any("分享" in t for t in top_texts) and any("更多" in t for t in top_texts):
            return True
        return False

    def detect_review_detail_page_strict(self, xml_str: str) -> bool:
        """
        严格判定评论详情页(用于 back() 后验证是否已回到列表,防止过度返回)
        详情页 = 店铺星级卡片 + (回复按钮 或 商家标签)
        商店主页虽也有星级卡片,但没有"回复"按钮/商家标签,
        用此方法可区分,避免把商店页误判为详情页再次 back() 把评论列表也退出
        :return True=确定仍在评论详情页
        """
        if not self.detect_review_detail_page(xml_str):
            return False
        items = self.extract_all_text(xml_str, min_len=1)
        has_reply_btn = any(it["text"] == "回复" for it in items)
        has_merchant = any("（商家）" in it["text"] or "(商家)" in it["text"] for it in items)
        return has_reply_btn or has_merchant

    def detect_image_viewer_page(self, xml_str: str) -> bool:
        """
        检测是否在评论图片大图浏览页(全屏看图)
        3d步点击商家回复候选时可能误点评论图片进入此页,特征:
          - "@用户名"节点(如"@segdsh";列表页/详情页用户名均无@前缀,此形态大图页独有)
          - 图片序号节点(如"1 / 2"/"3/9")
          - 无评论列表筛选栏(全部/最新/差评/中评)
        该页面对 detect_review_detail_page 返回 False(无星级卡/无"发条友善评论吧"),
        若不单独识别会被误判为"在列表"放行,导致后续 dump 全为图片页空转卡死。
        :return True=在图片大图浏览页(需 back 返回);False=不在
        """
        root = ET.fromstring(xml_str)
        texts = []
        for node in root.iter("node"):
            text = (node.attrib.get("text", "") + node.attrib.get("content-desc", "")).strip()
            if not text:
                continue
            texts.append(text)
        # 反向确认:页面含评论列表筛选栏(任意tab子串)则肯定不是大图页
        if any(tab in t for t in texts for tab in ("全部", "最新", "差评", "中评")):
            return False
        has_at_user = any(t.startswith("@") and len(t) > 1 for t in texts)
        has_img_seq = any(re.search(r"^\d+\s*/\s*\d+$", t) for t in texts)
        return has_at_user and has_img_seq

    # 商家回复日期格式:8月11日  11:08 / 2025年8月11日  11:08 / 2025-08-11 11:08
    _REPLY_DATE_PAT = re.compile(
        r'(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})'
        r'\s+\d{1,2}:\d{2}'
    )
    # 商家回复日期(不带时间):较老的回复详情页只显示日期不显示具体时间,如"2025年9月30日"
    _MERCHANT_REPLY_DATE_ONLY_PAT = re.compile(
        r'^(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})$'
    )
    # 商家回复日期(24小时内相对时间):如"3分钟前"/"2小时前"/"刚刚",由调用方换算成今天日期
    _MERCHANT_REPLY_REL_PAT = re.compile(
        r'^(\d+\s*分钟前|\d+\s*小时前|刚刚|昨天|前天)$'
    )

    def extract_merchant_reply_date(self, xml_str: str) -> str:
        """
        从评论详情页提取商家回复日期
        详情页结构:评论者 → 其他用户评论(带日期时间) → 丰裕（商家）→ 商家回复内容 → 回复日期(带时间) → 回复按钮
        只取"（商家）"标签之后的日期,排除其他用户评论的日期
        若有多条商家回复,取最早的一条回复日期
        :return 日期原始文本(由调用方标准化),未找到返回空串
        """
        items = self.extract_all_text(xml_str, min_len=1)
        dates = []  # 收集所有商家回复的日期
        for i, it in enumerate(items):
            text = it["text"]
            if "（商家）" not in text and "(商家)" not in text:
                continue
            # 从商家标签后找日期:商家回复日期在回复内容与"回复"按钮之间
            for j in range(i + 1, len(items)):
                t = items[j]["text"]
                if "（商家）" in t or "(商家)" in t:
                    break  # 下一条商家回复,本段结束
                m = self._REPLY_DATE_PAT.search(t)
                if m:
                    dates.append(m.group(1))  # 日期部分(不含时间)
                    break
                m2 = self._MERCHANT_REPLY_DATE_ONLY_PAT.search(t)
                if m2:
                    dates.append(m2.group(1))  # 无时间日期(较老回复)
                    break
                m3 = self._MERCHANT_REPLY_REL_PAT.search(t)
                if m3:
                    dates.append(m3.group(1))  # 相对时间(24小时内回复)
                    break
                if t == "回复":
                    break  # 本段未找到日期则结束,避免越界到其他用户评论
        if not dates:
            return ""
        # 取最早:优先带年份完整日期,其次"X月X日";相对时间(分钟/小时前)视为最新排最前
        def _key(d):
            if re.search(r'(分钟前|小时前|刚刚|昨天|前天)', d):
                return 99999999
            m = re.match(r"^(\d{4})年?(\d{1,2})月(\d{1,2})日$", d)
            if m:
                return int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))
            m = re.match(r"^(\d{1,2})月(\d{1,2})日$", d)
            if m:
                return int(m.group(1)) * 100 + int(m.group(2))
            m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", d)
            if m:
                return int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3))
            return 0
        dates.sort(key=_key)
        return dates[0]

    def extract_detail_merchant_reply(self, xml_str: str) -> str:
        """
        从评论详情页提取商家回复的完整内容(可能有多条回复)
        用于与列表页卡片 merchant_reply 做精确匹配(回复全文是最强归属标识)。
        注意:同一条评论可能有多次商家回复(商家补回复/追评回复),
        列表页卡片显示其中一条,详情页会列出全部,必须全部收集,
        否则 _composite_match 只拿第一条会因"回复全文不匹配"误拒绝。
        详情页结构:... → XX（商家）→ 回复内容1 → 回复日期 → 回复按钮
                          → XX（商家）→ 回复内容2 → 回复日期 → ...
        :return 去前缀标签/去空白占位符的全部回复段落,以"||"分隔;未找到返回空串
        """
        items = self.extract_all_text(xml_str, min_len=1)
        # 商家标签可能带店铺名前缀,如"丰裕（商家）: 尊敬的顾客..."
        strip_pat = re.compile(r'^(?:[^:：]{0,10}[（(]商家[）)])\s*[:：]?\s*')
        results = []
        i = 0
        n = len(items)
        while i < n:
            text = items[i]["text"]
            if "（商家）" not in text and "(商家)" not in text:
                i += 1
                continue
            parts = []
            rest = strip_pat.sub("", text)  # 标签节点若已合并回复内容
            if rest:
                parts.append(rest)
            j = i + 1
            while j < n:
                t = items[j]["text"]
                if "（商家）" in t or "(商家)" in t:
                    break  # 下一条商家回复,本段结束
                if t == "回复":
                    break  # 回复按钮,本段结束
                if (self._REPLY_DATE_PAT.search(t)
                        or self._MERCHANT_REPLY_DATE_ONLY_PAT.search(t)
                        or self._MERCHANT_REPLY_REL_PAT.search(t)):
                    break  # 到达回复日期节点,本段结束
                if not t.strip():
                    j += 1
                    continue
                # 过滤详情页噪声节点(头像/用户昵称/语音评价按钮等非回复内容)
                # 注意:语音评价按钮"语音评价"+"播放"两个节点 y 坐标常与商家回复内容
                # 节点重叠(浮动在回复右下角),uiautomator 按 y 升序会插入到回复内容之后、
                # 回复日期之前,若不过滤会被并入回复内容末尾,污染 _composite_match 的
                # 全文比对(虽然 norm_c in norm_d 仍能通过,但回复内容已不纯净)
                if t in ("头像", "语音评价", "播放", "图片", "收起") or "用户昵称" in t:
                    j += 1
                    continue
                parts.append(t)
                j += 1
            content = "".join(parts)
            content = re.sub(r"[\s\uFFFC\uFFFD]+", "", content)
            if len(content) >= 10:
                results.append(content)
            # 跳到本段结束处继续扫描(日期节点后可能还有下一条商家回复)
            i = j if j > i else i + 1
        return "||".join(results)

    # 详情页顶部区域的噪声节点(非评论者用户名)
    _DETAIL_TOP_NOISE = {"返回", "分享", "更多", "关注", "头像", "收藏", "语音评价",
                         "说点什么吧~", "说点什么吧～", "发条友善评论吧～", "喜欢就评论一下吧～"}

    def extract_detail_user(self, xml_str: str) -> str:
        """
        提取评论详情页顶部的评论者用户名(用于校验点进的是否目标评论)
        详情页顶部结构: 返回/分享/更多 → 头像 → 用户名(评论者)
        :return 用户名,未找到返回空串
        """
        items = self.extract_all_text(xml_str, min_len=1)
        # 详情页顶部区域高度:用户名/头像在屏幕顶部,用比例而非固定像素,
        # 兼容不同分辨率(手机/平板详情页顶部布局高度不同)
        _, screen_h = self.get_screen_size()
        top_limit = int(screen_h * 0.16)
        for it in items:
            bx = it["bounds"]
            # 过滤零面积元素(bounds如[0,0][0,0]的y=0会排在用户名前,干扰提取)
            if bx[2] <= bx[0] or bx[3] <= bx[1]:
                continue
            if bx[1] >= top_limit:
                break  # 已超出顶部区域(节点按y升序),未找到用户名
            text = it["text"]
            if text in self._DETAIL_TOP_NOISE:
                continue
            # 输入框占位符(如"说点什么吧~"/"发条友善评论吧～")非用户名
            if "评论吧" in text or "说点什么" in text:
                continue
            # 图片/视频节点(如"图片01"/"播放")非用户名,大图浏览页常以它们开头
            if re.match(r"^图片\d*$", text) or "播放" in text:
                continue
            # 发布时间节点("发布于X月X日"/"发布于X天前")非用户名。
            # 长评论详情页进入时自动滚到回复区,上滚一次后可能只露出
            # 发布时间而用户名仍在屏外,若不排除会被误当作用户名,
            # 导致复合身份校验误判"用户名不同"而放弃(实际是没滚到位)
            if text.startswith("发布于"):
                continue
            # 店铺人均价格节点(如"¥20/人")会出现在详情页顶部,非用户名
            if re.search(r"[¥￥]", text) or "人均" in text or re.search(r"/\s*人\s*$", text):
                continue
            if len(text) > 20:  # 用户名不会太长(可能是正文等)
                continue
            return text
        return ""

    # 详情页评分档位(用于跳过评分节点,定位评论内容前缀)
    _DETAIL_SCORE_TEXTS = {
        "很差", "较差", "一般", "好评", "很好", "满意", "超赞",
        "很糟糕", "糟糕", "还行", "不错", "非常满意",
        "超预期", "很棒", "还可以", "还不错",
    }

    def extract_detail_comment_info(self, xml_str: str) -> Dict[str, str]:
        """
        从评论详情页提取评论者身份信息(用于与列表页卡片做复合身份匹配)

        解决匿名用户用户名相同(如多个"匿名用户")时仅靠用户名无法区分归属的问题,
        额外提取评论发布日期和内容前缀作为辅助识别字段。

        详情页顶部结构(y升序):
            返回/分享/更多 → 头像 → 用户名(评论者) → "发布于X" → [评分] →
            [口味/环境/服务] → 评论内容 → 店铺星级卡片 → ... → 商家回复

        :return {'user': 评论者用户名, 'date': 评论发布日期(已去"发布于"前缀),
                 'score': 评分档位(很差/较差/一般/好评等,详情页顶部评分节点),
                 'content_prefix': 评论内容前N字(已去对象占位符)}
        """
        items = self.extract_all_text(xml_str, min_len=1)
        user = ""
        date = ""
        score = ""
        content_prefix = ""

        # 1. 提取用户名(复用 extract_detail_user 的过滤逻辑,保证行为一致)
        user = self.extract_detail_user(xml_str)

        # 2. 提取评论发布日期("发布于X天前" / "发布于X月X日")
        publish_pat = re.compile(r"^发布于\s*(.+)$")
        date_idx = -1
        for i, it in enumerate(items):
            m = publish_pat.match(it["text"])
            if m:
                date = m.group(1).strip()
                date_idx = i
                break

        # 3. 提取评论内容前缀
        #    找到用户名节点索引(同 extract_detail_user 逻辑),从其后的位置开始扫描
        #    顶部区域高度同样用比例(与 extract_detail_user 的 top_limit 一致)
        _, screen_h = self.get_screen_size()
        top_limit = int(screen_h * 0.16)
        user_idx = -1
        for i, it in enumerate(items):
            bx = it["bounds"]
            if bx[2] <= bx[0] or bx[3] <= bx[1]:
                continue
            if bx[1] >= top_limit:
                break
            text = it["text"]
            if text in self._DETAIL_TOP_NOISE:
                continue
            if "评论吧" in text or "说点什么" in text:
                continue
            if len(text) > 20:
                continue
            user_idx = i
            break

        # 内容扫描起点:用户名和日期节点中较后者之后
        start_idx = max(user_idx, date_idx) + 1
        for i in range(start_idx, len(items)):
            text = items[i]["text"]
            # 跳过用户名/日期节点本身
            if user and text == user:
                continue
            # 跳过评分档位,同时记录评分(详情页顶部评论者评分节点,用于跨屏拼回卡片)
            if text in self._DETAIL_SCORE_TEXTS:
                if not score:
                    score = text
                continue
            # 跳过口味/环境/服务子评分
            if re.match(r"^(口味|环境|服务)\s*[:：]", text):
                continue
            # 跳过人均价格
            if re.match(r"^[￥¥]\s*\d+", text):
                continue
            # 跳过店铺星级卡片("店名 · X.X 星")
            if re.search(r"·\s*\d+\.?\d*\s*星", text):
                continue
            # 跳过UI噪声(顶部/底部导航/操作栏)
            if text in self._DETAIL_TOP_NOISE:
                continue
            # 跳过帮助/回复/收藏等操作文本
            if text in {"有帮助", "没帮助", "回复", "收藏", "评论",
                        "说点什么吧~", "说点什么吧～", "发条友善评论吧～",
                        "这条评价内容有帮助吗？"}:
                continue
            # 跳过纯数字/评论数标签
            if text.isdigit() or re.match(r"^\d+\s*条评论$", text) or text == "条评论":
                continue
            # 遇到商家标签 → 已到商家回复区,停止搜索
            if "（商家）" in text or "(商家)" in text:
                break
            # 找到第一个长文本节点作为内容前缀(>8字,过滤短标签)
            if len(text) >= 8:
                # 去除对象占位符 ￼(U+FFFC) 等不可见字符
                content_prefix = re.sub(r"[\uFFFC\uFFFD]", "", text).strip()
                break

        return {"user": user, "date": date, "score": score,
                "content_prefix": content_prefix}

    # ---------- 截图 ----------

    def screenshot(self, save_path: str) -> str:
        """截图并保存到本地"""
        remote = "/sdcard/screenshot.png"
        self.shell(f"screencap -p {remote}")
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        self.run(["pull", remote, save_path])
        self.shell(f"rm {remote}")
        return save_path

    def screenshot_region(self, full_path: str, region: Tuple[int, int, int, int]) -> str:
        """
        截图后裁剪指定区域,用于 OCR 精确识别
        :param region: (x1, y1, x2, y2)
        """
        from PIL import Image

        self.screenshot(full_path)
        img = Image.open(full_path)
        cropped = img.crop(region)
        cropped.save(full_path)
        return full_path

    # ---------- 反扒辅助 ----------

    def random_sleep(self, min_s: float = 2.0, max_s: float = 5.0):
        """长随机延时,用于页面切换后等待加载,降低请求频率"""
        time.sleep(random.uniform(min_s, max_s))
