import base64
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
import urllib.parse
import urllib.request
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import platform_server as app


class DeepSeekConfigurationTest(unittest.TestCase):
    def test_current_model_and_deprecated_aliases(self):
        self.assertEqual(app.normalize_deepseek_model(None), "deepseek-flash")
        self.assertEqual(app.normalize_deepseek_model("deepseek-flash"), "deepseek-flash")
        self.assertEqual(app.normalize_deepseek_model("deepseek-chat"), "deepseek-flash")
        self.assertEqual(app.normalize_deepseek_model("deepseek-reasoner"), "deepseek-flash")
        self.assertEqual(app.normalize_deepseek_model("deepseek-v4-flash"), "deepseek-flash")
        self.assertEqual(app.normalize_deepseek_model("deepseek-v4-flash-vision-exp"), "deepseek-flash")
        self.assertEqual(app.normalize_deepseek_model("deepseek-v4-pro"), "deepseek-v4-pro")


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


class GradeRecognitionTest(unittest.TestCase):
    def test_platform_initialization_persists_only_grade_from_class_code(self):
        original = (app.DATA_DIR, app.UPLOAD_DIR, app.DB_PATH, app.SETUP_TOKEN_FILE)
        with tempfile.TemporaryDirectory() as directory:
            try:
                app.DATA_DIR = Path(directory)
                app.UPLOAD_DIR = app.DATA_DIR / "uploads"
                app.DB_PATH = app.DATA_DIR / "grade.db"
                app.SETUP_TOKEN_FILE = app.DATA_DIR / "setup-token.txt"
                with app.database() as db:
                    db.executescript("""
                        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                        CREATE TABLE teachers (
                          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                          password_hash TEXT NOT NULL, created_at TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0
                        );
                        CREATE TABLE groups (group_no INTEGER PRIMARY KEY, access_hash TEXT NOT NULL);
                        CREATE TABLE states (scope TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL);
                    """)
                app.initialize_platform(
                    "grade_teacher", "GradeTeacher#2026", "GRADE-RECOVERY-CODE-2026",
                    "2026八年级A班", "GRADE01", 1,
                )
                self.assertEqual(app.class_code_grade(), 8)
                self.assertEqual(app.state_get("class")["classGrade"], 8)
                with app.database() as db:
                    settings = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM settings")}
                self.assertNotIn("2026八年级A班", settings.values())
            finally:
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

    def test_knowledge_listing_requires_teacher_and_returns_index_status(self):
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.json_request(self.client(), "/api/knowledge")
        self.assertEqual(denied.exception.code, 401)

        student = self.client()
        self.json_request(student, "/api/login", "POST", {
            "role": "student", "classCode": "CLASS2026", "groupId": 1, "groupCode": "TEAM01",
        })
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.json_request(student, "/api/knowledge")
        self.assertEqual(denied.exception.code, 403)

        teacher = self.client()
        self.json_request(teacher, "/api/login", "POST", {
            "role": "teacher", "username": "test_teacher", "password": "StrongTeacher#2026",
        })
        with app.database() as db:
            db.execute(
                "INSERT INTO files(id,original_name,stored_name,mime,size,extracted_text,uploaded_at,uploaded_by,group_no) "
                "VALUES(?,?,?,?,?,?,?,?,NULL)",
                ("kb-regression", "reference.pdf", "kb-regression.pdf", "application/pdf", 10,
                 "indexed reference", app.utc_now(), "builtin-knowledge"),
            )
        try:
            status, listing = self.json_request(teacher, "/api/knowledge")
            self.assertEqual(status, 200)
            reference = next(item for item in listing["files"] if item["id"] == "kb-regression")
            self.assertEqual(reference["name"], "reference.pdf")
            self.assertGreater(reference["indexedChars"], 0)
            self.assertEqual(reference["chunkCount"], 0)
            self.assertGreaterEqual(listing["summary"]["indexed"], 1)
        finally:
            with app.database() as db:
                db.execute("DELETE FROM files WHERE id=?", ("kb-regression",))

    def test_desmos_pages_allow_required_official_assets(self):
        for path in ("/student/math-tools.html", "/student/stage3-graph.html"):
            with self.client().open(self.base + path, timeout=8) as response:
                policy = response.headers["Content-Security-Policy"]
            self.assertIn("script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.desmos.com https://*.desmos.com", policy)
            self.assertIn("style-src 'self' 'unsafe-inline' https://www.desmos.com https://*.desmos.com", policy)
            self.assertIn("font-src 'self' data: https://www.desmos.com https://*.desmos.com", policy)

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
            "project": "集成测试项目", "className": "八年级测试班", "currentStage": 2,
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
        with mock.patch.object(app, "DESMOS_API_KEY", "test-desmos-key"):
            status, config = self.json_request(student, "/api/math-tools/config")
        self.assertEqual(status, 200)
        self.assertEqual(config, {"apiKey": "test-desmos-key"})
        status, data = self.json_request(student, "/api/state")
        self.assertEqual(data["state"]["currentStage"], 2, "小组刷新不能被默认阶段1覆盖")
        self.assertEqual(data["state"]["project"], "集成测试项目")
        self.assertEqual(data["state"]["classGrade"], 8)
        with mock.patch.object(app, "deepseek_hint", return_value=(None, 0, "not_configured")) as upstream:
            status, project_hint = self.json_request(
                student, "/api/hints", "POST",
                {"action": "ask", "newThread": True, "done": "我已经了解项目材料", "type": "项目流程", "issue": "项目是什么，接下来要做什么"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(project_hint["source"], "teacher_fallback")
        upstream.assert_called_once()
        status, hint = self.json_request(
            student, "/api/hints", "POST",
            {"done": "已经画出草图", "type": "变量关系", "issue": "不知道如何表示两条边的关系"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(hint["source"], "teacher_fallback")
        self.assertTrue(hint["reply"].startswith("诊断："))
        self.assertIn("先画出已知关系", hint["reply"])
        self.assertIn("完成标志：", hint["reply"])

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
        self.assertEqual(groups[1]["latest"], saved["state"]["versions"][-1]["note"])
        self.assertEqual(saved["state"]["versions"][-1]["model"], "二次函数模型")
        self.assertTrue(saved["state"]["stageTaskStatus"][2][2])

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

    def test_hint_new_conversation_isolated_from_active_and_teacher_threads(self):
        original_group = app.state_get("group:2")
        original_class = app.state_get("class")
        self.addCleanup(app.state_put, "group:2", original_group)
        self.addCleanup(app.state_put, "class", original_class)
        student = self.client()
        status, _ = self.json_request(
            student, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 2, "groupCode": "TEAM02"},
        )
        self.assertEqual(status, 200)
        reply = ("诊断：当前问题需要先区分对象。\n支架：请只核对这个主题中的条件。\n下一步：记录一次独立尝试。", 20, "ok")
        thread_ids = []
        with mock.patch.object(app, "deepseek_hint", return_value=reply):
            for topic, issue in (
                ("数据处理", "如何用平均数比较XY两组数据"),
                ("计算核验", "12÷3的计算结果是否正确"),
                ("表达与解释", "如何解释一次函数图像的斜率"),
            ):
                status, result = self.json_request(
                    student, "/api/hints", "POST",
                    {"action": "ask", "newThread": True, "done": f"已尝试{issue}", "type": topic, "issue": issue},
                )
                self.assertEqual(status, 200)
                thread_ids.append(result["threadId"])
                if issue.startswith("如何用平均数"):
                    status, escalated = self.json_request(
                        student, "/api/hints", "POST",
                        {"action": "teacher", "threadId": result["threadId"], "issue": issue, "type": topic},
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(escalated["threadId"], result["threadId"])

        self.assertEqual(len(set(thread_ids)), 3)
        status, state = self.json_request(student, "/api/state")
        self.assertEqual(status, 200)
        threads = {item["id"]: item for item in state["state"]["helpThreads"]}
        self.assertEqual(len(threads), 3)
        self.assertEqual(threads[thread_ids[0]]["status"], "teacher")
        self.assertEqual(threads[thread_ids[1]]["messages"][0]["question"], "12÷3的计算结果是否正确")
        self.assertEqual(threads[thread_ids[2]]["messages"][0]["question"], "如何解释一次函数图像的斜率")
        self.assertEqual(state["state"]["activeHelpThread"]["id"], thread_ids[2])
        self.assertTrue(all(len(threads[thread_id]["messages"]) == 1 for thread_id in thread_ids))

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
        self.assertEqual(app.grade_from_text("2026八年级A班"), 8)
        self.assertEqual(app.grade_from_text("G9-MATH"), 9)
        self.assertIsNone(app.grade_from_text("CLASS2026"))
        self.assertIn("八年级学生", app.student_math_scope({"classGrade": 8}))
        self.assertIn("八年级学生", app.ark_free_messages({"classGrade": 8}, 1, "一次函数", [])[0]["content"])
        self.assertFalse(app.is_math_question("英雄联盟怎么出装"))
        self.assertTrue(app.is_math_question("比较两件装备的价格和性价比"))
        self.assertTrue(app.is_math_question("为什么？", [{"content": "先解一元一次方程"}]))

        stage_one = {"currentStage": 1, "teacherConfig": {"staticHints": ["打开任务理解表填写已知信息"]}}
        fallback = app.static_hint(stage_one, 1)
        self.assertIn("阶段1", fallback)
        self.assertNotIn("阶段2", fallback)
        self.assertFalse(app.hint_within_open_stage("打开阶段2任务理解表继续填写", 1))
        guarded = app.visible_help_entries([{"reply": "打开任务理解表填写已知信息", "source": "deepseek"}], 1)
        self.assertIn("尚未开放", guarded[0]["reply"])
        self.assertEqual(guarded[0]["source"], "stage_guard")

        model_reply = json.dumps({
            "diagnosis": "你们已经记录了当前尝试，但还需要先核对教师当前开放的课堂阶段和页面。",
            "guidance": "请打开阶段2任务理解表，填写已知信息、未知信息和现实约束，再保存小组理解。",
            "nextAction": "完成后检查三个字段是否对应项目问题，并用一句话说明它们之间的关系。",
        }, ensure_ascii=False).encode()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": model_reply.decode()}}]}).encode()

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), mock.patch.object(
            app.urllib.request, "urlopen", return_value=FakeResponse(),
        ):
            reply, _, status = app.deepseek_hint(stage_one, "L1", "已查看项目材料", "任务理解", "下一步做什么")
        self.assertIsNone(reply)
        self.assertEqual(status, "future_stage")

    def test_login_portal_uses_one_selected_role_and_form_submit(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('class="auth-role-switch"', script)
        self.assertIn('<form id="studentAuthForm">', script)
        self.assertIn('<form id="teacherAuthForm">', script)
        self.assertIn("const setAuthRole=role=>", script)
        self.assertIn("event.preventDefault()", script)
        self.assertIn("teacher?'进入教师端':'进入学生端'", script)
        self.assertNotIn("进入工作端", script)
        self.assertNotIn('class="auth-options"', script)
        self.assertIn('.auth-card:has(.auth-role-switch)', styles)
        self.assertIn('.auth-card .toolbar{justify-content:flex-end}', styles)

    def test_broadcast_publish_permissions_and_stale_state(self):
        teacher, student = self.client(), self.client()
        self.json_request(teacher, "/api/login", "POST", {"role": "teacher", "username": "test_teacher", "password": "StrongTeacher#2026"})
        self.json_request(student, "/api/login", "POST", {"role": "student", "classCode": "CLASS2026", "groupId": 1, "groupCode": "TEAM01"})
        for client, method, expected in [(self.client(), "GET", 401), (student, "POST", 403)]:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                self.json_request(client, "/api/broadcast", method, {"broadcast": "越权"} if method == "POST" else None)
            self.assertEqual(denied.exception.code, expected)
        message = {"broadcast": "请核对数学条件\n<x>不要执行HTML</x>", "broadcastType": "阶段提醒"}
        _, published = self.json_request(teacher, "/api/broadcast", "POST", message)
        student_message = self.json_request(student, "/api/broadcast")[1]
        self.assertEqual(student_message["broadcast"], message["broadcast"])
        self.assertFalse(student_message["broadcastViewed"])
        self.assertEqual(published["seenGroups"], [])
        self.assertEqual(published["unseenGroups"], [1, 2, 3])
        _, viewed = self.json_request(student, "/api/broadcast/ack", "POST", {"broadcastId": published["broadcastId"]})
        self.assertTrue(viewed["broadcastViewed"])
        self.assertEqual(viewed["seenGroups"], [1])
        teacher_message = self.json_request(teacher, "/api/broadcast")[1]
        self.assertEqual(teacher_message["seenGroups"], [1])
        self.assertEqual(teacher_message["unseenGroups"], [2, 3])
        self.json_request(student, "/api/broadcast/ack", "POST", {"broadcastId": published["broadcastId"]})
        self.json_request(teacher, "/api/state", "PUT", {"state": {"broadcast": "旧页面内容"}})
        self.assertEqual(self.json_request(student, "/api/state")[1]["state"]["broadcast"], message["broadcast"])
        with self.assertRaises(urllib.error.HTTPError) as invalid:
            self.json_request(teacher, "/api/broadcast", "POST", {"broadcast": "x" * 2001})
        self.assertEqual(invalid.exception.code, 400)
        self.assertEqual(self.json_request(student, "/api/broadcast")[1]["broadcast"], message["broadcast"])
        self.json_request(teacher, "/api/broadcast", "POST", {"broadcast": ""})
        self.assertEqual(self.json_request(student, "/api/broadcast")[1]["broadcast"], "")
        self.assertEqual(self.json_request(student, "/api/state")[1]["state"]["broadcast"], "")

        app.state_put("broadcast", {"broadcast": "旧广播", "broadcastType": "共性数学澄清"})
        self.assertEqual(self.json_request(student, "/api/broadcast")[1]["broadcastType"], "统一答疑")
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn('href="broadcast.html">统一答疑</a>', script)
        self.assertNotIn("准备共性澄清", script)
        self.assertIn("function commonIssues(s)", script)
        self.assertIn("</div>`+commonIssues(s)", script)
        self.assertNotIn('href="common.html">查看共性问题</a>', script)

    def test_broadcast_frontend_sync_and_send_failure(self):
        subprocess.run(["node", str(ROOT / "tests" / "broadcast_ui.cjs")], cwd=ROOT, check=True)

    def test_help_actions_and_recent_button_have_stable_layout(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('class="help-recent-head"', script)
        self.assertIn('recent-new-conversation', script)
        self.assertIn('class="help-actions"', script)
        self.assertIn("newThread:newConversation", script)
        self.assertIn("history.replaceState(null,'',`help.html?thread=${encodeURIComponent(result.threadId)}`)", script)
        self.assertIn('.help-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));', styles)
        self.assertNotIn('<a class="btn sm mt12" href="help.html?new=1">新对话</a>', script)

    def test_member_roles_require_save_before_lock(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn("btn('保存成员与角色','primary','saveMembers')", script)
        self.assertIn("btn('确认锁定','green','lockMembers'", script)
        self.assertIn("JSON.stringify({action:'save',members:members()})", script)
        self.assertIn("JSON.stringify({action:'submit'})", script)
        self.assertIn("锁定后需要由教师重新开放才能修改", script)
        self.assertIn("lock.disabled=true", script)
        self.assertNotIn("btn('提交并锁定成员'", script)

    def test_peer_explanation_is_removed_and_thoughts_keep_member_labels(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        server = (ROOT / "platform_server.py").read_text(encoding="utf-8")
        contract = (ROOT / "interfaces" / "openapi.yaml").read_text(encoding="utf-8")
        for source in (script, server, contract):
            self.assertNotIn("peerTried", source)
            self.assertNotIn("peer_tried", source)
            self.assertNotIn("同伴解释", source)
            self.assertNotIn("组员解释", source)
        self.assertIn('<label>${esc(m)}</label><div class="task">', script)
        self.assertIn("thought?'个人先想内容':'尚未填写'", script)
        self.assertIn("${esc(thought)}</small>", script)

    def test_student_navigation_uses_consistent_label_lengths(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        for label in ("课堂首页", "项目准备", "任务理解", "方案建模", "协作探究", "成果反思"):
            self.assertEqual(len(label), 4)
            self.assertIn(label, script if label == "课堂首页" else (ROOT / "assets" / "data.js").read_text(encoding="utf-8"))
        self.assertNotIn("方案与建模", (ROOT / "assets" / "data.js").read_text(encoding="utf-8"))
        for label in ("共享工作区", "自由工作区", "成员与角色", "提示与求助", "过程与回看"):
            self.assertEqual(len(label), 5)
            self.assertIn(label, script)
        self.assertIn("数据使用说明", script)
        self.assertIn("返回登录入口", script)
        self.assertIn("head('小组协作与角色'", script)

    def test_teacher_navigation_uses_consistent_titles(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        for label in ("课堂教学总览", "项目支架配置", "课堂阶段控制", "小组动态监测", "教师升级队列", "平台消息广播", "学习成果评价", "课后教学总结"):
            self.assertEqual(len(label), 6)
            self.assertIn(label, script)
        self.assertIn("课堂数据导出", script)
        self.assertIn("运行与数据治理", script)
        self.assertIn("开始轮换", script)
        self.assertNotIn("全班随机轮换一次", script)
        self.assertNotIn("固定产品边界", script)

    def test_stage_completion_requires_real_student_outputs(self):
        state = {"project": "测试项目", "teacherConfig": {"standard": "目标", "finalProduct": "成果"}}
        self.assertEqual(app.stage_task_status(state)[0], [False, False, False, False])
        self.assertFalse(any(app.validated_stage_submissions(state).values()))
        state["members"] = ["甲", "乙"]
        state["membersLocked"] = True
        state["preparationChecks"] = {"goal": True, "materials": True, "rules": True}
        state["stageSubmitted"] = {1: True}
        self.assertTrue(app.validated_stage_submissions(state)[1])

        student = self.client()
        self.json_request(student, "/api/login", "POST", {"role": "student", "classCode": "CLASS2026", "groupId": 1, "groupCode": "TEAM01"})
        current = self.json_request(student, "/api/state")[1]["state"]
        current["stageSubmitted"]["1"] = True
        with self.assertRaises(urllib.error.HTTPError) as incomplete:
            self.json_request(student, "/api/state", "PUT", {"state": current})
        self.assertEqual(incomplete.exception.code, 409)

    def test_stage_pages_use_compact_strip_but_dashboard_keeps_details(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("function stageStrip(compact=false)", script)
        self.assertIn("<div class=\"stage-state\">${label}</div>", script)
        self.assertIn("const title=esc(s.project)", script)
        self.assertIn("+dashboardStatus(s)+projectOverview(s.currentLesson)+", script)
        self.assertIn("+stageStrip(true)+", script)
        self.assertIn(".stage-strip.compact .stage-card", styles)

    def test_binary_evidence_upload_ai_compare_and_legacy_knowledge_removal(self):
        original_group_state = app.state_get("group:2")
        student = self.client()
        status, _ = self.json_request(
            student, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 2, "groupCode": "TEAM02"},
        )
        self.assertEqual(status, 200)
        raw = b"\xff\xd8\xff\xe0" + b"camera-evidence" * 100
        request = urllib.request.Request(
            self.base + "/api/files", data=raw, method="POST",
            headers={"Content-Type": "application/octet-stream", "X-File-Name": urllib.parse.quote("现场证据.jpg")},
        )
        with student.open(request, timeout=8) as response:
            uploaded = json.loads(response.read())
        self.assertEqual(uploaded["file"]["name"], "现场证据.jpg")
        self.assertEqual(uploaded["file"]["size"], len(raw))
        group_state = app.state_get("group:2")
        group_state["evidence"] = [
            {"text": "现场证据", "file": uploaded["file"]},
            {"text": "无附件计算", "file": None},
        ]
        app.state_put("group:2", group_state)
        delete_request = urllib.request.Request(
            self.base + "/api/files/" + uploaded["file"]["id"], method="DELETE"
        )
        with student.open(delete_request, timeout=8) as response:
            self.assertEqual(response.status, 200)
        group_state = app.state_get("group:2")
        self.assertEqual([item["text"] for item in group_state["evidence"]], ["无附件计算"])

        screenshot_request = urllib.request.Request(
            self.base + "/api/files", data=raw, method="POST",
            headers={
                "Content-Type": "image/png",
                "X-File-Name": urllib.parse.quote("模型截图.png"),
                "X-File-Purpose": "math-stage",
            },
        )
        with student.open(screenshot_request, timeout=8) as response:
            screenshot = json.loads(response.read())["file"]
        self.assertEqual(screenshot["uploadedBy"], "student-math-stage")
        group_state = app.state_get("group:2")
        group_state["mathTools"] = {"screenshots": [{"id": screenshot["id"], "name": screenshot["name"]}]}
        app.state_put("group:2", group_state)
        with student.open(self.base + "/api/files/" + screenshot["id"] + "?download=1", timeout=8) as response:
            self.assertTrue(response.headers["Content-Disposition"].startswith("attachment"))
        with student.open(urllib.request.Request(self.base + "/api/files/" + screenshot["id"], method="DELETE"), timeout=8) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(app.state_get("group:2")["mathTools"]["screenshots"], [])

        class_state = app.state_get("class")
        class_state.update({"aiOpen": True, "currentStage": 4})
        app.state_put("class", class_state)
        viewpoints = [
            {"who": "方案A", "claim": "选A", "basis": "实测三次", "risk": "样本较少"},
            {"who": "方案B", "claim": "选B", "basis": "预算计算", "risk": "价格会变"},
        ]
        with mock.patch.object(app, "ark_free_chat", return_value=("先核对样本范围，再比较预算边界。", 10, "ok", "test-model")) as compare:
            status, result = self.json_request(student, "/api/ai/compare", "POST", {"stage": 4, "viewpoints": viewpoints})
        self.assertEqual(status, 200)
        self.assertIn("核对", result["answer"])
        self.assertIn("方案A", compare.call_args.args[2])

        teacher = self.client()
        self.json_request(teacher, "/api/login", "POST", {"role": "teacher", "username": "test_teacher", "password": "StrongTeacher#2026"})
        class_state = app.state_get("class")
        config = class_state.setdefault("teacherConfig", {})
        config["knowledge"] = ["仅有旧记录.pdf"]
        app.state_put("class", class_state)
        status, removed = self.json_request(teacher, "/api/knowledge/remove", "POST", {"name": "仅有旧记录.pdf"})
        self.assertEqual(status, 200)
        self.assertNotIn("仅有旧记录.pdf", removed["state"]["teacherConfig"]["knowledge"])
        app.state_put("group:2", original_group_state)

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
            self.assertNotIn("speed", payload)
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
        self.assertIn("brandLockup(role)", script)
        self.assertIn("role==='student'?'/student/dashboard.html':'/teacher/dashboard.html'", script)
        self.assertNotIn("brand-mark", script)
        self.assertIn('class="auth-brand-link" href="/?current" aria-label="返回首页"', script)
        self.assertIn('class="brand-lockup-image"', script)
        self.assertIn("brandAsset(BRAND.wideLogo)", script)
        self.assertIn(".brand-lockup-image{display:block;width:auto;height:42px;", styles)
        self.assertIn(".auth-brand-lockup{filter:invert(1)}", styles)

    def test_free_ai_math_renderer_handles_bold_latex_and_prompt_requires_delimiters(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn(r"\\(?:boldsymbol|mathbf)\s*\{([^{}]*)\}", script)
        self.assertIn(r"\\(?:d|t)?frac", script)
        self.assertIn(r"\\(?:sin|cos|tan|cot|sec|csc|log|ln|lim|max|min|mod", script)
        self.assertIn("<strong>$1</strong>", script)
        self.assertGreaterEqual((ROOT / "platform_server.py").read_text(encoding="utf-8").count("MATH_FORMAT_RULE"), 3)

    def test_setup_portal_uses_aligned_form_and_enter_submit(self):
        script = (ROOT / "assets" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('<form id="setupForm" class="setup-form">', script)
        self.assertIn("document.querySelector('#setupForm').onsubmit=async event=>", script)
        self.assertIn("document.querySelector('#setupGroupCode').value", script)
        self.assertIn('class="icon-btn" id="copyRecovery"', script)
        self.assertNotIn("document.querySelector('#setupGroupCount')", script)
        self.assertNotIn("document.querySelector('#setupGroupPrefix')", script)
        portal = script[script.index('function renderAccessPortal'):script.index('const pages=')]
        self.assertNotIn('class="field span-2"', portal)
        self.assertIn('.copy-field:focus-within', styles)
        self.assertIn('.auth-card .toolbar{justify-content:flex-end}', styles)
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
        fake_project = mock.MagicMock()
        fake_project.__enter__.return_value = fake_project
        fake_project.read.return_value = json.dumps({"choices": [{"message": {"content": "当前项目是数学项目式学习，下一步先查看本阶段任务。"}}]}).encode()
        with mock.patch.object(app.urllib.request, "urlopen", return_value=fake_project) as upstream:
            status, project_result = self.json_request(
                student, "/api/ai/chat", "POST",
                {"prompt": "项目是什么，接下来要做什么", "newConversation": True},
            )
        self.assertEqual(status, 200)
        self.assertEqual(project_result["source"], "ark")
        self.assertIn("数学项目任务书", json.loads(upstream.call_args.args[0].data)["messages"][0]["content"])
        with mock.patch.dict(os.environ, {"ARK_API_KEY": ""}):
            with self.assertRaises(urllib.error.HTTPError) as context:
                self.json_request(
                    student, "/api/ai/chat", "POST",
                    {"prompt": "请解释如何生成函数图像", "newConversation": True},
                )
        self.assertEqual(context.exception.code, 503)
        self.assertIn("ARK_API_KEY", context.exception.read().decode())

        status, saved = self.json_request(student, "/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(saved["state"]["freeAi"]["activeConversationId"])
        self.assertEqual(len(saved["state"]["freeAi"]["conversations"]), 1)

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
            self.assertNotIn("speed", text_body)
            self.assertEqual(text_body["messages"][0]["role"], "system")
            self.assertIn("数学项目式学习小组协作场景", text_body["messages"][0]["content"])
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
            self.assertEqual(image_result["usage"], 3)
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

        for route in (
            "/teacher/versions.html", "/teacher/research.html", "/expert/overview.html",
            "/student/stage4-tools.html", "/student/stage4-versions.html",
        ):
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
        with self.assertRaises(urllib.error.HTTPError) as unsaved_lock:
            self.json_request(
                student, "/api/group/members", "POST",
                {"action": "submit"},
            )
        self.assertEqual(unsaved_lock.exception.code, 409)

        status, result = self.json_request(
            student, "/api/group/members", "POST",
            {"action": "save", "members": first_members},
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["membersLocked"])
        self.assertEqual(result["members"], first_members)
        self.assertEqual(len(result["roles"]), 5)
        self.assertEqual(result["state"]["members"], first_members)
        self.assertFalse(result["state"]["membersLocked"])
        status, result = self.json_request(
            student, "/api/group/members", "POST",
            {"action": "submit"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["membersLocked"])
        self.assertEqual(len(result["roles"]), 5)
        self.assertEqual(result["state"]["members"], first_members)
        self.assertTrue(result["state"]["membersLocked"])
        status, state_result = self.json_request(student, "/api/state")
        locked_state = state_result["state"]
        locked_state["members"] = first_members + ["越权修改"]
        with self.assertRaises(urllib.error.HTTPError) as direct_change:
            self.json_request(student, "/api/state", "PUT", {"state": locked_state})
        self.assertEqual(direct_change.exception.code, 409)
        locked_state["members"] = first_members
        locked_state["roles"] = locked_state["roles"][-1:] + locked_state["roles"][:-1]
        locked_state["roleRound"] = locked_state.get("roleRound", 1) + 1
        with self.assertRaises(urllib.error.HTTPError) as rotation:
            self.json_request(student, "/api/state", "PUT", {"state": locked_state})
        self.assertEqual(rotation.exception.code, 409)
        with self.assertRaises(urllib.error.HTTPError) as forbidden:
            self.json_request(student, "/api/roles/rotate", "POST", {"rounds": {"3": 1}})
        self.assertEqual(forbidden.exception.code, 403)
        seen = [{role} for role in result["roles"]]
        for round_no in range(1, 5):
            status, rotated = self.json_request(teacher, "/api/roles/rotate", "POST", {"rounds": {"3": round_no}})
            self.assertEqual(status, 200)
            with self.assertRaises(urllib.error.HTTPError) as duplicate:
                self.json_request(teacher, "/api/roles/rotate", "POST", {"rounds": {"3": round_no}})
            self.assertEqual(duplicate.exception.code, 409)
            state = self.json_request(student, "/api/state")[1]["state"]
            self.assertEqual(state["roleRound"], round_no + 1)
            self.assertEqual(len(set(state["roles"])), 5)
            for i, role in enumerate(state["roles"]):
                self.assertNotIn(role, seen[i])
                seen[i].add(role)
        with self.assertRaises(urllib.error.HTTPError) as exhausted:
            self.json_request(teacher, "/api/roles/rotate", "POST", {"rounds": {"3": 5}})
        self.assertEqual(exhausted.exception.code, 400)
        self.assertEqual(self.json_request(student, "/api/state")[1]["state"]["roleRound"], 5)
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
            {"action": "save", "members": second_members},
        )
        self.assertEqual(status, 200)
        self.assertFalse(result["membersLocked"])
        self.assertEqual(result["members"], second_members)
        with self.assertRaises(urllib.error.HTTPError) as changed_after_save:
            self.json_request(
                student, "/api/group/members", "POST",
                {"action": "submit", "members": second_members + ["庚"]},
            )
        self.assertEqual(changed_after_save.exception.code, 409)
        status, result = self.json_request(
            student, "/api/group/members", "POST",
            {"action": "submit"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["membersLocked"])
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
