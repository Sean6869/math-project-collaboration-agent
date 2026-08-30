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
        with client.open(request, timeout=3) as response:
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


if __name__ == "__main__":
    unittest.main()
