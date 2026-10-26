# 迈谱 Maple

**Math（数学）+ AI（人工智能）+ Project（项目）+ Learning（学习）+ Exploration（探索）**

每迈一步，皆有章谱。

面向初中数学项目式学习的一组一平板协作平台。平台采用后台管理式布局，课堂数据由服务端鉴权并保存到 SQLite；智能提示与学生自由工作区均由服务端统一接入模型，模型不可用时自动保留教学支架或平台引导。页面不内置教师、学生、项目或课堂演示数据。

## 本地启动

需要 Python 3.11 或更高版本。PDF教案提取使用 pdfplumber；Docker部署会自动安装。

```powershell
python -m pip install -r requirements.txt
python start_server.py
```

访问 `http://127.0.0.1:8000/`。本地首次启动可在入口创建管理员；生产环境通过 `MPT_TEACHER_USERNAME` 和 `MPT_TEACHER_PASSWORD` 完成一次性初始化。管理员在“账号与访问管理”中创建教师账号、分配班级，并查看或修改每班的小组数量和小组码。学生从对应班级入口进入，只需选择小组并输入 `01`、`02`、`03` 等小组码。教师注册码、班级码、恢复码和扫码令牌入口已移除。
学生端页面按当前 URL 加载，课堂阶段以教师端的统一状态为准；页面中的未提交输入会按账号、小组和页面暂存到浏览器，误刷新后自动恢复，点击保存成功后清除对应草稿。
生产数据清理由部署变量 `MPT_RESET_DATA_ONCE` 执行：每个新标记只运行一次，先备份数据库与上传文件，再清空课堂项目、阶段产出、过程记录和其他教师账号，同时保留管理员身份。操作方法见部署文档。

## 教案智能解析与多课时备课

教师在“项目支架配置”右侧确定总课时数并上传PDF/DOCX，点击“上传并解析”。豆包提取项目名称、授课教师、学校名称、项目设计教师、主学科、关联学科、授课年级、教材版本、教学单元或知识点、课时安排、项目概要、学习目标共12项，自动填入左侧；缺失信息留空。切换课时可查看、修改对应信息，点击“保存项目配置”才发布。已有未保存草稿时需确认替换。

- 复用服务器 `ARK_API_KEY`、`ARK_TEXT_URL`；可用 `ARK_PLAN_MODEL` 单独指定支持视觉输入和JSON输出的豆包模型/接入点，未设置时使用 `ARK_TEXT_MODEL`。不消耗学生自由工作区额度。
- 教案解析与课时数判断显式关闭模型深度思考，以加快含图PDF处理；解析使用独立的 `ARK_PLAN_TIMEOUT_SECONDS`（默认至少240秒，上限300秒），避免多课时及含图PDF受普通聊天超时限制。超时和连接故障会分别提示，原文件仍可重试。
- 教师备课解析独立于学生的初中数学范围限制。教案年级不会覆盖班级代码；小学教案需教师确认适用性，不能通过导入放宽学生AI范围。
- 单文件10MB，PDF最多40页、需视觉识别最多20页；文本最多10万字；最多30课时。DOCX提取正文和表格，含图片时明确提示先导出PDF，不能将未读取的图片当作已解析。
- 未发布原文件及解析结果只对上传教师可见，保留最近5次；任务状态持久化，刷新可继续查看，失败可重试。解析结果不是已发布配置，草稿按教师身份保存在当前浏览器会话中。
- 上传后的文件可在当前页面预览和下载，解析失败也不影响读取原文件。PDF使用浏览器原格式预览，DOCX提供安全正文预览并支持下载原文件（原排版及图片以下载文件为准）。
- 勾选“保存时向学生提供此文件”后保存，发布的材料独立保存在数据库，不受解析历史清理影响。学生各页面顶部均可直接预览、下载课程支持材料，无需跳转；取消勾选并保存可撤回，原下载地址立即失效。教师应在发布前核对文件是否包含答案或私密备注。
- 不再显示“本课时任务”或分钟分配表。课堂首页直接展示项目概要、知识点和学习目标。已发布课时与材料每3秒同步；两种学生AI读取同一项目信息，切换课时不清空学生产出、提交、角色或对话。
- 发布使用事务与版本检查；旧页面全量状态保存不能覆盖已发布信息。材料接口要求登录，只允许同站嵌入预览；数据目录、配置和源码不公开。

