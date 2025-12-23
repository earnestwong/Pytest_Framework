# Pytest + Playwright Web 测试框架

这是一个基于 pytest + Playwright + Python 的 Web 自动化测试框架练手项目，针对 ParaBank 网站进行测试。

## 项目特点

- 使用 Page Object Model (POM) 设计模式，提高代码复用性和可维护性
- 支持数据驱动测试，可通过外部数据源（Excel、JSON、YAML等）驱动测试用例执行
- 生成详细的测试报告，包括 HTML 报告和可选的 Allure 报告
- 支持截图和视频录制，方便问题定位和调试
- 集成到 CI/CD 流程中，实现自动化测试执行

## 项目结构

```
Pytest_Framework/
├── .qwen/                  # 工具配置文件
├── .specify/               # 项目规范和计划
├── pages/                  # Page Object 类
│   └── login_page.py       # 登录页面的 Page Object 类
├── tests/                  # 测试用例
│   └── test_login.py       # 登录页面的测试用例
├── utils/                  # 工具函数
├── data/                   # 测试数据
├── reports/                # 测试报告
├── videos/                 # 测试视频
├── screenshots/            # 测试截图
├── requirements.txt        # 项目依赖
├── pytest.ini              # pytest 配置文件
├── conftest.py             # 测试夹具配置
└── README.md               # 项目说明文档
```

## 安装步骤

1. 克隆项目到本地

2. 创建虚拟环境
   ```bash
   python -m venv venv
   ```

3. 激活虚拟环境
   - Windows: `venv\Scripts\activate`
   - macOS/Linux: `source venv/bin/activate`

4. 安装依赖包
   ```bash
   pip install -r requirements.txt
   ```

5. 安装 Playwright 浏览器驱动
   ```bash
   playwright install
   ```

## 使用方法

### 运行测试用例

1. 运行所有测试用例
   ```bash
   pytest
   ```

2. 运行指定测试文件
   ```bash
   pytest tests/test_login.py
   ```

3. 运行指定测试用例
   ```bash
   pytest tests/test_login.py::TestLogin::test_login_successful
   ```

4. 运行带有标签的测试用例
   ```bash
   pytest -m smoke
   ```

### 查看测试报告

测试执行完成后，HTML 报告将生成在 `reports/report.html` 文件中，可以直接在浏览器中打开查看。

### 配置测试环境

可以通过修改 `pytest.ini` 文件中的 `BASE_URL` 环境变量来配置测试环境的基础 URL。

## 测试用例设计

### 登录页面测试

- 登录成功场景
- 无效用户名登录场景
- 无效密码登录场景
- 空凭证登录场景
- 跳转到注册页面场景
- 跳转到忘记登录信息页面场景

### 后续扩展

- 注册页面测试
- 账户管理页面测试
- 转账功能测试
- 账单支付测试
- 贷款申请测试

## 技术栈

- Python 3.8+
- Pytest
- Playwright
- Page Object Model (POM)
- pytest-playwright
- pytest-html
- pytest-cov

## 开发流程

1. 分析测试需求，确定测试范围
2. 设计 Page Object 类，封装页面元素和操作
3. 编写测试用例，使用 pytest 框架和 Playwright API
4. 执行测试用例，分析测试结果
5. 优化测试框架，提高测试效率和稳定性

## 贡献

欢迎提交 Issue 和 Pull Request 来改进这个测试框架。

## 许可证

MIT License