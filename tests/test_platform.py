import http.cookiejar
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import platform_server as app


class LegacyMigrationTest(unittest.TestCase):
    def test_single_teacher_schema_is_upgraded_for_multiple_teachers(self):
        original = (app.DATA_DIR, app.UPLOAD_DIR, app.DB_PATH, app.SETUP_TOKEN_FILE)
        with tempfile.TemporaryDirectory() as directory:
            app.DATA_DIR = Path(directory)
            app.UPLOAD_DIR = app.DATA_DIR / "uploads"
            app.DB_PATH = app.DATA_DIR / "legacy.db"
            app.SETUP_TOKEN_FILE = app.DATA_DIR / "setup-token.txt"
            with app.database() as db:
                db.execute("""
                    CREATE TABLE teachers (
                      id INTEGER PRIMARY KEY CHECK(id=1), username TEXT UNIQUE NOT NULL,
                      password_hash TEXT NOT NULL, created_at TEXT NOT NULL
                    )
                """)
                db.execute(
                    "INSERT INTO teachers(id,username,password_hash,created_at) VALUES(1,?,?,?)",
                    ("legacy_admin", app.password_hash("LegacyTeacher#2026"), app.utc_now()),
                )
            app.init_database()
            with app.database() as db:
                schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='teachers'").fetchone()[0]
                self.assertNotIn("CHECK(id=1)", schema.replace(" ", ""))
                db.execute(
                    "INSERT INTO teachers(username,password_hash,created_at,is_admin) VALUES(?,?,?,0)",
                    ("second_teacher", app.password_hash("SecondTeacher#2026"), app.utc_now()),
                )
                self.assertEqual(db.execute("SELECT COUNT(*) FROM teachers").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT is_admin FROM teachers WHERE id=1").fetchone()[0], 1)
        app.DATA_DIR, app.UPLOAD_DIR, app.DB_PATH, app.SETUP_TOKEN_FILE = original


class PlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        app.DATA_DIR = Path(cls.temp.name)
        app.UPLOAD_DIR = app.DATA_DIR / "uploads"
        app.DB_PATH = app.DATA_DIR / "test.db"
        app.SETUP_TOKEN_FILE = app.DATA_DIR / "setup-token.txt"
        app.SETUP_TOKEN_DISPLAY = ""
        for key in ("DEEPSEEK_API_KEY", "MPT_TEACHER_USERNAME", "MPT_TEACHER_PASSWORD",
                    "MPT_CLASS_CODE", "MPT_GROUP_CODE_PREFIX", "MPT_GROUP_COUNT"):
            os.environ.pop(key, None)
        os.environ["MPT_ENV"] = "development"
        os.environ["MPT_SETUP_TOKEN"] = "TEST-SETUP-TOKEN-2026"
        app.init_database()
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        invalid = json.dumps({"setupToken": "WRONG-TOKEN"}).encode()
        invalid_request = urllib.request.Request(
            cls.base + "/api/setup", data=invalid, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(invalid_request, timeout=3)
            raise AssertionError("invalid setup token was accepted")
        except urllib.error.HTTPError as error:
            if error.code != 403:
                raise
        payload = json.dumps({
            "setupToken": "TEST-SETUP-TOKEN-2026", "username": "test_teacher",
            "password": "StrongTeacher#2026", "recoveryCode": "RECOVERY-CODE-TEST-2026", "classCode": "CLASS2026",
            "groupCodePrefix": "TEAM01", "groupCount": 3,
        }).encode()
        request = urllib.request.Request(
            cls.base + "/api/setup", data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 201:
                raise AssertionError(f"setup failed with status {response.status}")
        with app.database() as db:
            prefix = db.execute("SELECT value FROM settings WHERE key='group_code_prefix'").fetchone()
        if not prefix or prefix["value"] != "TEAM":
            raise AssertionError("initial group-code prefix was not persisted")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def client(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def json_request(self, client, path, method="GET", payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with client.open(request, timeout=8) as response:
            return response.status, json.loads(response.read())

    def test_teacher_state_and_student_hint_fallback(self):
        teacher = self.client()
        status, login = self.json_request(
            teacher, "/api/login", "POST",
            {"role": "teacher", "username": "test_teacher", "password": "StrongTeacher#2026"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(login["role"], "teacher")
        status, blank = self.json_request(teacher, "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn("project", blank["state"])
        self.assertEqual(blank["state"]["groupCount"], 3)
        state = {
            "project": "集成测试项目", "className": "测试班级", "currentStage": 2,
            "aiOpen": True, "hintQuota": 3, "maxHintLevel": 3,
            "teacherConfig": {"mathTopic": "几何测量", "standard": "建立并检验模型", "staticHints": ["先画出已知关系。"]},
            "workspace": {"problem": ""}, "groupId": 1,
        }
        status, saved = self.json_request(teacher, "/api/state", "PUT", {"state": state})
        self.assertEqual(status, 200)
        self.assertTrue(saved["ok"])
        status, runtime = self.json_request(teacher, "/api/runtime")
        self.assertEqual(status, 200)
        self.assertTrue(runtime["database"]["healthy"])
        self.assertEqual(runtime["data"]["groups"], 3)
        self.assertTrue(runtime["ai"]["model"])
        self.assertIn("freeAiConfigured", runtime["ai"])
        self.assertEqual(runtime["ai"]["freeAiTextModel"], app.ARK_TEXT_MODEL)
        self.assertEqual(runtime["ai"]["freeAiImageModel"], app.ARK_IMAGE_MODEL)

        student = self.client()
        status, login = self.json_request(
            student, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 1, "groupCode": "TEAM01"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(login["groupId"], 1)
        status, data = self.json_request(student, "/api/state")
        self.assertEqual(data["state"]["currentStage"], 2, "小组刷新不能被默认阶段1覆盖")
        self.assertEqual(data["state"]["project"], "集成测试项目")
        status, hint = self.json_request(
            student, "/api/hints", "POST",
            {"done": "已经画出草图", "type": "变量关系", "issue": "不知道如何表示两条边的关系"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(hint["source"], "teacher_fallback")
        self.assertTrue(hint["reply"].startswith("诊断："))
        self.assertIn("支架：教师预设支架是：先画出已知关系。", hint["reply"])
        self.assertIn("下一步：", hint["reply"])

        status, overview = self.json_request(teacher, "/api/state")
        self.assertEqual(status, 200)
        groups = {item["id"]: item for item in overview["state"]["groups"]}
        self.assertEqual(groups[1]["status"], "持续推进")
        self.assertEqual(groups[1]["help"], 0, "静态降级支架不应扣除AI额度")
        self.assertEqual(groups[1]["progress"], 0, "求助次数不能虚增学习产出进度")
        self.assertEqual(groups[2]["status"], "准备中")
        self.assertEqual(groups[2]["progress"], 0)

        student_state = data["state"]
        student_state["workspace"] = {
            "problem": "测量并建立面积模型", "known": "场地边界已知", "unknown": "最大面积",
            "constraints": "长度为正", "variables": "x表示宽", "relation": "y=x(20-x)",
            "model": "二次函数模型", "evidence": "边界值核验", "next": "绘制函数图像",
        }
        status, saved = self.json_request(student, "/api/state", "PUT", {"state": student_state})
        self.assertEqual(status, 200)
        self.assertTrue(saved["ok"])
        status, overview = self.json_request(teacher, "/api/state")
        groups = {item["id"]: item for item in overview["state"]["groups"]}
        self.assertGreater(groups[1]["progress"], 0)
        self.assertEqual(groups[1]["latest"], "工作区已保存")

    def test_hint_level_state_machine_stays_in_level_until_student_advances(self):
        student = self.client()
        status, _ = self.json_request(
            student, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 3, "groupCode": "TEAM03"},
        )
        self.assertEqual(status, 200)
        replies = [
            ("诊断：目标量还不明确。\n支架：先区分总面积和可种植面积。\n下一步：列出两个面积分别包含哪些部分。", 80, "ok"),
            ("诊断：已经区分面积，但条件还未对应。\n支架：逐条标记长度条件作用于外框还是内部区域。\n下一步：在草图旁写出条件来源。", 75, "ok"),
            ("诊断：你们已经把变量和面积对象分开记录，说明整理方向是有效的。\n支架：现在请比较两个面积表达式分别依赖哪些量，并追问其中一个量是否还缺少现实约束。\n下一步：把外框面积和内部面积各写成一行，只标出第一处仍需核对的关系。", 90, "ok"),
            ("诊断：条件已经整理，可以开始拆解。\n支架：先建立外框面积，再单独表示小路面积，最后比较两者关系。\n下一步：定义一个表示宽度的变量并注明范围。", 90, "ok"),
            ("诊断：你们回到前一层重新检查对象关系，说明没有急于接受结论，这是很好的核验习惯。\n支架：请重新观察当前模型中每个量的现实含义，并追问它是否真正对应题目中的一个条件。\n下一步：选择一个变量写出它的含义、单位和来源，再由小组判断是否需要修改。", 90, "ok"),
            ("诊断：你们已经把当前层的变量关系重新核对，说明能够根据证据修正思路。\n支架：请把变量、关系式和现实条件逐一对应，判断哪一项仍然缺少验证依据。\n下一步：任选一处关系做一次边界或单位核验，并由小组说明核验结果支持或否定了什么。", 90, "ok"),
        ]
        with mock.patch.object(app, "deepseek_hint", side_effect=replies):
            for question in ("优化目标到底是哪一个面积", "小路宽度属于哪个条件"):
                status, result = self.json_request(
                    student, "/api/hints", "POST",
                    {"action": "ask", "done": "已经画出场地草图并标出长度", "type": "变量关系", "issue": question},
                )
                self.assertEqual(status, 200)
                self.assertEqual(result["level"], "L1")
            self.assertEqual(result["hintUsage"]["L1"], 2)
            thread_id = result["threadId"]
            status, resolved = self.json_request(
                student, "/api/hints", "POST", {"action": "resolve", "threadId": thread_id},
            )
            self.assertEqual(status, 200)
            self.assertTrue(resolved["ok"])
            with self.assertRaises(urllib.error.HTTPError) as missing_attempt:
                self.json_request(
                    student, "/api/hints", "POST", {"action": "advance", "threadId": thread_id},
                )
            self.assertEqual(missing_attempt.exception.code, 400)
            status, advanced = self.json_request(
                student, "/api/hints", "POST",
                {"action": "advance", "threadId": thread_id,
                 "done": "根据L1提示重新区分了总面积和种植面积", "type": "数学建模"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(advanced["currentLevel"], 2)
            self.assertEqual(advanced["level"], "L2")
            self.assertIn("你们已经把变量和面积对象分开记录", advanced["reply"])
            status, reopened = self.json_request(student, "/api/state")
            self.assertEqual(status, 200)
            self.assertEqual(reopened["state"]["activeHelpThread"]["status"], "active")
            self.assertEqual(reopened["state"]["activeHelpThread"]["type"], "数学建模")
            self.assertEqual(
                reopened["state"]["activeHelpThread"]["messages"][-1]["done"],
                "根据L1提示重新区分了总面积和种植面积",
            )
            status, result = self.json_request(
                student, "/api/hints", "POST",
                {"action": "ask", "done": "定义x表示小路宽度", "type": "变量关系", "issue": "怎样组织面积关系"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["level"], "L2")
            self.assertEqual(result["hintUsage"], {"L1": 2, "L2": 2, "L3": 0, "L4": 0})
            status, revisited = self.json_request(
                student, "/api/hints", "POST",
                {"action": "ask", "threadId": result["threadId"], "level": 1,
                 "done": "重新核对了变量含义", "type": "数学建模"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(revisited["level"], "L1")
            status, refreshed = self.json_request(student, "/api/state")
            self.assertEqual(status, 200)
            self.assertEqual(refreshed["state"]["activeHelpThread"]["type"], "数学建模")
            status, advanced_again = self.json_request(
                student, "/api/hints", "POST",
                {"action": "advance", "threadId": result["threadId"], "done": "核对变量单位后确认了面积关系"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(advanced_again["level"], "L3")
            self.assertEqual(advanced_again["currentLevel"], 3)
            status, final_state = self.json_request(student, "/api/state")
            self.assertEqual(status, 200)
            active_messages = final_state["state"]["activeHelpThread"]["messages"]
            self.assertEqual(active_messages[-1]["level"], "L3")
            self.assertEqual(active_messages[-1]["done"], "核对变量单位后确认了面积关系")
        status, teacher_help = self.json_request(
            student, "/api/hints", "POST", {"action": "teacher", "issue": "仍然不能确定面积关系"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(teacher_help["created"])
        self.assertEqual(teacher_help["hintUsage"]["L4"], 1)
        status, state = self.json_request(student, "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["state"]["activeHelpThread"]["currentLevel"], 4)

    def test_password_and_hint_output_guards(self):
        encoded = app.password_hash("secret")
        self.assertTrue(app.password_ok("secret", encoded))
        self.assertFalse(app.password_ok("wrong", encoded))
        self.assertEqual(app.hint_policy({"hintPolicy": {"L1": 4, "L2": 3, "L3": 3, "L4": 1}}), app.HINT_POLICY_DEFAULT)
        self.assertIsNone(app.validate_hint("最终答案是 42"))
        self.assertEqual(app.validate_hint("先检查已知条件与目标量是否对应。"), "先检查已知条件与目标量是否对应。")
        structured = app.parse_model_hint(json.dumps({
            "diagnosis": "你们已经完成了有效的条件整理，并且能够说明当前尝试依据了哪些信息。现在还需要继续核对对象之间的对应关系，避免把形式相似误认为意义相同，这一步值得保留并说明，便于后续复盘。",
            "guidance": "请比较两个对象分别由哪些条件决定，再追问如果删去其中一条条件，当前判断是否仍然成立。请由组员说出理由，并用题目中的材料逐项核对，而不是直接接受模型给出的描述，确保追问能够推动判断。",
            "nextAction": "下一步请小组选择一个最容易检查的关系，写出对象、条件和核验依据，再通过单位、边界值或草图进行一次局部检查。记录观察到的现象，并说明它支持或暴露了哪一处仍需修订的地方。",
        }), "L2")
        self.assertIsNotNone(structured)
        self.assertIsNone(app.parse_model_hint('{"diagnosis":"太短","guidance":"太短","nextAction":"太短"}', "L2"))

    def test_login_portal_uses_one_selected_role_and_form_submit(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('class="auth-role-switch"', script)
        self.assertIn('<form id="studentAuthForm">', script)
        self.assertIn('<form id="teacherAuthForm">', script)
        self.assertIn("const setAuthRole=role=>", script)
        self.assertIn("event.preventDefault()", script)
        self.assertNotIn('class="auth-options"', script)
        self.assertIn('.auth-card:has(.auth-role-switch)', styles)

    def test_help_actions_and_recent_button_have_stable_layout(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('class="help-recent-head"', script)
        self.assertIn('recent-new-conversation', script)
        self.assertIn('class="help-actions"', script)
        self.assertIn('.help-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));', styles)
        self.assertNotIn('<a class="btn sm mt12" href="help.html?new=1">新对话</a>', script)

    def test_free_ai_stream_uses_ark_sse_and_configured_timeout(self):
        class FakeStreamResponse:
            def __init__(self, lines):
                self.lines = lines

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def __iter__(self):
                return iter(self.lines)

        original_key = os.environ.get("ARK_API_KEY")
        os.environ["ARK_API_KEY"] = "test-key"
        received = []
        response = FakeStreamResponse([
            'data: {"choices":[{"delta":{"content":"第一段 "}}]}\n'.encode(), b'\n',
            'data: {"choices":[{"delta":{"content":"第二段"}}]}\n'.encode(), b'\n',
            b'data: [DONE]\n', b'\n',
        ])
        try:
            with mock.patch.object(app.urllib.request, "urlopen", return_value=response) as urlopen:
                answer, latency, status, model = app.ark_free_chat_stream(
                    {}, 1, "测试流式", [], received.append,
                )
            self.assertEqual(answer, "第一段 第二段")
            self.assertEqual(received, ["第一段 ", "第二段"])
            self.assertEqual(status, "ok")
            self.assertEqual(model, app.ARK_TEXT_MODEL)
            request = urlopen.call_args.args[0]
            payload = json.loads(request.data)
            self.assertTrue(payload["stream"])
            self.assertEqual(payload["speed"], app.ARK_TEXT_SPEED)
            self.assertEqual(urlopen.call_args.kwargs["timeout"], app.ARK_TEXT_TIMEOUT_SECONDS)
        finally:
            if original_key is None:
                os.environ.pop("ARK_API_KEY", None)
            else:
                os.environ["ARK_API_KEY"] = original_key

        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("/api/ai/chat/stream", script)
        self.assertIn("getReader()", script)
        self.assertIn("new TextDecoder()", script)
        self.assertIn('id="aiImageMode"', script)
        self.assertIn("生成图片：${rawPrompt}", script)
        self.assertIn("input.value='';clearDraft();", script)
        self.assertIn(".recent-new-conversation{display:inline-flex;align-items:center;gap:5px;border-color:var(--blue);", styles)
        self.assertIn(".brand-mark{background:var(--blue)}", styles)
        self.assertIn(".auth-brand-lockup{filter:invert(1)}", styles)

    def test_setup_portal_uses_aligned_form_and_enter_submit(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('<form id="setupForm" class="setup-form">', script)
        self.assertIn("document.querySelector('#setupForm').onsubmit=async event=>", script)
        self.assertIn("document.querySelector('#setupGroupCode').value", script)
        self.assertIn('class="icon-btn" id="copyRecovery"', script)
        self.assertNotIn("document.querySelector('#setupGroupCount')", script)
        self.assertNotIn("document.querySelector('#setupGroupPrefix')", script)
        self.assertNotIn('class="field span-2"', script)
        self.assertIn('.copy-field:focus-within', styles)
        self.assertEqual(app.group_prefix_from_code("TEAM01"), "TEAM")
        self.assertEqual(app.group_prefix_from_code("TEAM"), "TEAM")

    def test_free_ai_conversations_text_image_routing_and_access(self):
        self.assertEqual(app.free_ai_intent("请解释如何生成函数图像"), "text")
        self.assertEqual(app.free_ai_intent("不要生成图片，只解释原理"), "text")
        self.assertEqual(app.free_ai_intent("请生成一张双曲线示意图"), "image")

        student = self.client()
        status, _ = self.json_request(
            student, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 2, "groupCode": "TEAM02"},
        )
        self.assertEqual(status, 200)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.json_request(
                student, "/api/ai/chat", "POST",
                {"prompt": "请解释如何生成函数图像", "newConversation": True},
            )
        self.assertEqual(context.exception.code, 503)
        self.assertIn("ARK_API_KEY", context.exception.read().decode())

        status, saved = self.json_request(student, "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(saved["state"]["freeAi"]["activeConversationId"], "")
        self.assertEqual(saved["state"]["freeAi"]["conversations"], [])

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self, limit=None):
                return self.payload

        original_key = os.environ.get("ARK_API_KEY")
        os.environ["ARK_API_KEY"] = "test-key"
        image_file = None
        try:
            text_upstream = FakeResponse(json.dumps({
                "choices": [{"message": {"content": "当 $x \\neq 0$ 时，可以继续化简。"}}],
            }).encode())
            with mock.patch.object(app.urllib.request, "urlopen", return_value=text_upstream) as urlopen:
                status, text_result = self.json_request(
                    student, "/api/ai/chat", "POST",
                    {"prompt": "请检查一元一次不等式", "newConversation": True},
                )
            self.assertEqual(status, 200)
            self.assertEqual(text_result["intent"], "text")
            self.assertEqual(text_result["source"], "ark")
            text_request = urlopen.call_args.args[0]
            self.assertEqual(text_request.full_url, app.ARK_TEXT_URL)
            text_body = json.loads(text_request.data)
            self.assertEqual(text_body["model"], app.ARK_TEXT_MODEL)
            self.assertEqual(text_body["speed"], app.ARK_TEXT_SPEED)
            self.assertEqual(text_body["messages"][0]["role"], "system")
            self.assertEqual(text_body["messages"][-1]["content"], "请检查一元一次不等式")
            conversation_id = text_result["conversationId"]

            error_body = json.dumps({
                "error": {"code": "InvalidParameter", "message": "model is unavailable"},
            }).encode()
            upstream_error = urllib.error.HTTPError(
                app.ARK_TEXT_URL, 400, "Bad Request", {}, io.BytesIO(error_body),
            )
            with mock.patch.object(app.urllib.request, "urlopen", side_effect=upstream_error):
                with self.assertRaises(urllib.error.HTTPError) as context:
                    self.json_request(
                        student, "/api/ai/chat", "POST",
                        {"prompt": "继续检查", "conversationId": conversation_id},
                    )
            self.assertEqual(context.exception.code, 400)
            self.assertIn("model is unavailable", context.exception.read().decode())

            encoded = app.base64.b64encode(b"fake-png").decode("ascii")
            image_upstream = FakeResponse(json.dumps({"data": [{"b64_json": encoded}]}).encode())
            with mock.patch.object(app.urllib.request, "urlopen", return_value=image_upstream) as urlopen:
                status, image_result = self.json_request(
                    student, "/api/ai/chat", "POST",
                    {"prompt": "请生成一张双曲线示意图", "conversationId": conversation_id},
                )
            self.assertEqual(status, 200)
            self.assertEqual(image_result["intent"], "image")
            self.assertEqual(image_result["source"], "ark")
            self.assertEqual(image_result["usage"], 2)
            image_file = image_result["file"]
            image_body = json.loads(urlopen.call_args.args[0].data)
            self.assertEqual(image_body["model"], app.ARK_IMAGE_MODEL)
            self.assertNotIn("sequential_image_generation", image_body)

            status, current = self.json_request(student, "/api/state")
            self.assertEqual(status, 200)
            conversation = current["state"]["freeAi"]["conversations"][-1]
            self.assertEqual(conversation["id"], conversation_id)
            self.assertEqual(conversation["messages"][-1]["kind"], "image")
            self.assertEqual(conversation["messages"][-1]["file"]["url"], image_file["url"])

            with student.open(self.base + image_file["url"], timeout=3) as response:
                self.assertEqual(response.read(), b"fake-png")

            rate_limit_body = json.dumps({
                "error": {"code": "SetLimitExceeded", "message": "model service paused"},
                "request_id": "test-request-id",
            }).encode()
            rate_limit = urllib.error.HTTPError(
                "https://ark.test/images", 429, "Too Many Requests", {}, io.BytesIO(rate_limit_body),
            )
            with mock.patch.object(app.urllib.request, "urlopen", side_effect=rate_limit):
                with self.assertRaises(app.UpstreamServiceError) as context:
                    app.ark_generate_image(2, "再次生成项目配图")
            self.assertEqual(context.exception.status, 429)
            self.assertIn("Safe Experience Mode", str(context.exception))
        finally:
            if original_key is None:
                os.environ.pop("ARK_API_KEY", None)
            else:
                os.environ["ARK_API_KEY"] = original_key

        other_student = self.client()
        status, _ = self.json_request(
            other_student, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 3, "groupCode": "TEAM03"},
        )
        self.assertEqual(status, 200)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.json_request(other_student, image_file["url"])
        self.assertEqual(context.exception.code, 404)

        for removed_route in ("/api/ai/file", "/api/ai/image"):
            with self.assertRaises(urllib.error.HTTPError) as context:
                self.json_request(student, removed_route, "POST", {"prompt": "旧入口"})
            self.assertEqual(context.exception.code, 404)

        teacher = self.client()
        status, _ = self.json_request(
            teacher, "/api/login", "POST",
            {"role": "teacher", "username": "test_teacher", "password": "StrongTeacher#2026"},
        )
        self.assertEqual(status, 200)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.json_request(teacher, "/api/ai/chat", "POST", {"prompt": "越权"})
        self.assertEqual(context.exception.code, 403)

        status, teacher_state = self.json_request(teacher, "/api/state")
        controlled_state = teacher_state["state"]
        controlled_state["aiOpen"] = False
        status, _ = self.json_request(teacher, "/api/state", "PUT", {"state": controlled_state})
        self.assertEqual(status, 200)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.json_request(student, "/api/ai/chat", "POST", {"prompt": "继续讨论"})
        self.assertEqual(context.exception.code, 409)
        controlled_state["aiOpen"] = True
        status, _ = self.json_request(teacher, "/api/state", "PUT", {"state": controlled_state})
        self.assertEqual(status, 200)


    def test_railway_port_overrides_local_port(self):
        environment = os.environ.copy()
        environment.update({"PORT": "9187", "MPT_PORT": "8000"})
        result = subprocess.run(
            [sys.executable, "-c", "import platform_server; print(platform_server.PORT)"],
            cwd=ROOT, env=environment, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "9187")

    def test_old_entry_redirect_and_removed_static_routes(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, file_pointer, code, message, headers, new_url):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(self.base + "/index.html", timeout=3)
            self.fail("old index route did not redirect")
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 302)
            self.assertEqual(error.headers["Location"], "/?current")
            self.assertEqual(error.headers["Clear-Site-Data"], '"cache"')

        for route in ("/teacher/versions.html", "/teacher/research.html", "/expert/overview.html"):
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(self.base + route, timeout=3)
            self.assertEqual(context.exception.code, 404, route)

    def test_group_count_and_member_lock_workflow(self):
        teacher = self.client()
        status, _ = self.json_request(
            teacher, "/api/login", "POST",
            {"role": "teacher", "username": "test_teacher", "password": "StrongTeacher#2026"},
        )
        self.assertEqual(status, 200)
        student = self.client()
        status, _ = self.json_request(
            student, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 3, "groupCode": "TEAM03"},
        )
        self.assertEqual(status, 200)

        first_members = ["甲", "乙", "丙", "丁", "戊"]
        status, result = self.json_request(
            student, "/api/group/members", "POST",
            {"action": "submit", "members": first_members},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["membersLocked"])
        self.assertEqual(len(result["roles"]), 5)
        status, state_result = self.json_request(student, "/api/state")
        locked_state = state_result["state"]
        locked_state["members"] = first_members + ["越权修改"]
        with self.assertRaises(urllib.error.HTTPError) as direct_change:
            self.json_request(student, "/api/state", "PUT", {"state": locked_state})
        self.assertEqual(direct_change.exception.code, 409)
        locked_state["members"] = first_members
        locked_state["roles"] = locked_state["roles"][-1:] + locked_state["roles"][:-1]
        locked_state["roleRound"] = locked_state.get("roleRound", 1) + 1
        status, rotated = self.json_request(student, "/api/state", "PUT", {"state": locked_state})
        self.assertEqual(status, 200)
        self.assertTrue(rotated["ok"])
        with self.assertRaises(urllib.error.HTTPError) as locked:
            self.json_request(
                student, "/api/group/members", "POST",
                {"action": "submit", "members": first_members + ["己"]},
            )
        self.assertEqual(locked.exception.code, 409)

        status, _ = self.json_request(
            teacher, "/api/group/members", "POST",
            {"action": "unlock", "groupId": 3},
        )
        self.assertEqual(status, 200)
        second_members = first_members + ["己"]
        status, result = self.json_request(
            student, "/api/group/members", "POST",
            {"action": "submit", "members": second_members},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["members"], second_members)
        status, result = self.json_request(
            teacher, "/api/group/members", "POST",
            {"action": "submit", "groupId": 3, "members": ["甲", "乙", "丙", "丁"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["membersLocked"])

        status, result = self.json_request(
            teacher, "/api/groups/configure", "POST",
            {"groupCount": 5, "groupCodePrefix": "NEWG"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["addedCodes"], [
            {"groupNo": 4, "code": "TEAM04"},
            {"groupNo": 5, "code": "TEAM05"},
        ])
        fifth = self.client()
        status, login = self.json_request(
            fifth, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 5, "groupCode": "TEAM05"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(login["groupId"], 5)
        status, result = self.json_request(
            teacher, "/api/groups/configure", "POST", {"groupCount": 3},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["groupCount"], 3)

    def test_z_admin_access_reset_and_password_recovery(self):
        teacher = self.client()
        status, _ = self.json_request(
            teacher, "/api/login", "POST",
            {"role": "teacher", "username": "test_teacher", "password": "StrongTeacher#2026"},
        )
        self.assertEqual(status, 200)
        status, access = self.json_request(teacher, "/api/admin/access")
        self.assertEqual(status, 200)
        self.assertEqual(access["username"], "test_teacher")
        self.assertTrue(access["recoveryConfigured"])

        status, result = self.json_request(
            teacher, "/api/admin/access", "POST",
            {"action": "group_code", "currentPassword": "StrongTeacher#2026", "groupNo": 1, "groupCode": "RESET01"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["logoutRole"], "student")
        student = self.client()
        status, login = self.json_request(
            student, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 1, "groupCode": "RESET01"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(login["groupId"], 1)

        status, result = self.json_request(
            teacher, "/api/admin/access", "POST",
            {"action": "teacher", "currentPassword": "StrongTeacher#2026", "username": "admin_teacher", "newPassword": "ChangedTeacher#2026"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["logoutRole"], "teacher")
        anonymous = self.client()
        status, recovered = self.json_request(
            anonymous, "/api/admin/recover", "POST",
            {"username": "admin_teacher", "recoveryCode": "RECOVERY-CODE-TEST-2026", "newPassword": "RecoveredTeacher#2026"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(recovered["ok"])
        status, login = self.json_request(
            anonymous, "/api/login", "POST",
            {"role": "teacher", "username": "admin_teacher", "password": "RecoveredTeacher#2026"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(login["role"], "teacher")

        status, invite = self.json_request(
            anonymous, "/api/admin/access", "POST",
            {"action": "teacher_invite", "currentPassword": "RecoveredTeacher#2026"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(invite["inviteCode"])
        second = self.client()
        status, registered = self.json_request(
            second, "/api/teacher/register", "POST",
            {"inviteCode": invite["inviteCode"], "username": "second_teacher", "password": "SecondTeacher#2026"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(registered["username"], "second_teacher")
        status, second_login = self.json_request(
            second, "/api/login", "POST",
            {"role": "teacher", "username": "second_teacher", "password": "SecondTeacher#2026"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(second_login["role"], "teacher")
        status, session = self.json_request(second, "/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(session["username"], "second_teacher")
        self.assertFalse(session["isAdmin"])
        with self.assertRaises(urllib.error.HTTPError) as forbidden:
            self.json_request(second, "/api/admin/access")
        self.assertEqual(forbidden.exception.code, 403)

        reused = self.client()
        with self.assertRaises(urllib.error.HTTPError) as reuse_error:
            self.json_request(
                reused, "/api/teacher/register", "POST",
                {"inviteCode": invite["inviteCode"], "username": "third_teacher", "password": "ThirdTeacher#2026"},
            )
        self.assertEqual(reuse_error.exception.code, 403)

    def test_zz_admin_factory_reset_requires_recovery_code_and_clears_classroom(self):
        admin = self.client()
        status, login = self.json_request(
            admin, "/api/login", "POST",
            {"role": "teacher", "username": "admin_teacher", "password": "RecoveredTeacher#2026"},
        )
        self.assertEqual(status, 200)
        status, _ = self.json_request(
            admin, "/api/state", "PUT",
            {"state": {"project": "temporary project", "className": "temporary class"}},
        )
        self.assertEqual(status, 200)

        with self.assertRaises(urllib.error.HTTPError) as wrong_code:
            self.json_request(
                admin, "/api/admin/factory-reset", "POST",
                {"recoveryCode": "WRONG-RECOVERY-CODE", "confirmation": "RESET"},
            )
        self.assertEqual(wrong_code.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as wrong_confirmation:
            self.json_request(
                admin, "/api/admin/factory-reset", "POST",
                {"recoveryCode": "RECOVERY-CODE-TEST-2026", "confirmation": "NO"},
            )
        self.assertEqual(wrong_confirmation.exception.code, 400)

        status, result = self.json_request(
            admin, "/api/admin/factory-reset", "POST",
            {"recoveryCode": "RECOVERY-CODE-TEST-2026", "confirmation": "RESET"},
        )
        self.assertEqual(status, 200)
        self.assertIn("admin", result["preserved"])
        with app.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM states").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM teachers").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT event_type FROM events ORDER BY id DESC LIMIT 1").fetchone()[0], "factory_reset")

        fresh_admin = self.client()
        status, login = self.json_request(
            fresh_admin, "/api/login", "POST",
            {"role": "teacher", "username": "admin_teacher", "password": "RecoveredTeacher#2026"},
        )
        self.assertEqual(status, 200)
        status, data = self.json_request(fresh_admin, "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn("project", data["state"])


if __name__ == "__main__":
    unittest.main()
