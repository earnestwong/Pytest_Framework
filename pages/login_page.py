from playwright.sync_api import Page
from pages.base_page import BasePage

class LoginPage(BasePage):
    """登录页面的Page Object类"""
    
    def __init__(self, page: Page):
        super().__init__(page)
        
        # 页面元素定位器
        self.username_input = "#loginPanel > form > div:nth-child(2) > input"
        self.password_input = "#loginPanel > form > div:nth-child(4) > input"
        self.login_button = "#loginPanel > form > div:nth-child(5) > input"
        self.register_link = "#loginPanel > p:nth-child(3) > a"
        self.forgot_login_link = "#loginPanel > p:nth-child(2) > a"
        self.error_message = "#rightPanel > p"
        
    def login(self, username: str, password: str):
        """执行登录操作"""
        self.fill(self.username_input, username)
        self.fill(self.password_input, password)
        self.click(self.login_button)
    
    def get_error_message(self) -> str:
        """获取错误信息"""
        return self.get_text(self.error_message) if self.is_element_visible(self.error_message) else ""
    
    def go_to_register(self):
        """跳转到注册页面"""
        self.click(self.register_link)
    
    def go_to_forgot_login(self):
        """跳转到忘记登录信息页面"""
        self.click(self.forgot_login_link)
    
    def is_login_successful(self) -> bool:
        """判断登录是否成功"""
        return self.is_element_visible("#leftPanel") and self.is_element_visible("#accountTable")

class LoginPage:
    """登录页面的Page Object类"""
    
    def __init__(self, page: Page):
        self.page = page
        
        # 页面元素定位器
        self.username_input = "#loginPanel > form > div:nth-child(2) > input"
        self.password_input = "#loginPanel > form > div:nth-child(4) > input"
        self.login_button = "#loginPanel > form > div:nth-child(5) > input"
        self.register_link = "#loginPanel > p:nth-child(3) > a"
        self.forgot_login_link = "#loginPanel > p:nth-child(2) > a"
        self.error_message = "#rightPanel > p"
        
    def login(self, username: str, password: str):
        """执行登录操作"""
        self.page.fill(self.username_input, username)
        self.page.fill(self.password_input, password)
        self.page.click(self.login_button)
    
    def get_error_message(self) -> str:
        """获取错误信息"""
        return self.page.text_content(self.error_message) if self.page.is_visible(self.error_message) else ""
    
    def go_to_register(self):
        """跳转到注册页面"""
        self.page.click(self.register_link)
    
    def go_to_forgot_login(self):
        """跳转到忘记登录信息页面"""
        self.page.click(self.forgot_login_link)
    
    def is_login_successful(self) -> bool:
        """判断登录是否成功"""
        return self.page.is_visible("#leftPanel") and self.page.is_visible("#accountTable")