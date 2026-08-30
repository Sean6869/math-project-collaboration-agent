import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
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
        self.assertEqual(groups[1]["help"], 1)
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

    def test_password_and_hint_output_guards(self):
        encoded = app.password_hash("secret")
        self.assertTrue(app.password_ok("secret", encoded))
        self.assertFalse(app.password_ok("wrong", encoded))
        self.assertIsNone(app.validate_hint("最终答案是 42"))
        self.assertEqual(app.validate_hint("先检查已知条件与目标量是否对应。"), "先检查已知条件与目标量是否对应。")

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


if __name__ == "__main__":
    unittest.main()
