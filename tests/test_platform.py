import http.cookiejar
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
            "groupCodePrefix": "TEAM", "groupCount": 3,
        }).encode()
        request = urllib.request.Request(
            cls.base + "/api/setup", data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 201:
                raise AssertionError(f"setup failed with status {response.status}")

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
        self.assertEqual(hint["reply"], "先画出已知关系。")

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
            ("诊断：条件已经整理，可以开始拆解。\n支架：先建立外框面积，再单独表示小路面积，最后比较两者关系。\n下一步：定义一个表示宽度的变量并注明范围。", 90, "ok"),
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
            status, advanced = self.json_request(
                student, "/api/hints", "POST",
                {"action": "advance", "attempt": "根据提示重新区分了总面积和种植面积"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(advanced["currentLevel"], 2)
            status, result = self.json_request(
                student, "/api/hints", "POST",
                {"action": "ask", "done": "定义x表示小路宽度", "type": "变量关系", "issue": "怎样组织面积关系"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["level"], "L2")
            self.assertEqual(result["hintUsage"], {"L1": 2, "L2": 1, "L3": 0, "L4": 0})
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
        self.assertIsNone(app.validate_hint("最终答案是 42"))
        self.assertEqual(app.validate_hint("先检查已知条件与目标量是否对应。"), "先检查已知条件与目标量是否对应。")

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
        self.assertEqual(result["addedCodes"][-1], {"groupNo": 5, "code": "NEWG05"})
        fifth = self.client()
        status, login = self.json_request(
            fifth, "/api/login", "POST",
            {"role": "student", "classCode": "CLASS2026", "groupId": 5, "groupCode": "NEWG05"},
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
