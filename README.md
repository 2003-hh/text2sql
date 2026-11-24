# 小哲 Text2SQL Python 后端

项目提供固定计划和自然语言问数、人工澄清与确认、Redis 会话恢复、只读 MySQL 查询以及原课程网页。迁移计划见 [python-migration-plan.md](docs/python-migration-plan.md)。

## 本地运行

需要 Python 3.13、Docker（含 Compose）和 Ollama。以下命令在仓库根目录的 PowerShell 中执行。结构化计划可以跳过 Ollama，网页上的自然语言问数需要 Ollama。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
```

### MySQL 和 Redis

在 `.env` 中填写 `T2S_MYSQL_PASSWORD`。按手头的数据选择一种启动方式。

- **从公开仓库直接试跑。** 再填写 `T2S_DEMO_MYSQL_ROOT_PASSWORD`，给 root 与只读应用账号设置不同的本地密码。执行 `docker compose --profile demo up -d --wait mysql redis`。Compose 拉取 MySQL 8.0 与 Redis 8 的镜像，首次启动 MySQL 会用 [虚构样例](infra/demo.sql) 初始化表和只读账号。样例覆盖三道课堂查询，金额与课堂数据可能不同。
- **复用已有课程 MySQL。** 在原课程目录运行 `docker compose up -d --wait db`，或启动现有 MySQL 容器。只在本仓库运行 `docker compose up -d --wait redis`。将课程数据库地址、端口、库名和只读账号填入 `.env`。已有 MySQL 占用 `13323` 时，若改用演示数据库，可在 `.env` 里改成空闲端口。

默认端口是 MySQL `127.0.0.1:13323`、Redis `127.0.0.1:16379`（需要 Redis 8 的 JSON 和搜索命令）。两者的数据保存在本机 Docker 卷。MySQL 只在新卷上运行初始化 SQL，已初始化卷的账号密码需在 MySQL 中修改。停止容器可用 `docker compose stop`。

### Ollama 和网页

先启动 Ollama，拉取默认模型并启动 FastAPI。

```powershell
ollama pull qwen3.5:9b
.\.venv\Scripts\python.exe -m uvicorn t2s.main:create_app --factory --app-dir src --host 127.0.0.1 --port 18330 --workers 1
```

打开 [问数工作台](http://127.0.0.1:18330/)。Ollama 地址和模型可在 `.env` 覆盖。实际密码、镜像、容器和数据卷保存在本机。

| 接口 | 含义 |
|---|---|
| `GET /health` | 应用存活。HTTP 200，正文 `{"status":"ok"}` |
| `GET /ready` | 用只读账号读取课程订单表，检查 Redis 和 checkpoint 所需命令，并加载业务目录。依赖异常时返回 HTTP 503 和各项状态 |
| `GET /docs` | Swagger API 调试页面 |

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Invoke-RestMethod http://127.0.0.1:18330/health
Invoke-RestMethod http://127.0.0.1:18330/ready | ConvertTo-Json
```

使用两个独立 Python 进程验证 Redis 里的存档能否恢复。第一次调用暂停并退出，第二次使用同一个会话 ID 读回并恢复，成功后清理这条测试存档。

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
$id = [guid]::NewGuid().ToString()
.\.venv\Scripts\python.exe -m t2s.workflow.smoke pause $id
.\.venv\Scripts\python.exe -m t2s.workflow.smoke resume $id
```

Python Redis 使用独立的 Redis 8 容器和命名卷，宿主端口是 `16379`，通过 AOF 保留数据。

## 固定计划查询

当前可用 Swagger 或 PowerShell 提交结构化计划。创建会话时只校验计划，并存入 Redis 等待确认。输入 `end_inclusive=true` 时会把结束日转成排他边界。以下示例查 2026 年 9 月每天实付金额，单位为分。

```powershell
$base = 'http://127.0.0.1:18330'
$body = @{
    plan = @{
        metric = 'paid'
        dimension = 'day'
        start = '2026-09-01'
        end = '2026-09-30'
        end_inclusive = $true
    }
} | ConvertTo-Json

$pending = Invoke-RestMethod "$base/sessions" -Method Post -ContentType 'application/json' -Body $body
$pending | Select-Object id, stage, revision, executions
$sessionUrl = "$base/sessions/$($pending.id)"
Invoke-RestMethod $sessionUrl | Select-Object id, stage, revision, executions

