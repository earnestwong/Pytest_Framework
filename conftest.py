import pytest
from playwright.sync_api import Playwright, sync_playwright
import os

@pytest.fixture(scope="session")
def playwright():
    """创建Playwright实例"""
    with sync_playwright() as playwright:
        yield playwright

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

@pytest.fixture
def base_url():
    """获取基础URL"""
    return os.getenv("BASE_URL", "https://parabank.parasoft.com/parabank/index.htm")