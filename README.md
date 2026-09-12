# 迈谱 Maple

**Math（数学）+ AI（人工智能）+ Project（项目）+ Learning（学习）+ Exploration（探索）**

每迈一步，皆有章谱。

面向初中数学项目式学习的一组一平板协作平台。平台采用后台管理式布局，课堂数据由服务端鉴权并保存到 SQLite；智能提示与学生自由工作区均由服务端统一接入模型，模型不可用时自动保留教学支架或平台引导。页面不内置教师、学生、项目或课堂演示数据。

## 本地启动

需要 Python 3.11 或更高版本，不需要安装第三方依赖。

```powershell
python start_server.py
```

访问 `http://127.0.0.1:8000/`。平台不包含默认教师、学生、项目或课堂数据。首次启动时，终端会显示随机的一次性教师注册码；负责人在入口页完成平台注册，并设置管理员恢复码、班级码与小组访问规则。恢复码用于忘记教师密码时重置账号，只保存哈希，请离线保管。管理员登录后可在“账号与访问管理”中重置账号或访问码，并生成一次性教师注册码；其他教师在登录入口点击“教师注册”创建自己的账号。若旧入口仍停留在已经打开的标签页，关闭该标签页或普通刷新一次即可，无需手动清理浏览器缓存；`/index.html` 会由服务器强制转到当前登录入口。
学生端页面按当前 URL 加载，课堂阶段以教师端的统一状态为准；页面中的未提交输入会按账号、小组和页面暂存到浏览器，误刷新后自动恢复，点击保存成功后清除对应草稿。
管理员还可在“账号与访问管理”中使用恢复码执行“恢复出厂数据”：清空课堂项目、阶段产出、过程记录、上传文件及其他教师账号，同时保留管理员身份、恢复码和班级/小组访问规则。该操作不可撤销。

## DeepSeek

密钥只配置在服务器环境变量中：

```powershell
$env:DEEPSEEK_API_KEY="你的密钥"
python start_server.py
```

默认调用 `https://api.deepseek.com/chat/completions`，模型为 `deepseek-flash`（V4.1 Flash），可通过 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL` 调整。旧配置中的 `deepseek-chat`、`deepseek-reasoner`、`deepseek-v4-flash` 和 `deepseek-v4-flash-vision-exp` 会自动迁移到当前 Flash 模型。由于当前模型默认开启思考，平台会显式关闭思考输出并要求结构化 JSON，以继续支持稳定的分层提示；L1、L2、L3 分别执行“定位—拆解—局部示范”契约，学生可在同一级连续提问并主动升级，L4 为独立教师求助额度。超时、错误或越界回答会使用教师支架且不扣除 AI 额度。

## 学生自由AI工作区

学生端的“自由AI工作区”与分层提示分开，采用一个连续聊天界面：普通问题调用火山方舟豆包文本模型；学生明确要求“生成一张图片、绘制配图”等内容时，系统自动切换到 Seedream 图片模型。每个主题独立保存在“最近”列表中，文本和图片属于同一对话链。AI 返回的 Markdown 与常用 LaTeX 数学符号由前端安全转换后展示。表格、JSON、PPT 和展示提纲生成入口已移除。

可选环境变量：

```text
DEEPSEEK_REASONING_MODEL=deepseek-v4-pro
ARK_API_KEY=你的火山方舟密钥
ARK_TEXT_URL=https://ark.cn-beijing.volces.com/api/v3/chat/completions
ARK_TEXT_MODEL=doubao-seed-2-0-lite-260428
ARK_TEXT_SPEED=fast
ARK_IMAGE_URL=https://ark.cn-beijing.volces.com/api/v3/images/generations
ARK_IMAGE_MODEL=doubao-seedream-5-0-260128
ARK_IMAGE_RESPONSE_FORMAT=b64_json
```

图片生成结果会保存到 `/app/data/uploads`；如果使用 Railway，必须将该目录挂载到持久化 Volume。正式部署前应把 API 密钥只填入部署平台的环境变量，不要提交到 GitHub。

## 生产部署

复制 `.env.example`，至少设置 `MPT_SETUP_TOKEN` 和 `DEEPSEEK_API_KEY`，然后在 HTTPS 反向代理之后运行：

```bash
docker build -t math-project-agent .
docker run --env-file .env -p 8000:8000 -v math-project-data:/app/data math-project-agent
```

生产环境要求：

- 设置 `MPT_ENV=production` 和至少12位的一次性教师注册码，首位教师在入口页注册后该注册码立即失效；
- 如需无人值守初始化，也可同时设置教师账号、强密码、`MPT_ADMIN_RECOVERY_CODE`、班级码、小组码前缀和小组数量；
- 小组访问码前缀在初始化时写入班级配置；教师扩容时只需修改小组数量，系统会沿用此前缀自动生成连续编号（例如 `TEAM04`）。
- 旧数据库若尚未保存前缀，升级后会优先从 `MPT_GROUP_CODE_PREFIX` 自动迁移；若部署环境也没有该变量，系统会拒绝猜测并提示配置一次，以避免生成错误访问码。
- 设置 `MPT_COOKIE_SECURE=1`，仅通过 HTTPS 提供服务；本地 `127.0.0.1` 预览会自动使用非 Secure 会话 Cookie；
- 持久化并定期备份 `/app/data`；
- 不把 `.env`、数据库、上传文件或 DeepSeek 密钥提交到代码仓库；
- 由学校或项目负责人完成数据告知、授权、保留期限与账号发放。

### Railway

Railway 会自动注入 `PORT`，应用会优先使用该端口；部署时选择仓库根目录的 `Dockerfile`，设置 `MPT_ENV=production`、`MPT_COOKIE_SECURE=1`、`MPT_SETUP_TOKEN` 和 `DEEPSEEK_API_KEY`，并在服务设置中将 Healthcheck Path 设为 `/api/health`。首次部署后在 Railway 服务中添加持久化 Volume，并将挂载目录设为 `/app/data`，同时设置 `RAILWAY_RUN_UID=0` 以保证非 root 镜像可以写入 Railway Volume，否则 SQLite 数据和上传文件会在重新部署后丢失或无法写入。平台使用 SQLite 时保持单实例运行；如需多实例并发，应先迁移到 PostgreSQL 等服务端数据库。学生端和教师端使用同一域名的相对 API 路径与同一 `/app/data`，因此会连接到同一数据库。

## 验证

```bash
python -m unittest discover -s tests -v
python -m py_compile platform_server.py start_server.py
node --check assets/app.js
node --check assets/data.js
python tests/evaluate_deepseek_hints.py
```

最后一项使用服务器环境中已配置的 DeepSeek 密钥进行真实 L1—L3 教学支架验证，不会输出或写入密钥。

详细接口见 `interfaces/openapi.yaml`。
