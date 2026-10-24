# 课堂流程修改与验收记录

日期：2026-10-24。依据用户提供的《智能体逐项可执行修改说明》执行；五阶段 ID 与既有地址保持兼容。实现和验收信息保存在仓库中，不作为平台页面文案。

| 项目 | 根因与实现 | 验证入口 |
| --- | --- | --- |
| T01 | 学生阶段导航增加行动副标题；当前任务卡读取教师配置；首页与阶段任务直达工作页，浏览不触发提交；师生进度均按实际任务计算，教师放行不会虚增完成度 | classroom_ui；stage_task_status 一致性测试 |
| T02 | 简明任务独立保存四字段、教师与确认时间及原文指纹；原任务变化后必须复核，未确认显示原始材料 | brief_invalidated；changed_source_requires_teacher_brief_review |
| T03 | 一次记录一位成员；个人记录与小组讨论各自保存；三项主输入及折叠旧字段 | individual_first_versions；classroom_ui |
| T04 | 三步建模，光标插入符号，照片与式子/列表并存；服务端生成真实版本，后续修改必须有理由 | versions_are_real_snapshots；classroom_ui |
| T05 | 表格、图形、算式可选；两项检验记录关联方案版本，完成不再要求打开某个工具 | frontend_backend_task_matrix；浏览器页面检查 |
| T06 | A/B共享比较条件，分别记录证据、条件与待核验点；选择和理由由学生输入；修改条件取消已核对状态 | comparisonHistory；classroom_ui |
| T07 | 默认每组每阶段2次；原分层提示兼容，单次请求去重；失败不扣次数；支持教师增额和学生后续尝试 | hints_stage_limit_retry_idempotent_and_teacher_extra |
| T08 | 服务端教学边界、输入意图拦截、输出扫描；自由AI流式内容审核后发送；提交前重查教师开关 | ai_input_and_output_filter；stream_does_not_emit_unchecked_answer_fragments |
| T09 | 成员可确认本阶段分工，任意活动可建立新轮次；不轮换需记录原因；保留原成员和角色快照 | roles API；classroom_ui |
| T10 | 本人、代录、纸笔照片分别标记；代录须复述确认；个人记录追加版本和原成员称呼 | individual_first_versions_proxy_confirmation |
| T11 | 成果页展示真实版本和修改理由，人工选择方案和证据引用；反思三问；历史引用阻止删除原附件 | model snapshots；product references；classroom_ui |
| T12 | 全班目标与组有效阶段分离；暂缓、放行、回退、恢复跟随均留日志；回退保留原提交快照并重新开放编辑 | group_control_rollback_holds_history |
| T13 | 待回应组优先，展示算式、版本、条件和证据状态；筛选及只读组工作区；未更新阈值可配置 | group summaries；classroom_ui |
| T14 | 服务端时间计算等待时长，教师优先级和主题聚合，定向广播二次确认；回应后由学生确认是否解决 | broadcast权限回归；queue/help-feedback API |
| T15 | 自有项目按年级、教材、主题、课时筛选复制；只复制配置与教案结构，副本为未发布草稿 | files_export_and_project_copy_are_isolated |
| T16 | 材料状态与四维评分分开，默认资料不足；评价草稿、发布、撤回及历史分别保存；学生仅可读本组已发布评价 | evaluation_draft_publication_and_withdrawal；classroom_ui |
| T17 | 课后摘要连接组记录与版本；教师复盘三问；导出事件ID、匿名组ID、版本和证据，不导出AI私人对话和凭据 | export_uses_ids_and_versions_without_names_keys_or_conversations |
| T18 | 服务端版本检查，陈旧窗口返回409；本机草稿按账号、项目、组、页面隔离并限时保留；人工比较再合并；附件/事件/调用均绑定项目 | conflict_never_silently_overwrites；断网刷新与双窗口浏览器测试 |
| T19 | 登录三步，失败保留班级与组选项并清除组码；项目限已发布；二维码只携带5分钟有效一次性令牌，可撤销 | one_time_group_entry_is_scoped_expires_and_can_be_revoked |
| T20 | 真实接口成功才显示服务器保存；错误保留输入，提供再次同步；冲突使用可读字段而非JSON；学生页面不显示接口密钥配置说明 | 断网浏览器测试；平台既有错误路径回归 |

## 数据迁移和兼容

- `files`、`events`、`ai_calls` 新增 `project_id`，写入时由服务器当前会话归属。唯一旧项目可自动迁移；多项目遗留的无归属记录不猜测分配。
- `states` 的班级与组对象携带服务端 `_revision`；客户端不能省略或伪造版本来覆盖内容。新客户端在编辑开始时固定版本，轮询不替换未提交内容。
- 个人、方案、比较、成果及阶段回退历史由服务端保留；旧字段仍可读。
- 慢速模型请求不持有全平台写锁，完成时仅合并该请求负责的数据，避免覆盖等待期间学生保存的数学记录。
- 新增 `qrcode==8.2`；生产构建通过 requirements 安装。入口令牌只以哈希存库，重新生成会使同组旧码失效。
- 学生会话拿不到其他组求助、评价、教师审计和未发布项目；教师按项目所有权访问，管理员保留管理权限。

## 验收

自动化后端回归覆盖72项；浏览器连接临时真实API，执行个人修订、小组理解、方案建模、比较、角色确认、简明任务、评价草稿/发布、桌面/平板显示、保存失败保留输入、刷新恢复及双窗口冲突。

运行命令：

```text
python -m unittest discover -s tests -v
node tests/classroom_ui.cjs
node --check assets/app.js
node --check assets/classroom.js
python -m py_compile platform_server.py classroom.py lesson_plans.py
```

AI测试使用可控响应检验输入与输出防护及流式泄漏路径，没有对真实课堂学生行为或任意模型输出作出保证。生产数据保存期限继续以学校真实政策为准；本次未擅自设置服务器数据自动删除期限。