## DeepSeek配置

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
ARK_TEXT_MODEL=doubao-seed-2-1-turbo-260628
ARK_RESPONSES_URL=https://ark.cn-beijing.volces.com/api/v3/responses
MPT_KNOWLEDGE_SOURCE_DIR=/path/to/必须要的教材、教师用书和课标
ARK_IMAGE_URL=https://ark.cn-beijing.volces.com/api/v3/images/generations
ARK_IMAGE_MODEL=doubao-seedream-5-0-260128
ARK_IMAGE_RESPONSE_FORMAT=b64_json
DESMOS_API_KEY=你的Desmos API Key
```

图片生成结果会保存到 `/app/data/uploads`；如果使用 Railway，必须将该目录挂载到持久化 Volume。正式部署前应把 API 密钥只填入部署平台的环境变量，不要提交到 GitHub。

项目知识库会在启动时扫描 `MPT_KNOWLEDGE_SOURCE_DIR`（未设置时使用仓库同级的“必须要的教材、教师用书和课标”目录）。文本型 PDF/DOCX 在本地建立索引；扫描型 PDF 通过火山方舟 Files/Responses API 按 20 页分块识别，避免整本教材超出模型图像上下文限额。学生 AI 会根据问题选取相关分块并在回答中标注文件名；教师可在“项目知识库”查看索引状态、预览或下载原文件。教材原文件不进入 Git ，生产部署需将该目录挂载到服务器并配置 `ARK_API_KEY`。

自由 AI 每个小组使用独立额度，单次提问限制为 1800 字，请求只携带最近 6 条消息，文本输出限制为 1000 tokens，超额后端不再调用模型。

数学工具区和阶段3“模型表征与检验”共用 [Desmos API v1.12](https://www.desmos.com/api/v1.12/docs/index.html?lang=zh-CN) 的五种中文版工具：图形计算器、科学计算器、四功能计算器、几何工具和 3D 计算器。阶段3会分别保留五种工具状态，保存时同步生成当前画面截图；独立数学工具区也可按需保存截图。截图作为小组文件保存，支持预览、下载和删除。请在 [Desmos API Key 页面](https://www.desmos.com/my-api) 申请客户端 Key，并在本地 `.env` 或 Railway Variables 中设置 `DESMOS_API_KEY`；建议在 Desmos 后台把 Key 限制为平台部署域名。未配置时页面会显示配置提示，不会出现空白嵌入区。

学生侧栏的“数学可视化”独立页面本地集成了 [edu-ai-builders/math-viz-kit](https://github.com/edu-ai-builders/math-viz-kit) 的 127 个单文件交互工具（上游提交 `633d31a058755886f560674a6ca0a208e9cdaab2`），覆盖小学至大学并支持按学段、难度、优先级和认知动作筛选。工具资源放在 `vendor/math-viz-kit`，同源加载且不依赖外站 iframe。

## 生产部署

复制 `.env.example`，首次生产部署至少设置 `MPT_TEACHER_USERNAME` 和 `MPT_TEACHER_PASSWORD`，然后在 HTTPS 反向代理之后运行：

```bash
docker build -t math-project-agent .
docker run --env-file .env -p 8000:8000 -v math-project-data:/app/data math-project-agent
```

生产环境要求：

- 设置 `MPT_ENV=production`、管理员教师账号和至少8位强密码；初始化完成后可从部署环境移除明文初始化密码；
- 每个班级由管理员指定任课教师、小组数量及独立小组码；默认连续生成 `01`、`02`、`03`，也可逐组修改；
- 教师密码使用慢哈希完成登录验证；为满足管理员显式查看需求，另用 `/app/data/account-encryption.key` 加密保存可查看副本。必须同时备份数据目录和该密钥；
- 设置 `MPT_COOKIE_SECURE=1`，仅通过 HTTPS 提供服务；本地 `127.0.0.1` 预览会自动使用非 Secure 会话 Cookie；
- 持久化并定期备份 `/app/data`；
- 不把 `.env`、数据库、上传文件或 DeepSeek 密钥提交到代码仓库；
- 由学校或项目负责人完成数据告知、授权、保留期限与账号发放。

### Railway

Railway 会自动注入 `PORT`，应用会优先使用该端口；部署时选择仓库根目录的 `Dockerfile`，设置 `MPT_ENV=production`、`MPT_COOKIE_SECURE=1`、`MPT_TEACHER_USERNAME`、`MPT_TEACHER_PASSWORD` 和模型密钥，并将 Healthcheck Path 设为 `/api/health`。添加持久化 Volume 并挂载到 `/app/data`；若 Volume 写入权限受限，按 Railway 当前文档设置运行 UID。平台使用 SQLite 时保持单实例运行。清空数据时给 `MPT_RESET_DATA_ONCE` 设置一个全新标记，部署成功并核对备份后再将其留空；不要删除 Volume。

### 腾讯云

广州轻量应用服务器 4核4GB 可承载约14台课堂设备。仓库提供 Docker Compose、HTTPS 反向代理、一键部署、更新和 COS 备份脚本，完整步骤见 [腾讯云部署指南](docs/腾讯云部署.md)。

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

## 课堂流程与保存保护

学生端支持简明任务、逐成员的本人/代录/纸笔记录、带修改原因的数学方案版本、共同条件下的A/B比较和成果证据引用。教师可单独暂缓、放行、回退小组，设置已学知识与每阶段提示次数，分别保存评价草稿或发布评价。项目副本保留备课配置，不复制学生记录，发布后才向学生开放。

编辑草稿按账号、项目、小组和页面暂存；只有接口确认成功才显示服务器保存。另一窗口更新同一记录时要求比较版本后保存，模型请求等待期间的工作区保存不会被旧请求覆盖。新增文件、事件、模型调用都绑定项目，旧版客户端需刷新以获取版本协议。

详细实现与逐项验收见 `docs/classroom-changes.md`。
