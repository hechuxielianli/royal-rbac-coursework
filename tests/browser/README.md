# 浏览器回归

应用正常运行不需要Node。只有本开发测试需要Node、Playwright 1.62.1和Microsoft Edge。先按项目README安装Python依赖，再在项目根目录安装浏览器测试依赖：

```powershell
npm install --prefix tests/browser
```

在第一个终端启动专用测试服务：

```powershell
.\.venv\Scripts\python.exe tests/browser/serve.py --port 5053
```

该脚本每次创建新的临时数据库，保持服务终端打开。不要用正常`run.py`启动的实例代替它：回归会修改业务和授权，测试结束只恢复部分策略，临时业务状态不清零。

在第二个终端运行：

```powershell
$env:BASE_URL = 'http://127.0.0.1:5053'
$env:ALLOW_TEST_MUTATIONS = '1'
node tests/browser/e2e.mjs
```

脚本默认使用本地安装的Playwright与Edge的`msedge`通道。可用`PLAYWRIGHT_MODULE`指定已有Playwright模块，用`EDGE_PATH`指定浏览器可执行文件，用`RESULT_PATH`指定JSON输出文件（父目录需存在）。停止专用服务使用Ctrl+C，临时数据库不属于提交内容。

九组场景覆盖目录归属、hash路由、会话过期且不重放写请求、真实审计、版本冲突草稿、仅册封/仅审计管理能力、无菜单状态，以及资源/事件文本转义、390px窄屏和tab方向键。最后一组不是完整XSS渗透测试或WCAG认证。
