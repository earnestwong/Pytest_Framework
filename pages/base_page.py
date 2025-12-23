from playwright.sync_api import Page
import time

class BasePage:
    """基础页面类，封装所有页面共有的方法和属性"""
    
    def __init__(self, page: Page):
        self.page = page
        self.base_url = "https://parabank.parasoft.com/parabank/index.htm"
    
    def navigate_to(self, url: str):
        """导航到指定URL"""
        self.page.goto(url)
    
    def navigate_to_home(self):
        """导航到首页"""
        self.page.goto(self.base_url)
    
    def get_title(self) -> str:
        """获取页面标题"""
        return self.page.title()
    
    def get_current_url(self) -> str:
        """获取当前页面URL"""
        return self.page.url
    
    def wait_for_element(self, locator: str, timeout: int = 30000):
        """等待元素可见"""
        self.page.wait_for_selector(locator, timeout=timeout)
    
    def click(self, locator: str):
        """点击元素"""
        self.page.click(locator)
    
    def fill(self, locator: str, value: str):
        """填写文本框"""
        self.page.fill(locator, value)
    
    def get_text(self, locator: str) -> str:
        """获取元素文本"""
        return self.page.text_content(locator)
    
    def is_element_visible(self, locator: str) -> bool:
        """判断元素是否可见"""
        return self.page.is_visible(locator)
    
    def take_screenshot(self, name: str):
        """截图"""
        self.page.screenshot(path=f"screenshots/{name}_{time.time()}.png")
    
    def scroll_to_element(self, locator: str):
        """滚动到元素位置"""
        element = self.page.locator(locator)
        element.scroll_into_view_if_needed()
    
    def get_attribute(self, locator: str, attribute: str) -> str:
        """获取元素属性值"""
        return self.page.get_attribute(locator, attribute)
    
    def select_option(self, locator: str, option: str):
        """选择下拉选项"""
        self.page.select_option(locator, option)
    
    def check(self, locator: str):
        """勾选复选框"""
        self.page.check(locator)
    
    def uncheck(self, locator: str):
        """取消勾选复选框"""
        self.page.uncheck(locator)