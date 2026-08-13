import pytest
from playwright.sync_api import Playwright, sync_playwright
import os

# ========== 通用配置 ==========
# MuMu 模拟器 ADB 端口(老版本 7555,新版本 16384,按实际填写)
MUMU_ADB_PORT = os.getenv("MUMU_ADB_PORT", "7555")
MUMU_DEVICE_SERIAL = f"127.0.0.1:{MUMU_ADB_PORT}"


@pytest.fixture(scope="session")
def playwright():
    """创建Playwright实例"""
    with sync_playwright() as playwright:
        yield playwright


@pytest.fixture(scope="session")
def adb_device():
    """
    连接 MuMu 模拟器,返回已连接的 ADBHelper 实例
    session 级别复用,避免反复连接
    """
    from utils.adb_helper import ADBHelper

    adb = ADBHelper(device_serial=MUMU_DEVICE_SERIAL)
    # 主动连接 MuMu
    adb.connect(f"127.0.0.1:{MUMU_ADB_PORT}")

    # 校验设备在线
    devices = adb.get_devices()
    if MUMU_DEVICE_SERIAL not in devices:
        pytest.fail(
            f"MuMu 设备未连接: {MUMU_DEVICE_SERIAL},"
            f"当前可用设备: {devices}\n"
            f"请确认:1) MuMu 已启动 2) ADB 端口正确(环境变量 MUMU_ADB_PORT)"
        )

    print(f"[ADB] 已连接设备: {MUMU_DEVICE_SERIAL}, 分辨率: {adb.get_screen_size()}")
    yield adb


@pytest.fixture(scope="session")
def ocr_engine():
    """
    初始化 PaddleOCR 引擎,session 级别复用
    首次加载模型较慢(约 5-10s)
    """
    from utils.ocr_helper import OCRHelper

    use_gpu = os.getenv("OCR_USE_GPU", "0") == "1"
    return OCRHelper(use_gpu=use_gpu)


@pytest.fixture
def dianping_page(adb_device, ocr_engine):
    """
    大众点评店铺页 Page Object
    每个测试用例独立实例,避免状态污染
    """
    from pages.dianping_shop_page import DianpingShopPage

    return DianpingShopPage(adb=adb_device, ocr=ocr_engine)

@pytest.fixture(scope="session")
def browser(playwright):
    """创建浏览器实例"""
    browser = playwright.chromium.launch(
        headless=False,  # 非无头模式，方便调试
        slow_mo=1000,  # 慢动作执行，方便观察
    )
    yield browser
    browser.close()

@pytest.fixture
def context(browser):
    """创建浏览器上下文"""
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},  # 设置视口大小
        record_video_dir="videos/",  # 录制视频目录
        record_video_size={"width": 1920, "height": 1080},  # 视频大小
    )
    yield context
    context.close()

@pytest.fixture
def page(context):
    """创建页面实例"""
    page = context.new_page()
    page.goto(os.getenv("BASE_URL", "https://parabank.parasoft.com/parabank/index.htm"))
    yield page
    page.close()

@pytest.fixture(scope="session")
def custom_base_url():
    """获取基础URL"""
    return os.getenv("BASE_URL", "https://parabank.parasoft.com/parabank/index.htm")