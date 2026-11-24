# 规则与模型对照评测

两组使用相同的 FastAPI、业务目录、LangGraph 后端校验、人工确认、MySQL 和 Redis。区别在自由问数规划节点调用的实现。规则组使用小型词典和日期解析作为对照，对无法识别的范围默认继续澄清。模型组使用当前 Ollama 规划器。已实现的只改日期和指标文字澄清规则，两组共用。

## 复跑

在项目根目录分别打开两个 PowerShell，使用同一份本机 `.env` 和数据库。先启动 MySQL、Redis 及 Ollama。

```powershell
# 窗口 1
.\.venv\Scripts\python.exe -m uvicorn evals.comparison.planners:rules_app --factory --app-dir src --host 127.0.0.1 --port 18331
# 窗口 2
.\.venv\Scripts\python.exe -m uvicorn evals.comparison.planners:model_app --factory --app-dir src --host 127.0.0.1 --port 18332
```

另开 PowerShell 运行：

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m evals.comparison.run
```

报告写到 `.run/evals/rule-vs-model/`。题集在运行前固定，四组各六题。原有 12 题继续由 `python -m evals.run` 独立执行。参考 SQL 与查询编译器独立。模型提交的确认卡错误时脚本停在确认前。运行错误与题目未通过分别统计。

- 整题通过要求每轮阶段与计划符合预期，查询题还需要结果逐行一致。
- 错误确认卡含提前确认、缺计划和计划字段有误。
- 额外追问统计直接回答或拒绝时产生追问，以及本应澄清时问了额外字段。一题在首个偏差后停止，统计的是观察到的轮次。
- 确认卡耗时只在两组都通过且产生确认卡的题目上配对统计。累计用户输入接口响应耗时，排除人工、SQL 执行和轨迹读取时间。
- 两个实验服务共用规划节点的 Trace 事件名。看运行入口和报告分组区分规则组与模型组。实验没有替换默认服务配置。单次测量用于定位可复查的差异，模型输出会随运行变化。
