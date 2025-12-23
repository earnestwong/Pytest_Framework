import pytest
from pages.login_page import LoginPage

class TestLogin:
    """登录页面的测试用例"""
    
    @pytest.mark.smoke
    def test_login_successful(self, page):
        """测试登录成功场景"""
        login_page = LoginPage(page)
        
        # 执行登录操作
        login_page.login("john", "demo")
        
        # 验证登录是否成功
        assert login_page.is_login_successful(), "登录失败"
        assert page.title() == "ParaBank | Accounts Overview", "页面标题不正确"
    
    @pytest.mark.regression
    def test_login_invalid_username(self, page):
        """测试无效用户名登录场景"""
        login_page = LoginPage(page)
        
        # 使用无效用户名登录
        login_page.login("invalid_user", "demo")
        
        # 验证错误信息
        error_message = login_page.get_error_message()
        assert "An internal error has occurred" in error_message or "Login and/or password are wrong." in error_message, f"错误信息不正确: {error_message}"
    
    @pytest.mark.regression
    def test_login_invalid_password(self, page):
        """测试无效密码登录场景"""
        login_page = LoginPage(page)
        
        # 使用无效密码登录
        login_page.login("john", "invalid_password")
        
        # 验证错误信息
        error_message = login_page.get_error_message()
        assert "An internal error has occurred" in error_message or "Login and/or password are wrong." in error_message, f"错误信息不正确: {error_message}"
    
    @pytest.mark.regression
    def test_login_empty_credentials(self, page):
        """测试空凭证登录场景"""
        login_page = LoginPage(page)
        
        # 使用空用户名和密码登录
        login_page.login("", "")
        
        # 验证错误信息
        error_message = login_page.get_error_message()
        assert "An internal error has occurred" in error_message or "Login and/or password are wrong." in error_message, f"错误信息不正确: {error_message}"
    
    @pytest.mark.regression
    def test_go_to_register(self, page):
        """测试跳转到注册页面场景"""
        login_page = LoginPage(page)
        
        # 跳转到注册页面
        login_page.go_to_register()
        
        # 验证是否跳转到注册页面
        assert page.title() == "ParaBank | Register for Free Online Account Access", "页面标题不正确"
        assert "Register for Online Account Access" in page.text_content("#rightPanel > h1"), "页面内容不正确"
    
    @pytest.mark.regression
    def test_go_to_forgot_login(self, page):
        """测试跳转到忘记登录信息页面场景"""
        login_page = LoginPage(page)
        
        # 跳转到忘记登录信息页面
        login_page.go_to_forgot_login()
        
        # 验证是否跳转到忘记登录信息页面
        assert page.title() == "ParaBank | Forgot Login Info", "页面标题不正确"
        assert "Forgot Login Info" in page.text_content("#rightPanel > h1"), "页面内容不正确"