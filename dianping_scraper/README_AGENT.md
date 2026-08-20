# dianping_scraper 调用接口说明（供 Agent 使用）

版本: 2.0.0 ｜ 平台: Windows ｜ 可执行文件: `dianping_scraper.exe`（等价 Python 脚本: `dianping_scraper.py`）

## 核心约定

- 所有子命令支持 `--json`：stdout 只输出**最后一行单行 JSON 结果**（键 `status` 表示结果），人类可读日志全部走 stderr。解析时读 stdout 最后一行即可。
- 所有子命令支持 `--quiet`：抑制 stderr 日志。
- 退出码：

| 退出码 | 含义 | 触发场景 |
|--------|------|----------|
| 0 | 成功 | check-env 全部就绪 / capture 到达 isEnd 底部或达到 target / export 完成 |
| 1 | 一般错误 | 参数错误、文件不存在、未知异常（JSON 含 `error` 字段） |
| 2 | 环境未就绪 | Python/mitmproxy/pywin32 缺失，或 mitmdump 启动失败 |
| 3 | 等待超时 | `--wait-timeout` 秒内未检测到评论列表请求 |
| 4 | 部分完成 | isEnd 与 target 均未满足就停止（停滞/滚动上限/用户中断），数据仍然有效 |

`status` 取值：`ok` / `error` / `env_not_ready` / `timeout` / `partial` / `interrupted`

## 子命令

### 1. check-env — 环境自检

```
dianping_scraper.exe check-env --json
```

返回 JSON `checks` 对象含五项检查：`python`、`mitmproxy`、`pywin32`、`ca_cert_file`、`ca_cert_trusted`，每项有 `ok` 布尔值。全部通过则退出码 0。

**建议调用方在任何 capture 之前先执行此命令。**

### 2. capture — 抓取评论（需要人工配合）

```
dianping_scraper.exe capture <store_name> <store_id> [options] --json
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `store_name` | 必填 | 门店名称（仅用于提示与日志） |
| `store_id` | 必填 | 门店ID，决定输出文件名 `{store_id}_capture.jsonl` |
| `--target` | **无（不启用）** | 目标评论条数；**不传则一直抓到 isEnd 到底**（推荐）。传了则在达到 N 条时提前停止 |
| `--max-scroll` | 3000 | 最大滚动轮数 |
| `--port` | 8888 | 代理端口 |
| `--scroll-clicks` | 10 | 每轮滚轮格数 |
| `--scroll-interval` | 1.0 | 轮间隔秒 |
| `--check-every` | 20 | 每 N 轮统计一次进度 |
| `--stall-limit` | 3 | 连续 N 次无新增则停止（isEnd 未收到时的兜底，防卡死） |
| `--wait-timeout` | 180 | 等待用户打开评论列表的超时秒数 |
| `--output-dir` | cwd | 抓取文件/日志输出目录 |
| `--capture-file` | 自动 | 显式指定 jsonl 路径 |
| `--log-file` | 自动 | 显式指定日志路径 |
| `--no-scroll` | off | 只代理抓包，不自动滚动 |
| `--cursor-pos` | 自动 | 手动指定滚轮位置 `x,y`（覆盖窗口自动定位） |
| `--window-keyword` | 大众点评等 | 窗口标题关键词，可重复 |

**关键行为（调用方必读）：**

1. 启动后先设置系统代理 → 然后打印提示等待**人工**在微信小程序中打开该门店的评论列表页。
2. 检测到 `outsidesiftedreviewlist` 请求后自动定位窗口并开始滚动。
3. 停止条件（按判定优先级）：
   - **`isEnd=true` 到底（主判定）**：评论接口响应顶层字段 `isEnd` 为 `true` 时立即停止，status=`ok`，`stop_reason=end_reached`
   - **达到 `--target`**（仅当显式传入）：status=`ok`，`stop_reason=target_reached`
   - **停滞兜底**：连续 `--stall-limit` 次检查无新增（网络异常/isEnd 未被抓到的兜底）：status=`partial`，`stop_reason=stalled`
   - **滚动上限** `--max-scroll`：status=`partial`，`stop_reason=max_scroll`
   - **等待超时** `--wait-timeout` 秒未检测到评论列表请求（用户未打开页面）：退出码 3，status=`timeout`
   - **用户中断** Ctrl+C：退出码 4，status=`interrupted`
4. 无论何种退出路径（含异常/中断）都会自动关闭系统代理并终止 mitmdump，不会遗留代理导致断网。
5. 返回 JSON 含：`reviews`（去重后评论数）、`max_start`、`is_end`、`shop_review_count`（接口报告的评论总数，可用于校验完整性：`reviews == shop_review_count` 表示全量抓齐）、`stop_reason`、`capture_file`。
6. mitmdump 的错误日志写入 `{store_id}_mitmdump.err.log`。

**典型调用流程：** 提示用户打开评论列表 → 启动 capture（阻塞，可能持续数分钟到数十分钟）→ 依据退出码与 `reviews` 决定是否重试/续抓（重复运行会续写同一 jsonl，解析时按 mainId 去重，安全）。

### 3. export — 解析导出（纯计算，无需 UI）

```
dianping_scraper.exe export <store_id> --org-code <code> --store-name <name> [options] --json
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `store_id` | 必填 | 用于定位 `{store_id}_capture.jsonl`（可用 `--input` 覆盖） |
| `--input` | 自动 | 显式指定抓取 jsonl 路径 |
| `--org-code` | 必填 | 写入输出的 org_code 列 |
| `--store-name` | 必填 | 写入输出的 store_name 列 |
| `--output` | 自动 | 输出路径，默认 `{store_id}_reviews.{csv|json}` |
| `--format` | csv | `csv` 或 `json` |
| `--encoding` | utf-8-sig | CSV 编码，可选 `utf-8` / `gbk` |
| `--rating-scale` | ten | `ten`=换算 0.0..5.0 星级（默认，与 store_reviews 表一致）；`raw`=原始 0..50 星值 |