$reply = @{ action = 'confirm'; revision = $pending.revision } | ConvertTo-Json
$done = Invoke-RestMethod "$sessionUrl/respond" -Method Post -ContentType 'application/json' -Body $reply
$done | ConvertTo-Json -Depth 12
```

新会话处于 `confirm`，`executions=0`。确认通过后进入 `done` 并返回 `rows`。用同一版本再次确认会得到 HTTP 409。也可将动作改为 `cancel`，直接结束会话。按会话 ID 读取状态不触发查询。部署保持一个 Uvicorn worker，由进程内锁和 Redis 执行领取记录保护确认边界。

复现三道课程题的真实验收。在项目根目录运行第一条验收命令，然后保持 Redis 和 MySQL 运行，重启 FastAPI，再运行最后一条命令。验收脚本会比对重启前完整状态、旧版本响应、并发确认结果和独立 SQL 的每行结果。

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
New-Item -ItemType Directory -Force '.run' | Out-Null
$record = '.run/fixed-plan-local.json'
.\.venv\Scripts\python.exe tests/acceptance/fixed_plan.py pause --record $record
# 重启 FastAPI，保留 Redis 与 MySQL。
.\.venv\Scripts\python.exe tests/acceptance/fixed_plan.py finish --record $record
```

SQL 由受控课程目录生成，并用 PyMySQL 的占位符传入日期和品类。

## 自然语言网页

打开 [问数页面](http://127.0.0.1:18330/)。Python 项目独立维护从 V16 复制的页面资源，位于 `src/t2s/resources/web`。FastAPI 同源提供网页与 API，使用本机 Ollama 生成候选计划。模型默认 `qwen3.5:9b`。可在 `.env` 设置 `T2S_OLLAMA_URL`、`T2S_OLLAMA_MODEL` 和 `T2S_OLLAMA_TIMEOUT`。

`POST /sessions` 接受 `{"question":"2026年9月每天实付金额是多少？"}` 或原有的 `{"plan":{...}}`，一次只传一种。结构化计划无需调用模型。自然语言的原话依据由程序核验，通过后才进入待确认。问「2026 年 9 月每天销售额」可观察口径澄清。问「每天键盘实付金额是多少」并点选日期，可观察原分组和筛选是否保留。

接着核对确认卡，确认后查看结果或导出 CSV。刷新页面会按同一会话 ID 读取已保存的状态与结果。`GET /choices` 提供指标点选文案。回复接口仍使用卡片版本，支持 `choose`、`reply`、`confirm` 和 `cancel`。

如需重跑浏览器验收，将 Playwright 和 Chromium 装到本地测试环境后运行 `python tests/acceptance/browser_flow.py`。脚本使用真实模型与数据库，覆盖补日期、保存条件、刷新、旧卡拦截、确认和 CSV 数值核对。验收截图和 CSV 写入 `.run/browser`。

## 查看执行轨迹

生成确认卡后，在卡片下方展开「执行轨迹」即可查看中文时间线。面板展开时，会在点选、确认或取消后自动更新。点击任意节点，可展开字段原值和中文说明。可用「查看 JSON」打开原始事件，记录较多时使用「加载更多」。

通过 `GET /sessions/{id}/trace` 查看一个会话的结构化事件。新建或操作会话后，把响应里的完整 `id` 填入 URL。接口只读取历史。

```powershell
$sessionId = '<会话响应中的完整 id>'
$url = "http://127.0.0.1:18330/sessions/$sessionId/trace"
$trace = Invoke-RestMethod "$($url)?limit=10"
$trace.events | Format-Table step, name, stage, revision, duration_ms
if ($trace.has_more) {
    Invoke-RestMethod "$($url)?after=$($trace.next_after)&limit=10"
}
```

- `session_id` 串联同一会话，`request_id` 区分每次提问、点选或确认。人工操作记录推进后的 `revision`，提交的旧版本另记在 `summary.submitted_revision`。
- `session_restored` 表示通过会话读取接口从 checkpoint 取回状态，刷新也会产生该事件。读取 Trace 本身不会新增事件。
- `sql_started` 只在拿到执行权且即将调用执行器时写入，`sql_finished` 记录成功返回的行数。中途失败会写 `sql_failed`。这些事件记录执行边界，调用失败的摘要不会披露原始异常。
- Redis 每会话默认保留最近 500 条、最后一次事件写入后保留 7 天。可在 `.env` 配置 `T2S_TRACE_MAX_EVENTS` 和 `T2S_TRACE_TTL_SECONDS`。分页使用 `next_after`，`history_trimmed=true` 表示游标前后的部分旧记录已被裁剪。整段轨迹过期后可用 `after=0` 重新读取。
- 公开事件仅保存字段白名单里的摘要，用户原话、SQL 参数、原始模型输出与结果行均不写入。Trace 写入失败会退回脱敏日志，读取存储失败时接口返回 503。

运行 `tests/acceptance/trace_flow.py pause --record .run/trace-local.json`，重启 FastAPI 后再运行同一脚本的 `finish` 动作。脚本使用真实 Ollama、Redis 和 MySQL，验证追问、确认、取消、旧卡、分页和恢复。

## Eval

[T2S Eval](evals/README.md)
