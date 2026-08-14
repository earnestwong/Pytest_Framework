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

    def __init__(self, device_serial: Optional[str] = None, adb_path: str = "adb"):
        """
        :param device_serial: 设备序列号,MuMu 默认 127.0.0.1:7555,留空则取首个设备
        :param adb_path: adb 可执行文件路径,已加入 PATH 时用默认值即可
        """
        self.device_serial = device_serial
        self.adb_path = adb_path
        self._base_cmd = [adb_path]
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
        """获取屏幕分辨率"""
        out = self.shell("wm size")
        # 形如 "Physical size: 1080x1920"
        parts = out.split(":")[-1].strip().split("x")
        return int(parts[0]), int(parts[1])

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
                timeout=5,
                encoding="utf-8",
                errors="ignore",
            )
            # dump 命令忽略退出码(uiautomator dump 即使成功也可能返回非零)
            subprocess.run(
                self._base_cmd + ["shell", f"uiautomator dump {remote}"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="ignore",
            )
            time.sleep(0.5)
            # 读取文件内容(文件不存在则 cat 返回空,触发重试)
            cat = subprocess.run(
                self._base_cmd + ["shell", f"cat {remote}"],
                capture_output=True,
                text=True,
                timeout=10,
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
        if not hit_node:
            return None
        # 找到验证弹窗,尝试定位滑块(下半屏的可拖动元素)
        w, h = self.get_screen_size()
        # 滑块通常在弹窗下半部,先用屏幕中下部作为搜索区域
        slider_y_min = int(h * 0.55)
        slider_y_max = int(h * 0.85)
        # 在该区域找可点击节点(滑块通常 clickable=true)
        slider = None
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
        # 慢速水平滑动(模拟人手拖动,800-1000ms)
        duration = random.randint(800, 1000)
        # 加轻微 Y 抖动
        y1 = y + random.randint(-5, 5)
        y2 = y + random.randint(-5, 5)
        print(f"  [验证] 检测到验证弹窗[{info['hint'][:20]}],尝试滑动 ({x1},{y1})->({x2},{y2}) {duration}ms")
        self.shell(f"input swipe {x1} {y1} {x2} {y2} {duration}")
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
    _SHOP_STAR_PAT = re.compile(r'·?\s*\d+(?:\.\d+)?\s*星')

    def detect_review_detail_page(self, xml_str: str) -> bool:
        """
        检测是否在评论详情页
        详情页标志(任一命中即判定,兼容长详情页星级卡片滚出屏幕的情况):
          - 店铺星级卡片("· 3.3 星"):详情页顶部店铺卡
          - "发条友善评论吧":详情页评论区输入框(列表页为"说点什么吧~",不命中)
          - "这条评价内容有帮助吗":详情页独有
          - "发布于X月X日":详情页发布时间(列表页仅日期无前缀)
          - "X月X日 时:分":详情页评论/回复带时间(列表页日期无时间)
          - 顶部(y<250)同时有"分享"和"更多":详情页导航(列表页顶部为 规则/评价/搜索)
        :return True=在详情页(需 back 返回);False=不在详情页
        """
        root = ET.fromstring(xml_str)
        texts = []
        top_texts = []
        for node in root.iter("node"):
            text = (node.attrib.get("text", "") + node.attrib.get("content-desc", "")).strip()
            if not text:
                continue
            if self._SHOP_STAR_PAT.search(text):
                return True
            texts.append(text)
            bounds = node.attrib.get("bounds", "")
            coords = bounds.replace("][", ",").strip("[]").split(",")
            if len(coords) == 4:
                try:
                    if int(coords[1]) < 250:  # 顶部导航栏区域
                        top_texts.append(text)
                except ValueError:
                    pass
        joined = "|".join(texts)
        if "发条友善评论吧" in joined:
            return True
        if "这条评价内容有帮助吗" in joined:
            return True
        if re.search(r"发布于\s*\d{1,2}月\d{1,2}日", joined):
            return True
        if self._REPLY_DATE_PAT.search(joined):
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

    # 商家回复日期格式:8月11日  11:08 / 2025年8月11日  11:08 / 2025-08-11 11:08
    _REPLY_DATE_PAT = re.compile(
        r'(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})'
        r'\s+\d{1,2}:\d{2}'
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
                if t == "回复":
                    break  # 本段未找到日期则结束,避免越界到其他用户评论
        if not dates:
            return ""
        # 取最早:优先带年份完整日期,其次"X月X日"
        def _key(d):
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

    # 详情页顶部区域的噪声节点(非评论者用户名)
    _DETAIL_TOP_NOISE = {"返回", "分享", "更多", "关注", "头像", "收藏"}

    def extract_detail_user(self, xml_str: str) -> str:
        """
        提取评论详情页顶部的评论者用户名(用于校验点进的是否目标评论)
        详情页顶部结构: 返回/分享/更多 → 头像 → 用户名(评论者)
        :return 用户名,未找到返回空串
        """
        items = self.extract_all_text(xml_str, min_len=1)
        for it in items:
            bx = it["bounds"]
            if bx[1] >= 300:
                break  # 已超出顶部区域(节点按y升序),未找到用户名
            text = it["text"]
            if text in self._DETAIL_TOP_NOISE:
                continue
            if len(text) > 20:  # 用户名不会太长(可能是正文等)
                continue
            return text
        return ""

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