**输出列**（与 MySQL `store_reviews` 表字段对齐）：
`org_code, store_name, username, review_date, rating, price_per_person, content, sentiment, store_feedback`

- `review_date`：相对时间（"3天前"等）已转为 `YYYY-MM-DD`
- `sentiment`：好评(≥4.0) / 中评(≥3.0) / 差评，基于 5 分制
- `store_feedback`：商家回复（userType==10），多条以换行连接。来自回复接口的带时间：`[商家回应 2026-08-14]内容`；仅列表内嵌的：`[商家名]内容`
- 返回 JSON 含：`rows`、`output`（绝对路径）、`sentiment` 分布、`reviews_with_replies`、`reply_stats`（`merchant_replies_from_reply_api` / `merchant_replies_embedded_extra` / `reply_api_mainIds`）。**若 `reviews_with_replies=0` 会附带 `warning` 字段**——如该店实际有商家回复，说明抓取时没点开过评论详情页，完整回复未被记录。

**export 成功后必做**：将返回 JSON 中 `output` 指向的 CSV 文件（computer:// 链接形式）随结果摘要一起发给用户，不要只报告统计数字。

## 评论列表接口（两种 tab 格式，均已适配）

小程序评论页有两个 tab，对应**两种不同的响应结构**（与门店无关）：

| tab | 响应格式 | 典型 URL |
|-----|---------|---------|
| 全部 | `data.result.reviewList` | reviewlist 系接口 |
| 最新 | `data.list`（顶层含 `isEnd`/`startIndex`/`shopReviewCount`） | `outsidesiftedreviewlist.bin` |

capture/export 的接口匹配关键字是 `reviewlist`（同时命中 `outsidesiftedreviewlist` / `outsideshopreviewlist` 等），两种结构都会解析，任一 tab 滚动都能抓到。isEnd 检测兼容顶层与 `result` 内两种位置。

## 商家回复来源（重要：主来源是列表内嵌）

export 的商家回复（store_feedback）自动合并两个来源，内容不丢：

1. **列表内嵌（主来源）**：评论列表响应的 `comments[]` 里 userType==10 的条目。**回复内容是全的**，只滚列表即可全部获得，无需点开详情页。唯一缺失：无回复时间。
2. **回复接口（补充，仅补时间）**：`m.dianping.com/ugc/paginateDpFeedReply`，请求 URL 的 `mainId` 参数关联评论，`result.records[]` 含 `replyTime`。**只有操作者在抓取期间点开过评论详情页才会触发该请求**。内容与列表内嵌一致（实测一致），作用是给回复补上时间，格式 `[商家回应 2026-08-14]内容`。

合并去重规则：同一回复文本只在回复接口版本（带时间）出现时用带时间版本，其余用内嵌版本。因此**只滚列表就能拿到全部回复内容**；若需要回复时间，才需要在抓取期间点开带"商家回复"的评论详情页（该接口 token 动态绑定会话，无法事后重放）。

## 完整调用序列（推荐）

```
1. check-env --json          # 退出码非 0 则先跑 setup_env.ps1 修复环境
2. capture <name> <id> ... --json   # 需要人工打开评论列表页（回复内容随列表一并抓全；如需回复时间，期间点开带商家回复的评论详情）
3. export <id> --org-code ... --json  # 得到 CSV 直接导入 store_reviews；检查 reviews_with_replies
4. 把导出的 CSV 文件发给用户  # 必做：用输出里的 output 路径，以 computer:// 链接形式在回复中提供给用户
```

## 注意事项

- capture 仅支持 Windows（依赖 win32 API 与微信 PC 端小程序）。
- 重复 capture 同一门店是安全的：数据追加写，export 按 mainId 去重。
- 旧版位置参数语法 `dianping_scraper.exe <name> <id>` 已废弃，必须使用子命令。
- exe 与 py 版本功能一致；py 版本要求 Python 3.11+ 且安装 mitmproxy、pywin32。
- **isEnd 字段说明**：位于 `outsidesiftedreviewlist` 接口响应 JSON 的顶层，与 `list`/`startIndex`/`shopReviewCount` 平级。仅在真正翻到最后一页时为 `true`（实测丰裕 132 页仅末页为 true）。
- **大评论量店铺**：`shopReviewCount` 上万的店铺（如光明村 3.4 万条）可能翻不到底，永远收不到 `isEnd=true`，此时靠停滞兜底/max-scroll 停止（status=`partial`）。建议开始抓取后看首屏返回的 `shop_review_count`，若评论量过大可提前用 `--target` 设上限或手动 Ctrl+C（数据仍有效）。
- **pywin32 检查语义**：`pywin32` 检查的是**当前进程内** import（exe 已内置打包，任何机器都应通过；py 版则取决于运行解释器）。外部找到的 python 仅用于启动 mitmdump，**不需要** pywin32。
- **`.pth` 桥接陷阱（已知问题）**：若机器上存在 venv 靠 `.pth` 把 site-packages 桥接给另一个解释器，纯 Python 包能桥接成功，但 pywin32 这类带原生 DLL 的包会"pip 显示已安装、实际 import 失败"（桥接目录里的 `pywin32.pth` 不会被 site.py 递归处理）。setup_env.ps1 已做防御：pip 安装后实测 import，失败则 `--force-reinstall --no-deps` 强制装入本解释器。根治方法是把 pywin32 直接装进实际使用的解释器本体，不用桥接。
