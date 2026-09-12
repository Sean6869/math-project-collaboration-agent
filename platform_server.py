"""迈谱 Maple：无第三方运行依赖的数学项目式学习服务。"""

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


def env_seconds(name: str, default: int, minimum: int = 5, maximum: int = 300) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def normalize_deepseek_model(model: str | None) -> str:
    configured = (model or "").strip() or "deepseek-flash"
    aliases = {
        "deepseek-chat": "deepseek-flash",
        "deepseek-reasoner": "deepseek-flash",
        "deepseek-v4-flash": "deepseek-flash",
        "deepseek-v4-flash-vision-exp": "deepseek-flash",
    }
    return aliases.get(configured, configured)


DATA_DIR = Path(os.environ.get("MPT_DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "platform.db"
HOST = os.environ.get("MPT_HOST", "127.0.0.1")
# Railway injects PORT at runtime; keep MPT_PORT for local compatibility.
PORT = int(os.environ.get("PORT") or os.environ.get("MPT_PORT", "8000"))
SESSION_SECONDS = int(os.environ.get("MPT_SESSION_SECONDS", "28800"))
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = int(os.environ.get("MPT_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_URL = DEEPSEEK_BASE_URL + "/chat/completions"
DEEPSEEK_MODEL = normalize_deepseek_model(os.environ.get("DEEPSEEK_MODEL"))
DEEPSEEK_REASONING_MODEL = os.environ.get("DEEPSEEK_REASONING_MODEL", "deepseek-v4-pro")
ARK_TEXT_URL = os.environ.get("ARK_TEXT_URL", "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
ARK_TEXT_MODEL = os.environ.get("ARK_TEXT_MODEL", "doubao-seed-2-0-lite-260428")
ARK_TEXT_SPEED = os.environ.get("ARK_TEXT_SPEED", "fast").strip() or "fast"
ARK_IMAGE_URL = os.environ.get("ARK_IMAGE_URL", "https://ark.cn-beijing.volces.com/api/v3/images/generations")
ARK_IMAGE_MODEL = os.environ.get("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128")
ARK_IMAGE_RESPONSE_FORMAT = os.environ.get("ARK_IMAGE_RESPONSE_FORMAT", "b64_json").strip().lower()
ARK_TEXT_TIMEOUT_SECONDS = env_seconds("ARK_TEXT_TIMEOUT_SECONDS", 120)
ARK_IMAGE_TIMEOUT_SECONDS = env_seconds("ARK_IMAGE_TIMEOUT_SECONDS", 180)
MATH_FORMAT_RULE = "所有数学表达式必须用 $...$、$$...$$、\\(...\\) 或 \\[...\\] 包裹；不得裸露输出LaTex命令。"
FREE_AI_QUOTA_DEFAULT = 30
FREE_AI_HISTORY_LIMIT = 40
FREE_AI_CONVERSATION_LIMIT = 20
COOKIE_NAME = "mpt_session"
LOGIN_FAILURES: dict[str, list[float]] = {}
LOGIN_LOCK = threading.Lock()
SETUP_TOKEN_DISPLAY = ""
SETUP_TOKEN_FILE = DATA_DIR / "setup-token.txt"

CLASS_KEYS = {
    "project", "className", "currentStage", "stagePaused",
    "aiOpen", "freeAiEnabled", "freeAiQuota", "projectType", "projectHours", "hintQuota",
    "maxHintLevel", "hintPolicy", "broadcast", "teacherConfig", "groups", "queue", "common",
    "evaluations", "audit",
}
GROUP_KEYS = {
    "groupId", "roles", "roleRound", "stageSubmitted", "stageTimes",
    "workspace", "versions", "evidence", "viewpoints", "firstThoughts",
    "debate", "product", "events", "questionTree", "graphConfig", "checklist", "preparationChecks", "freeAi",
}
HINT_POLICY_DEFAULT = {"L1": 20, "L2": 10, "L3": 5, "L4": 3}
HINT_LEVEL_NAMES = {1: "定位", 2: "拆解", 3: "局部示范", 4: "教师介入"}
ROLE_NAMES = ["建模者", "质疑者", "记录者", "解释者", "验证者", "资料员", "协调者", "展示者", "计算者", "检查者"]
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


class UpstreamServiceError(ValueError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


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


def group_prefix_from_code(value: str) -> str:
    """Persist the prefix while accepting a first code such as TEAM01."""
    code = clean_code(value)
    return re.sub(r"\d+$", "", code) or code


def grade_from_text(*values: object) -> int | None:
    """Extract a junior-high grade without retaining the access code itself."""
    text = " ".join(str(value or "") for value in values).upper()
    patterns = {
        7: (r"七年级", r"初一", r"(?:^|\D)7年级", r"(?:^|[^A-Z0-9])G(?:RADE)?[-_ ]?7(?:\D|$)"),
        8: (r"八年级", r"初二", r"(?:^|\D)8年级", r"(?:^|[^A-Z0-9])G(?:RADE)?[-_ ]?8(?:\D|$)"),
        9: (r"九年级", r"初三", r"(?:^|\D)9年级", r"(?:^|[^A-Z0-9])G(?:RADE)?[-_ ]?9(?:\D|$)"),
    }
    return next((grade for grade, choices in patterns.items() if any(re.search(pattern, text) for pattern in choices)), None)


def class_code_grade() -> int | None:
    with database() as db:
        row = db.execute("SELECT value FROM settings WHERE key='class_grade'").fetchone()
    try:
        grade = int(row["value"]) if row else 0
    except (TypeError, ValueError):
        grade = 0
    return grade if grade in {7, 8, 9} else None


def persist_class_grade(code: str) -> int | None:
    """Persist only the grade inferred from a valid class code, never the code."""
    grade = grade_from_text(code)
    with database() as db:
        if grade:
            db.execute(
                "INSERT INTO settings(key,value) VALUES('class_grade',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(grade),),
            )
        else:
            db.execute("DELETE FROM settings WHERE key='class_grade'")
    class_state = state_get("class")
    resolved = grade or grade_from_text(class_state.get("className", ""))
    if resolved:
        class_state["classGrade"] = resolved
    else:
        class_state.pop("classGrade", None)
    state_put("class", class_state)
    return resolved


def student_grade(state: dict) -> int | None:
    try:
        stored = int(state.get("classGrade", 0))
    except (TypeError, ValueError):
        stored = 0
    return stored if stored in {7, 8, 9} else grade_from_text(state.get("className", ""))


def student_math_scope(state: dict) -> str:
    grade = student_grade(state)
    audience = {7: "七年级", 8: "八年级", 9: "九年级"}.get(grade, "初中阶段")
    return (
        f"服务对象为中国{audience}学生。只处理与初中数学直接相关的概念、计算、推理、图形、统计、"
        "数学表达或数学建模任务。生活、科学、项目或游戏只能作为数学问题的情境，不能回答其非数学内容。"
        "表达、术语和步骤应以该年级或更早所学内容为主；确需扩展时，要先用学生已有知识解释，并明确标为拓展。"
        "对于与数学无直接关系的请求，不提供实质答案，只回复：这里只支持初中数学相关问题，请把问题改写为包含"
        "数量关系、计算、图形、统计或数学建模的提问。"
    )


# ponytail: lexical gate blocks obvious off-topic traffic before model calls; upgrade to a reviewed classifier only if real classroom prompts show recurring false decisions.
MATH_TERMS = (
    "数学", "数值", "数量关系", "算式", "公式", "方程", "不等式", "函数", "变量", "代数", "因式",
    "分数", "小数", "整数", "实数", "平方", "根式", "比例", "比值", "百分", "几何", "图形", "三角形",
    "四边形", "多边形", "圆周", "圆心", "半径", "直径", "角度", "内角", "外角", "边长", "边的关系",
    "面积", "周长", "体积", "坐标", "斜率", "证明", "统计", "概率", "平均数", "中位数", "众数", "频率",
    "测量", "误差", "建模", "勾股", "全等", "轴对称", "一次函数", "二次函数", "双曲线",
)
MATH_CONTEXT_TERMS = (
    "数量", "计算", "关系", "图像", "数据", "单位", "距离", "速度", "时间", "价格", "成本", "性价比",
    "长度", "宽度", "最大值", "最小值", "最优", "模型", "相似", "平移", "旋转",
)
MATH_SYMBOL_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*[+\-×÷*/=<>≤≥]\s*[A-Za-z\d(]|[A-Za-z]\s*=\s*[-+]?\d|"
    r"\b(?:sin|cos|tan|sqrt)\b|\\(?:frac|sqrt|times|div)|[π√∠△])",
    re.IGNORECASE,
)
MATH_CONTINUATION_RE = re.compile(
    r"^(?:为什么|怎么算|如何算|继续|请继续|继续检查|再解释一下|这一步|上一步|上一题|这个呢|然后呢|哪里错了|"
    r"请检查|帮我检查|明白了|谢谢)[？?！!。 ]*$"
)


def has_math_signal(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    contextual = sum(term in compact for term in MATH_CONTEXT_TERMS)
    return any(term in compact for term in MATH_TERMS) or contextual >= 2 or bool(MATH_SYMBOL_RE.search(text or ""))


def is_math_question(text: str, history: list[dict] | None = None) -> bool:
    if has_math_signal(text):
        return True
    clean = re.sub(r"\s+", "", text or "")
    if not MATH_CONTINUATION_RE.fullmatch(clean):
        return False
    recent = " ".join(
        str(item.get("content", item.get("question", item.get("reply", ""))))
        for item in (history or [])[-6:] if isinstance(item, dict)
    )
    return has_math_signal(recent)


def ensure_math_question(text: str, history: list[dict] | None = None) -> None:
    if not is_math_question(text, history):
        raise UpstreamServiceError(
            "这里只支持初中数学相关问题，请把问题改写为包含数量关系、计算、图形、统计或数学建模的提问。",
            HTTPStatus.UNPROCESSABLE_ENTITY,
        )


def validated_members(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= len(ROLE_NAMES):
        raise ValueError(f"成员数量应为1—{len(ROLE_NAMES)}人")
    members = [str(item).strip()[:40] for item in value]
    if any(not item for item in members):
        raise ValueError("请填写全部成员称呼")
    if len(set(members)) != len(members):
        raise ValueError("成员称呼不能重复")
    return members


def roles_for_members(existing: object, count: int) -> list[str]:
    roles = [str(item) for item in existing] if isinstance(existing, list) else []
    roles = [item for item in roles if item in ROLE_NAMES]
    roles.extend(item for item in ROLE_NAMES if item not in roles)
    return roles[:count]


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


def group_code_prefix() -> str:
    """Return the immutable prefix used to issue sequential group access codes."""
    with database() as db:
        row = db.execute("SELECT value FROM settings WHERE key='group_code_prefix'").fetchone()
    stored = clean_code(row["value"]) if row else ""
    if len(stored) >= 4:
        return stored

    # Upgrade installations created before the prefix was persisted. The
    # fallback is only accepted from the existing deployment configuration and
    # is immediately persisted; expansion never trusts a request field.
    legacy_prefix = clean_code(os.environ.get("MPT_GROUP_CODE_PREFIX", ""))
    if len(legacy_prefix) < 4:
        return ""
    with database() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES('group_code_prefix',?) "
            "ON CONFLICT(key) DO NOTHING",
            (legacy_prefix,),
        )
    return legacy_prefix


def initialize_platform(username: str, teacher_password: str, recovery_code: str, class_code: str, group_prefix: str, count: int) -> None:
    username = username.strip()
    recovery_code = recovery_code.strip()
    class_code = clean_code(class_code)
    group_prefix = group_prefix_from_code(group_prefix)
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
            "INSERT INTO teachers(id,username,password_hash,created_at,is_admin) VALUES(1,?,?,?,1)",
            (username, password_hash(teacher_password), utc_now()),
        )
        db.execute("INSERT INTO settings(key,value) VALUES('class_code_hash',?)", (password_hash(class_code),))
        db.execute("INSERT INTO settings(key,value) VALUES('admin_recovery_hash',?)", (password_hash(recovery_code),))
        db.execute("INSERT INTO settings(key,value) VALUES('group_count',?)", (str(count),))
        db.execute("INSERT INTO settings(key,value) VALUES('group_code_prefix',?)", (group_prefix,))
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
    grade = grade_from_text(class_code)
    if grade:
        with database() as db:
            db.execute("INSERT INTO settings(key,value) VALUES('class_grade',?)", (str(grade),))
    class_state = {"groupCount": count, "groups": summaries}
    if grade:
        class_state["classGrade"] = grade
    state_put("class", class_state)
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
              id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL, created_at TEXT NOT NULL,
              is_admin INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS groups (group_no INTEGER PRIMARY KEY, access_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, role TEXT NOT NULL, teacher_id INTEGER, group_no INTEGER,
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
              uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL, group_no INTEGER
            );
            CREATE TABLE IF NOT EXISTS ai_calls (
              id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, group_no INTEGER NOT NULL,
              level TEXT NOT NULL, model TEXT NOT NULL, latency_ms INTEGER NOT NULL,
              status TEXT NOT NULL, detail TEXT NOT NULL
            );
            """
        )
        teacher_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='teachers'").fetchone()
        if teacher_sql and "check(id=1)" in re.sub(r"\s+", "", teacher_sql["sql"] or "").lower():
            legacy_columns = {row[1] for row in db.execute("PRAGMA table_info(teachers)").fetchall()}
            db.execute("ALTER TABLE teachers RENAME TO teachers_legacy")
            db.execute("""
                CREATE TABLE teachers (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  is_admin INTEGER NOT NULL DEFAULT 0
                )
            """)
            admin_value = "is_admin" if "is_admin" in legacy_columns else "CASE WHEN id=1 THEN 1 ELSE 0 END"
            db.execute(
                f"INSERT INTO teachers(id,username,password_hash,created_at,is_admin) "
                f"SELECT id,username,password_hash,created_at,{admin_value} FROM teachers_legacy"
            )
            db.execute("DROP TABLE teachers_legacy")
        teacher_columns = {row[1] for row in db.execute("PRAGMA table_info(teachers)").fetchall()}
        if "is_admin" not in teacher_columns:
            db.execute("ALTER TABLE teachers ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE teachers SET is_admin=1 WHERE id=1")
        session_columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
        if "teacher_id" not in session_columns:
            db.execute("ALTER TABLE sessions ADD COLUMN teacher_id INTEGER")
        file_columns = {row[1] for row in db.execute("PRAGMA table_info(files)").fetchall()}
        if "group_no" not in file_columns:
            db.execute("ALTER TABLE files ADD COLUMN group_no INTEGER")
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


def state_put(scope: str, value: dict, preserve_roles: bool = False) -> None:
    with database() as db:
        if preserve_roles:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM states WHERE scope=?", (scope,)).fetchone()
            saved = json.loads(row[0]) if row else {}
            for key in ("roles", "roleRound", "roleHistory"):
                value.pop(key, None)
                if key in saved:
                    value[key] = saved[key]
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        db.execute(
            "INSERT INTO states(scope,data,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(scope) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
            (scope, payload, utc_now()),
        )


def _has_value(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return any(_has_value(item) for item in (value.values() if isinstance(value, dict) else value))
    return value is not None and value is not False


def stage_task_status(group_state: dict) -> list[list[bool]]:
    workspace = group_state.get("workspace", {}) if isinstance(group_state.get("workspace"), dict) else {}
    members = [name for name in group_state.get("members", []) if isinstance(name, str) and name.strip()]
    thoughts = group_state.get("firstThoughts", {}) if isinstance(group_state.get("firstThoughts"), dict) else {}
    questions = group_state.get("questionTree", []) if isinstance(group_state.get("questionTree"), list) else []
    graph = group_state.get("graphConfig", {}) if isinstance(group_state.get("graphConfig"), dict) else {}
    checklist = group_state.get("checklist", {}) if isinstance(group_state.get("checklist"), dict) else {}
    preparation = group_state.get("preparationChecks", {}) if isinstance(group_state.get("preparationChecks"), dict) else {}
    evidence = group_state.get("evidence", []) if isinstance(group_state.get("evidence"), list) else []
    viewpoints = group_state.get("viewpoints", []) if isinstance(group_state.get("viewpoints"), list) else []
    versions = group_state.get("versions", []) if isinstance(group_state.get("versions"), list) else []
    product = group_state.get("product", {}) if isinstance(group_state.get("product"), dict) else {}
    rehearsal_checks = product.get("rehearsalChecks", []) if isinstance(product.get("rehearsalChecks"), list) else []
    reflection = [line.strip() for line in str(product.get("reflection", "")).splitlines()]
    graph_complete = all(_has_value(graph.get(key)) for key in ("a", "b", "c", "min", "max", "note"))
    checklist_complete = len(checklist) >= 6 and all(value is True for value in checklist.values())
    return [
        [preparation.get("goal") is True, preparation.get("materials") is True,
         bool(group_state.get("membersLocked")) and bool(members), preparation.get("rules") is True],
        [bool(members) and all(_has_value(thoughts.get(str(i), thoughts.get(i))) for i in range(len(members))),
         _has_value(workspace.get("problem")),
         all(_has_value(workspace.get(key)) for key in ("known", "unknown", "constraints")),
         len([item for item in questions if _has_value(item)]) >= 3 and _has_value(questions[0] if questions else "")],
        [all(_has_value(workspace.get(key)) for key in ("variables", "constraints")),
         _has_value(workspace.get("relation")),
         _has_value(workspace.get("model")) and len(versions) >= 1,
         graph_complete or checklist_complete],
        [len(evidence) >= 1, any(isinstance(item, dict) and item.get("verified") is True for item in evidence),
         len(viewpoints) >= 2 and all(isinstance(item, dict) and all(_has_value(item.get(key)) for key in ("claim", "basis", "risk")) for item in viewpoints[:2]),
         len(versions) >= 2],
        [all(_has_value(value) for value in (workspace.get("problem"), workspace.get("model"), product.get("claim"), product.get("limitations")))
         and any(isinstance(item, dict) and item.get("verified") is True for item in evidence),
         _has_value(product.get("rehearsal")) and len(rehearsal_checks) >= 4 and all(value is True for value in rehearsal_checks[:4]),
         all(_has_value(product.get(key)) for key in ("audience", "claim", "limitations")),
         len(reflection) >= 3 and all(reflection[:3])],
    ]


def validated_stage_submissions(group_state: dict) -> dict:
    raw = group_state.get("stageSubmitted", {}) if isinstance(group_state.get("stageSubmitted"), dict) else {}
    status = stage_task_status(group_state)
    return {number: bool(raw.get(str(number), raw.get(number, False))) and all(status[number - 1]) for number in range(1, 6)}


def hint_policy(state: dict) -> dict[str, int]:
    raw = state.get("hintPolicy") if isinstance(state.get("hintPolicy"), dict) else {}
    result = {}
    for level, default in HINT_POLICY_DEFAULT.items():
        try:
            result[level] = max(1, min(50, int(raw.get(level, default))))
        except (TypeError, ValueError):
            result[level] = default
    if not result["L1"] > result["L2"] > result["L3"] > result["L4"]:
        return HINT_POLICY_DEFAULT.copy()
    return result


def hint_usage(group_state: dict) -> dict[str, int]:
    raw = group_state.get("hintUsage") if isinstance(group_state.get("hintUsage"), dict) else {}
    result = {}
    for level in HINT_POLICY_DEFAULT:
        try:
            result[level] = max(0, int(raw.get(level, 0)))
        except (TypeError, ValueError):
            result[level] = 0
    return result


def active_help_thread(group_state: dict) -> dict | None:
    thread_id = str(group_state.get("activeHelpThreadId", ""))
    threads = group_state.get("helpThreads", [])
    if not thread_id or not isinstance(threads, list):
        return None
    return next(
        (item for item in threads if isinstance(item, dict) and item.get("id") == thread_id and item.get("status") in {"active", "teacher"}),
        None,
    )


def help_total(group_state: dict) -> int:
    return sum(hint_usage(group_state).values())


def highest_hint_level(group_state: dict) -> int:
    levels = []
    for item in group_state.get("helpHistory", []):
        value = str(item.get("level", "")) if isinstance(item, dict) else ""
        if re.fullmatch(r"L[1-4]", value):
            levels.append(int(value[1:]))
    return max(levels, default=0)


def group_completion(group_state: dict) -> tuple[int, int]:
    signals = stage_task_status(group_state)
    submitted_count = sum(validated_stage_submissions(group_state).values())
    partial = sum(round(sum(stage) / len(stage) * 20) for stage in signals)
    return min(100, max(partial, submitted_count * 20)), submitted_count


def remember_roles(state: dict) -> None:
    members = state.get("members", [])
    roles = roles_for_members(state.get("roles"), len(members))
    history = state.setdefault("roleHistory", {})
    for i, name in enumerate(members):
        if name not in history:
            # Older versions rotated the whole list right once per round.
            rounds = min(int(state.get("roleRound", 1)), len(roles))
            history[name] = [roles[(i + offset) % len(roles)] for offset in range(rounds)]
        if roles[i] not in history[name]:
            history[name].append(roles[i])


def dynamic_group_summaries(class_state: dict) -> list[dict]:
    stored = class_state.get("groups", [])
    stored = stored if isinstance(stored, list) else []
    count = group_count()
    summaries = []
    queue = class_state.get("queue", []) if isinstance(class_state.get("queue", []), list) else []
    for number in range(1, count + 1):
        saved = next((item for item in stored if isinstance(item, dict) and int(item.get("id", 0)) == number), {})
        group_state = state_get(f"group:{number}")
        progress, submitted_count = group_completion(group_state)
        versions = group_state.get("versions", [])
        latest = versions[-1].get("note", "模型版本已保存") if isinstance(versions, list) and versions and isinstance(versions[-1], dict) else "工作区已保存" if _has_value(group_state.get("workspace")) else "尚未保存"
        active = progress > 0 or bool(help_total(group_state)) or bool(group_state.get("helpHistory"))
        waiting = bool(saved.get("teacher")) or any(int(item.get("group", 0)) == number and not item.get("resolved") for item in queue if isinstance(item, dict))
        status = "等待教师" if waiting else "已完成" if submitted_count == 5 else "持续推进" if active else "准备中"
        summaries.append({
            "id": number,
            "roleRound": int(group_state.get("roleRound", 1)),
            "membersLocked": bool(group_state.get("membersLocked")),
            "stage": int(group_state.get("currentStage", class_state.get("currentStage", 1))),
            "submitted": submitted_count,
            "currentSubmitted": validated_stage_submissions(group_state).get(int(class_state.get("currentStage", 1)), False),
            "status": status,
            "help": help_total(group_state),
            "maxHint": highest_hint_level(group_state),
            "teacher": bool(saved.get("teacher")),
            "latest": latest,
            "issue": str(saved.get("issue", ""))[:120],
            "progress": progress,
        })
    return summaries


def current_broadcast() -> dict:
    saved = state_get("broadcast")
    if saved:
        if saved.get("broadcastType") == "共性数学澄清":
            saved["broadcastType"] = "统一答疑"
        saved["seenGroups"] = sorted({int(group) for group in saved.get("seenGroups", []) if str(group).isdigit() and 1 <= int(group) <= group_count()})
        return saved
    return {"broadcast": state_get("class").get("broadcast", ""), "broadcastType": "教师广播", "broadcastId": "", "seenGroups": []}


def broadcast_for_session(session: dict) -> dict:
    message = current_broadcast()
    seen = message.get("seenGroups", [])
    if session.get("role") == "student":
        message["broadcastViewed"] = int(session.get("groupId") or 0) in seen
    else:
        message["unseenGroups"] = [number for number in range(1, group_count() + 1) if number not in seen]
    return message


def acknowledge_broadcast(group_no: int, broadcast_id: str) -> dict:
    if not broadcast_id:
        raise ValueError("广播标识无效，请刷新页面后重试")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT data FROM states WHERE scope='broadcast'").fetchone()
        message = json.loads(row[0]) if row else {}
        if not message.get("broadcast") or message.get("broadcastId") != broadcast_id:
            raise ValueError("广播已更新或撤下，请刷新后查看最新内容")
        seen = {int(group) for group in message.get("seenGroups", []) if str(group).isdigit()}
        first_view = group_no not in seen
        seen.add(group_no)
        message["seenGroups"] = sorted(seen)
        db.execute(
            "INSERT INTO states(scope,data,updated_at) VALUES('broadcast',?,?) "
            "ON CONFLICT(scope) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at",
            (json.dumps(message, ensure_ascii=False, separators=(",", ":")), utc_now()),
        )
    if first_view:
        record_event("student", group_no, "broadcast_view", None, broadcast_id)
    message["broadcastViewed"] = True
    return message


def merged_state(group_no: int) -> dict:
    value = state_get("class")
    group_state = state_get(f"group:{group_no}")
    value.update(group_state)
    value.update(current_broadcast())
    value["stageSubmitted"] = validated_stage_submissions(group_state)
    value["broadcastViewed"] = group_no in value.get("seenGroups", [])
    # 课堂阶段、暂停状态和 AI 开关由教师端统一控制；小组初始状态中的
    # currentStage=1 不能覆盖教师已经开放的阶段，否则学生刷新后会被误判回阶段1。
    class_state = state_get("class")
    for key in ("currentStage", "stagePaused", "aiOpen"):
        if key in class_state:
            value[key] = class_state[key]
    value["freeAiEnabled"] = class_state.get("freeAiEnabled", True) is not False
    try:
        value["freeAiQuota"] = max(1, min(100, int(class_state.get("freeAiQuota", FREE_AI_QUOTA_DEFAULT))))
    except (TypeError, ValueError):
        value["freeAiQuota"] = FREE_AI_QUOTA_DEFAULT
    value["groupId"] = group_no
    value["hintPolicy"] = hint_policy(value)
    value["hintUsage"] = hint_usage(group_state)
    value["hintUsed"] = sum(value["hintUsage"].values())
    value["hintQuota"] = sum(value["hintPolicy"].values())
    value["maxHintLevel"] = 3
    value["activeHelpThread"] = active_help_thread(group_state)
    value["helpThreads"] = group_state.get("helpThreads", []) if isinstance(group_state.get("helpThreads", []), list) else []
    value["freeAi"] = free_ai_state(group_state)
    value["groups"] = dynamic_group_summaries(value)
    topics: dict[str, list[int]] = {}
    for item in value["groups"]:
        if item.get("issue"):
            topics.setdefault(str(item["issue"]), []).append(int(item["id"]))
    value["common"] = [
        {"id": f"C-{index:02d}", "title": title, "groups": numbers,
         "note": "多个小组在提示请求中选择了相同卡点类型。", "action": "请教师结合各组原始记录判断是否需要共性澄清"}
        for index, (title, numbers) in enumerate(topics.items(), 1) if len(numbers) >= 2
    ]
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
            "SELECT role,teacher_id,group_no,expires_at FROM sessions WHERE token_hash=?", (token_hash(token),)
        ).fetchone()
    if not row or row["expires_at"] < int(time.time()):
        return None
    teacher = None
    if row["role"] == "teacher":
        with database() as db:
            teacher = db.execute("SELECT username,is_admin FROM teachers WHERE id=?", (row["teacher_id"] or 1,)).fetchone()
    return {
        "role": row["role"], "groupId": row["group_no"],
        "teacherId": row["teacher_id"],
        "username": teacher["username"] if teacher else None,
        "isAdmin": bool(teacher and teacher["is_admin"]),
    }


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
    scaffold = str(source[min(level_no - 1, len(source) - 1)]).strip().rstrip("。.!！？；; ")[:180]
    diagnosis = (
        "你们已经愿意记录尝试并把卡点交给教师支架继续核对，这说明小组在依据过程调整思路，而不是直接等待结论。"
        "当前需要补足的是对对象、条件和依据的对应检查，也便于后续复盘。"
    )
    guidance = (
        f"教师预设支架是：{scaffold}。请先判断这条支架具体帮助你们检查哪个量、哪个关系或哪条证据，"
        "再由一名组员向另一名组员追问：如果删去一项条件，当前判断还成立吗？请说出判断依据。"
    )
    if level_no <= 1:
        next_action = (
            "下一步请把题目中的一个目标量和至少两个相关条件写在同一处，并用自己的话说明它们为什么有关。"
            "完成后由组员互相核对：是否漏掉了范围、单位或现实限制；暂时不要跳到后续计算。"
        )
    elif level_no == 2:
        next_action = (
            "下一步请把当前卡点拆成一个最小可检查的问题，分别写出它的已知、未知和需要连接的两个对象。"
            "然后选择表格、草图或关系式中的一种表示，并由小组说明这一步如何支持下一步判断。"
        )
    else:
        next_action = (
            "下一步请由小组完成一次局部核验：选择一个边界值、单位检查或反例，记录输入、观察和依据。"
            "先不要替教师补出最终结论，请你们说明核验结果支持了哪一处关系，以及还剩哪一步需要自己判断。"
        )
    return f"诊断：{diagnosis}\n支架：{guidance}\n下一步：{next_action}"


def validate_hint(text: str, level: str = "L1") -> str | None:
    clean = re.sub(r"\s+", " ", text or "").strip()
    blocked = ("最终答案", "答案是", "完整证明", "完整模型", "故最大值为", "直接得到最优", "最终结论为")
    limits = {"L1": 760, "L2": 900, "L3": 1100}
    if not clean or len(clean) > limits.get(level, 360) or any(item in clean for item in blocked):
        return None
    return clean


def parse_model_hint(text: str, level: str) -> str | None:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", text or "", flags=re.IGNORECASE).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    diagnosis = re.sub(r"\s+", " ", str(value.get("diagnosis", ""))).strip()
    guidance = re.sub(r"\s+", " ", str(value.get("guidance", ""))).strip()
    next_action = re.sub(r"\s+", " ", str(value.get("nextAction", ""))).strip()
    if not diagnosis or not guidance or not next_action:
        return None
    # Keep the three-part contract meaningful even when the model returns
    # syntactically valid JSON with very short, generic content.
    if any(len(part) < 80 for part in (diagnosis, guidance, next_action)):
        return None
    minimum = {"L1": 240, "L2": 240, "L3": 240}.get(level, 240)
    combined = f"诊断：{diagnosis}\n支架：{guidance}\n下一步：{next_action}"
    clean = validate_hint(combined, level) if len(combined) >= minimum else None
    if not clean:
        return None
    if level == "L1" and any(item in clean for item in ("写出函数", "写出表达式", "列出不等式", "求最大", "求最小")):
        return None
    if level == "L2" and any(item in clean for item in ("直接得到最优", "完整证明", "最后化简并求出", "最终答案")):
        return None
    if level == "L3":
        student_markers = ("请你完成", "由你完成", "学生完成", "小组完成", "留给你", "自行完成", "先停在这一步")
        forbidden = ("最后化简", "求出最大值", "给出最终模型", "完成全部推导", "直接得到最优")
        if not any(item in clean for item in student_markers) or any(item in clean for item in forbidden):
            return None
    return clean


def deepseek_hint(
    state: dict, level: str, done: str, issue_type: str, issue: str,
    history: list[dict] | None = None, checked_work: bool = False,
) -> tuple[str | None, int, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None, 0, "not_configured"
    config = state.get("teacherConfig", {})
    knowledge_names = config.get("knowledge", []) if isinstance(config, dict) else []
    with database() as db:
        texts = db.execute("SELECT original_name,extracted_text FROM files WHERE extracted_text<>'' ORDER BY uploaded_at DESC LIMIT 5").fetchall()
    knowledge = "\n".join(f"[{row['original_name']}] {row['extracted_text'][:4000]}" for row in texts)
    contracts = {
        "L1": "定位支架：只指出一个尚未明确的目标、条件或检查项；提出一个聚焦问题和一个立即可执行的观察/核对动作。不得让学生写函数、表达式、不等式，不得给完整公式、建模方法或结果。",
        "L2": "结构支架：把卡点拆成2—3个子问题，建议变量、图表或数量关系的组织方式和处理顺序；下一步只让学生做第一个整理动作。不得完成全部建模或推导。",
        "L3": "局部示范：只给一个类比小例子或一步局部推导，说明核验方法；必须明确写‘请你完成/由学生完成’的剩余一步。不得要求学生最后化简、求最大值、完成全部推导，不得给最终模型、证明、最优值或成品答案。",
    }
    system = (
        "你是服务于中国初中数学项目式学习小组的教学支架。必须依据学生当前尝试和历史提示生成有推进作用、但保留学生思考责任的反馈。"
        + student_math_scope(state)
        + MATH_FORMAT_RULE
        + f"当前严格执行{level}：{contracts[level]}"
        "不得重复历史提示；信息不足时先澄清，不得猜测。材料冲突、数学结论不可靠或需要教师判断时，在nextAction中写明请教师核验。"
        "回答必须体现苏格拉底式引导：诊断开头先具体肯定学生已经完成的有效工作，再指出一个可改进点；支架至少提出一个能让学生自己判断的追问；下一步给出一个可在当前材料上执行的动作，并保留学生作出数学判断的责任。"
        "diagnosis、guidance、nextAction三个字段都不少于80个中文字符，目标各100字左右，不能用空话凑字数。"
        "只输出一个JSON对象，不要Markdown和额外文字，格式为："
        '{"diagnosis":"针对本次尝试的简短诊断","guidance":"符合当前层级的支架","nextAction":"学生下一步可执行动作；L3必须明确留下由学生完成的剩余一步"}'
    )
    recent = []
    for item in (history or [])[-6:]:
        if isinstance(item, dict):
            recent.append({
                "层级": item.get("level", ""), "学生提问": item.get("question", item.get("prompt", "")),
                "学生尝试": item.get("done", ""), "支架": item.get("reply", ""),
            })
    user = {
        "适用年级": student_grade(state) or "初中阶段",
        "项目": state.get("project", ""), "数学主题": config.get("mathTopic", ""),
        "课程目标": config.get("standard", ""), "当前阶段": state.get("currentStage", 1),
        "提示层级": level, "已完成": done, "卡点类型": issue_type, "具体卡点": issue,
        "共享工作区": state.get("workspace", {}), "已检查工作区": checked_work,
        "同一求助主题的最近对话": recent, "已审核材料": knowledge_names, "材料摘录": knowledge,
    }
    payload = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
        "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
        "stream": False, "temperature": 0.15, "max_tokens": 700,
    }, ensure_ascii=False).encode()
    request = urllib.request.Request(
        DEEPSEEK_URL, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Maple-Platform/1"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read())
        latency = round((time.perf_counter() - started) * 1000)
        return parse_model_hint(body["choices"][0]["message"]["content"], level), latency, "ok"
    except urllib.error.HTTPError as exc:
        latency = round((time.perf_counter() - started) * 1000)
        try:
            detail = json.loads(exc.read()).get("error", {})
            message = re.sub(r"\s+", " ", str(detail.get("message", ""))).strip()[:160]
        except (json.JSONDecodeError, AttributeError, ValueError):
            message = ""
        return None, latency, f"HTTP {exc.code}: {message}".rstrip(": ")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        latency = round((time.perf_counter() - started) * 1000)
        return None, latency, type(exc).__name__


def free_ai_settings(state: dict) -> tuple[bool, int]:
    enabled = state.get("freeAiEnabled", True) is not False
    try:
        quota = max(1, min(100, int(state.get("freeAiQuota", FREE_AI_QUOTA_DEFAULT))))
    except (TypeError, ValueError):
        quota = FREE_AI_QUOTA_DEFAULT
    return enabled, quota


def free_ai_state(group_state: dict) -> dict:
    raw = group_state.get("freeAi") if isinstance(group_state.get("freeAi"), dict) else {}
    conversations = raw.get("conversations") if isinstance(raw.get("conversations"), list) else []
    conversations = [item for item in conversations if isinstance(item, dict) and item.get("id")]
    legacy_messages = raw.get("messages") if isinstance(raw.get("messages"), list) else []
    if not conversations and legacy_messages:
        first_prompt = next(
            (str(item.get("content", "")).strip() for item in legacy_messages
             if isinstance(item, dict) and item.get("role") == "user" and item.get("content")),
            "历史对话",
        )
        conversations = [{
            "id": "legacy", "title": first_prompt[:20], "createdAt": utc_now(), "updatedAt": utc_now(),
            "messages": legacy_messages[-FREE_AI_HISTORY_LIMIT:],
        }]
    for conversation in conversations:
        messages = conversation.get("messages") if isinstance(conversation.get("messages"), list) else []
        conversation["messages"] = messages[-FREE_AI_HISTORY_LIMIT:]
    try:
        usage = max(0, int(raw.get("usage", 0)))
    except (TypeError, ValueError):
        usage = 0
    conversations = conversations[-FREE_AI_CONVERSATION_LIMIT:]
    active_id = str(raw.get("activeConversationId", ""))
    if not any(item["id"] == active_id for item in conversations):
        active_id = conversations[-1]["id"] if conversations else ""
    return {"conversations": conversations, "activeConversationId": active_id, "usage": usage}


def free_ai_context(state: dict, group: int) -> str:
    config = state.get("teacherConfig", {}) if isinstance(state.get("teacherConfig"), dict) else {}
    workspace = state.get("workspace", {}) if isinstance(state.get("workspace"), dict) else {}
    context = {
        "班级": state.get("className", ""), "适用年级": student_grade(state) or "初中阶段",
        "项目": state.get("project", ""), "数学主题": config.get("mathTopic", ""),
        "课程目标": config.get("standard", ""), "最终成果": config.get("finalProduct", ""),
        "当前阶段": state.get("currentStage", 1), "小组": group, "共享工作区": workspace,
    }
    return json.dumps(context, ensure_ascii=False)


def ark_free_messages(state: dict, group: int, prompt: str, history: list[dict]) -> list[dict]:
    system = (
        "你是‘迈谱 Maple’学生小组自由工作区的初中数学助手。"
        + student_math_scope(state)
        + "你的任务是帮助学生减少机械性整理，把更多时间留给小组协作和高阶思考。"
        "你可以解释概念、检查思路和书写数学表达，但不要替学生完成未经讨论的整个项目，"
        "不要伪造数据；缺少信息时明确列出需要补充的内容。"
        + MATH_FORMAT_RULE
        + "回答使用清晰的Markdown。"
        f"当前项目上下文：{free_ai_context(state, group)}"
    )
    messages = [{"role": "system", "content": system}]
    for item in history[-12:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).strip()[:3000]
        if content:
            messages.append({"role": item["role"], "content": content})
    messages.append({"role": "user", "content": prompt})
    return messages


def ark_text_request(messages: list[dict], stream: bool) -> urllib.request.Request:
    api_key = os.environ.get("ARK_API_KEY", "")
    payload = json.dumps({"model": ARK_TEXT_MODEL, "messages": messages, "stream": stream, "speed": ARK_TEXT_SPEED}, ensure_ascii=False).encode()
    return urllib.request.Request(
        ARK_TEXT_URL, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Maple-Platform/1"},
        method="POST",
    )


def ark_free_chat(
    state: dict, group: int, prompt: str, history: list[dict],
) -> tuple[str | None, int, str, str]:
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        return None, 0, "not_configured", ARK_TEXT_MODEL
    request = ark_text_request(ark_free_messages(state, group, prompt, history), False)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=ARK_TEXT_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read())
        latency = round((time.perf_counter() - started) * 1000)
        answer = str(body["choices"][0]["message"]["content"]).strip()
        return answer[:12000] if answer else None, latency, "ok", ARK_TEXT_MODEL
    except urllib.error.HTTPError as exc:
        latency = round((time.perf_counter() - started) * 1000)
        try:
            detail = json.loads(exc.read()).get("error", {})
            message = re.sub(r"\s+", " ", str(detail.get("message", ""))).strip()[:300]
        except (json.JSONDecodeError, AttributeError, ValueError):
            message = ""
        return None, latency, f"HTTP {exc.code}: {message}".rstrip(": "), ARK_TEXT_MODEL
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        latency = round((time.perf_counter() - started) * 1000)
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
            return None, latency, f"timeout after {ARK_TEXT_TIMEOUT_SECONDS}s", ARK_TEXT_MODEL
        return None, latency, type(exc).__name__, ARK_TEXT_MODEL


def sse_payloads(response):
    data_lines = []
    for raw_line in response:
        line = raw_line.decode("utf-8", "replace").rstrip("\r\n") if isinstance(raw_line, bytes) else str(raw_line).rstrip("\r\n")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield "\n".join(data_lines)


def ark_free_chat_stream(
    state: dict, group: int, prompt: str, history: list[dict], on_delta,
) -> tuple[str | None, int, str, str]:
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        return None, 0, "not_configured", ARK_TEXT_MODEL
    request = ark_text_request(ark_free_messages(state, group, prompt, history), True)
    started = time.perf_counter()
    answer_parts = []
    try:
        with urllib.request.urlopen(request, timeout=ARK_TEXT_TIMEOUT_SECONDS) as response:
            for raw_event in sse_payloads(response):
                if raw_event == "[DONE]":
                    break
                body = json.loads(raw_event)
                choice = (body.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content is None:
                    content = (choice.get("message") or {}).get("content")
                if content:
                    text = str(content)
                    answer_parts.append(text)
                    on_delta(text)
        latency = round((time.perf_counter() - started) * 1000)
        answer = "".join(answer_parts).strip()
        return answer[:12000] if answer else None, latency, "ok", ARK_TEXT_MODEL
    except urllib.error.HTTPError as exc:
        latency = round((time.perf_counter() - started) * 1000)
        try:
            detail = json.loads(exc.read()).get("error", {})
            message = re.sub(r"\s+", " ", str(detail.get("message", ""))).strip()[:300]
        except (json.JSONDecodeError, AttributeError, ValueError):
            message = ""
        return None, latency, f"HTTP {exc.code}: {message}".rstrip(": "), ARK_TEXT_MODEL
    except (AttributeError, urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        latency = round((time.perf_counter() - started) * 1000)
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
            return None, latency, f"timeout after {ARK_TEXT_TIMEOUT_SECONDS}s", ARK_TEXT_MODEL
        return None, latency, type(exc).__name__, ARK_TEXT_MODEL


def free_ai_intent(prompt: str) -> str:
    compact = re.sub(r"\s+", "", prompt.lower())
    if re.search(r"(?:不要|不需要|无需).{0,8}(?:生成|画|绘制|制作|创建|设计).{0,8}(?:图片|图像|配图|海报|封面|插图)", compact):
        return "text"
    if (
        re.search(r"(?:如何|怎么|怎样|为什么|原理|解释).{0,12}(?:生成|画|绘制|制作|创建|设计)", compact)
        and not re.search(r"(?:帮我|给我|我要|想要|需要|请直接).{0,12}(?:生成|画|绘制|制作|创建|设计)", compact)
    ):
        return "text"
    image_request = re.search(
        r"(?:生成|画|绘制|制作|创建|设计).{0,18}(?:图片|图像|配图|海报|封面|插图|示意图)|"
        r"(?:图片|图像|配图|海报|封面|插图|示意图).{0,12}(?:生成|画|绘制|制作|创建|设计)",
        compact,
    )
    return "image" if image_request else "text"


def save_generated_file(group: int, name: str, mime: str, raw: bytes) -> dict:
    file_id = secrets.token_hex(12)
    stored = f"ai-{file_id}{Path(name).suffix or '.bin'}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / stored).write_bytes(raw)
    with database() as db:
        db.execute(
            "INSERT INTO files(id,original_name,stored_name,mime,size,extracted_text,uploaded_at,uploaded_by,group_no) VALUES(?,?,?,?,?,?,?,?,?)",
            (file_id, name[:160], stored, mime, len(raw), "", utc_now(), "student-ai", group),
        )
    return {"id": file_id, "name": name[:160], "mime": mime, "size": len(raw), "url": f"/api/ai/files/{file_id}"}


def ark_generate_image(group: int, prompt: str) -> tuple[dict, int]:
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        raise UpstreamServiceError("图片生成服务尚未配置，请教师在部署环境填写 ARK_API_KEY", HTTPStatus.SERVICE_UNAVAILABLE)
    response_format = ARK_IMAGE_RESPONSE_FORMAT if ARK_IMAGE_RESPONSE_FORMAT in {"url", "b64_json"} else "b64_json"
    payload = json.dumps({
        "model": ARK_IMAGE_MODEL, "prompt": prompt, "size": "2K",
        "stream": False, "response_format": response_format, "watermark": True,
    }, ensure_ascii=False).encode()
    request = urllib.request.Request(
        ARK_IMAGE_URL, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Maple-Platform/1"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=ARK_IMAGE_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read(2000).decode("utf-8", "replace").replace("\n", " ").strip()
        error_code = ""
        error_message = ""
        request_id = ""
        try:
            decoded = json.loads(detail)
            error = decoded.get("error", {}) if isinstance(decoded, dict) else {}
            if isinstance(error, dict):
                error_code = str(error.get("code", "")).strip()
                error_message = str(error.get("message", "")).strip()
            request_id = str(decoded.get("request_id", "")).strip() if isinstance(decoded, dict) else ""
        except (AttributeError, TypeError, json.JSONDecodeError):
            pass
        if exc.code == HTTPStatus.TOO_MANY_REQUESTS and error_code == "SetLimitExceeded":
            message = (
                "方舟图片模型已被当前账号暂停：已达到该模型的推理上限。"
                "请在火山方舟的模型激活页面调整或关闭 Safe Experience Mode 后再重试。"
            )
            status = HTTPStatus.TOO_MANY_REQUESTS
        else:
            message = f"图片生成接口HTTP {exc.code}"
            if error_message:
                message += f": {error_message[:500]}"
            elif detail:
                message += f": {detail[:500]}"
            status = exc.code if 400 <= exc.code < 500 else HTTPStatus.BAD_GATEWAY
        audit_detail = f"HTTP {exc.code};code={error_code};message={error_message[:400]}"
        if request_id:
            audit_detail += f";request_id={request_id[:120]}"
        with database() as db:
            db.execute(
                "INSERT INTO ai_calls(ts,group_no,level,model,latency_ms,status,detail) VALUES(?,?,?,?,?,?,?)",
                (utc_now(), group, "FREE_IMAGE", ARK_IMAGE_MODEL,
                 round((time.perf_counter() - started) * 1000),
                 "rate_limited" if exc.code == HTTPStatus.TOO_MANY_REQUESTS else "failed",
                 audit_detail[:2000]),
            )
        raise UpstreamServiceError(message, int(status)) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpstreamServiceError(f"图片生成接口请求失败：{type(exc).__name__}", HTTPStatus.BAD_GATEWAY) from exc

    try:
        image = result.get("data", [{}])[0]
        encoded = str(image.get("b64_json", ""))
        if encoded:
            raw = base64.b64decode(encoded, validate=True)
        else:
            image_url = str(image.get("url", ""))
            if image_url.startswith("data:image/"):
                raw = base64.b64decode(image_url.split(",", 1)[1], validate=True)
            elif image_url:
                try:
                    with urllib.request.urlopen(image_url, timeout=ARK_IMAGE_TIMEOUT_SECONDS) as image_response:
                        raw = image_response.read(20 * 1024 * 1024 + 1)
                except urllib.error.HTTPError as exc:
                    raise UpstreamServiceError(f"生成图片地址下载HTTP {exc.code}", HTTPStatus.BAD_GATEWAY) from exc
            else:
                raise ValueError("图片服务未返回图片内容（既无b64_json也无url）")
        if not raw:
            raise ValueError("生成图片为空")
        if len(raw) > 20 * 1024 * 1024:
            raise ValueError("生成图片超过20MB限制")
        metadata = save_generated_file(group, "迈谱-项目配图.png", "image/png", raw)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise UpstreamServiceError(f"图片生成结果解析失败：{exc}", HTTPStatus.BAD_GATEWAY) from exc
    return metadata, round((time.perf_counter() - started) * 1000)


class Handler(SimpleHTTPRequestHandler):
    server_version = "Maple"

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

    def secure_cookie_enabled(self) -> bool:
        if os.environ.get("MPT_COOKIE_SECURE", "0") != "1":
            return False
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        return host not in {"127.0.0.1", "localhost", "::1"}

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
            hint_ai_configured = bool(os.environ.get("DEEPSEEK_API_KEY"))
            free_ai_configured = bool(os.environ.get("ARK_API_KEY"))
            self.json_response({
                "status": "ok",
                "database": DB_PATH.exists(),
                "initialized": is_initialized(),
                "aiConfigured": hint_ai_configured or free_ai_configured,
                "hintAiConfigured": hint_ai_configured,
                "freeAiConfigured": free_ai_configured,
            })
            return
        if route.path == "/api/system":
            self.json_response({"initialized": is_initialized(), "groupCount": group_count()})
            return
        if route.path == "/api/session":
            session = self.current_session()
            self.json_response({"initialized": is_initialized(), "groupCount": group_count(), "authenticated": bool(session), **(session or {})})
            return
        if route.path == "/api/broadcast":
            session = self.require_session()
            if session:
                self.json_response(broadcast_for_session(session))
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
                "security": {
                    "secureCookie": self.secure_cookie_enabled(),
                    "secureCookieConfigured": os.environ.get("MPT_COOKIE_SECURE", "0") == "1",
                    "sessionSeconds": SESSION_SECONDS,
                },
                "ai": {
                    "configured": bool(os.environ.get("DEEPSEEK_API_KEY")),
                    "model": DEEPSEEK_MODEL,
                    "freeAiConfigured": bool(os.environ.get("ARK_API_KEY")),
                    "freeAiTextModel": ARK_TEXT_MODEL,
                    "freeAiImageModel": ARK_IMAGE_MODEL,
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
            session = self.require_session("teacher")
            if not session:
                return
            if not session.get("isAdmin"):
                self.json_response({"error": "只有管理员教师可以管理账号"}, HTTPStatus.FORBIDDEN)
                return
            with database() as db:
                teacher = db.execute("SELECT username FROM teachers WHERE id=?", (session.get("teacherId") or 1,)).fetchone()
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
            session = self.require_session()
            if not session:
                return
            with database() as db:
                if session["role"] == "student":
                    rows = db.execute(
                        "SELECT id,original_name,mime,size,uploaded_at,uploaded_by,extracted_text<>'' AS indexed "
                        "FROM files WHERE group_no=? OR uploaded_by='teacher' ORDER BY uploaded_at DESC",
                        (session["groupId"],),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT id,original_name,mime,size,uploaded_at,uploaded_by,extracted_text<>'' AS indexed "
                        "FROM files ORDER BY uploaded_at DESC"
                    ).fetchall()
            self.json_response({"files": [dict(row) for row in rows]})
            return
        if route.path.startswith("/api/ai/files/"):
            session = self.require_session()
            if not session:
                return
            file_id = route.path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[A-Za-z0-9]+", file_id):
                self.json_response({"error": "文件标识无效"}, HTTPStatus.BAD_REQUEST)
                return
            with database() as db:
                if session["role"] == "student":
                    row = db.execute(
                        "SELECT original_name,stored_name,mime,size FROM files "
                        "WHERE id=? AND uploaded_by='student-ai' AND group_no=?",
                        (file_id, session["groupId"]),
                    ).fetchone()
                else:
                    row = db.execute(
                        "SELECT original_name,stored_name,mime,size FROM files WHERE id=? AND uploaded_by='student-ai'",
                        (file_id,),
                    ).fetchone()
            target = UPLOAD_DIR / row["stored_name"] if row else None
            if not row or not target.is_file() or target.resolve().parent != UPLOAD_DIR.resolve():
                self.json_response({"error": "文件不存在"}, HTTPStatus.NOT_FOUND)
                return
            raw = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", row["mime"])
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(row['original_name'])}")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
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
            elif route == "/api/teacher/register":
                self.register_teacher(self.read_json())
            elif route == "/api/login":
                self.login(self.read_json())
            elif route == "/api/admin/recover":
                self.recover_teacher(self.read_json())
            elif route == "/api/admin/factory-reset":
                session = self.require_session("teacher")
                if session and session.get("isAdmin"):
                    self.factory_reset(self.read_json(), session)
                elif session:
                    self.json_response({"error": "只有管理员教师可以恢复出厂数据"}, HTTPStatus.FORBIDDEN)
            elif route == "/api/admin/access":
                session = self.require_session("teacher")
                if session and session.get("isAdmin"):
                    self.update_access(self.read_json(), session)
                elif session:
                    self.json_response({"error": "只有管理员教师可以管理账号"}, HTTPStatus.FORBIDDEN)
            elif route == "/api/groups/configure":
                session = self.require_session("teacher")
                if session:
                    self.configure_groups(self.read_json(), session)
            elif route == "/api/broadcast":
                session = self.require_session("teacher")
                if session:
                    body = self.read_json()
                    content = body.get("broadcast")
                    kind = body.get("broadcastType", "教师广播")
                    if not isinstance(content, str) or len(content) > 2000:
                        raise ValueError("广播内容须为文本，最多2000字")
                    if kind not in ("教师广播", "统一答疑", "阶段提醒", "时间提醒", "成果要求"):
                        raise ValueError("广播类型无效")
                    message = {"broadcast": content.strip(), "broadcastType": kind, "broadcastId": secrets.token_hex(12), "seenGroups": []}
                    state_put("broadcast", message)
                    record_event("teacher", None, "broadcast" if message["broadcast"] else "broadcast_clear", None, message["broadcast"])
                    self.json_response(broadcast_for_session(session))
            elif route == "/api/broadcast/ack":
                session = self.require_session("student")
                if session:
                    body = self.read_json()
                    self.json_response(acknowledge_broadcast(int(session["groupId"]), str(body.get("broadcastId", ""))))
            elif route == "/api/group/members":
                session = self.require_session()
                if session:
                    self.update_group_members(self.read_json(), session)
            elif route == "/api/roles/rotate":
                session = self.require_session("teacher")
                if session:
                    self.rotate_roles(self.read_json(), session)
            elif route == "/api/logout":
                self.logout()
            elif route == "/api/hints":
                session = self.require_session("student")
                if session:
                    self.hint(session, self.read_json())
            elif route == "/api/ai/chat":
                session = self.require_session("student")
                if session:
                    self.free_ai_chat(session, self.read_json())
            elif route == "/api/ai/chat/stream":
                session = self.require_session("student")
                if session:
                    self.free_ai_chat_stream(session, self.read_json())
            elif route == "/api/files":
                session = self.require_session("teacher")
                if session:
                    self.upload(session, self.read_json(MAX_UPLOAD_BYTES * 2))
            elif route == "/api/escalations":
                session = self.require_session("student")
                if session:
                    body = self.read_json()
                    self.hint(session, {"action": "teacher", "issue": str(body.get("reason", "需要教师介入"))[:300], "type": "教师求助"})
            elif route == "/api/state/reset":
                if self.require_session("teacher"):
                    with database() as db:
                        db.execute("DELETE FROM states")
                        db.execute("DELETE FROM events")
                        db.execute("DELETE FROM ai_calls")
                    self.json_response({"ok": True})
            else:
                self.json_response({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except UpstreamServiceError as exc:
            self.json_response({"error": str(exc)}, exc.status)
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
        if session["role"] == "student":
            if "members" in incoming and incoming["members"] != group_state.get("members", []):
                self.json_response({"error": "成员名单请在成员与角色页面提交"}, HTTPStatus.CONFLICT)
                return
            if "membersLocked" in incoming and bool(incoming["membersLocked"]) != bool(group_state.get("membersLocked", False)):
                self.json_response({"error": "成员名单锁定状态只能通过正式提交或教师授权修改"}, HTTPStatus.CONFLICT)
                return
            current_roles = roles_for_members(group_state.get("roles"), len(group_state.get("members", [])))
            incoming_roles = incoming.get("roles", current_roles)
            try:
                current_round = int(group_state.get("roleRound", 1))
                incoming_round = int(incoming.get("roleRound", current_round))
            except (TypeError, ValueError):
                self.json_response({"error": "角色轮次必须是整数"}, HTTPStatus.BAD_REQUEST)
                return
            if incoming_roles != current_roles or incoming_round != current_round:
                self.json_response({"error": "角色由教师统一轮换，请刷新获取最新分工"}, HTTPStatus.CONFLICT)
                return
        candidate = dict(group_state)
        candidate.update({key: incoming[key] for key in GROUP_KEYS if key in incoming})
        requested = incoming.get("stageSubmitted", {}) if isinstance(incoming.get("stageSubmitted"), dict) else {}
        saved_submissions = group_state.get("stageSubmitted", {}) if isinstance(group_state.get("stageSubmitted"), dict) else {}
        valid_submissions = validated_stage_submissions(candidate)
        for number in range(1, 6):
            wants_submit = bool(requested.get(str(number), requested.get(number, False)))
            was_submitted = bool(saved_submissions.get(str(number), saved_submissions.get(number, False)))
            if wants_submit and not was_submitted and not valid_submissions[number]:
                self.json_response({"error": f"阶段{number}任务尚未全部完成，不能提交"}, HTTPStatus.CONFLICT)
                return
        incoming["stageSubmitted"] = valid_submissions
        if session["role"] == "teacher" and "hintPolicy" in incoming:
            raw_policy = incoming["hintPolicy"]
            if not isinstance(raw_policy, dict):
                self.json_response({"error": "分层提示额度格式无效"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                checked_policy = {level: int(raw_policy[level]) for level in HINT_POLICY_DEFAULT}
            except (KeyError, TypeError, ValueError):
                self.json_response({"error": "请完整填写L1—L4额度"}, HTTPStatus.BAD_REQUEST)
                return
            if any(not 1 <= value <= 50 for value in checked_policy.values()):
                self.json_response({"error": "每层额度应为1—50次"}, HTTPStatus.BAD_REQUEST)
                return
            if not checked_policy["L1"] > checked_policy["L2"] > checked_policy["L3"] > checked_policy["L4"]:
                self.json_response({"error": "额度必须按L1、L2、L3、L4逐级递减"}, HTTPStatus.BAD_REQUEST)
                return
            incoming["hintPolicy"] = checked_policy
        group_state.update({key: incoming[key] for key in GROUP_KEYS if key in incoming})
        group_state["groupId"] = group
        state_put(f"group:{group}", group_state, preserve_roles=True)
        if session["role"] == "teacher":
            class_state = state_get("class")
            class_state.update({key: incoming[key] for key in CLASS_KEYS - {"groups", "queue", "common"} if key in incoming})
            resolved_grade = class_code_grade() or grade_from_text(class_state.get("className", ""))
            if resolved_grade:
                class_state["classGrade"] = resolved_grade
            else:
                class_state.pop("classGrade", None)
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
        teacher_id = None
        login_grade = None
        with database() as db:
            if role == "teacher":
                row = db.execute("SELECT id,username,password_hash FROM teachers WHERE username=?", (str(body.get("username", "")).strip(),)).fetchone()
                valid = row and hmac.compare_digest(str(body.get("username", "")), row["username"]) and password_ok(str(body.get("password", "")), row["password_hash"])
                teacher_id = row["id"] if valid else None
            elif role == "student":
                try:
                    group_no = int(body.get("groupId"))
                except (TypeError, ValueError):
                    group_no = 0
                class_hash = db.execute("SELECT value FROM settings WHERE key='class_code_hash'").fetchone()
                group_row = db.execute("SELECT access_hash FROM groups WHERE group_no=?", (group_no,)).fetchone()
                supplied_class_code = clean_code(str(body.get("classCode", "")))
                valid = bool(class_hash and group_row and password_ok(supplied_class_code, class_hash["value"]) and password_ok(clean_code(str(body.get("groupCode", ""))), group_row["access_hash"]))
                login_grade = grade_from_text(supplied_class_code) if valid else None
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
            db.execute("INSERT INTO sessions(token_hash,role,teacher_id,group_no,expires_at,created_at) VALUES(?,?,?,?,?,?)", (token_hash(token), role, teacher_id, group_no, expires, utc_now()))
        if role == "student" and login_grade:
            persist_class_grade(f"{login_grade}年级")
        with LOGIN_LOCK:
            LOGIN_FAILURES.pop(client, None)
        secure = "; Secure" if self.secure_cookie_enabled() else ""
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
            teacher = db.execute("SELECT id,username FROM teachers WHERE username=?", (username,)).fetchone()
            recovery = db.execute("SELECT value FROM settings WHERE key='admin_recovery_hash'").fetchone()
            valid = bool(teacher and recovery and hmac.compare_digest(username, teacher["username"]) and password_ok(recovery_code, recovery["value"]))
            if not valid:
                with LOGIN_LOCK:
                    LOGIN_FAILURES.setdefault(client, []).append(time.time())
                time.sleep(0.35)
                self.json_response({"error": "管理员账号或恢复码不正确"}, HTTPStatus.UNAUTHORIZED)
                return
            db.execute("UPDATE teachers SET password_hash=? WHERE id=?", (password_hash(new_password), teacher["id"]))
            db.execute("DELETE FROM sessions")
        with LOGIN_LOCK:
            LOGIN_FAILURES.pop(client, None)
        record_event("system", None, "teacher_password_recovered", None, username)
        self.json_response({"ok": True}, cookie=f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")

    def update_access(self, body: dict, session: dict) -> None:
        action = str(body.get("action", ""))
        current_password = str(body.get("currentPassword", ""))
        updated_class_code = None
        with database() as db:
            teacher = db.execute(
                "SELECT username,password_hash FROM teachers WHERE id=?",
                (session.get("teacherId") or 1,),
            ).fetchone()
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
                    "UPDATE teachers SET username=?,password_hash=? WHERE id=?",
                    (username, password_hash(new_password) if new_password else teacher["password_hash"], session.get("teacherId") or 1),
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
                updated_class_code = code
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
            elif action == "teacher_invite":
                invite_code = secrets.token_urlsafe(12).upper()
                db.execute(
                    "INSERT INTO settings(key,value) VALUES('teacher_invite_hash',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (password_hash(invite_code),),
                )
                detail = "teacher_invite"
            else:
                raise ValueError("不支持的管理员操作")
        if updated_class_code is not None:
            persist_class_grade(updated_class_code)
        record_event("teacher", None, f"admin_{action}", None, detail)
        self.json_response({"ok": True, "logoutRole": logout_role, **({"inviteCode": invite_code} if action == "teacher_invite" else {})})

    def factory_reset(self, body: dict, session: dict) -> None:
        if str(body.get("confirmation", "")).strip().upper() != "RESET":
            raise ValueError("请输入 RESET 确认恢复出厂数据")
        recovery_code = str(body.get("recoveryCode", "")).strip()
        with database() as db:
            recovery = db.execute("SELECT value FROM settings WHERE key='admin_recovery_hash'").fetchone()
            if not recovery or not password_ok(recovery_code, recovery["value"]):
                time.sleep(0.35)
                self.json_response({"error": "管理员恢复码不正确"}, HTTPStatus.UNAUTHORIZED)
                return
            db.execute("DELETE FROM states")
            db.execute("DELETE FROM events")
            db.execute("DELETE FROM ai_calls")
            db.execute("DELETE FROM files")
            db.execute("DELETE FROM teachers WHERE is_admin=0")
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM settings WHERE key='teacher_invite_hash'")
        for path in UPLOAD_DIR.iterdir():
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
        record_event("system", None, "factory_reset", None, "课堂数据已清空，管理员账号与访问规则保留")
        self.json_response({"ok": True, "preserved": ["admin", "recovery_code", "class_and_group_access"]})

    def configure_groups(self, body: dict, session: dict) -> None:
        try:
            count = int(body.get("groupCount"))
        except (TypeError, ValueError) as exc:
            raise ValueError("请填写有效的小组数量") from exc
        if not 1 <= count <= 20:
            raise ValueError("小组数量应为1—20组")
        old_count = group_count()
        if count == old_count:
            self.json_response({"ok": True, "groupCount": count, "addedCodes": []})
            return
        prefix = group_code_prefix() if count > old_count else ""
        if count > old_count and len(prefix) < 4:
            raise ValueError("当前班级缺少已保存的小组访问码前缀，无法安全扩容；请先配置 MPT_GROUP_CODE_PREFIX 后重启服务")
        added_codes = []
        with database() as db:
            if count > old_count:
                for number in range(old_count + 1, count + 1):
                    code = f"{prefix}{number:02d}"
                    db.execute("INSERT INTO groups(group_no,access_hash) VALUES(?,?)", (number, password_hash(code)))
                    added_codes.append({"groupNo": number, "code": code})
            else:
                db.execute("DELETE FROM sessions WHERE role='student' AND group_no>?", (count,))
                db.execute("DELETE FROM groups WHERE group_no>?", (count,))
                db.execute("DELETE FROM states WHERE scope LIKE 'group:%' AND CAST(substr(scope,7) AS INTEGER)>?", (count,))
                db.execute("DELETE FROM events WHERE group_no>?", (count,))
                db.execute("DELETE FROM ai_calls WHERE group_no>?", (count,))
            db.execute("UPDATE settings SET value=? WHERE key='group_count'", (str(count),))
        class_state = state_get("class")
        class_state["groupCount"] = count
        class_state["groups"] = dynamic_group_summaries(class_state)
        evaluations = class_state.get("evaluations", {})
        if isinstance(evaluations, dict):
            class_state["evaluations"] = {key: value for key, value in evaluations.items() if str(key).isdigit() and int(key) <= count}
        state_put("class", class_state)
        record_event("teacher", None, "group_count_changed", None, f"{old_count}->{count};teacher={session.get('username', '')}")
        self.json_response({"ok": True, "groupCount": count, "addedCodes": added_codes})

    def rotate_roles(self, body: dict, session: dict) -> None:
        expected = body.get("rounds")
        if not isinstance(expected, dict) or not expected:
            raise ValueError("请刷新课堂页面后发起轮换")
        results = []
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            groups = [row[0] for row in db.execute("SELECT group_no FROM groups")]
            for group in groups:
                row = db.execute("SELECT data FROM states WHERE scope=?", (f"group:{group}",)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("membersLocked"):
                    continue
                current = int(state.get("roleRound", 1))
                if expected.get(str(group)) != current:
                    self.json_response({"error": "分工已变化，请刷新后核对；本次未重复轮换"}, 409)
                    return
                results.append((group, state))
            if not results:
                raise ValueError("尚无已锁定成员的小组")
            for group, state in results:
                remember_roles(state)
                roles = roles_for_members(state.get("roles"), len(state["members"]))
                choices = [[role for role in roles if role not in state["roleHistory"][name]] for name in state["members"]]
                for options in choices:
                    secrets.SystemRandom().shuffle(options)
                assigned = {}
                def match(member, seen):
                    for role in choices[member]:
                        if role in seen:
                            continue
                        seen.add(role)
                        if role not in assigned or match(assigned[role], seen):
                            assigned[role] = member
                            return True
                    return False
                if not all(match(i, set()) for i in range(len(roles))):
                    raise ValueError(f"第{group}组已无全员不重复的角色分配，本次所有小组均未轮换")
                state["roles"] = [next(role for role, member in assigned.items() if member == i) for i in range(len(roles))]
                state["roleRound"] = int(state.get("roleRound", 1)) + 1
                remember_roles(state)
            for group, state in results:
                db.execute("UPDATE states SET data=?,updated_at=? WHERE scope=?", (json.dumps(state, ensure_ascii=False), utc_now(), f"group:{group}"))
        record_event("teacher", None, "role_rotate", None, f"groups={[group for group, _ in results]}")
        self.json_response({"ok": True, "groups": [group for group, _ in results]})

    def update_group_members(self, body: dict, session: dict) -> None:
        action = str(body.get("action", "submit"))
        count = group_count()
        try:
            requested_group = int(body.get("groupId") or session.get("groupId") or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("请选择有效小组") from exc
        group = int(session.get("groupId")) if session["role"] == "student" else requested_group
        if not 1 <= group <= count:
            raise ValueError("小组不存在")
        group_state = state_get(f"group:{group}")
        if action == "unlock":
            if session["role"] != "teacher":
                self.json_response({"error": "只有教师可以开放成员信息修改"}, HTTPStatus.FORBIDDEN)
                return
            group_state["membersLocked"] = False
            state_put(f"group:{group}", group_state)
            record_event("teacher", group, "members_unlocked", 1, str(session.get("username", "")))
            self.json_response({"ok": True, "members": group_state.get("members", []), "membersLocked": False})
            return
        if action not in {"save", "submit"}:
            raise ValueError("不支持的成员操作")
        if session["role"] == "student" and group_state.get("membersLocked"):
            self.json_response({"error": "成员信息已提交锁定，请联系教师开放修改"}, HTTPStatus.CONFLICT)
            return

        student_lock = action == "submit" and session["role"] == "student"
        if student_lock:
            saved_members = group_state.get("members", [])
            if not saved_members:
                self.json_response({"error": "请先保存成员与角色，再确认锁定"}, HTTPStatus.CONFLICT)
                return
            members = validated_members(saved_members)
            if "members" in body and validated_members(body.get("members")) != members:
                self.json_response({"error": "成员信息已修改，请先重新保存"}, HTTPStatus.CONFLICT)
                return
        else:
            members = validated_members(body.get("members"))
            old_members = group_state.get("members", [])
            remember_roles(group_state)
            if members != old_members:
                group_state["firstThoughts"] = {}
            group_state["members"] = members
            if len(members) != len(old_members):
                group_state["roles"] = ROLE_NAMES[:len(members)]
                group_state["roleRound"] = 1
            else:
                group_state["roles"] = roles_for_members(group_state.get("roles"), len(members))

        remember_roles(group_state)
        group_state["membersLocked"] = action == "submit"
        state_put(f"group:{group}", group_state)
        self.sync_group_summary(group, group_state)
        if action == "save":
            event_type = "members_saved" if session["role"] == "student" else "members_teacher_saved"
        else:
            event_type = "members_submitted" if session["role"] == "student" else "members_teacher_updated"
        record_event(session["role"], group, event_type, 1, f"count={len(members)}")
        self.json_response({"ok": True, "members": members, "roles": group_state["roles"], "membersLocked": group_state["membersLocked"]})

    def register_teacher(self, body: dict) -> None:
        if not is_initialized():
            self.json_response({"error": "平台尚未初始化"}, HTTPStatus.CONFLICT)
            return
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        invite_code = str(body.get("inviteCode", "")).strip()
        if len(username) < 3 or len(username) > 40:
            raise ValueError("教师账号长度应为3—40个字符")
        if len(password) < 12:
            raise ValueError("教师密码至少需要12个字符")
        if not invite_code:
            raise ValueError("请输入教师注册码")
        with database() as db:
            invite = db.execute("SELECT value FROM settings WHERE key='teacher_invite_hash'").fetchone()
            if not invite or not password_ok(invite_code, invite["value"]):
                time.sleep(0.35)
                self.json_response({"error": "教师注册码无效或已使用"}, HTTPStatus.FORBIDDEN)
                return
            try:
                db.execute("INSERT INTO teachers(username,password_hash,created_at,is_admin) VALUES(?,?,?,0)", (username, password_hash(password), utc_now()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("教师账号已存在") from exc
            db.execute("DELETE FROM settings WHERE key='teacher_invite_hash'")
        record_event("system", None, "teacher_registered", None, username)
        self.json_response({"ok": True, "username": username}, HTTPStatus.CREATED)

    def logout(self) -> None:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        if COOKIE_NAME in cookie:
            with database() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(cookie[COOKIE_NAME].value),))
        self.json_response({"ok": True}, cookie=f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")

    def prepare_free_ai_chat(self, session: dict, body: dict) -> dict:
        group = int(session["groupId"])
        state = merged_state(group)
        enabled, quota = free_ai_settings(state)
        if not enabled or not state.get("aiOpen", True):
            raise UpstreamServiceError("自由工作区已由教师暂停", HTTPStatus.CONFLICT)
        prompt = str(body.get("prompt", "")).strip()[:2400]
        if not prompt:
            raise ValueError("请输入要讨论的内容")
        group_state = state_get(f"group:{group}")
        workspace = free_ai_state(group_state)
        if workspace["usage"] >= quota:
            raise UpstreamServiceError("自由工作区本组额度已用完，请联系教师调整", HTTPStatus.CONFLICT)
        requested_id = str(body.get("conversationId", "")).strip()
        create_new = bool(body.get("newConversation"))
        conversation = None if create_new else next(
            (item for item in workspace["conversations"] if item.get("id") == requested_id), None,
        )
        if conversation is None and not create_new and not requested_id and workspace["activeConversationId"]:
            conversation = next(
                (item for item in workspace["conversations"] if item.get("id") == workspace["activeConversationId"]), None,
            )
        ensure_math_question(prompt, conversation.get("messages", []) if conversation else [])
        if conversation is None:
            conversation = {
                "id": secrets.token_urlsafe(8), "title": prompt[:20],
                "createdAt": utc_now(), "updatedAt": utc_now(), "messages": [],
            }
            workspace["conversations"].append(conversation)
        return {
            "group": group, "state": state, "group_state": group_state,
            "workspace": workspace, "conversation": conversation,
            "prompt": prompt, "intent": free_ai_intent(prompt),
            "quota": quota, "timestamp": datetime.now().strftime("%H:%M"),
        }

    def free_ai_failure(self, context: dict, model: str, latency: int, status: str) -> UpstreamServiceError:
        with database() as db:
            db.execute(
                "INSERT INTO ai_calls(ts,group_no,level,model,latency_ms,status,detail) VALUES(?,?,?,?,?,?,?)",
                (utc_now(), context["group"], "FREE_IMAGE" if context["intent"] == "image" else "FREE_TEXT",
                 model, latency, "failed", status[:1000]),
            )
        if status == "not_configured":
            return UpstreamServiceError("豆包文本服务未配置 ARK_API_KEY", HTTPStatus.SERVICE_UNAVAILABLE)
        match = re.match(r"HTTP (\d+):?\s*(.*)", status)
        if match:
            upstream_status = int(match.group(1))
            detail = match.group(2) or "方舟未返回错误详情"
            response_status = upstream_status if 400 <= upstream_status < 500 else HTTPStatus.BAD_GATEWAY
            return UpstreamServiceError(
                f"豆包文本接口HTTP {upstream_status}: {detail}", int(response_status),
            )
        return UpstreamServiceError(f"豆包文本接口请求失败：{status}", HTTPStatus.BAD_GATEWAY)

    def commit_free_ai_chat(self, context: dict, answer: str, model: str, status: str,
                            latency: int, file_metadata: dict | None = None) -> dict:
        conversation = context["conversation"]
        workspace = context["workspace"]
        assistant_message = {
            "role": "assistant", "kind": "image" if file_metadata else "text", "content": answer,
            "time": context["timestamp"], "source": "ark",
        }
        if file_metadata:
            assistant_message["file"] = file_metadata
        conversation["messages"].extend([
            {"role": "user", "kind": "text", "content": context["prompt"], "time": context["timestamp"]},
            assistant_message,
        ])
        conversation["messages"] = conversation["messages"][-FREE_AI_HISTORY_LIMIT:]
        conversation["updatedAt"] = utc_now()
        workspace["conversations"] = [
            item for item in workspace["conversations"] if item.get("id") != conversation["id"]
        ] + [conversation]
        workspace["conversations"] = workspace["conversations"][-FREE_AI_CONVERSATION_LIMIT:]
        workspace["activeConversationId"] = conversation["id"]
        workspace["usage"] += 1
        context["group_state"]["freeAi"] = workspace
        state_put(f"group:{context['group']}", context["group_state"])
        intent = context["intent"]
        with database() as db:
            db.execute(
                "INSERT INTO ai_calls(ts,group_no,level,model,latency_ms,status,detail) VALUES(?,?,?,?,?,?,?)",
                (utc_now(), context["group"], "FREE_IMAGE" if intent == "image" else "FREE_TEXT",
                 model, latency, "returned", ("image_saved" if intent == "image" else status)[:1000]),
            )
        record_event("student", context["group"], "free_ai_chat", context["state"].get("currentStage"), f"{intent}|{status}")
        response = {
            "ok": True, "answer": answer, "intent": intent, "source": "ark", "model": model,
            "conversationId": conversation["id"], "usage": workspace["usage"], "quota": context["quota"],
        }
        if file_metadata:
            response["file"] = file_metadata
        return response

    def free_ai_chat(self, session: dict, body: dict) -> None:
        context = self.prepare_free_ai_chat(session, body)
        if context["intent"] == "image":
            file_metadata, latency = ark_generate_image(context["group"], context["prompt"])
            response = self.commit_free_ai_chat(
                context, "已根据你的描述生成图片。你可以继续在本对话中提出修改方向，或讨论图片如何用于项目。",
                ARK_IMAGE_MODEL, "ok", latency, file_metadata,
            )
        else:
            answer, latency, status, model = ark_free_chat(
                context["state"], context["group"], context["prompt"], context["conversation"]["messages"],
            )
            if not answer:
                raise self.free_ai_failure(context, model, latency, status)
            response = self.commit_free_ai_chat(context, answer, model, status, latency)
        self.json_response(response)

    def sse_start(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def sse_event(self, payload: dict) -> None:
        raw = f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()
        self.wfile.write(raw)
        self.wfile.flush()

    def free_ai_chat_stream(self, session: dict, body: dict) -> None:
        context = self.prepare_free_ai_chat(session, body)
        self.sse_start()
        self.sse_event({"type": "start", "conversationId": context["conversation"]["id"], "intent": context["intent"]})
        try:
            if context["intent"] == "image":
                self.sse_event({"type": "status", "message": "正在生成图片…"})
                file_metadata, latency = ark_generate_image(context["group"], context["prompt"])
                answer = "已根据你的描述生成图片。你可以继续在本对话中提出修改方向，或讨论图片如何用于项目。"
                response = self.commit_free_ai_chat(context, answer, ARK_IMAGE_MODEL, "ok", latency, file_metadata)
            else:
                answer, latency, status, model = ark_free_chat_stream(
                    context["state"], context["group"], context["prompt"],
                    context["conversation"]["messages"],
                    lambda delta: self.sse_event({"type": "delta", "content": delta}),
                )
                if not answer:
                    raise self.free_ai_failure(context, model, latency, status)
                response = self.commit_free_ai_chat(context, answer, model, status, latency)
            self.sse_event({"type": "done", **response})
        except UpstreamServiceError as exc:
            self.sse_event({"type": "error", "message": str(exc), "status": exc.status})
        except (AttributeError, ValueError, TypeError, KeyError) as exc:
            self.sse_event({"type": "error", "message": f"豆包文本接口响应解析失败：{exc}", "status": HTTPStatus.BAD_GATEWAY})
        except (BrokenPipeError, ConnectionResetError):
            return


    def hint(self, session: dict, body: dict) -> None:
        group = int(session["groupId"])
        state = merged_state(group)
        group_state = state_get(f"group:{group}")
        policy = hint_policy(state)
        usage = hint_usage(group_state)
        threads = group_state.get("helpThreads", [])
        if not isinstance(threads, list):
            threads = []
        action = str(body.get("action", "ask")).strip().lower()
        done = str(body.get("done", "")).strip()[:1600]
        new_thread = body.get("newThread") is True
        requested_thread_id = str(body.get("threadId", "")).strip()
        if new_thread and requested_thread_id:
            raise ValueError("新建主题不能同时指定已有主题")
        thread = None if new_thread else active_help_thread(group_state)
        if requested_thread_id:
            thread = next(
                (item for item in threads if isinstance(item, dict) and item.get("id") == requested_thread_id
                 and item.get("status") in {"active", "resolved", "teacher"}),
                None,
            )
            if not thread:
                raise ValueError("求助主题不存在或已被清理")
            group_state["activeHelpThreadId"] = thread["id"]
        raw_issue_type = str(body.get("type", "")).strip()
        if thread:
            issue_type = raw_issue_type[:80] or str(thread.get("type", "其他"))[:80]
        else:
            issue_type = raw_issue_type[:80] or "其他"
        issue = str(body.get("issue", body.get("reason", ""))).strip()[:1200]
        if not issue and thread:
            issue = str(thread.get("issue", "")).strip()[:1200]

        if action == "resolve":
            if not thread:
                raise ValueError("当前没有进行中的求助主题")
            thread["status"] = "resolved"
            thread["resolvedAt"] = utc_now()
            group_state["activeHelpThreadId"] = ""
            group_state["helpThreads"] = threads[-30:]
            state_put(f"group:{group}", group_state)
            record_event("student", group, "help_resolved", state.get("currentStage"), str(thread.get("id")))
            self.json_response({"ok": True, "message": "当前问题已关闭，可以从L1开始新的求助主题"})
            return

        if action == "advance":
            if not thread or thread.get("status") not in {"active", "resolved"}:
                raise ValueError("请先建立并使用当前求助主题")
            if not state.get("aiOpen", True):
                self.json_response({"error": "AI已由教师暂停，可直接请求教师介入"}, HTTPStatus.CONFLICT)
                return
            if not done:
                raise ValueError("请填写看完当前层提示后的新尝试，再进入下一层")
            if thread.get("status") == "resolved":
                thread["status"] = "active"
                thread.pop("resolvedAt", None)
                group_state["activeHelpThreadId"] = thread["id"]
            messages = thread.get("messages", []) if isinstance(thread.get("messages"), list) else []
            if not messages:
                raise ValueError("当前层级至少获得一次支架后才能升级")
            level_no = int(thread.get("currentLevel", 1))
            if level_no >= 3:
                raise ValueError("L3之后请提交教师介入")
            next_level_no = level_no + 1
            next_level = f"L{next_level_no}"
            if usage[next_level] >= policy[next_level]:
                raise ValueError(f"{next_level}额度已用完，请先联系教师调整")
            attempt = done
            ensure_math_question(f"{attempt}\n{issue}", messages)
            thread["type"] = issue_type
            thread["currentLevel"] = next_level_no
            thread.setdefault("transitions", []).append({"time": utc_now(), "from": f"L{level_no}", "to": next_level, "attempt": attempt})
            reply, latency, status = deepseek_hint(
                state, next_level, attempt, issue_type, issue, messages,
                bool(body.get("checkedWork")),
            )
            result = "returned" if reply else "fallback"
            if not reply:
                reply = static_hint(state.get("teacherConfig", {}), next_level_no)
            entry = {
                "time": datetime.now().strftime("%H:%M"), "ts": utc_now(), "threadId": thread["id"],
                "level": next_level, "type": issue_type,
                "prompt": issue, "question": issue,
                "done": attempt, "reply": reply, "source": "deepseek" if result == "returned" else "teacher_fallback",
            }
            messages.append(entry)
            history = group_state.get("helpHistory", [])
            if not isinstance(history, list):
                history = []
            history.append(entry)
            thread["messages"] = messages[-40:]
            thread["updatedAt"] = utc_now()
            if result == "returned":
                usage[next_level] += 1
            with database() as db:
                db.execute(
                    "INSERT INTO ai_calls(ts,group_no,level,model,latency_ms,status,detail) VALUES(?,?,?,?,?,?,?)",
                    (utc_now(), group, next_level, DEEPSEEK_MODEL, latency, result, status[:200]),
                )
            group_state["helpHistory"] = history[-120:]
            group_state["helpThreads"] = threads[-30:]
            group_state["hintUsage"] = usage
            group_state["hintUsed"] = sum(usage.values())
            state_put(f"group:{group}", group_state)
            self.sync_group_summary(group, group_state, issue_type)
            record_event("student", group, "hint_level_advanced", state.get("currentStage"), f"{level_no}->{next_level_no}")
            self.json_response({
                "ok": True, "reply": reply, "level": next_level, "source": "deepseek" if result == "returned" else "teacher_fallback",
                "currentLevel": next_level_no, "hintUsage": usage, "hintPolicy": policy, "threadId": thread["id"], "latencyMs": latency,
            })
            return

        if action == "teacher":
            if usage["L4"] >= policy["L4"]:
                self.json_response({"error": "本项目教师求助额度已用完，请当面联系教师"}, HTTPStatus.CONFLICT)
                return
            if not thread:
                thread = {
                    "id": secrets.token_hex(8), "status": "active", "currentLevel": 1,
                    "type": issue_type, "issue": issue or "需要教师介入", "createdAt": utc_now(), "updatedAt": utc_now(),
                    "messages": [], "transitions": [],
                }
                threads.append(thread)
                group_state["activeHelpThreadId"] = thread["id"]
            elif thread.get("status") == "resolved":
                thread["status"] = "active"
                thread.pop("resolvedAt", None)
                group_state["activeHelpThreadId"] = thread["id"]
            thread["type"] = issue_type
            created = self.escalate(group, state, "学生请求教师介入", issue or str(thread.get("issue", "需要教师介入")), str(thread.get("id", "")))
            if created:
                usage["L4"] += 1
            thread["status"] = "teacher"
            thread["currentLevel"] = 4
            thread["updatedAt"] = utc_now()
            group_state["activeHelpThreadId"] = thread["id"]
            group_state["hintUsage"] = usage
            group_state["hintUsed"] = sum(usage.values())
            group_state["helpThreads"] = threads[-30:]
            state_put(f"group:{group}", group_state)
            self.sync_group_summary(group, group_state, issue_type)
            self.json_response({"ok": True, "created": created, "message": "已提交教师介入", "threadId": thread["id"], "hintUsage": usage, "hintPolicy": policy})
            return

        if action != "ask":
            raise ValueError("不支持的求助操作")
        if not state.get("aiOpen", True):
            self.json_response({"error": "AI已由教师暂停，可直接请求教师介入"}, HTTPStatus.CONFLICT)
            return
        if not done or (not issue and not thread):
            raise ValueError("请先填写已有进展和具体卡点")
        existing_messages = thread.get("messages", []) if thread and isinstance(thread.get("messages"), list) else []
        ensure_math_question(f"{done}\n{issue}", existing_messages)
        if not thread:
            thread = {
                "id": secrets.token_hex(8), "status": "active", "currentLevel": 1,
                "type": issue_type, "issue": issue, "createdAt": utc_now(), "updatedAt": utc_now(),
                "messages": [], "transitions": [],
            }
            threads.append(thread)
            group_state["activeHelpThreadId"] = thread["id"]
        if thread.get("status") == "resolved":
            thread["status"] = "active"
            thread.pop("resolvedAt", None)
            group_state["activeHelpThreadId"] = thread["id"]
        if thread.get("status") != "active":
            self.json_response({"error": "当前主题已转教师，请点击“新对话”建立其他问题"}, HTTPStatus.CONFLICT)
            return
        group_state["activeHelpThreadId"] = thread["id"]
        level_no = max(1, min(3, int(thread.get("currentLevel", 1))))
        try:
            requested_level_no = int(body.get("level", level_no))
        except (TypeError, ValueError):
            requested_level_no = level_no
        if not 1 <= requested_level_no <= level_no:
            raise ValueError(f"当前主题最多只能选择已解锁的L1—L{level_no}")
        level_no = requested_level_no
        level = f"L{level_no}"
        if usage[level] >= policy[level]:
            self.json_response({"error": f"{level}额度已用完，请选择进入下一层、关闭问题或请求教师", "currentLevel": level_no}, HTTPStatus.CONFLICT)
            return
        thread["type"] = issue_type
        messages = thread.get("messages", []) if isinstance(thread.get("messages"), list) else []
        reply, latency, status = deepseek_hint(
            state, level, done, issue_type, issue, messages,
            bool(body.get("checkedWork")),
        )
        if not reply:
            reply = static_hint(state.get("teacherConfig", {}), level_no)
            result = "fallback" if status != "ok" else "rejected"
        else:
            result = "returned"
        with database() as db:
            db.execute("INSERT INTO ai_calls(ts,group_no,level,model,latency_ms,status,detail) VALUES(?,?,?,?,?,?,?)", (utc_now(), group, level, DEEPSEEK_MODEL, latency, result, status[:200]))
        history = group_state.get("helpHistory", [])
        if not isinstance(history, list):
            history = []
        entry = {
            "time": datetime.now().strftime("%H:%M"), "ts": utc_now(), "threadId": thread["id"],
            "level": level, "type": issue_type, "prompt": issue, "question": issue, "done": done,
            "reply": reply, "source": "deepseek" if result == "returned" else "teacher_fallback",
        }
        history.append(entry)
        messages.append(entry)
        thread["messages"] = messages[-40:]
        thread["updatedAt"] = utc_now()
        if result == "returned":
            usage[level] += 1
        group_state["helpHistory"] = history[-120:]
        group_state["helpThreads"] = threads[-30:]
        group_state["hintUsage"] = usage
        group_state["hintUsed"] = sum(usage.values())
        state_put(f"group:{group}", group_state)
        self.sync_group_summary(group, group_state, issue_type)
        record_event("student", group, "hint_return" if result == "returned" else "fallback_hint", state.get("currentStage"), f"{level}|{issue_type}")
        self.json_response({
            "reply": reply, "level": level, "source": "deepseek" if result == "returned" else "teacher_fallback",
            "hintUsage": usage, "hintPolicy": policy, "currentLevel": level_no, "threadId": thread["id"], "latencyMs": latency,
        })

    def escalate(self, group: int, state: dict, reason: str, issue: str, thread_id: str = "") -> bool:
        class_state = state_get("class")
        queue = class_state.get("queue", [])
        if not isinstance(queue, list):
            queue = []
        if thread_id and any(item.get("threadId") == thread_id and not item.get("resolved") for item in queue if isinstance(item, dict)):
            return False
        queue.append({"id": f"Q-{time.time_ns()}", "group": group, "threadId": thread_id, "type": "教师介入", "reason": f"{reason}：{issue}"[:300], "priority": "高", "time": datetime.now().strftime("%H:%M"), "resolved": False})
        class_state["queue"] = queue[-100:]
        state_put("class", class_state)
        record_event("system", group, "teacher_escalate", state.get("currentStage"), reason)
        return True

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
        latest = versions[-1].get("note", "模型版本已保存") if isinstance(versions, list) and versions and isinstance(versions[-1], dict) else "工作区已保存" if _has_value(group_state.get("workspace")) else "尚未保存"
        progress, submitted_count = group_completion(group_state)
        active = progress > 0 or bool(help_total(group_state)) or bool(group_state.get("helpHistory"))
        item.update({
            "id": group, "stage": int(group_state.get("currentStage", class_state.get("currentStage", 1))),
            "help": help_total(group_state), "maxHint": highest_hint_level(group_state),
            "latest": latest, "status": "等待教师" if item.get("teacher") else "已完成" if submitted_count == 5 else "持续推进" if active else "准备中", "progress": progress,
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
    print(f"迈谱 Maple 已启动：http://{browser_host}:{PORT}/")
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
