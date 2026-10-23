# 暗金典藏界面

本轮将演示界面改为“Dark Parchment & Gilded Obsidian”：黑曜石指挥舱、暗金边框与局部羊皮纸卷轴。重构范围是视觉和相应交互反馈，后端资源、授权、管理版本及事务契约保留。

## 视觉与交互

- 字体：Google Fonts的Cinzel用于西文铭文，Noto Serif SC用于中文，JetBrains Mono用于权限码和事件轨迹；采用`display=swap`并保留本机后备。CSP仅增加字体CSS与字体文件所需的两个来源，脚本和接口仍为本地来源。字体调用依据[Google Fonts CSS API](https://developers.google.com/fonts/docs/css2)。
- 桌面：左侧身份卡和领地导航、中间政令/账本/权限配置、右侧sticky事件终端。终端内部可滚动，页面滚动仍保留日志观察区域；窄屏改为单列，不遮挡业务内容。
- 权限层次：MENU采用金线旗帜导航；PAGE采用火漆卷轴卡片；BUTTON采用铸铁鎏金操作按钮。锁定资源显示斜纹与文字状态；无权动作按钮仍disabled，主动越权验证继续发送真实请求。
- 拦截反馈：服务端403、409或明确拒绝事件后，为原触发控件添加`intercept-shake`水平动画，CSS时长0.38秒。按id/testid记录控件来源，策略刷新重绘后仍能定位对应控件；不是在点击时预判或伪造拦截。减少动态效果时跳过震颤。
- 日志：放行是翡翠绿，权限拦截是火漆红，职责/策略或业务前置条件冲突是琥珀金。分类依据最终`allowed`及`DECISION_DENY`中的规范错误码，刷新已持久化事件后仍可恢复。新事件淡入，刷新历史卡片不反复播放淡入；详情、筛选、最近40条与手动刷新语义保留。
- 独立页面：服务器仍只在授权后返回真实正文；模板根据事件裁决显示羊皮纸圣旨或红色拦截视图，低透明度盾徽作为背景水印。未添加客户端隐藏秘密内容的替代方案。

## 验证方法

原Python与浏览器回归命令保留：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
.\.venv\Scripts\python.exe tests/browser/serve.py --port 5053
```

保持专用临时服务运行，在另一终端按[浏览器说明](../tests/browser/README.md)安装开发依赖并运行：

```powershell
$env:BASE_URL = 'http://127.0.0.1:5053'
$env:ALLOW_TEST_MUTATIONS = '1'
node tests/browser/e2e.mjs
node tests/browser/visual.mjs
```

原`e2e.mjs`未作修改。补充视觉检查另行记录截图、浏览器测量、真实403/409反馈、持久化颜色、独立200/403、390px、减弱动画及字体不可用后备，不与原9场景混计。两个脚本都只能对专用临时数据库服务执行，不能指向日常实例。

## 本轮结果

2026-10-23在Windows、Python 3.13及Microsoft Edge/Playwright 1.62.1下完成：

- `pytest -q --tb=short`：**92 passed in 22.07s**。
- 原`tests/browser/e2e.mjs`：**9/9通过**，未修改该脚本；SHA-256仍为`f0a73db26f49b746b042101327acc61476a233dd442d79351375d26a7550c051`。
- 补充`tests/browser/visual.mjs`：**6/6通过**；没有观察到JavaScript异常或CSP拒绝。
- Cinzel、Noto Serif SC、JetBrains Mono三种FontFace均实际加载；另拦截两个字体域名，验证本机后备下登录与真实拒绝流程可用。
- 1440×1000三栏分离，页面滚动后终端顶部25px、面板高度868px；1440×600时面板底部493px，在视口内。390px的登录、大厅、管理、页面拒绝及独立档案没有页面级横向溢出。
- 页面、菜单、沙盘三个真实拒绝路径及SSD 409均观察到源控件0.38s水平震颤，重复触发也能重启。减少动态效果偏好下没有震颤或事件淡入。
- 刷新已持久化日志后，放行/拦截/冲突左色条分别为`rgb(130,196,164)`、`rgb(216,141,126)`、`rgb(224,182,96)`；错误类型与响应事件ID相符，未用中间`DECISION_ALLOW`步骤覆盖最终拒绝。
- 独立布防档案成功200与真实403均检查；拒绝HTML不含机密正文，水印透明度0.055。另检验未登录401、空event、普通/映射混合records及动态内容转义。
- 额外查看1440px与390px的权限图谱、直接权限配置及继承表单，六种布局均无页面级横向溢出；查看真实账本核对卡片。52个静态DOM id、原data属性和表单name保留，后端路由差异仅为两个字体CSP来源。

截图、JSON和命令输出在本地`dist/ui-review/`，不上传GitHub。第一次补充检查中的账号共用Cookie，以及刷新后过早读取旧DOM的问题，均修正为独立浏览器上下文和等待真实响应完成，不以固定sleep掩盖；原E2E未因此放宽断言。

报告PDF及其历史截图不在本轮视觉重构范围内。上述检查覆盖所列视口和交互，不等于完整可访问性、安全或全终端认证。
