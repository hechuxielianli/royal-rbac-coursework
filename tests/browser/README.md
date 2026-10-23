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

## 暗金界面的补充检查

在同一专用临时服务上运行：

```powershell
node tests/browser/visual.mjs
```

该脚本独立于原9组E2E，检查1440px三栏、事件终端sticky、真实页面/菜单/沙盘403与SSD 409的0.38s源控件反馈、持久化日志三色、独立200/403档案视图、390px布局，以及减弱动画和Google Fonts不可用时的后备。它会提交被拒的冲突草稿，不改变该用户已保存的角色分配；仍只能对上述临时服务执行。

截图、浏览器实际测量和JSON结果默认写入不纳入Git的`dist/ui-review/`，可用`VISUAL_OUTPUT`更改。字体加载结果单独记录；远程字体故障不应阻止登录或业务。色彩和布局检查是针对本轮视口与交互的回归，不代表完整可访问性认证。

## 库滕贝格场景检查

在上述临时服务上执行`node tests/browser/kuttenberg.mjs`。四组场景检查真实100格罗申支出与余额动效、拒绝请求对应事件编号的卫兵警告及键盘关闭、SSD反走私法令与草稿保留、390px档案首字及减少动态效果。没有伪造响应或使用固定余额。截图和JSON默认输出到不纳入Git的`dist/kuttenberg-review/`，可通过`THEME_OUTPUT`指定位置；所用环境变量与原E2E相同。
