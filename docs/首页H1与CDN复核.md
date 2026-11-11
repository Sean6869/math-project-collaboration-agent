# 首页 H1 与 CDN 复核（2026-11-11）

## 现场证据

- 用户提供的 Bing Live URL 截图显示“H1 tag missing”，同时明确表示该 URL 可以被索引。
- 当前直接请求首页（普通 User-Agent 与 Bingbot User-Agent）均返回 HTTP 200，原始 HTML 已包含一个 H1：“迈谱数学项目式学习平台”。仅修改 User-Agent 不能模拟腾讯云按搜索引擎 IP 识别的真实回源路径。
- 因而不能仅凭截图认定当前源站首页没有 H1，也不能断言 CDN 是此次提示的根因。
- 用户截图确认腾讯云 CDN 的 SEO 配置已开启。该功能用于搜索引擎回源，不负责生成或修补 HTML 标题。

## 已修复的页面状态

首页会被 JavaScript 重绘。此前，初始化、师生项目选择与接口异常状态仅有 H2；接口失败时原有静态 H1 会被异常页面替换。现在这些状态都保留一个可见 H1；异常首页同时保留平台名称和简短介绍。项目选择标题的视觉样式保持原样。

验证入口：`tests/portal_heading_ui.cjs`。静态 HTML、匿名登录、会话接口失败、班级接口失败、初始化、学生项目选择、教师项目选择共 7 种状态通过；同时检查 canonical、description 及项目选择页在 1100/720/390px 下的排布。测试使用临时数据，未操作线上账户。

## 部署后操作

1. 在服务器运行 `sudo bash /opt/maple/app/deploy/tencent/update.sh`。
2. 如 CDN 保留了旧副本，在腾讯云“刷新预热”刷新首页 URL `https://www.mapleclass.online/`，并刷新本次变动资源所在目录 `https://www.mapleclass.online/assets/`。
3. 在 Bing Webmaster Tools 中重新运行 Live URL 检测，并查看测试页 HTML 中的 H1；必要时提交一次索引请求。

本次修复尚未由本地工作区自动部署至服务器，也不保证必应立即更新检测结果或搜索展示。

参考：
- https://cloud.tencent.com/document/product/228/41738
- https://www.bing.com/webmasters/help/url-inspection-55a30305
