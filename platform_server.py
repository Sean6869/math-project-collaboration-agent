"""数学项目协作智能体：无第三方运行依赖的生产服务。"""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(ROOT / ".env")
DATA_DIR = Path(os.environ.get("MPT_DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "platform.db"
HOST = os.environ.get("MPT_HOST", "127.0.0.1")
PORT = int(os.environ.get("MPT_PORT", "8000"))
SESSION_SECONDS = int(os.environ.get("MPT_SESSION_SECONDS", "28800"))
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = int(os.environ.get("MPT_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
DEEPSEEK_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
COOKIE_NAME = "mpt_session"
LOGIN_FAILURES: dict[str, list[float]] = {}
LOGIN_LOCK = threading.Lock()
SETUP_TOKEN_DISPLAY = ""
SETUP_TOKEN_FILE = DATA_DIR / "setup-token.txt"

CLASS_KEYS = {
    "project", "className", "groupCount", "currentStage", "stagePaused",
    "aiOpen", "projectType", "projectHours", "hintQuota",
    "maxHintLevel", "broadcast", "teacherConfig", "groups", "queue", "common",
    "evaluations", "audit",
}
GROUP_KEYS = {
    "groupId", "roles", "members", "roleRound", "stageSubmitted", "stageTimes",
    "workspace", "versions", "evidence", "viewpoints", "firstThoughts", "helpHistory",
    "debate", "product", "hintUsed", "events", "questionTree", "graphConfig", "checklist",
}
ALLOWED_UPLOADS = {
    ".pdf", ".docx", ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".webp",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def database() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=20, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}:{digest.hex()}"


def password_ok(password: str, encoded: str) -> bool:
    try:
        salt_hex, digest_hex = encoded.split(":", 1)
        actual = password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
        return hmac.compare_digest(actual, digest_hex)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_code(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()[:32]


def is_initialized() -> bool:
    with database() as db:
        teacher = db.execute("SELECT 1 FROM teachers LIMIT 1").fetchone()
        class_code = db.execute("SELECT 1 FROM settings WHERE key='class_code_hash'").fetchone()
        groups = db.execute("SELECT COUNT(*) AS count FROM groups").fetchone()["count"]
    return bool(teacher and class_code and groups)


def group_count() -> int:
    with database() as db:
        row = db.execute("SELECT value FROM settings WHERE key='group_count'").fetchone()
    return int(row["value"]) if row else 0


def initialize_platform(username: str, teacher_password: str, recovery_code: str, class_code: str, group_prefix: str, count: int) -> None:
    username = username.strip()
    recovery_code = recovery_code.strip()
    class_code = clean_code(class_code)
    group_prefix = clean_code(group_prefix)
    if len(username) < 3 or len(username) > 40:
        raise ValueError("教师账号长度应为3—40个字符")
    if len(teacher_password) < 12:
        raise ValueError("教师密码至少需要12个字符")
    if len(recovery_code) < 16:
        raise ValueError("管理员恢复码至少需要16个字符")
    if len(class_code) < 6:
        raise ValueError("班级访问码至少需要6个字符")
    if len(group_prefix) < 4:
        raise ValueError("小组访问码前缀至少需要4个字符")
    if not 1 <= count <= 20:
        raise ValueError("小组数量应为1—20组")
    with database() as db:
        if db.execute("SELECT 1 FROM teachers LIMIT 1").fetchone():
            raise ValueError("平台已经完成初始化")
        db.execute(
            "INSERT INTO teachers(id,username,password_hash,created_at) VALUES(1,?,?,?)",
            (username, password_hash(teacher_password), utc_now()),
        )
        db.execute("INSERT INTO settings(key,value) VALUES('class_code_hash',?)", (password_hash(class_code),))
        db.execute("INSERT INTO settings(key,value) VALUES('admin_recovery_hash',?)", (password_hash(recovery_code),))
        db.execute("INSERT INTO settings(key,value) VALUES('group_count',?)", (str(count),))
        db.execute("DELETE FROM settings WHERE key='setup_token_hash'")
        for number in range(1, count + 1):
            db.execute(
                "INSERT INTO groups(group_no,access_hash) VALUES(?,?)",
                (number, password_hash(f"{group_prefix}{number:02d}")),
            )
    summaries = [
        {"id": number, "stage": 1, "status": "准备中", "help": 0, "maxHint": 0,
         "teacher": False, "latest": "尚未保存", "issue": "", "progress": 0}
        for number in range(1, count + 1)
    ]
    state_put("class", {"groupCount": count, "groups": summaries})
    if SETUP_TOKEN_FILE.exists():
        SETUP_TOKEN_FILE.unlink()


def init_database() -> None:
    global SETUP_TOKEN_DISPLAY
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS teachers (
              id INTEGER PRIMARY KEY CHECK(id=1), username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS groups (group_no INTEGER PRIMARY KEY, access_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, role TEXT NOT NULL, group_no INTEGER,
              expires_at INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS states (
              scope TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
              group_no INTEGER, event_type TEXT NOT NULL, stage INTEGER, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
              id TEXT PRIMARY KEY, original_name TEXT NOT NULL, stored_name TEXT NOT NULL,
              mime TEXT NOT NULL, size INTEGER NOT NULL, extracted_text TEXT NOT NULL,
              uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_calls (
              id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, group_no INTEGER NOT NULL,
              level TEXT NOT NULL, model TEXT NOT NULL, latency_ms INTEGER NOT NULL,
              status TEXT NOT NULL, detail TEXT NOT NULL
            );
            """
        )
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    env = os.environ.get("MPT_ENV", "development").lower()
    supplied = {
        "username": os.environ.get("MPT_TEACHER_USERNAME", ""),
        "password": os.environ.get("MPT_TEACHER_PASSWORD", ""),
        "recovery_code": os.environ.get("MPT_ADMIN_RECOVERY_CODE", ""),
        "class_code": os.environ.get("MPT_CLASS_CODE", ""),
        "group_prefix": os.environ.get("MPT_GROUP_CODE_PREFIX", ""),
        "group_count": os.environ.get("MPT_GROUP_COUNT", ""),
    }
    if not is_initialized() and all(supplied.values()):
        initialize_platform(
            supplied["username"], supplied["password"], supplied["recovery_code"], supplied["class_code"],
            supplied["group_prefix"], int(supplied["group_count"]),
        )
    elif not is_initialized():
        configured_token = clean_code(os.environ.get("MPT_SETUP_TOKEN", ""))
        if env == "production" and len(configured_token) < 12:
            raise RuntimeError("生产环境首次启动必须设置完整初始化环境变量，或设置至少12位的 MPT_SETUP_TOKEN")
        with database() as db:
            row = db.execute("SELECT 1 FROM settings WHERE key='setup_token_hash'").fetchone()
            if not row:
                token = configured_token or secrets.token_urlsafe(12).upper()
                db.execute("INSERT INTO settings(key,value) VALUES('setup_token_hash',?)", (password_hash(token),))
                if not configured_token:
                    SETUP_TOKEN_FILE.write_text(token, encoding="utf-8")
                    SETUP_TOKEN_DISPLAY = token
            elif env != "production" and SETUP_TOKEN_FILE.exists():
                SETUP_TOKEN_DISPLAY = SETUP_TOKEN_FILE.read_text(encoding="utf-8").strip()


def state_get(scope: str) -> dict:
    with database() as db:
        row = db.execute("SELECT data FROM states WHERE scope=?", (scope,)).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row["data"])
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def state_put(scope: str, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with database() as db:
        db.execute(
            "INSERT INTO states(scope,data,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(scope) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
            (scope, payload, utc_now()),
        )


def merged_state(group_no: int) -> dict:
    value = state_get("class")
    value.update(state_get(f"group:{group_no}"))
    value["groupId"] = group_no
    with database() as db:
        calls = db.execute(
            "SELECT ts,group_no,level,model,latency_ms,status FROM ai_calls ORDER BY id DESC LIMIT 50"
        ).fetchall()
    value["aiCalls"] = [dict(row) for row in calls]
    return value


def record_event(actor: str, group_no: int | None, event_type: str, stage: int | None, data: str) -> None:
    with database() as db:
        db.execute(
            "INSERT INTO events(ts,actor,group_no,event_type,stage,data) VALUES(?,?,?,?,?,?)",
            (utc_now(), actor, group_no, event_type[:64], stage, data[:2000]),
        )


def session_from_cookie(raw_cookie: str | None) -> dict | None:
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie or "")
        token = cookie[COOKIE_NAME].value
    except (KeyError, AttributeError, TypeError):
        return None
    with database() as db:
        row = db.execute(
            "SELECT role,group_no,expires_at FROM sessions WHERE token_hash=?", (token_hash(token),)
        ).fetchone()
    if not row or row["expires_at"] < int(time.time()):
        return None
    return {"role": row["role"], "groupId": row["group_no"]}


def extract_document(name: str, raw: bytes) -> str:
    suffix = Path(name).suffix.lower()
    try:
        if suffix in {".txt", ".md", ".csv"}:
            return raw.decode("utf-8", errors="replace")[:120_000]
        if suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                xml = archive.read("word/document.xml")
            root = ElementTree.fromstring(xml)
            return "\n".join("".join(node.itertext()) for node in root.iter() if node.tag.endswith("}p"))[:120_000]
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return ""
    return ""


def static_hint(config: dict, level_no: int) -> str:
    defaults = [
        "先标出题目中的已知量、未知量和限制条件，再指出当前缺少哪一个关系。",
        "把卡点拆成一个可检验的子问题，并选择表格、图形或式子中的一种表示方式。",
        "只完成下一步核验：代入一个边界值或反例，检查当前关系是否仍满足题目条件。",
    ]
    hints = config.get("staticHints") if isinstance(config, dict) else None
    source = hints if isinstance(hints, list) and hints else defaults
    return str(source[min(level_no - 1, len(source) - 1)])[:260]


def validate_hint(text: str) -> str | None:
    clean = re.sub(r"\s+", " ", text or "").strip()
    blocked = ("最终答案", "答案是", "完整证明", "完整模型", "故最大值为", "直接得到最优")
    if not clean or len(clean) > 360 or any(item in clean for item in blocked):
        return None
    return clean


def deepseek_hint(state: dict, level: str, done: str, issue_type: str, issue: str) -> tuple[str | None, int, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None, 0, "not_configured"
    config = state.get("teacherConfig", {})
    knowledge_names = config.get("knowledge", []) if isinstance(config, dict) else []
    with database() as db:
        texts = db.execute("SELECT original_name,extracted_text FROM files WHERE extracted_text<>'' ORDER BY uploaded_at DESC LIMIT 5").fetchall()
    knowledge = "\n".join(f"[{row['original_name']}] {row['extracted_text'][:4000]}" for row in texts)
    system = (
        "你是初中数学项目式学习的小组支架。你只能给当前层级的最小充分提示，不能给完整模型、完整证明、最终数值答案或代替学生决策。"
        "L1只定位目标、条件或检查项；L2只给子问题、变量关系或表征结构；L3只给局部例子或一步核验。"
        "回答使用简体中文，不超过180字，最多提出两个问题。材料不足、存在冲突或需要高阶判断时明确写‘请教师核验’。"
    )
    user = {
        "项目": state.get("project", ""), "数学主题": config.get("mathTopic", ""),
        "课程目标": config.get("standard", ""), "当前阶段": state.get("currentStage", 1),
        "提示层级": level, "已完成": done, "卡点类型": issue_type, "具体卡点": issue,
        "共享工作区": state.get("workspace", {}), "已审核材料": knowledge_names, "材料摘录": knowledge,
    }
    payload = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
        "thinking": {"type": "disabled"}, "stream": False, "temperature": 0.2, "max_tokens": 240,
    }, ensure_ascii=False).encode()
    request = urllib.request.Request(
        DEEPSEEK_URL, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "MPT-Platform/1"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = json.loads(response.read())
        latency = round((time.perf_counter() - started) * 1000)
        return validate_hint(body["choices"][0]["message"]["content"]), latency, "ok"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        latency = round((time.perf_counter() - started) * 1000)
        return None, latency, type(exc).__name__


class Handler(SimpleHTTPRequestHandler):
    server_version = "MPT"

    def translate_path(self, path: str) -> str:
        clean = urlparse(path).path.lstrip("/")
        target = (ROOT / clean).resolve()
        if target != ROOT and ROOT not in target.parents:
            return str(ROOT / "__not_found__")
        return str(target)

    def end_headers(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/") and (path in ("/", "/index.html") or Path(path).suffix in {".html", ".js", ".css"}):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write(f"[{utc_now()}] {self.address_string()} {fmt % args}\n")

    def json_response(self, payload: object, status: int = 200, cookie: str | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self, limit: int = MAX_JSON_BYTES) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length <= 0 or length > limit:
            raise ValueError("请求为空或超过大小限制")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("JSON格式无效") from exc
        if not isinstance(value, dict):
            raise ValueError("请求必须是JSON对象")
        return value

    def current_session(self) -> dict | None:
        return session_from_cookie(self.headers.get("Cookie"))

    def require_session(self, role: str | None = None) -> dict | None:
        session = self.current_session()
        if not session:
            self.json_response({"error": "请先登录"}, HTTPStatus.UNAUTHORIZED)
            return None
        if role and session["role"] != role:
            self.json_response({"error": "没有执行此操作的权限"}, HTTPStatus.FORBIDDEN)
            return None
        return session

    def check_origin(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and urlparse(origin).netloc != host:
            self.json_response({"error": "来源校验失败"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self) -> None:
        route = urlparse(self.path)
        if route.path == "/index.html":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/?current")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Clear-Site-Data", '"cache"')
            self.end_headers()
            return
        if route.path == "/api/health":
            self.json_response({"status": "ok", "database": DB_PATH.exists(), "initialized": is_initialized(), "aiConfigured": bool(os.environ.get("DEEPSEEK_API_KEY"))})
            return
        if route.path == "/api/system":
            self.json_response({"initialized": is_initialized(), "groupCount": group_count()})
            return
        if route.path == "/api/session":
            session = self.current_session()
            self.json_response({"initialized": is_initialized(), "groupCount": group_count(), "authenticated": bool(session), **(session or {})})
            return
        if route.path == "/api/runtime":
            if not self.require_session("teacher"):
                return
            with database() as db:
                quick_check = db.execute("PRAGMA quick_check").fetchone()[0]
                counts = {
                    table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("groups", "states", "events", "files", "ai_calls", "sessions")
                }
                failed_calls = db.execute("SELECT COUNT(*) FROM ai_calls WHERE status<>'returned'").fetchone()[0]
                event_range = db.execute("SELECT MIN(ts),MAX(ts),COUNT(DISTINCT group_no) FROM events").fetchone()
                recent = db.execute(
                    "SELECT ts,actor,group_no,event_type,stage,data FROM events ORDER BY id DESC LIMIT 30"
                ).fetchall()
            self.json_response({
                "environment": os.environ.get("MPT_ENV", "development").lower(),
                "database": {"healthy": quick_check == "ok", "sizeBytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0},
                "security": {"secureCookie": os.environ.get("MPT_COOKIE_SECURE", "0") == "1", "sessionSeconds": SESSION_SECONDS},
                "ai": {
                    "configured": bool(os.environ.get("DEEPSEEK_API_KEY")),
                    "model": DEEPSEEK_MODEL,
                    "calls": counts["ai_calls"],
                    "failedCalls": failed_calls,
                },
                "data": {
                    **counts,
                    "groupsWithEvents": event_range[2],
                    "firstEventAt": event_range[0],
                    "lastEventAt": event_range[1],
                },
                "recentEvents": [dict(row) for row in recent],
            })
            return
        if route.path == "/api/admin/access":
            if not self.require_session("teacher"):
                return
            with database() as db:
                teacher = db.execute("SELECT username FROM teachers WHERE id=1").fetchone()
                recovery = db.execute("SELECT 1 FROM settings WHERE key='admin_recovery_hash'").fetchone()
            self.json_response({
                "username": teacher["username"],
                "groupCount": group_count(),
                "recoveryConfigured": bool(recovery),
            })
            return
        if route.path == "/api/state":
            session = self.require_session()
            if not session:
                return
            query = parse_qs(route.query)
            count = group_count()
            group = session.get("groupId") or int(query.get("group", ["1"])[0])
            group = max(1, min(count, int(group)))
            self.json_response({"state": merged_state(group), "role": session["role"]})
            return
        if route.path == "/api/files":
            if not self.require_session():
                return
            with database() as db:
                rows = db.execute("SELECT id,original_name,mime,size,uploaded_at,uploaded_by,extracted_text<>'' AS indexed FROM files ORDER BY uploaded_at DESC").fetchall()
            self.json_response({"files": [dict(row) for row in rows]})
            return
        if route.path == "/api/export":
            if not self.require_session("teacher"):
                return
            self.export_events(parse_qs(route.query).get("format", ["csv"])[0])
            return
        if route.path.startswith("/api/"):
            self.json_response({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if not self.check_origin():
            return
        route = urlparse(self.path).path
        try:
            if route == "/api/setup":
                self.initialize_from_request(self.read_json())
            elif route == "/api/login":
                self.login(self.read_json())
            elif route == "/api/admin/recover":
                self.recover_teacher(self.read_json())
            elif route == "/api/admin/access":
                if self.require_session("teacher"):
                    self.update_access(self.read_json())
            elif route == "/api/logout":
                self.logout()
            elif route == "/api/hints":
                session = self.require_session("student")
                if session:
                    self.hint(session, self.read_json())
            elif route == "/api/files":
                session = self.require_session("teacher")
                if session:
                    self.upload(session, self.read_json(MAX_UPLOAD_BYTES * 2))
            elif route == "/api/escalations":
                session = self.require_session("student")
                if session:
                    body = self.read_json()
                    state = merged_state(int(session["groupId"]))
                    self.escalate(int(session["groupId"]), state, "学生主动求助", str(body.get("reason", "需要教师介入"))[:300])
                    self.json_response({"ok": True})
            elif route == "/api/state/reset":
                if self.require_session("teacher"):
                    with database() as db:
                        db.execute("DELETE FROM states")
                        db.execute("DELETE FROM events")
                        db.execute("DELETE FROM ai_calls")
                    self.json_response({"ok": True})
            else:
                self.json_response({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_PUT(self) -> None:
        if not self.check_origin():
            return
        if urlparse(self.path).path != "/api/state":
            self.json_response({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        session = self.require_session()
        if not session:
            return
        try:
            incoming = self.read_json().get("state")
        except ValueError as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(incoming, dict):
            self.json_response({"error": "state必须是对象"}, HTTPStatus.BAD_REQUEST)
            return
        count = group_count()
        group = int(session.get("groupId") or incoming.get("groupId") or 1)
        group = max(1, min(count, group))
        group_state = state_get(f"group:{group}")
        group_state.update({key: incoming[key] for key in GROUP_KEYS if key in incoming})
        group_state["groupId"] = group
        state_put(f"group:{group}", group_state)
        if session["role"] == "teacher":
            class_state = state_get("class")
            class_state.update({key: incoming[key] for key in CLASS_KEYS - {"groups", "queue", "common"} if key in incoming})
            incoming_groups = incoming.get("groups")
            if not class_state.get("groups") and isinstance(incoming_groups, list):
                class_state["groups"] = incoming_groups
            elif isinstance(incoming_groups, list):
                teacher_flags = {int(item.get("id", 0)): bool(item.get("teacher")) for item in incoming_groups if isinstance(item, dict)}
                for item in class_state.get("groups", []):
                    if int(item.get("id", 0)) in teacher_flags:
                        item["teacher"] = teacher_flags[int(item["id"])]
            incoming_queue = incoming.get("queue")
            if isinstance(incoming_queue, list):
                existing = {str(item.get("id")): item for item in class_state.get("queue", []) if isinstance(item, dict)}
                for item in incoming_queue:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("id"))
                    if key in existing:
                        existing[key]["resolved"] = bool(item.get("resolved"))
                    else:
                        existing[key] = item
                class_state["queue"] = list(existing.values())[-100:]
            state_put("class", class_state)
        self.sync_group_summary(group, group_state)
        record_event(session["role"], group, "state_save", incoming.get("currentStage"), "workspace")
        self.json_response({"ok": True, "updatedAt": utc_now()})

    def initialize_from_request(self, body: dict) -> None:
        if is_initialized():
            self.json_response({"error": "平台已经完成初始化"}, HTTPStatus.CONFLICT)
            return
        supplied_token = clean_code(str(body.get("setupToken", "")))
        with database() as db:
            row = db.execute("SELECT value FROM settings WHERE key='setup_token_hash'").fetchone()
        if not row or not password_ok(supplied_token, row["value"]):
            time.sleep(0.35)
            self.json_response({"error": "教师注册码无效"}, HTTPStatus.FORBIDDEN)
            return
        try:
            count = int(body.get("groupCount"))
        except (TypeError, ValueError) as exc:
            raise ValueError("请填写有效的小组数量") from exc
        initialize_platform(
            str(body.get("username", "")), str(body.get("password", "")), str(body.get("recoveryCode", "")),
            str(body.get("classCode", "")), str(body.get("groupCodePrefix", "")), count,
        )
        record_event("system", None, "platform_initialized", None, f"groups={count}")
        self.json_response({"ok": True, "groupCount": count}, HTTPStatus.CREATED)

    def login(self, body: dict) -> None:
        if not is_initialized():
            self.json_response({"error": "平台尚未初始化"}, HTTPStatus.CONFLICT)
            return
        client = self.client_address[0]
        with LOGIN_LOCK:
            recent = [stamp for stamp in LOGIN_FAILURES.get(client, []) if time.time() - stamp < 600]
            LOGIN_FAILURES[client] = recent
        if len(recent) >= 5:
            self.json_response({"error": "登录失败次数过多，请10分钟后重试"}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        role = body.get("role")
        group_no = None
        with database() as db:
            if role == "teacher":
                row = db.execute("SELECT username,password_hash FROM teachers WHERE id=1").fetchone()
                valid = row and hmac.compare_digest(str(body.get("username", "")), row["username"]) and password_ok(str(body.get("password", "")), row["password_hash"])
            elif role == "student":
                try:
                    group_no = int(body.get("groupId"))
                except (TypeError, ValueError):
                    group_no = 0
                class_hash = db.execute("SELECT value FROM settings WHERE key='class_code_hash'").fetchone()
                group_row = db.execute("SELECT access_hash FROM groups WHERE group_no=?", (group_no,)).fetchone()
                valid = bool(class_hash and group_row and password_ok(clean_code(str(body.get("classCode", ""))), class_hash["value"]) and password_ok(clean_code(str(body.get("groupCode", ""))), group_row["access_hash"]))
            else:
                valid = False
            if not valid:
                with LOGIN_LOCK:
                    LOGIN_FAILURES.setdefault(client, []).append(time.time())
                time.sleep(0.35)
                self.json_response({"error": "登录信息不正确"}, HTTPStatus.UNAUTHORIZED)
                return
            token = secrets.token_urlsafe(32)
            expires = int(time.time()) + SESSION_SECONDS
            db.execute("INSERT INTO sessions(token_hash,role,group_no,expires_at,created_at) VALUES(?,?,?,?,?)", (token_hash(token), role, group_no, expires, utc_now()))
        with LOGIN_LOCK:
            LOGIN_FAILURES.pop(client, None)
        secure = "; Secure" if os.environ.get("MPT_COOKIE_SECURE", "0") == "1" else ""
        cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_SECONDS}{secure}"
        record_event(role, group_no, "login", None, "")
        self.json_response({"ok": True, "role": role, "groupId": group_no}, cookie=cookie)

    def recover_teacher(self, body: dict) -> None:
        if not is_initialized():
            self.json_response({"error": "平台尚未初始化"}, HTTPStatus.CONFLICT)
            return
        client = self.client_address[0]
        with LOGIN_LOCK:
            recent = [stamp for stamp in LOGIN_FAILURES.get(client, []) if time.time() - stamp < 600]
            LOGIN_FAILURES[client] = recent
        if len(recent) >= 5:
            self.json_response({"error": "恢复失败次数过多，请10分钟后重试"}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        username = str(body.get("username", "")).strip()
        recovery_code = str(body.get("recoveryCode", "")).strip()
        new_password = str(body.get("newPassword", ""))
        if len(new_password) < 12:
            raise ValueError("新教师密码至少需要12个字符")
        with database() as db:
            teacher = db.execute("SELECT username FROM teachers WHERE id=1").fetchone()
            recovery = db.execute("SELECT value FROM settings WHERE key='admin_recovery_hash'").fetchone()
            valid = bool(teacher and recovery and hmac.compare_digest(username, teacher["username"]) and password_ok(recovery_code, recovery["value"]))
            if not valid:
                with LOGIN_LOCK:
                    LOGIN_FAILURES.setdefault(client, []).append(time.time())
                time.sleep(0.35)
                self.json_response({"error": "管理员账号或恢复码不正确"}, HTTPStatus.UNAUTHORIZED)
                return
            db.execute("UPDATE teachers SET password_hash=? WHERE id=1", (password_hash(new_password),))
            db.execute("DELETE FROM sessions")
        with LOGIN_LOCK:
            LOGIN_FAILURES.pop(client, None)
        record_event("system", None, "teacher_password_recovered", None, username)
        self.json_response({"ok": True}, cookie=f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")

    def update_access(self, body: dict) -> None:
        action = str(body.get("action", ""))
        current_password = str(body.get("currentPassword", ""))
        with database() as db:
            teacher = db.execute("SELECT username,password_hash FROM teachers WHERE id=1").fetchone()
            if not teacher or not password_ok(current_password, teacher["password_hash"]):
                time.sleep(0.35)
                self.json_response({"error": "当前教师密码不正确"}, HTTPStatus.UNAUTHORIZED)
                return
            logout_role = None
            detail = action
            if action == "teacher":
                username = str(body.get("username", "")).strip()
                new_password = str(body.get("newPassword", ""))
                if len(username) < 3 or len(username) > 40:
                    raise ValueError("教师账号长度应为3—40个字符")
                if new_password and len(new_password) < 12:
                    raise ValueError("新教师密码至少需要12个字符")
                if username == teacher["username"] and not new_password:
                    raise ValueError("账号和密码均未修改")
                db.execute(
                    "UPDATE teachers SET username=?,password_hash=? WHERE id=1",
                    (username, password_hash(new_password) if new_password else teacher["password_hash"]),
                )
                db.execute("DELETE FROM sessions WHERE role='teacher'")
                logout_role = "teacher"
                detail = username
            elif action == "class_code":
                code = clean_code(str(body.get("classCode", "")))
                if len(code) < 6:
                    raise ValueError("班级访问码至少需要6个字符")
                db.execute("UPDATE settings SET value=? WHERE key='class_code_hash'", (password_hash(code),))
                db.execute("DELETE FROM sessions WHERE role='student'")
                logout_role = "student"
            elif action == "group_code":
                try:
                    group = int(body.get("groupNo"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("请选择有效小组") from exc
                code = clean_code(str(body.get("groupCode", "")))
                if len(code) < 6:
                    raise ValueError("小组访问码至少需要6个字符")
                updated = db.execute("UPDATE groups SET access_hash=? WHERE group_no=?", (password_hash(code), group))
                if updated.rowcount != 1:
                    raise ValueError("小组不存在")
                db.execute("DELETE FROM sessions WHERE role='student' AND group_no=?", (group,))
                logout_role = "student"
                detail = f"group={group}"
            elif action == "all_group_codes":
                prefix = clean_code(str(body.get("groupPrefix", "")))
                if len(prefix) < 4:
                    raise ValueError("小组访问码前缀至少需要4个字符")
                count = group_count()
                for group in range(1, count + 1):
                    db.execute("UPDATE groups SET access_hash=? WHERE group_no=?", (password_hash(f"{prefix}{group:02d}"), group))
                db.execute("DELETE FROM sessions WHERE role='student'")
                logout_role = "student"
                detail = f"groups={count}"
            elif action == "recovery_code":
                recovery_code = str(body.get("recoveryCode", "")).strip()
                if len(recovery_code) < 16:
                    raise ValueError("管理员恢复码至少需要16个字符")
                db.execute(
                    "INSERT INTO settings(key,value) VALUES('admin_recovery_hash',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (password_hash(recovery_code),),
                )
            else:
                raise ValueError("不支持的管理员操作")
        record_event("teacher", None, f"admin_{action}", None, detail)
        self.json_response({"ok": True, "logoutRole": logout_role})

    def logout(self) -> None:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        if COOKIE_NAME in cookie:
            with database() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(cookie[COOKIE_NAME].value),))
        self.json_response({"ok": True}, cookie=f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")

    def hint(self, session: dict, body: dict) -> None:
        group = int(session["groupId"])
        state = merged_state(group)
        if not state.get("aiOpen", True):
            self.json_response({"error": "AI已由教师暂停"}, HTTPStatus.CONFLICT)
            return
        done = str(body.get("done", "")).strip()[:1600]
        issue = str(body.get("issue", "")).strip()[:1200]
        issue_type = str(body.get("type", "其他")).strip()[:80]
        if not done or not issue:
            raise ValueError("请先填写已有进展和具体卡点")
        used = int(state.get("hintUsed", 0))
        quota = int(state.get("hintQuota", 3))
        maximum = int(state.get("maxHintLevel", 3))
        if used >= quota:
            self.escalate(group, state, "提示额度已用完", issue)
            self.json_response({"escalated": True, "message": "提示额度已用完，已转教师"})
            return
        level_no = min(used + 1, maximum)
        level = f"L{level_no}"
        reply, latency, status = deepseek_hint(state, level, done, issue_type, issue)
        if not reply:
            reply = static_hint(state.get("teacherConfig", {}), level_no)
            result = "fallback" if status != "ok" else "rejected"
        else:
            result = "returned"
        with database() as db:
            db.execute("INSERT INTO ai_calls(ts,group_no,level,model,latency_ms,status,detail) VALUES(?,?,?,?,?,?,?)", (utc_now(), group, level, DEEPSEEK_MODEL, latency, result, status[:200]))
        group_state = state_get(f"group:{group}")
        history = group_state.get("helpHistory", [])
        if not isinstance(history, list):
            history = []
        history.append({"time": datetime.now().strftime("%H:%M"), "level": level if result == "returned" else "静态支架", "type": issue_type, "prompt": issue, "reply": reply})
        group_state["helpHistory"] = history[-50:]
        group_state["hintUsed"] = used + 1
        state_put(f"group:{group}", group_state)
        self.sync_group_summary(group, group_state, issue_type)
        record_event("student", group, "hint_return" if result == "returned" else "fallback_hint", state.get("currentStage"), f"{level}|{issue_type}")
        self.json_response({"reply": reply, "level": level, "source": "deepseek" if result == "returned" else "teacher_fallback", "hintUsed": used + 1, "latencyMs": latency})

    def escalate(self, group: int, state: dict, reason: str, issue: str) -> None:
        class_state = state_get("class")
        queue = class_state.get("queue", [])
        if not isinstance(queue, list):
            queue = []
        queue.append({"id": f"Q-{int(time.time())}", "group": group, "type": "教师介入", "reason": f"{reason}：{issue}"[:300], "priority": "高", "time": datetime.now().strftime("%H:%M"), "resolved": False})
        class_state["queue"] = queue[-100:]
        state_put("class", class_state)
        record_event("system", group, "teacher_escalate", state.get("currentStage"), reason)

    def sync_group_summary(self, group: int, group_state: dict, issue: str = "") -> None:
        class_state = state_get("class")
        groups = class_state.get("groups", [])
        if not isinstance(groups, list):
            groups = []
        count = group_count()
        while len(groups) < count:
            number = len(groups) + 1
            groups.append({"id": number, "stage": 1, "status": "准备中", "help": 0, "maxHint": 0, "teacher": False, "latest": "尚未保存", "issue": "", "progress": 0})
        item = groups[group - 1]
        versions = group_state.get("versions")
        latest = versions[-1].get("note", "工作区已保存") if isinstance(versions, list) and versions else "工作区已保存"
        submitted = group_state.get("stageSubmitted", {})
        progress = round(sum(1 for value in submitted.values() if value) / 5 * 100) if isinstance(submitted, dict) else 0
        item.update({
            "id": group, "stage": int(group_state.get("currentStage", class_state.get("currentStage", 1))),
            "help": int(group_state.get("hintUsed", 0)), "maxHint": int(group_state.get("hintUsed", 0)),
            "latest": latest, "status": "等待教师" if item.get("teacher") else "持续推进", "progress": progress,
        })
        if issue:
            item["issue"] = issue[:120]
        class_state["groups"] = groups
        topics: dict[str, list[int]] = {}
        for summary in groups:
            if summary.get("issue"):
                topics.setdefault(str(summary["issue"]), []).append(int(summary["id"]))
        class_state["common"] = [
            {"id": f"C-{index:02d}", "title": title, "groups": numbers,
             "note": "多个小组在提示请求中选择了相同卡点类型。", "action": "请教师结合各组原始记录判断是否需要共性澄清"}
            for index, (title, numbers) in enumerate(topics.items(), 1) if len(numbers) >= 2
        ]
        state_put("class", class_state)

    def upload(self, session: dict, body: dict) -> None:
        name = Path(str(body.get("name", ""))).name[:180]
        suffix = Path(name).suffix.lower()
        if not name or suffix not in ALLOWED_UPLOADS:
            raise ValueError("不支持此文件类型")
        try:
            raw = base64.b64decode(str(body.get("content", "")), validate=True)
        except ValueError as exc:
            raise ValueError("文件内容无效") from exc
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError("文件为空或超过上传限制")
        file_id = secrets.token_hex(12)
        stored = f"{file_id}{suffix}"
        (UPLOAD_DIR / stored).write_bytes(raw)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        extracted = extract_document(name, raw)
        with database() as db:
            db.execute("INSERT INTO files(id,original_name,stored_name,mime,size,extracted_text,uploaded_at,uploaded_by) VALUES(?,?,?,?,?,?,?,?)", (file_id, name, stored, mime, len(raw), extracted, utc_now(), session["role"]))
        class_state = state_get("class")
        config = class_state.get("teacherConfig", {})
        if not isinstance(config, dict):
            config = {}
        knowledge = config.get("knowledge", [])
        if not isinstance(knowledge, list):
            knowledge = []
        if name not in knowledge:
            knowledge.append(name)
        config["knowledge"] = knowledge
        class_state["teacherConfig"] = config
        state_put("class", class_state)
        record_event("teacher", None, "file_upload", None, name)
        self.json_response({"file": {"id": file_id, "name": name, "size": len(raw), "indexed": bool(extracted)}})

    def export_events(self, fmt: str) -> None:
        with database() as db:
            rows = db.execute("SELECT ts,group_no,event_type,stage,data FROM events ORDER BY id").fetchall()
        if fmt == "json":
            raw = json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2).encode()
            mime, name = "application/json; charset=utf-8", "课堂过程事件.json"
        else:
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["timestamp", "group", "stage", "event_type", "data"])
            for row in rows:
                writer.writerow([row["ts"], f"G{row['group_no']:02d}" if row["group_no"] else "CLASS", row["stage"] or "", row["event_type"], row["data"]])
            raw = ("\ufeff" + output.getvalue()).encode("utf-8")
            mime, name = "text/csv; charset=utf-8", "课堂过程事件.csv"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)
        record_event("teacher", None, "data_export", None, fmt)


def main() -> None:
    init_database()
    env = os.environ.get("MPT_ENV", "development").lower()
    browser_host = "127.0.0.1" if HOST in {"0.0.0.0", "::"} else HOST
    print(f"数学项目协作智能体已启动：http://{browser_host}:{PORT}/")
    if browser_host != HOST:
        print(f"监听地址：{HOST}:{PORT}（浏览器请使用上面的本地地址）")
    print(f"服务根目录：{ROOT}")
    print(f"数据目录：{DATA_DIR}")
    print(f"运行模式：{env}；DeepSeek：{'已配置' if os.environ.get('DEEPSEEK_API_KEY') else '未配置，将使用教师静态支架'}")
    if not is_initialized():
        print("平台尚未初始化，请在登录页完成首位教师注册。")
        if SETUP_TOKEN_DISPLAY:
            print(f"本机一次性教师注册码：{SETUP_TOKEN_DISPLAY}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
