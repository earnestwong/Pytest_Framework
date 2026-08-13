# Pytest + Playwright Web 测试框架

这是一个基于 pytest + Playwright + Python 的 Web 自动化测试框架练手项目，针对 ParaBank 网站进行测试；并扩展了大众点评 App 评价采集能力（ADB + uiautomator 原生文本直读，OCR 兜底）。

## 项目特点

- 使用 Page Object Model (POM) 设计模式，提高代码复用性和可维护性
- 支持数据驱动测试，可通过外部数据源（Excel、JSON、YAML等）驱动测试用例执行
- 生成详细的测试报告，包括 HTML 报告和可选的 Allure 报告
- 支持截图和视频录制，方便问题定位和调试
- 集成到 CI/CD 流程中，实现自动化测试执行
- 扩展模块:大众点评 App 评价采集(ADB 直连 + uiautomator2 原生文本 + PaddleOCR 兜底)

## 项目结构

```
Pytest_Framework/
├── pages/                       # Page Object 类
│   ├── base_page.py             # Web 页面基类
│   ├── login_page.py            # 登录页面
│   └── dianping_shop_page.py    # 大众点评店铺页 Page Object
├── tests/                       # 测试用例
│   ├── test_login.py            # 登录页测试
│   └── test_dianping_reviews.py # 大众点评采集测试
├── utils/                       # 工具函数
│   ├── adb_helper.py            # ADB 封装(滑动/点击/dump/验证码处理)
│   ├── review_parser.py         # 评价卡片结构化解析
│   ├── review_summarizer.py     # 跨屏去重汇总
│   ├── csv_exporter.py          # CSV 导出(utf8mb4/日期标准化)
│   └── ocr_helper.py            # PaddleOCR 封装(H5 兜底)
├── collect_native.py            # 采集脚本:原生文本直读(主用)
├── collect_ocr.py               # 采集脚本:OCR 兜底(H5 页)
├── collect_semiauto.py          # 采集脚本:半自动辅助
├── conftest.py                  # pytest 夹具(adb_device/ocr_engine/dianping_page)
├── requirements.txt             # 项目依赖
├── pytest.ini                   # pytest 配置
├── .gitignore
└── README.md
```

> 运行时产物 `screenshots/`、`reports/` 已被 .gitignore 排除,不会入库。

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

6. (可选)大众点评采集模块:配置 ADB
   - 方式一:将本机 `adb.exe` 所在目录加入系统 PATH
   - 方式二:修改 `collect_native.py` 顶部的 `DEFAULT_ADB_PATH`,填入完整路径
     ```python
     DEFAULT_ADB_PATH = r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"
     ```
   - 真机 USB 连接:开启「USB 调试」和「USB 调试(安全设置)」,重启设备
   - MuMu 模拟器:`adb connect 127.0.0.1:16384`(端口按实际版本)

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

### 大众点评评价采集

#### 前置条件
1. 安卓设备已连接(USB 真机或 MuMu 模拟器),`adb devices` 能看到设备
2. 大众点评 App 已登录,并已进入目标店铺的「评价」tab
3. 已在 App 内筛选好评/中评/差评(脚本不判断情感,直接抓取当前列表)

#### 运行采集

```bash
# 必传参数:--shop(店铺名) 和 --org-code(机构编码,与店铺名一致)
python collect_native.py --shop "丰裕（淮海店）" --org-code "丰裕（淮海店）"

# MuMu 模拟器:指定设备地址
python collect_native.py --shop "丰裕（淮海店）" --org-code "丰裕（淮海店）" --device 127.0.0.1:16384

# 指定 adb 路径(未加入 PATH 时)
python collect_native.py --shop "店名" --org-code "店名" --adb-path "C:\path\to\adb.exe"

# 冒烟测试:只滑 5 屏
python collect_native.py --shop "店名" --org-code "店名" --scroll 5
```

#### 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--shop` | 是 | 店铺名,写入 CSV 的 `store_name` 列,并用于报告文件名 |
| `--org-code` | 是 | 机构编码,写入 CSV 的 `org_code` 列 |
| `--scroll` | 否 | 滑动屏数,`0`=无限滑动到底(默认),连续 5 屏无新评价则停 |
| `--device` | 否 | ADB 设备地址,留空=自动选首个 USB 真机;MuMu 填 `127.0.0.1:16384` |
| `--adb-path` | 否 | adb.exe 路径,默认走系统 PATH |
| `--ratio` | 否 | 滑动距离比例,默认 `0.38` |
| `--swipe-mode` | 否 | `auto`(默认)/`swipe`(真机)/`roll`(MuMu) |

#### 输出
- **CSV**:`reports/dianping/{店铺名}_reviews_{时间戳}.csv`(utf8mb4,无 BOM)
- **JSON**:`reports/dianping/{店铺名}_reviews_{时间戳}.json`
- **验证码截图**:`screenshots/captcha/`(反扒弹窗自动处理留证)

#### CSV 字段

| 字段 | 说明 |
|------|------|
| `org_code` | 机构编码(与 `store_name` 一致) |
| `store_name` | 店铺名 |
| `username` | 用户名 |
| `review_date` | 评价日期,统一 `YYYY-MM-DD`(`N小时前`/`N天前`/`昨天` 等相对时间自动换算) |
| `rating` | 评分原文(很棒/超预期/不错/还可以/一般/较差/很糟糕) |
| `price_per_person` | 人均单价(从 `¥XX/人` 提取) |
| `content` | 评价全文(已自动展开「全文」) |
| `sentiment` | 情感标签,由 `rating` 映射:positive/neutral/negative |
| `store_feedback` | 商家回复 |

#### 反扒机制
- 人类化操作:点击 ±8px 随机偏移、滑动 Y ±0.08 抖动、随机延时
- 慢速滑动:690~710ms,降低惯性滚动漏评
- 每 3 屏插入 5~8s 长延时
- 滑动验证码:自动检测含「验证」的弹窗,左→右拖动滑块,截图留证
- 无 Appium/uiautomator2 server APK 注入,降低被检测风险

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
- ADB + uiautomator(大众点评 App 采集)
- PaddleOCR(H5 页面文本识别兜底)

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