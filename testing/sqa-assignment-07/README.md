# 作业七：十种测试技术的复现材料

本目录收录针对库滕堡RBAC政令系统的功能、等价类、规格、风险、压力、回归、探索、用户、场景和随机测试材料。报告单独提交，代码、证据和图像在此归档。

原系统基线为[c4cb79108249](https://github.com/hechuxielianli/royal-rbac-coursework/tree/c4cb791082497b2e79fea41dbee773a6be6a446d)；[sut/](sut/)保存本次包含四类修复的软件副本，主目录继续保存原系统。

## 结果与入口

- 原有98项、功能与规格20项、等价类与边界41项、随机性质4项、缺陷确认11项、会话扩展11项，共185项pytest用例通过。
- 随机测试生成1100个样本，执行2229条受检操作请求。
- 三条浏览器场景通过；两轮探索保留五项观察。
- 压力测试4330条请求，2165条成功生产响应与2165条流水对应，库存24变为2189。16 VU时P95首次超过500 ms。
- P1完成三项用户任务，人工计时109/158/53秒，走错菜单1/3/0次，容易程度4/2/4分。

| 目录 | 内容 |
| --- | --- |
| [sut/](sut/) | 被测软件和原有98项测试 |
| [tests/](tests/) | 新增功能、规格、等价类、随机、缺陷及会话测试 |
| [scripts/](scripts/) | 压力、浏览器、用户任务、基线复现和图表脚本 |
| [evidence/](evidence/) | 测试设计、结构化结果、日志、CSV和用户反馈 |
| [figures/](figures/) | 报告中的三幅图像及压力图SVG |

## 执行测试

在仓库根目录执行：

```powershell
cd testing/sqa-assignment-07
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
.\scripts\run_tests.ps1
```

从本目录启动软件：`.venv\Scripts\python sut/run.py`。公开演示口令为Kingdom@2026。

四类缺陷的修复前后比较见[regression-before.json](evidence/regression-before.json)、[regression-after-records.json](evidence/regression-after-records.json)和[source-fix.diff](evidence/source-fix.diff)。原版复现运行 `.venv\Scripts\python scripts/reproduce_baseline.py`，脚本检查11项用例在原版均失败，结果另存baseline-replay文件。

## 压力与浏览器

压力实验运行 `.venv\Scripts\python scripts/stress.py`，脚本使用独立数据库和5088端口，结束后停止服务。正式记录为[stress-summary.json](evidence/stress-summary.json)与[stress-requests.csv](evidence/stress-requests.csv)。

浏览器检查使用Node、Microsoft Edge和Playwright。在本目录执行：

```powershell
npm install
$env:PLAYWRIGHT_MODULE = 'playwright'
.venv\Scripts\python scripts/serve_browser.py
```

保留5089端口的测试服务，在另一个终端从本目录执行：

```powershell
$env:PLAYWRIGHT_MODULE = 'playwright'
node scripts/browser_checks.mjs scenarios
node scripts/browser_checks.mjs explore1
node scripts/browser_checks.mjs explore2
```

[browser-results.json](evidence/browser-results.json)中的S1—S3对应报告B01—B03；[exploration.json](evidence/exploration.json)保存两轮探索。用户任务页运行 `.venv\Scripts\python scripts/serve_study.py`，地址为http://127.0.0.1:5087/study。P1人工计时来源见[user-timing-supplement.md](evidence/user-timing-supplement.md)。

重绘压力图需安装matplotlib，再运行 `python scripts/make_chart.py`。本次测试在Windows与Python 3.13环境执行。

## 记录说明

公开记录保留运行结果、状态码、耗时和业务数据。本机绝对路径替换为占位符，运行时CSRF及会话令牌值已去除。本地原始记录保留；基线ZIP按源码白名单整理，文件哈希见[baseline-manifest.json](evidence/baseline-manifest.json)，公开材料哈希见[materials-manifest.json](evidence/materials-manifest.json)。
