# 数学项目协作智能体

面向初中数学项目式学习的一组一平板协作平台。平台采用后台管理式布局，课堂数据由服务端鉴权并保存到 SQLite；智能提示由服务端接入 DeepSeek，模型不可用时自动返回教师预设支架。页面不内置教师、学生、项目或课堂演示数据。

## 本地启动

需要 Python 3.11 或更高版本，不需要安装第三方依赖。

```powershell
python start_server.py
```

访问 `http://127.0.0.1:8000/`。平台不包含默认教师、学生、项目或课堂数据。首次启动时，终端会显示随机的一次性教师注册码；负责人在入口页完成平台注册，并设置管理员恢复码、班级码与小组访问规则。恢复码用于忘记教师密码时重置账号，只保存哈希，请离线保管。管理员登录后可在“账号与访问管理”中重置账号或访问码，并生成一次性教师注册码；其他教师在登录入口点击“教师注册”创建自己的账号。若旧入口仍停留在已经打开的标签页，关闭该标签页或普通刷新一次即可，无需手动清理浏览器缓存；`/index.html` 会由服务器强制转到当前登录入口。

## DeepSeek

密钥只配置在服务器环境变量中：

```powershell
$env:DEEPSEEK_API_KEY="你的密钥"
python start_server.py
```

默认调用 `https://api.deepseek.com/chat/completions`，模型为 `deepseek-v4-flash`，可通过 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL` 调整。平台关闭模型思考输出，限制提示长度和层级，并在超时、错误或越界回答时使用教师支架。

## 生产部署

复制 `.env.example`，至少设置 `MPT_SETUP_TOKEN` 和 `DEEPSEEK_API_KEY`，然后在 HTTPS 反向代理之后运行：

```bash
docker build -t math-project-agent .
docker run --env-file .env -p 8000:8000 -v math-project-data:/app/data math-project-agent
```

生产环境要求：

- 设置 `MPT_ENV=production` 和至少12位的一次性教师注册码，首位教师在入口页注册后该注册码立即失效；
- 如需无人值守初始化，也可同时设置教师账号、强密码、`MPT_ADMIN_RECOVERY_CODE`、班级码、小组码前缀和小组数量；
- 设置 `MPT_COOKIE_SECURE=1`，仅通过 HTTPS 提供服务；本地 `127.0.0.1` 预览会自动使用非 Secure 会话 Cookie；
- 持久化并定期备份 `/app/data`；
- 不把 `.env`、数据库、上传文件或 DeepSeek 密钥提交到代码仓库；
- 由学校或项目负责人完成数据告知、授权、保留期限与账号发放。

## 验证

```bash
python -m unittest discover -s tests -v
python -m py_compile platform_server.py start_server.py
node --check assets/app.js
node --check assets/data.js
```

详细接口见 `interfaces/openapi.yaml`。
