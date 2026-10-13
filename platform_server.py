"""迈谱 Maple：数学项目式学习服务。"""

from __future__ import annotations

import base64
import csv
import functools
import gzip
import hashlib
import hmac
import http.client
import io
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
import lesson_plans
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
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


def normalize_ark_text_model(model: str | None) -> str:
    configured = (model or "").strip() or "doubao-seed-2-1-turbo-260628"
    aliases = {
        "doubao-seed-2-0-mini-260428": "doubao-seed-2-1-turbo-260628",
        "doubao-seed-2.0-mini": "doubao-seed-2-1-turbo-260628",
        "doubao-seed-2-1-turbo": "doubao-seed-2-1-turbo-260628",
    }
    return aliases.get(configured, configured)


DATA_DIR = Path(os.environ.get("MPT_DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "platform.db"
DEFAULT_KNOWLEDGE_SOURCE_DIR = ROOT.parent / "必须要的教材、教师用书和课标"
KNOWLEDGE_SOURCE_DIR = Path(
    os.environ.get("MPT_KNOWLEDGE_SOURCE_DIR", "").strip() or str(DEFAULT_KNOWLEDGE_SOURCE_DIR)
).resolve()
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
ARK_RESPONSES_URL = os.environ.get("ARK_RESPONSES_URL", "https://ark.cn-beijing.volces.com/api/v3/responses")
ARK_TEXT_MODEL = normalize_ark_text_model(os.environ.get("ARK_TEXT_MODEL"))
ARK_IMAGE_URL = os.environ.get("ARK_IMAGE_URL", "https://ark.cn-beijing.volces.com/api/v3/images/generations")
ARK_IMAGE_MODEL = os.environ.get("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128")
ARK_IMAGE_RESPONSE_FORMAT = os.environ.get("ARK_IMAGE_RESPONSE_FORMAT", "b64_json").strip().lower()
DESMOS_API_KEY = os.environ.get("DESMOS_API_KEY", "").strip()
ARK_TEXT_TIMEOUT_SECONDS = env_seconds("ARK_TEXT_TIMEOUT_SECONDS", 60)
ARK_IMAGE_TIMEOUT_SECONDS = env_seconds("ARK_IMAGE_TIMEOUT_SECONDS", 120)
MATH_FORMAT_RULE = "所有数学表达式必须用 $...$、$$...$$、\\(...\\) 或 \\[...\\] 包裹；不得裸露输出LaTex命令。"
FREE_AI_QUOTA_DEFAULT = 30
FREE_AI_HISTORY_LIMIT = 24
FREE_AI_CONVERSATION_LIMIT = 20
COOKIE_NAME = "mpt_session"
LOGIN_FAILURES: dict[str, list[float]] = {}
LOGIN_LOCK = threading.Lock()
SETUP_TOKEN_DISPLAY = ""
SETUP_TOKEN_FILE = DATA_DIR / "setup-token.txt"
COMPRESSIBLE_STATIC_TYPES = {".html", ".js", ".css", ".json", ".txt", ".md", ".svg"}


@functools.lru_cache(maxsize=64)
def compressed_static(path: str, modified_ns: int, size: int) -> bytes:
    """Cache only recently requested compressed assets; the mtime key invalidates edits."""
    del modified_ns, size
    return gzip.compress(Path(path).read_bytes(), compresslevel=5, mtime=0)

CLASS_KEYS = {
    "project", "className", "currentStage", "stagePaused",
    "aiOpen", "freeAiEnabled", "freeAiQuota", "projectType", "projectHours", "hintQuota",
    "maxHintLevel", "hintPolicy", "broadcast", "teacherConfig", "groups", "queue", "common",
    "evaluations", "audit",
}
GROUP_KEYS = {
    "groupId", "roles", "roleRound", "stageSubmitted", "stageTimes",
    "workspace", "versions", "evidence", "viewpoints", "comparisonAi", "firstThoughts",
    "debate", "product", "events", "questionTree", "graphConfig", "checklist", "preparationChecks", "freeAi", "mathTools",
}
HINT_POLICY_DEFAULT = {"L1": 20, "L2": 10, "L3": 5, "L4": 3}
HINT_LEVEL_NAMES = {1: "定位", 2: "拆解", 3: "局部示范", 4: "教师介入"}
ROLE_NAMES = ["建模者", "质疑者", "记录者", "解释者", "验证者", "资料员", "协调者", "展示者", "计算者", "检查者"]
ALLOWED_UPLOADS = {
    ".pdf", ".docx", ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".webp",
}
TEACHER_KNOWLEDGE_UPLOADS = {".pdf", ".docx", ".md"}


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
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA synchronous=NORMAL")
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
        f"你服务于中国{audience}学生的数学项目式学习小组协作场景。用户提问不需要先证明是数学问题；"
        "项目主题、项目任务书、驱动性问题、任务与子任务、项目计划、材料、实施、作品、展示、评价、反思、修改、优化，"
        "以及小组成员、分工、角色、讨论、沟通、协商、共识、冲突、教师反馈和下一步，均属于服务范围。"
        "回答前判断用户是在问项目流程、协作安排还是数学内容：项目流程和协作问题直接结合数学项目任务书回答；"
        "数学问题给出符合年级的概念、数量关系、计算、图形、统计、函数、建模、验证或推理；混合问题同时说明两者关系。"
        "不要因为问题没有数学术语而拒答，也不要把项目流程问题硬改写成数学题。所有回答都要回到当前数学项目任务书、当前阶段和共享工作区；"
        "信息不足时明确指出需要补充什么。数学解释的表达、术语和步骤应以该年级或更早所学内容为主；确需扩展时先用已有知识解释并标明拓展。"
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
PROJECT_CONTEXT_TERMS = (
    "项目", "项目式学习", "任务书", "主题", "驱动性问题", "核心问题", "任务", "子任务", "探究", "调查", "研究", "实践",
    "数据收集", "方案设计", "项目计划", "实施", "作品", "成果", "展示", "汇报", "评价", "反思", "修改", "优化", "下一步",
    "小组", "团队", "同伴", "同学", "组员", "组长", "成员", "合作", "协作", "分工", "角色", "职责", "共同完成", "交流", "沟通",
    "讨论", "分享", "倾听", "回应", "解释", "补充", "质疑", "协商", "共识", "冲突", "协调", "互助", "互评", "教师", "老师", "学生", "课堂", "学习目标",
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


def has_project_signal(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return any(term in compact for term in PROJECT_CONTEXT_TERMS)


def is_math_question(text: str, history: list[dict] | None = None) -> bool:
    if has_math_signal(text) or has_project_signal(text):
        return True
    clean = re.sub(r"\s+", "", text or "")
    if not MATH_CONTINUATION_RE.fullmatch(clean):
        return False
    recent = " ".join(
        str(item.get("content", item.get("question", item.get("reply", ""))))
        for item in (history or [])[-6:] if isinstance(item, dict)
    )
    return has_math_signal(recent) or has_project_signal(recent)


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
        # WAL is persistent. Setting it once avoids negotiating journal mode on
        # every short request, which is noticeable when a class polls together.
        db.execute("PRAGMA journal_mode=WAL")
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
            CREATE TABLE IF NOT EXISTS knowledge_provider_files (
              local_file_id TEXT PRIMARY KEY, provider_file_id TEXT NOT NULL,
              status TEXT NOT NULL, expires_at INTEGER NOT NULL,
              updated_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
              id TEXT PRIMARY KEY, local_file_id TEXT NOT NULL,
              provider_file_id TEXT NOT NULL DEFAULT '', page_start INTEGER NOT NULL,
              page_end INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
              summary TEXT NOT NULL DEFAULT '', expires_at INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS knowledge_chunks_file ON knowledge_chunks(local_file_id,status);
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
    lesson_plans.initialize(sys.modules[__name__])
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
        row = db.execute("SELECT data,updated_at FROM states WHERE scope=?", (scope,)).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row["data"])
        if not isinstance(value, dict):
            return {}
        if scope.startswith("group:"):
            # Recover only the saved model, never invent past revisions or submissions.
            ensure_model_version(value, row["updated_at"], recover_only=True)
        return value
    except json.JSONDecodeError:
        return {}


def state_put(scope: str, value: dict, preserve_roles: bool = False) -> None:
    with database() as db:
        if scope == 'class':
            db.execute('BEGIN IMMEDIATE')
            lesson_plans.preserve_published_project(db, value)
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


def model_versions(state: dict) -> list[dict]:
    versions = state.get("versions")
    return [item for item in versions if isinstance(item, dict) and _has_value(item.get("model"))] if isinstance(versions, list) else []


def ensure_model_version(state: dict, timestamp: str, recover_only: bool = False) -> None:
    workspace = state.get("workspace")
    if not isinstance(workspace, dict) or not _has_value(workspace.get("model")):
        return
    valid = model_versions(state)
    if recover_only and valid:
        return
    snapshot = {key: workspace.get(key, "") for key in ("model", "variables", "relation", "constraints")}
    if valid and all(snapshot[key] == valid[-1].get(key, snapshot[key]) for key in snapshot):
        return
    versions = state.get("versions")
    versions = list(versions) if isinstance(versions, list) else []
    number = max([len(versions)] + [int(item["v"][1:]) for item in valid if re.fullmatch(r"V\d+", str(item.get("v", "")))]) + 1
    versions.append({"v": f"V{number}", "time": timestamp,
                     "note": "从已保存模型恢复版本记录" if recover_only else "保存模型与变量、关系、约束快照",
                     **snapshot})
    state["versions"] = versions


def stage_task_status(group_state: dict) -> list[list[bool]]:
    workspace = group_state.get("workspace", {}) if isinstance(group_state.get("workspace"), dict) else {}
    members = [name for name in group_state.get("members", []) if isinstance(name, str) and name.strip()]
    thoughts = group_state.get("firstThoughts", {}) if isinstance(group_state.get("firstThoughts"), dict) else {}
    questions = group_state.get("questionTree", []) if isinstance(group_state.get("questionTree"), list) else []
    graph = group_state.get("graphConfig", {}) if isinstance(group_state.get("graphConfig"), dict) else {}
    math_tools = group_state.get("mathTools", {}) if isinstance(group_state.get("mathTools"), dict) else {}
    checklist = group_state.get("checklist", {}) if isinstance(group_state.get("checklist"), dict) else {}
    preparation = group_state.get("preparationChecks", {}) if isinstance(group_state.get("preparationChecks"), dict) else {}
    evidence = group_state.get("evidence", []) if isinstance(group_state.get("evidence"), list) else []
    viewpoints = group_state.get("viewpoints", []) if isinstance(group_state.get("viewpoints"), list) else []
    versions = model_versions(group_state)
    product = group_state.get("product", {}) if isinstance(group_state.get("product"), dict) else {}
    rehearsal_checks = product.get("rehearsalChecks", []) if isinstance(product.get("rehearsalChecks"), list) else []
    reflection = [line.strip() for line in str(product.get("reflection", "")).splitlines()]
    try:
        numbers = [float(graph[key]) for key in ("a", "b", "c", "min", "max")]
        graph_complete = all(math.isfinite(value) for value in numbers) and numbers[3] < numbers[4] and _has_value(graph.get("note"))
    except (KeyError, TypeError, ValueError):
        graph_complete = False
    desmos_states = math_tools.get("states", {}) if isinstance(math_tools.get("states"), dict) else {}
    legacy_desmos = math_tools.get("desmosState")
    if isinstance(legacy_desmos, dict):
        desmos_states = {"legacy": legacy_desmos, **desmos_states}
    desmos_complete = _has_value(math_tools.get("note")) and any(
        isinstance(state, dict) and any(
            isinstance(item, dict) and (_has_value(item.get("latex")) or _has_value(item.get("columns")))
            for item in (state.get("expressions", {}).get("list", []) if isinstance(state.get("expressions"), dict) else [])
        )
        for state in desmos_states.values()
    )
    checklist_complete = all(checklist.get(str(i), checklist.get(i)) is True for i in range(6))
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
         graph_complete or desmos_complete or checklist_complete],
        [len(evidence) >= 1, any(isinstance(item, dict) and item.get("verified") is True for item in evidence),
         sum(isinstance(item, dict) and all(_has_value(item.get(key)) for key in ("claim", "basis", "risk")) for item in viewpoints) >= 2,
         len(versions) >= 2],
        [all(_has_value(value) for value in (workspace.get("problem"), workspace.get("model"), product.get("evidenceValidation"), product.get("claim"))),
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
    current_stage = class_current_stage(class_state)
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
            "stage": current_stage,
            "submitted": submitted_count,
            "currentSubmitted": validated_stage_submissions(group_state).get(current_stage, False),
            "status": status,
            "help": help_total(group_state),
            "maxHint": highest_hint_level(group_state),
            "teacher": bool(saved.get("teacher")),
            "latest": latest,
            "issue": str(saved.get("issue", ""))[:120],
            "progress": progress,
        })
    return summaries


def current_broadcast(class_state: dict | None = None) -> dict:
    saved = state_get("broadcast")
    if saved:
        if saved.get("broadcastType") == "共性数学澄清":
            saved["broadcastType"] = "统一答疑"
        saved["seenGroups"] = sorted({int(group) for group in saved.get("seenGroups", []) if str(group).isdigit() and 1 <= int(group) <= group_count()})
        return saved
    return {"broadcast": (class_state or state_get("class")).get("broadcast", ""), "broadcastType": "教师广播", "broadcastId": "", "seenGroups": []}


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


STUDENT_STAGE_PAGE_RE = re.compile(r"^/student/stage([1-5])(?:-[^/]*)?\.html$")


def class_current_stage(class_state: dict | None = None) -> int:
    """Return the teacher-controlled phase, with a safe value for old/corrupt state."""
    value = (class_state or state_get("class")).get("currentStage", 1)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, value))


def student_stage_page_locked(path: str, session: dict | None, current_stage: int) -> bool:
    """Only phases already opened by the teacher may be reached by a student."""
    if not session or session.get("role") != "student":
        return False
    match = STUDENT_STAGE_PAGE_RE.fullmatch(path)
    return bool(match and int(match.group(1)) > class_current_stage({"currentStage": current_stage}))


def merged_state(group_no: int, monitoring: bool = True) -> dict:
    class_state = state_get("class")
    value = dict(class_state)
    group_state = state_get(f"group:{group_no}")
    value.update(group_state)
    value.update(current_broadcast(class_state))
    value["stageSubmitted"] = validated_stage_submissions(group_state)
    value["stageTaskStatus"] = stage_task_status(group_state)
    value["broadcastViewed"] = group_no in value.get("seenGroups", [])
    # 课堂阶段、暂停状态和 AI 开关由教师端统一控制；小组初始状态中的
    # currentStage=1 不能覆盖教师已经开放的阶段，否则学生刷新后会被误判回阶段1。
    for key in ("currentStage", "stagePaused", "aiOpen"):
        if key in class_state:
            value[key] = class_current_stage(class_state) if key == "currentStage" else class_state[key]
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
    raw_active_thread = active_help_thread(group_state)
    visible_stage = class_current_stage({"currentStage": value.get("currentStage", 1)})
    value["helpHistory"] = visible_help_entries(group_state.get("helpHistory"), visible_stage)
    value["helpThreads"] = visible_help_threads(group_state.get("helpThreads"), visible_stage)
    value["activeHelpThread"] = next(
        (item for item in value["helpThreads"] if item.get("id") == raw_active_thread.get("id")), None,
    ) if isinstance(raw_active_thread, dict) else None
    value["freeAi"] = free_ai_state(group_state)
    # Student pages only need the persisted summaries. Recomputing every group
    # on each poll multiplied one small request into up to 20 state reads.
    value["groups"] = dynamic_group_summaries(value) if monitoring else list(class_state.get("groups", []))
    topics: dict[str, list[int]] = {}
    for item in value["groups"]:
        if item.get("issue"):
            topics.setdefault(str(item["issue"]), []).append(int(item["id"]))
    value["common"] = [
        {"id": f"C-{index:02d}", "title": title, "groups": numbers,
         "note": "多个小组在提示请求中选择了相同卡点类型。", "action": "请教师结合各组原始记录判断是否需要共性澄清"}
        for index, (title, numbers) in enumerate(topics.items(), 1) if len(numbers) >= 2
    ]
    if monitoring:
        with database() as db:
            calls = db.execute(
                "SELECT ts,group_no,level,model,latency_ms,status FROM ai_calls ORDER BY id DESC LIMIT 50"
            ).fetchall()
        value["aiCalls"] = [dict(row) for row in calls]
    else:
        value.pop("aiCalls", None)
    value["currentLesson"] = lesson_plans.current(sys.modules[__name__])
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
        if suffix == ".pdf":
            import pdfplumber
            pages = []
            total = 0
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for number, page in enumerate(pdf.pages, 1):
                    text = re.sub(r"[ \t]+", " ", page.extract_text() or "").strip()
                    if text:
                        entry = f"[第{number}页]\n{text}"
                        pages.append(entry)
                        total += len(entry)
                    if total >= 120_000:
                        break
            return "\n\n".join(pages)[:120_000]
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError, ValueError):
        return ""
    return ""


def sync_builtin_knowledge() -> dict:
    """Import administrator-provided references without publishing them in Git."""
    result = {"found": 0, "imported": 0, "indexed": 0}
    if not KNOWLEDGE_SOURCE_DIR.is_dir():
        return result
    for source in sorted(KNOWLEDGE_SOURCE_DIR.iterdir(), key=lambda item: item.name):
        if not source.is_file() or source.suffix.lower() not in ALLOWED_UPLOADS:
            continue
        result["found"] += 1
        identity = f"{source.name}\0{source.stat().st_size}"
        file_id = "kb" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:22]
        stored = f"{file_id}{source.suffix.lower()}"
        target = UPLOAD_DIR / stored
        with database() as db:
            existing = db.execute("SELECT extracted_text FROM files WHERE id=?", (file_id,)).fetchone()
        if existing and target.is_file():
            result["indexed"] += bool(existing["extracted_text"])
            continue
        if not target.is_file():
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
        raw = target.read_bytes()
        extracted = extract_document(source.name, raw)
        with database() as db:
            db.execute(
                "INSERT OR REPLACE INTO files(id,original_name,stored_name,mime,size,extracted_text,uploaded_at,uploaded_by,group_no) "
                "VALUES(?,?,?,?,?,?,?,?,NULL)",
                (file_id, source.name, stored, mimetypes.guess_type(source.name)[0] or "application/octet-stream",
                 len(raw), extracted, utc_now(), "builtin-knowledge"),
            )
        result["imported"] += 1
        result["indexed"] += bool(extracted)
    return result


def ark_upload_document(path: Path, name: str) -> dict:
    """Stream a document to Ark Files API without buffering a large PDF in memory."""
    api_key = os.environ.get("ARK_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ARK_API_KEY 未配置")
    boundary = "----MapleKnowledge" + secrets.token_hex(12)
    expires_at = int(time.time()) + 30 * 24 * 60 * 60
    fields = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nuser_data\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"expire_at\"\r\n\r\n{expires_at}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{quote(name)}\"\r\n"
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode()
    ending = f"\r\n--{boundary}--\r\n".encode()
    connection = http.client.HTTPSConnection("ark.cn-beijing.volces.com", timeout=300)
    try:
        connection.putrequest("POST", "/api/v3/files")
        connection.putheader("Authorization", f"Bearer {api_key}")
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(len(fields) + path.stat().st_size + len(ending)))
        connection.endheaders()
        connection.send(fields)
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                connection.send(chunk)
        connection.send(ending)
        response = connection.getresponse()
        raw = response.read()
        if response.status >= 400:
            raise ValueError(f"Files API HTTP {response.status}: {raw.decode('utf-8', 'replace')[:240]}")
        result = json.loads(raw)
        if not result.get("id"):
            raise ValueError("Files API 未返回文件标识")
        return result
    finally:
        connection.close()


def ark_file_status(file_id: str) -> dict:
    request = urllib.request.Request(
        f"https://ark.cn-beijing.volces.com/api/v3/files/{quote(file_id)}",
        headers={"Authorization": f"Bearer {os.environ.get('ARK_API_KEY', '')}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


KNOWLEDGE_PROGRESS_LOCK = threading.Lock()
KNOWLEDGE_PROGRESS: dict[str, dict] = {}


def knowledge_progress(file_id: str, phase: str = "", *, api: str = "", pages: str = "") -> None:
    """Track the operation actually executing, separately from queued records."""
    with KNOWLEDGE_PROGRESS_LOCK:
        if phase:
            KNOWLEDGE_PROGRESS[file_id] = {"phase": phase, "api": api, "pages": pages,
                                           "startedAt": utc_now(), "startedEpoch": time.time()}
        else:
            KNOWLEDGE_PROGRESS.pop(file_id, None)


def sync_ark_knowledge_files() -> None:
    """Keep scanned built-in PDFs available to the multimodal document model."""
    if not os.environ.get("ARK_API_KEY"):
        return
    with database() as db:
        rows = db.execute(
            "SELECT f.id,f.original_name,f.stored_name,p.provider_file_id,p.status,p.expires_at "
            "FROM files f LEFT JOIN knowledge_provider_files p ON p.local_file_id=f.id "
            "WHERE f.uploaded_by='builtin-knowledge' AND f.mime='application/pdf' ORDER BY f.original_name"
        ).fetchall()
    refresh_before = int(time.time()) + 24 * 60 * 60
    for row in rows:
        # Large PDFs are consumed through chunks, never as whole-file inputs.
        # Do not block their indexing behind redundant uploads of every textbook.
        if pdf_page_count(row["stored_name"]) > 24:
            continue
        try:
            provider_id = row["provider_file_id"]
            if provider_id and int(row["expires_at"] or 0) > refresh_before:
                knowledge_progress(row["id"], "查询云端文件状态", api="Files API")
                meta = ark_file_status(provider_id)
            else:
                target = UPLOAD_DIR / row["stored_name"]
                if not target.is_file():
                    continue
                knowledge_progress(row["id"], "正在上传文档", api="Files API")
                meta = ark_upload_document(target, row["original_name"])
            status = str(meta.get("status", "processing"))
            expires_at = int(meta.get("expire_at") or int(time.time()) + 7 * 24 * 60 * 60)
            with database() as db:
                db.execute(
                    "INSERT OR REPLACE INTO knowledge_provider_files(local_file_id,provider_file_id,status,expires_at,updated_at,error) "
                    "VALUES(?,?,?,?,?,?)",
                    (row["id"], meta["id"], status, expires_at, utc_now(), ""),
                )
        except Exception as exc:
            with database() as db:
                db.execute(
                    "INSERT OR REPLACE INTO knowledge_provider_files(local_file_id,provider_file_id,status,expires_at,updated_at,error) "
                    "VALUES(?,?,?,?,?,?)",
                    (row["id"], row["provider_file_id"] or "", "failed", 0, utc_now(), str(exc)[:500]),
                )
        finally:
            knowledge_progress(row["id"])
    for _ in range(30):
        with database() as db:
            pending = db.execute(
                "SELECT local_file_id,provider_file_id FROM knowledge_provider_files WHERE status='processing'"
            ).fetchall()
        if not pending:
            break
        time.sleep(10)
        for item in pending:
            try:
                knowledge_progress(item["local_file_id"], "查询云端解析结果", api="Files API")
                meta = ark_file_status(item["provider_file_id"])
                with database() as db:
                    db.execute(
                        "UPDATE knowledge_provider_files SET status=?,expires_at=?,updated_at=?,error='' WHERE local_file_id=?",
                        (str(meta.get("status", "processing")), int(meta.get("expire_at") or 0), utc_now(), item["local_file_id"]),
                    )
            except Exception as exc:
                with database() as db:
                    db.execute(
                        "UPDATE knowledge_provider_files SET error=?,updated_at=? WHERE local_file_id=?",
                        (str(exc)[:500], utc_now(), item["local_file_id"]),
                    )
            finally:
                knowledge_progress(item["local_file_id"])


KNOWLEDGE_SYNC_RUNNING = threading.Event()
KNOWLEDGE_SYNC_START_LOCK = threading.Lock()


def start_ark_knowledge_sync() -> bool:
    """Start one knowledge refresh worker; return whether a new worker started."""
    with KNOWLEDGE_SYNC_START_LOCK:
        if KNOWLEDGE_SYNC_RUNNING.is_set():
            return False
        if not os.environ.get("ARK_API_KEY") or not KNOWLEDGE_SOURCE_DIR.is_dir():
            return False
        def worker() -> None:
            try:
                # A previous process may have stopped mid-chunk. Those rows are
                # not running jobs in this process and must be eligible again.
                with database() as db:
                    db.execute("UPDATE knowledge_chunks SET status='queued' WHERE status='processing'")
                sync_ark_knowledge_files()
                sync_ark_knowledge_chunks()
            finally:
                KNOWLEDGE_SYNC_RUNNING.clear()
                with KNOWLEDGE_PROGRESS_LOCK:
                    KNOWLEDGE_PROGRESS.clear()
        KNOWLEDGE_SYNC_RUNNING.set()
        threading.Thread(target=worker, name="knowledge-file-sync", daemon=True).start()
        return True


STAGE_HINT_ROUTES = {
    1: [
        "打开“阶段1—项目材料与课堂规则”，核对项目目标和成果对象，再点击“保存确认”。",
        "打开“阶段1—成员与角色确认”，填写本组成员与角色，再点击“保存成员与角色”。",
        "回到“阶段1—项目材料与课堂规则”，逐项检查目标、材料和课堂规则是否都已确认。",
    ],
    2: [
        "打开“阶段2—任务理解表”，补充“已知信息、未知信息、现实约束”，再点击“保存小组理解”。",
        "打开“阶段2—问题树”，写出核心问题和两个子问题，再点击“保存问题树”。",
        "回到“阶段2—任务理解表”，检查项目问题、已知信息和现实约束是否互相对应。",
    ],
    3: [
        "打开“阶段3—数学方案构建”，补充“变量”和“数量关系”，再点击“保存方案”。",
        "打开“阶段3—模型表征与检验”，选择合适工具表示当前模型，并填写“检验说明”。",
        "打开“阶段3—数学核验清单”，检查单位、范围和边界情况，再点击“保存检查结果”。",
    ],
    4: [
        "打开“阶段4—过程证据包”，填写“证据内容”或上传附件，再点击“加入证据包”。",
        "打开“阶段4—观点协商”，选择协商者并填写协商观点，再点击“加入讨论记录”。",
        "回到“阶段4—过程证据包”，选择一条证据完成核验，并说明它支持哪一处模型关系。",
    ],
    5: [
        "打开“阶段5—成果整理”，补充“证据与验证”和“核心结论”，再点击“保存成果草稿”。",
        "打开“阶段5—展示预演”，填写成员展示分工和发现的问题，再点击“保存预演与检查”。",
        "打开“阶段5—反思评价”，完成三项小组反思，再点击“保存反思”。",
    ],
}

HINT_PAGE_STAGES = {
    "项目材料与课堂规则": 1, "成员与角色确认": 1,
    "任务理解表": 2, "问题树": 2,
    "数学方案构建": 3, "模型表征与检验": 3, "数学核验清单": 3,
    "过程证据包": 4, "观点协商": 4,
    "成果整理": 5, "展示预演": 5, "反思评价": 5,
}


def hint_within_open_stage(text: str, current_stage: int) -> bool:
    """Reject navigation advice that points students beyond the teacher-opened stage."""
    stage = max(1, min(5, int(current_stage or 1)))
    if any(int(number) > stage for number in re.findall(r"阶段\s*([1-5])", str(text or ""))):
        return False
    return not any(required > stage and label in str(text or "") for label, required in HINT_PAGE_STAGES.items())


def visible_help_entries(entries: object, current_stage: int) -> list[dict]:
    result = []
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        visible = dict(item)
        if visible.get("reply") and not hint_within_open_stage(str(visible["reply"]), current_stage):
            visible["reply"] = "这条历史提示指向尚未开放的阶段，现已停用。请点击“新对话”，系统会按当前阶段重新提示。"
            visible["source"] = "stage_guard"
        result.append(visible)
    return result


def visible_help_threads(threads: object, current_stage: int) -> list[dict]:
    result = []
    for item in threads if isinstance(threads, list) else []:
        if not isinstance(item, dict):
            continue
        visible = dict(item)
        visible["messages"] = visible_help_entries(item.get("messages"), current_stage)
        result.append(visible)
    return result


def static_hint(state: dict, level_no: int) -> str:
    stage = class_current_stage({"currentStage": state.get("currentStage", 1)})
    defaults = STAGE_HINT_ROUTES[stage]
    config = state.get("teacherConfig", {}) if isinstance(state.get("teacherConfig"), dict) else {}
    hints = config.get("staticHints") if isinstance(config, dict) else None
    source = hints if isinstance(hints, list) and hints else defaults
    scaffold = str(source[min(level_no - 1, len(source) - 1)]).strip().rstrip("。.!！？；; ")[:180]
    if not scaffold or not hint_within_open_stage(scaffold, stage):
        scaffold = defaults[min(level_no - 1, len(defaults) - 1)].rstrip("。")
    diagnosis = "已记录本组尝试；现在只补齐一个最关键的条件或核验步骤。"
    return f"诊断：{diagnosis}\n怎么做：{scaffold}\n完成标志：页面保存成功，并能用一句话说明新增内容如何支持判断。"


def validate_hint(text: str, level: str = "L1") -> str | None:
    clean = re.sub(r"\s+", " ", text or "").strip()
    clean = re.sub(r"\bunknown\b", "‘未知信息’", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bevidence\b", "‘证据与验证’", clean, flags=re.IGNORECASE)
    blocked = ("最终答案", "答案是", "完整证明", "完整模型", "故最大值为", "直接得到最优", "最终结论为")
    limits = {"L1": 360, "L2": 440, "L3": 520}
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
    if any(len(part) < 20 for part in (diagnosis, guidance, next_action)):
        return None
    minimum = {"L1": 80, "L2": 90, "L3": 100}.get(level, 80)
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


def knowledge_query_terms(query: str) -> list[str]:
    clean = re.sub(r"\s+", "", str(query or "").lower())
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", clean)
    words = re.findall(r"[a-z0-9]{2,}", clean)
    terms = words[:12]
    for block in chinese:
        terms.extend(block[index:index + 2] for index in range(max(0, len(block) - 1)))
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))[:40]


MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", re.IGNORECASE)


def markdown_image_urls(text: str, limit: int = 2) -> list[str]:
    """Return a small, safe set of remote images embedded in Markdown."""
    urls = []
    for raw in MARKDOWN_IMAGE_RE.findall(text or ""):
        url = raw.rstrip('.,;')
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def knowledge_image_urls(state: dict, query: str, limit: int = 2) -> list[str]:
    """Select Markdown images only when the question is clearly visual."""
    if not re.search(r"图|图片|图像|看图|下图|如图|几何体|图形|表格|柱状图|函数图象", str(query or "")):
        return []
    config = state.get("teacherConfig", {}) if isinstance(state.get("teacherConfig"), dict) else {}
    approved = {str(name) for name in config.get("knowledge", []) if isinstance(name, str)}
    terms = knowledge_query_terms(query)
    with database() as db:
        rows = db.execute(
            "SELECT original_name,extracted_text,uploaded_by FROM files "
            "WHERE uploaded_by IN ('teacher','builtin-knowledge') AND extracted_text<>'' "
            "AND lower(original_name) LIKE '%.md' ORDER BY uploaded_at DESC"
        ).fetchall()
    ranked = []
    for row in rows:
        if row["uploaded_by"] == "teacher" and approved and row["original_name"] not in approved:
            continue
        text = row["extracted_text"] or ""
        urls = markdown_image_urls(text, limit=limit)
        if not urls:
            continue
        lowered = (row["original_name"] + "\n" + text).lower()
        score = sum(min(lowered.count(term), 8) for term in terms)
        ranked.append((score, row["original_name"], urls))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][2][:limit] if ranked else []


def knowledge_context(state: dict, query: str, *, max_documents: int = 3, max_chars: int = 4200) -> tuple[str, list[str]]:
    """Select compact, query-relevant excerpts from approved and built-in materials."""
    config = state.get("teacherConfig", {}) if isinstance(state.get("teacherConfig"), dict) else {}
    approved = {str(name) for name in config.get("knowledge", []) if isinstance(name, str)}
    with database() as db:
        rows = db.execute(
            "SELECT original_name,extracted_text,uploaded_by FROM files "
            "WHERE uploaded_by IN ('teacher','builtin-knowledge') AND extracted_text<>'' ORDER BY uploaded_at DESC"
        ).fetchall()
        chunk_rows = db.execute(
            "SELECT f.original_name || '（第' || c.page_start || '-' || c.page_end || '页）' AS original_name,"
            "c.summary AS extracted_text,'builtin-knowledge' AS uploaded_by FROM knowledge_chunks c "
            "JOIN files f ON f.id=c.local_file_id WHERE c.status='active' AND c.summary<>''"
        ).fetchall()
        rows = list(rows) + list(chunk_rows)
    terms = knowledge_query_terms(" ".join((query, str(state.get("project", "")), str(config.get("mathTopic", "")),
                                             str(config.get("standard", "")))))
    ranked = []
    for row in rows:
        if row["uploaded_by"] == "teacher" and approved and row["original_name"] not in approved:
            continue
        name, text = row["original_name"], row["extracted_text"]
        lowered = (name + "\n" + text).lower()
        hits = [(lowered.count(term), term) for term in terms if term in lowered]
        score = sum(min(count, 8) * (3 if term in name.lower() else 1) for count, term in hits)
        ranked.append((score, name, text, [term for _, term in hits]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    excerpts, sources, used = [], [], 0
    for _, name, text, hits in ranked[:max_documents]:
        positions = [text.lower().find(term) for term in hits if text.lower().find(term) >= 0]
        start = max(0, (min(positions) if positions else 0) - 350)
        excerpt = re.sub(r"\n{3,}", "\n\n", text[start:start + 2200]).strip()
        if not excerpt:
            continue
        block = f"[{name}]\n{excerpt}"
        if used + len(block) > max_chars:
            block = block[:max(0, max_chars - used)]
        if not block:
            break
        excerpts.append(block)
        sources.append(name)
        used += len(block)
        if used >= max_chars:
            break
    return "\n\n".join(excerpts), sources


@functools.lru_cache(maxsize=128)
def pdf_page_count(stored_name: str) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(UPLOAD_DIR / stored_name)).pages)
    except Exception:
        return 0


def ark_knowledge_documents(state: dict, query: str, limit: int = 2) -> list[dict]:
    """Choose a small set of processed PDFs so scanned pages can be read by Ark."""
    grade = student_grade(state)
    terms = knowledge_query_terms(query)
    with database() as db:
        rows = db.execute(
            "SELECT f.original_name AS name,p.provider_file_id AS fileId,f.stored_name AS storedName,"
            "f.extracted_text AS searchable,0 AS isChunk "
            "FROM files f JOIN knowledge_provider_files p ON p.local_file_id=f.id "
            "WHERE f.uploaded_by='builtin-knowledge' AND p.status='active' AND p.provider_file_id<>'' AND p.expires_at>? "
            "AND NOT EXISTS (SELECT 1 FROM knowledge_chunks x WHERE x.local_file_id=f.id) "
            "UNION ALL SELECT f.original_name || '（第' || c.page_start || '-' || c.page_end || '页）' AS name,"
            "c.provider_file_id AS fileId,'' AS storedName,c.summary AS searchable,1 AS isChunk "
            "FROM knowledge_chunks c JOIN files f ON f.id=c.local_file_id "
            "WHERE c.status='active' AND c.provider_file_id<>'' AND c.expires_at>?",
            (int(time.time()), int(time.time())),
        ).fetchall()
    ranked = []
    for row in rows:
        name = row["name"]
        if not row["isChunk"] and not 0 < pdf_page_count(row["storedName"]) <= 24:
            continue
        searchable = (name + "\n" + (row["searchable"] or "")).lower()
        score = sum((8 if term in name.lower() else 2) for term in terms if term in searchable)
        if grade and f"{grade}年级" in name:
            score += 12
        if any(term in query for term in ("课标", "课程标准", "核心素养", "课程目标")) and "课程标准" in name:
            score += 20
        if any(term in query for term in ("教学", "怎么教", "活动设计")) and "教师用书" in name:
            score += 8
        if "数学课程标准" in name:
            score += 2
        ranked.append((score, name, row["fileId"]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [{"name": name, "fileId": file_id} for _, name, file_id in ranked[:limit]]


def deepseek_hint(
    state: dict, level: str, done: str, issue_type: str, issue: str,
    history: list[dict] | None = None, checked_work: bool = False,
) -> tuple[str | None, int, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None, 0, "not_configured"
    config = state.get("teacherConfig", {})
    knowledge_query = " ".join((done, issue_type, issue))
    knowledge, knowledge_names = knowledge_context(state, knowledge_query, max_documents=4, max_chars=7200)
    contracts = {
        "L1": "定位支架：只指出一个尚未明确的目标、条件或检查项；提出一个聚焦问题和一个立即可执行的观察/核对动作。不得让学生写函数、表达式、不等式，不得给完整公式、建模方法或结果。",
        "L2": "结构支架：把卡点拆成2—3个子问题，建议变量、图表或数量关系的组织方式和处理顺序；下一步只让学生做第一个整理动作。不得完成全部建模或推导。",
        "L3": "局部示范：只给一个类比小例子或一步局部推导，说明核验方法；必须明确写‘请你完成/由学生完成’的剩余一步。不得要求学生最后化简、求最大值、完成全部推导，不得给最终模型、证明、最优值或成品答案。",
    }
    current_stage = class_current_stage({"currentStage": state.get("currentStage", 1)})
    current_routes = "；".join(STAGE_HINT_ROUTES[current_stage])
    stage_boundary = f"阶段{current_stage + 1}及之后" if current_stage < 5 else "当前阶段之后"
    system = (
        "你是服务于中国初中数学项目式学习小组的教学支架。必须依据学生当前尝试和历史提示生成有推进作用、但保留学生思考责任的反馈。"
        + student_math_scope(state)
        + MATH_FORMAT_RULE
        + f"当前严格执行{level}：{contracts[level]}"
        "不得重复历史提示；信息不足时先澄清，不得猜测。材料冲突、数学结论不可靠或需要教师判断时，在nextAction中写明请教师核验。"
        "材料摘录存在时必须以其为依据，不得编造摘录中没有的结论；需要指出依据时使用材料文件名。"
        f"教师当前只开放到阶段{current_stage}，严禁建议学生进入{stage_boundary}的页面，也不得使用尚未开放页面的名称。"
        f"当前阶段可执行路径为：{current_routes}。项目流程或协作类提问必须从这些路径中选择一个，回答控制在2—4行，并写成‘打开哪个中文页面—填写哪个中文字段—点击哪个中文按钮’。"
        "禁止输出unknown、evidence等代码字段名。"
        "diagnosis、guidance、nextAction各20—60个中文字符，不能用空话凑字数。"
        "只输出一个JSON对象，不要Markdown和额外文字，格式为："
        '{"diagnosis":"针对本次尝试的简短诊断","guidance":"符合当前层级的支架","nextAction":"学生下一步可执行动作；L3必须明确留下由学生完成的剩余一步"}'
    )
    recent = []
    for item in visible_help_entries(history, current_stage)[-6:]:
        if isinstance(item, dict):
            recent.append({
                "层级": item.get("level", ""), "学生提问": item.get("question", item.get("prompt", "")),
                "学生尝试": item.get("done", ""), "支架": item.get("reply", ""),
            })
    charter = project_charter(state)
    user = {
        "适用年级": student_grade(state) or "初中阶段",
        "项目": state.get("project", ""), "数学主题": config.get("mathTopic", ""),
        "课程目标": config.get("standard", ""), "当前阶段": state.get("currentStage", 1),
        "当前课时项目信息": charter.get("当前课时", {}), "数学项目任务书": charter,
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
        parsed = parse_model_hint(body["choices"][0]["message"]["content"], level)
        if parsed and not hint_within_open_stage(parsed, current_stage):
            return None, latency, "future_stage"
        return parsed, latency, "ok"
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


def project_charter(state: dict) -> dict:
    config = state.get("teacherConfig", {}) if isinstance(state.get("teacherConfig"), dict) else {}
    lesson = state.get("currentLesson") if isinstance(state.get("currentLesson"), dict) else {}
    lesson_info = lesson.get("projectInfo") if isinstance(lesson.get("projectInfo"), dict) else {}
    compact_lesson = {
        key: lesson.get(key, "") for key in ("lessonNo", "title", "mathFocus", "objectives", "output")
        if lesson.get(key) not in (None, "", [], {})
    }
    compact_lesson["projectInfo"] = {
        key: lesson_info.get(key, "") for key in ("title", "grade", "teachingUnit", "learningObjectives")
        if lesson_info.get(key)
    }
    return {
        "项目名称": state.get("project", ""), "数学主题": config.get("mathTopic", ""),
        "课程目标": config.get("standard", ""), "最终成果": config.get("finalProduct", ""),
        "当前阶段": state.get("currentStage", 1), "当前课时": compact_lesson,
    }


def free_ai_context(state: dict, group: int) -> str:
    config = state.get("teacherConfig", {}) if isinstance(state.get("teacherConfig"), dict) else {}
    workspace = state.get("workspace", {}) if isinstance(state.get("workspace"), dict) else {}
    context = {
        "班级": state.get("className", ""), "适用年级": student_grade(state) or "初中阶段",
        "项目": state.get("project", ""), "数学主题": config.get("mathTopic", ""),
        "课程目标": config.get("standard", ""), "最终成果": config.get("finalProduct", ""),
        "当前阶段": state.get("currentStage", 1), "小组": group, "共享工作区": workspace,
        "数学项目任务书": project_charter(state),
    }
    return json.dumps(context, ensure_ascii=False)


def ark_free_messages(state: dict, group: int, prompt: str, history: list[dict]) -> list[dict]:
    current_stage = class_current_stage({"currentStage": state.get("currentStage", 1)})
    stage_boundary = f"阶段{current_stage + 1}及之后" if current_stage < 5 else "当前阶段之后"
    knowledge, sources = knowledge_context(state, prompt)
    image_urls = knowledge_image_urls(state, prompt)
    system = (
        "你是‘迈谱 Maple’学生小组自由工作区的初中数学助手。"
        + student_math_scope(state)
        + "你的任务是帮助学生减少机械性整理，把更多时间留给小组协作和高阶思考。"
        "你可以回答项目是什么、接下来做什么，解释概念、检查思路和书写数学表达，但不要替学生完成未经讨论的整个项目，"
        "不要伪造数据；缺少信息时明确列出需要补充的内容。"
        "回答项目知识与数学内容时优先使用下方知识库摘录；使用后在相关段落末尾以[文件名]标注来源。"
        "知识库没有支持某项结论时明确说明依据不足，不得凭空补写教材或课标内容。"
        f"教师当前只开放到阶段{current_stage}。若学生询问平台操作，只能引用当前阶段路径：{'；'.join(STAGE_HINT_ROUTES[current_stage])}。"
        f"不得建议进入{stage_boundary}的页面，也不得使用尚未开放页面的名称。"
        + MATH_FORMAT_RULE
        + "回答使用清晰的Markdown。默认直接回答核心问题，控制在2至4个要点、约300至500个汉字内；学生明确要求详细说明时再展开。"
        f"当前项目上下文：{free_ai_context(state, group)}"
        f"\n本次检索到的知识库来源：{json.dumps(sources, ensure_ascii=False)}"
        f"\n知识库摘录：\n{knowledge or '本次没有检索到可用正文摘录。'}"
    )
    messages = [{"role": "system", "content": system}]
    for item in history[-4:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).strip()[:800]
        if content:
            messages.append({"role": item["role"], "content": content})
    if image_urls:
        messages.append({"role": "user", "content": [
            {"type": "text", "text": prompt},
            *({"type": "image_url", "image_url": {"url": url}} for url in image_urls),
        ]})
    else:
        messages.append({"role": "user", "content": prompt})
    return messages


def ark_text_request(messages: list[dict], stream: bool) -> urllib.request.Request:
    api_key = os.environ.get("ARK_API_KEY", "")
    payload = json.dumps({
        "model": ARK_TEXT_MODEL, "messages": messages, "stream": stream,
        "temperature": 0.2, "max_tokens": 600,
        "thinking": {"type": "disabled"},
    }, ensure_ascii=False).encode()
    return urllib.request.Request(
        ARK_TEXT_URL, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Maple-Platform/1"},
        method="POST",
    )


def ark_document_request(state: dict, group: int, prompt: str, history: list[dict], stream: bool) -> tuple[urllib.request.Request | None, list[str]]:
    documents = ark_knowledge_documents(state, prompt)
    if not documents:
        return None, []
    base_messages = ark_free_messages(state, group, prompt, history)
    system = base_messages[0]["content"]
    sources = [item["name"] for item in documents]
    content = [{"type": "input_file", "file_id": item["fileId"]} for item in documents]
    image_urls = knowledge_image_urls(state, prompt)
    content.extend({"type": "input_image", "image_url": url} for url in image_urls)
    content.append({
        "type": "input_text",
        "text": (
            f"学生问题：{prompt}\n本轮附带资料：{json.dumps(sources, ensure_ascii=False)}。"
            "请读取附带资料（含扫描页和相关图片）后回答，只引用文件中能够确认的内容，并在对应段落末尾标注[文件名]。"
        ),
    })
    input_items = [{"role": "system", "content": system}]
    for item in base_messages[1:-1]:
        input_items.append({"role": item["role"], "content": item["content"]})
    input_items.append({"role": "user", "content": content})
    payload = json.dumps({
        "model": ARK_TEXT_MODEL, "input": input_items, "stream": stream,
        "max_output_tokens": 600, "thinking": {"type": "disabled"},
    }, ensure_ascii=False).encode()
    return urllib.request.Request(
        ARK_RESPONSES_URL, data=payload,
        headers={"Authorization": f"Bearer {os.environ.get('ARK_API_KEY', '')}",
                 "Content-Type": "application/json", "User-Agent": "Maple-Platform/1"},
        method="POST",
    ), sources


def ark_response_answer(body: dict) -> str:
    parts = []
    for item in body.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(str(content["text"]))
    return "".join(parts).strip()


def create_pdf_chunk(path: Path, page_start: int, page_end: int) -> Path:
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(path))
    writer = PdfWriter()
    for page in reader.pages[page_start - 1:page_end]:
        writer.add_page(page)
    chunk_path = UPLOAD_DIR / f".knowledge-{secrets.token_hex(8)}.pdf"
    with chunk_path.open("wb") as target:
        writer.write(target)
    return chunk_path


def summarize_ark_chunk(provider_file_id: str, source_name: str, page_start: int, page_end: int) -> str:
    payload = json.dumps({
        "model": ARK_TEXT_MODEL,
        "input": [{"role": "user", "content": [
            {"type": "input_file", "file_id": provider_file_id},
            {"type": "input_text", "text": (
                "请把这段扫描版数学资料整理成可检索索引，保留原文中的章节标题、概念、公式名称、"
                "课程目标和关键词，不要补充文件外知识。输出纯文本，控制在1800字以内。"
                f"文件：{source_name}，页码：{page_start}-{page_end}。"
            )},
        ]}],
        "thinking": {"type": "disabled"}, "max_output_tokens": 1800,
    }, ensure_ascii=False).encode()
    request = urllib.request.Request(
        ARK_RESPONSES_URL, data=payload,
        headers={"Authorization": f"Bearer {os.environ.get('ARK_API_KEY', '')}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        answer = ark_response_answer(json.loads(response.read()))
    return answer[:9000]


def sync_ark_knowledge_chunks() -> None:
    """Build bounded searchable summaries for scanned PDFs in one background worker."""
    if not os.environ.get("ARK_API_KEY"):
        return
    from pypdf import PdfReader
    with database() as db:
        documents = db.execute(
            "SELECT id,original_name,stored_name FROM files WHERE uploaded_by='builtin-knowledge' AND mime='application/pdf' "
            "ORDER BY CASE WHEN original_name LIKE '%课程标准%' THEN 0 "
            "WHEN original_name LIKE '%课程方案%' THEN 1 ELSE 2 END,original_name"
        ).fetchall()
    for document in documents:
        path = UPLOAD_DIR / document["stored_name"]
        if not path.is_file():
            continue
        try:
            page_count = len(PdfReader(str(path)).pages)
        except Exception:
            continue
        if page_count <= 24:
            continue
        for start in range(1, page_count + 1, 20):
            end = min(page_count, start + 19)
            chunk_id = "kc" + hashlib.sha256(f"{document['id']}:{start}:{end}".encode()).hexdigest()[:22]
            with database() as db:
                row = db.execute(
                    "SELECT status,summary,expires_at,updated_at FROM knowledge_chunks WHERE id=?", (chunk_id,)
                ).fetchone()
                if row and row["status"] == "active" and row["summary"] and int(row["expires_at"] or 0) > int(time.time()) + 86400:
                    continue
                if row and row["status"] == "processing":
                    continue
                if row and row["status"] == "failed":
                    try:
                        age = datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"])
                        if age.total_seconds() < 1800:
                            continue
                    except (TypeError, ValueError):
                        pass
                db.execute(
                    "INSERT OR REPLACE INTO knowledge_chunks(id,local_file_id,page_start,page_end,status,summary,expires_at,updated_at,error) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (chunk_id, document["id"], start, end, "processing", "", 0, utc_now(), ""),
                )
            chunk_path = None
            try:
                page_range = f"第{start}-{end}页"
                knowledge_progress(document["id"], "正在本地切分PDF", pages=page_range)
                chunk_path = create_pdf_chunk(path, start, end)
                knowledge_progress(document["id"], "正在上传分块", api="Files API", pages=page_range)
                meta = ark_upload_document(chunk_path, f"{Path(document['original_name']).stem}-第{start}-{end}页.pdf")
                provider_id = meta["id"]
                knowledge_progress(document["id"], "等待云端解析分块", api="Files API（轮询）", pages=page_range)
                for _ in range(45):
                    meta = ark_file_status(provider_id)
                    if meta.get("status") != "processing":
                        break
                    time.sleep(2)
                if meta.get("status") != "active":
                    raise ValueError(f"切片文件状态：{meta.get('status', 'unknown')}")
                knowledge_progress(document["id"], "正在调用模型生成摘要", api="Responses API", pages=page_range)
                summary = summarize_ark_chunk(provider_id, document["original_name"], start, end)
                if not summary:
                    raise ValueError("切片未返回可检索摘要")
                with database() as db:
                    db.execute(
                        "UPDATE knowledge_chunks SET provider_file_id=?,status='active',summary=?,expires_at=?,updated_at=?,error='' WHERE id=?",
                        (provider_id, summary, int(meta.get("expire_at") or int(time.time()) + 7 * 86400), utc_now(), chunk_id),
                    )
                knowledge_progress(document["id"])
                time.sleep(2)
            except urllib.error.HTTPError as exc:
                with database() as db:
                    db.execute(
                        "UPDATE knowledge_chunks SET status='failed',updated_at=?,error=? WHERE id=?",
                        (utc_now(), f"HTTP {exc.code}: {exc.reason}"[:500], chunk_id),
                    )
                if exc.code == 429:
                    return
            except Exception as exc:
                with database() as db:
                    db.execute("UPDATE knowledge_chunks SET status='failed',updated_at=?,error=? WHERE id=?", (utc_now(), str(exc)[:500], chunk_id))
            finally:
                knowledge_progress(document["id"])
                if chunk_path:
                    chunk_path.unlink(missing_ok=True)


def ark_free_chat(
    state: dict, group: int, prompt: str, history: list[dict],
) -> tuple[str | None, int, str, str]:
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        return None, 0, "not_configured", ARK_TEXT_MODEL
    request, sources = ark_document_request(state, group, prompt, history, False)
    request = request or ark_text_request(ark_free_messages(state, group, prompt, history), False)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=ARK_TEXT_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read())
        latency = round((time.perf_counter() - started) * 1000)
        answer = ark_response_answer(body) if sources else str(body["choices"][0]["message"]["content"]).strip()
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
    request, sources = ark_document_request(state, group, prompt, history, True)
    request = request or ark_text_request(ark_free_messages(state, group, prompt, history), True)
    started = time.perf_counter()
    answer_parts = []
    try:
        with urllib.request.urlopen(request, timeout=ARK_TEXT_TIMEOUT_SECONDS) as response:
            for raw_event in sse_payloads(response):
                if raw_event == "[DONE]":
                    break
                body = json.loads(raw_event)
                if sources:
                    if body.get("type") == "response.output_text.delta" and body.get("delta"):
                        text = str(body["delta"])
                        answer_parts.append(text)
                        on_delta(text)
                    continue
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


def knowledge_snapshot() -> dict:
    """Report stored evidence and actual retrieval eligibility without calling AI."""
    now = int(time.time())
    running = KNOWLEDGE_SYNC_RUNNING.is_set()
    with KNOWLEDGE_PROGRESS_LOCK:
        progress = {key: dict(value) for key, value in KNOWLEDGE_PROGRESS.items()}
    configured = bool(os.environ.get("ARK_API_KEY", "").strip())
    with database() as db:
        rows = db.execute(
            "SELECT f.id,f.original_name AS name,f.stored_name,f.mime,f.size,"
            "f.uploaded_at AS uploadedAt,f.uploaded_by AS uploadedBy,"
            "length(trim(f.extracted_text)) AS indexedChars,f.extracted_text AS extractedText,"
            "coalesce(p.status,'') AS providerStatus,coalesce(p.provider_file_id,'') AS providerId,"
            "coalesce(p.expires_at,0) AS providerExpiresAt,coalesce(p.updated_at,'') AS providerUpdatedAt,"
            "coalesce(p.error,'') AS providerError "
            "FROM files f LEFT JOIN knowledge_provider_files p ON p.local_file_id=f.id "
            "WHERE f.uploaded_by IN ('teacher','builtin-knowledge') "
            "ORDER BY f.uploaded_by='builtin-knowledge' DESC,f.original_name"
        ).fetchall()
        chunks = db.execute("SELECT * FROM knowledge_chunks").fetchall()
    by_file = {}
    for chunk in chunks:
        by_file.setdefault(chunk["local_file_id"], []).append(chunk)
    files = []
    for row in rows:
        item = dict(row)
        stored = item.pop("stored_name")
        extracted_text = item.pop("extractedText") or ""
        provider_id = item.pop("providerId")
        records = by_file.get(item["id"], [])
        action = progress.get(item["id"], {}) if running else {}
        ready = [c for c in records if c["status"] == "active" and c["summary"].strip()]
        cloud_chunks = [c for c in records if c["status"] == "active" and c["provider_file_id"] and c["expires_at"] > now]
        present = (UPLOAD_DIR / stored).is_file()
        pages = pdf_page_count(stored) if present and item["mime"] == "application/pdf" else 0
        expected = math.ceil(pages / 20) if pages > 24 else 0
        image_count = len(MARKDOWN_IMAGE_RE.findall(extracted_text)) if str(item["name"]).lower().endswith(".md") else 0
        is_markdown = str(item["name"]).lower().endswith(".md")
        direct = bool(present and configured and item["uploadedBy"] == "builtin-knowledge"
                      and item["providerStatus"] == "active" and provider_id
                      and item["providerExpiresAt"] > now and 0 < pages <= 24 and not records)
        # Local excerpts/summaries remain usable even if the cloud file expires.
        local = bool(item["indexedChars"] or ready)
        item.update({
            "filePresent": present, "pageCount": pages, "chunkCount": len(ready),
            "chunkTotal": expected, "chunkFailed": sum(c["status"] == "failed" for c in records),
            "chunkProcessing": sum(c["status"] == "processing" for c in records),
            "imageCount": image_count,
            "cloudChunkCount": len(cloud_chunks) if configured else 0,
            "localSearchable": local, "cloudReadable": direct or bool(configured and cloud_chunks),
            "searchable": local or direct or bool(configured and cloud_chunks),
            "statusUpdatedAt": max([item["providerUpdatedAt"]] + [c["updated_at"] for c in records]),
            "currentAction": action.get("phase", ""), "currentApi": action.get("api", ""),
            "actionStartedAt": action.get("startedAt", ""),
            "actionElapsedSeconds": max(0, now - int(action["startedEpoch"])) if action else 0,
        })
        # Chunk summaries are the useful result for scanned PDFs. Prefer their
        # readiness over the small local text layer, which is often only a few
        # characters even when every page has already been read.
        if ready:
            label = "内容摘要可供参考" if len(ready) >= expected > 0 else "部分内容可供参考"
        elif image_count and item["indexedChars"] and configured:
            label = "文字和图片可供参考"
        elif item["indexedChars"]:
            label = "仅少量文字可供参考" if item["mime"] == "application/pdf" and item["indexedChars"] < 1000 else "已提取文字可供参考"
        elif item["cloudReadable"]:
            label = "可供AI读取"
        else:
            label = "暂未识别出可用内容"
        details = [f"已提取 {item['indexedChars']} 字"] if item["indexedChars"] else []
        if image_count:
            details.append(f"包含 {image_count} 张图片，提问图片相关内容时会读取相关图片")
        if expected:
            details.append(f"已完成 {len(ready)}/{expected} 部分")
        if not present:
            task = "原文件缺失"
        elif not configured:
            task = "未配置云端识别"
        elif item["uploadedBy"] != "builtin-knowledge":
            task = "补充材料仅使用本地文字"
        elif action:
            task = action["phase"]
        elif item["chunkFailed"] or (item["providerStatus"] == "failed" and not expected):
            task = "部分识别失败" if local else "识别失败"
        elif is_markdown and local:
            # Markdown converted by SoMark is already the searchable source.
            # It does not need the PDF cloud-recognition pipeline; treating it
            # as a stopped cloud job makes a successfully imported file look
            # unavailable to teachers.
            task = "文字和图片已就绪" if image_count else "文字内容已就绪"
        elif expected and len(ready) < expected:
            task = "排队等待分块识别" if running else "识别未完成，后台任务已停止"
        elif not expected and not direct:
            task = ("等待云端解析结果" if item["providerStatus"] == "processing" else "排队等待识别") if running else "云端未就绪，后台任务已停止"
        else:
            task = "本轮处理完成"
        # Keep diagnostic fields for maintenance; show teachers plain descriptions.
        teacher_task = {
            "原文件缺失": "找不到原文件，请联系管理员",
            "未配置云端识别": "识别服务尚未开通，请联系管理员",
            "补充材料仅使用本地文字": "AI可参考已提取的文字，图片内容尚未识别",
            "查询云端文件状态": "正在检查资料是否可以读取",
            "正在上传文档": "正在发送资料以便识别",
            "查询云端解析结果": "正在检查资料识别进度",
            "正在本地切分PDF": "正在按页整理资料",
            "正在上传分块": "正在发送这些页面以便识别",
            "等待云端解析分块": "正在等待这些页面的识别结果",
            "正在调用模型生成摘要": "AI正在阅读并整理这些页面",
            "排队等待分块识别": "排队等待识别",
            "识别未完成，后台任务已停止": "识别已停止，尚未完成，请联系管理员",
            "云端未就绪，后台任务已停止": "识别已停止，资料尚不可读取，请联系管理员",
            "等待云端解析结果": "等待识别结果",
            "文字内容已就绪": "已提取文字，可供AI参考",
            "文字和图片已就绪": "已提取文字和图片，可供AI参考",
            "本轮处理完成": "本次处理已完成",
        }.get(task, task)
        details.append(teacher_task)
        if action:
            if action["pages"]:
                details.append(action["pages"])
            details.append(f"当前步骤已用时 {item['actionElapsedSeconds']} 秒")
        if item["providerStatus"] == "active" and item["providerExpiresAt"] <= now:
            details.append("原文读取期限已到，需要重新处理")
        if expected and not item["cloudChunkCount"] and ready:
            details.append("目前仅能参考已保存的摘要，暂不能读取对应原文")
        errors = [c["error"] for c in records if c["error"]] + [item.pop("providerError")]
        errors = [error for error in errors if error]
        # Provider exception strings can contain signed URLs; expose a safe diagnosis only.
        if errors:
            error_text = " ".join(errors).lower()
            reason = ("识别服务繁忙或额度不足，请联系管理员" if "429" in error_text else
                      "识别服务的访问设置有误，请联系管理员" if any(code in error_text for code in ("401", "403")) else
                      "识别服务长时间未响应" if "timed out" in error_text or "timeout" in error_text else
                      "资料处理失败，请联系管理员查看原因")
            details.append(reason)
        item.update({"statusLabel": label, "taskStatus": task, "statusDetail": "；".join(details)})
        files.append(item)
    return {"files": files, "checkedAt": utc_now(), "syncRunning": running,
            "summary": {"total": len(files), "indexed": sum(f["searchable"] for f in files),
                        "bytes": sum(int(f["size"]) for f in files)}}


class Handler(SimpleHTTPRequestHandler):
    server_version = "Maple"

    def translate_path(self, path: str) -> str:
        clean = unquote(urlparse(path).path).lstrip("/")
        target = (ROOT / clean).resolve()
        if target != ROOT and ROOT not in target.parents:
            return str(ROOT / "__not_found__")
        relative = target.relative_to(ROOT)
        public_page = len(relative.parts) == 2 and relative.parts[0] in {'student', 'teacher'} and target.suffix == '.html'
        public_asset = len(relative.parts) >= 2 and relative.parts[0] == 'assets' and target.suffix.lower() in {'.js', '.css', '.png', '.jpg', '.jpeg', '.webp', '.svg', '.ico', '.woff', '.woff2'}
        visualization = len(relative.parts) >= 3 and relative.parts[:2] == ('vendor', 'math-viz-kit') and target.suffix.lower() == '.html'
        if target not in (ROOT, ROOT / 'index.html') and not public_page and not public_asset and not visualization:
            return str(ROOT / '__not_found__')
        return str(target)

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def send_head(self):
        target = Path(self.translate_path(self.path))
        if target.is_file() and target.suffix.lower() in COMPRESSIBLE_STATIC_TYPES:
            stat = target.stat()
            accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
            encoding_tag = "-gzip" if accepts_gzip else "-identity"
            etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}{encoding_tag}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("ETag", etag)
                self.send_header("Vary", "Accept-Encoding")
                self.end_headers()
                return None
            raw = compressed_static(str(target), stat.st_mtime_ns, stat.st_size) if accepts_gzip else target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", self.guess_type(str(target)))
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept-Encoding")
            if accepts_gzip:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            return io.BytesIO(raw)
        return super().send_head()

    def end_headers(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            if path.startswith("/vendor/math-viz-kit/"):
                self.send_header("Cache-Control", "public, max-age=86400")
            elif Path(path).suffix in {".html", ".js", ".css"} or path == "/":
                self.send_header("Cache-Control", "no-cache")
            elif path.startswith("/assets/images/"):
                self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        document_preview = path in ('/api/lesson-plan/source', '/api/lesson-plan/material')
        visualization = path.startswith('/vendor/math-viz-kit/')
        self.send_header("X-Frame-Options", "SAMEORIGIN" if document_preview or visualization else "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if path in {"/student/math-tools.html", "/student/stage3-graph.html"}:
            # Desmos v1.12 contains generated Function/eval code. Scope unsafe-eval
            # to the two calculator pages instead of weakening the whole site.
            content_policy = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.desmos.com https://*.desmos.com; style-src 'self' 'unsafe-inline' https://www.desmos.com https://*.desmos.com; font-src 'self' data: https://www.desmos.com https://*.desmos.com; img-src 'self' data: blob: https://www.desmos.com https://*.desmos.com; connect-src 'self' https://www.desmos.com https://*.desmos.com; worker-src 'self' blob:"
        elif visualization:
            # The vendored tools are self-contained. Blocking remote font calls
            # removes a slow third-party dependency on school/mobile networks.
            content_policy = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; frame-src 'self'"
        else:
            content_policy = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        self.send_header("Content-Security-Policy", content_policy)
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write(f"[{utc_now()}] {self.address_string()} {fmt % args}\n")

    def json_response(self, payload: object, status: int = 200, cookie: str | None = None) -> None:
        # Drain small rejected request bodies: closing with unread bytes can reset
        # the TCP connection before Windows clients receive the 401/403 response.
        if status >= 400 and not getattr(self, '_body_read', False):
            previous_timeout = self.connection.gettimeout()
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if 0 < length <= MAX_JSON_BYTES:
                    self.connection.settimeout(1)
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            finally:
                self.connection.settimeout(previous_timeout)
                self._body_read = True
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
            self._body_read = True
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

    def require_teacher(self) -> dict | None:
        """Require an authenticated teacher for teacher-only endpoints."""
        return self.require_session("teacher")

    def check_origin(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and urlparse(origin).netloc != host:
            self.json_response({"error": "来源校验失败"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self) -> None:
        route = urlparse(self.path)
        if lesson_plans.handle(sys.modules[__name__], self, 'GET', route.path):
            return
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
        if route.path == "/api/math-tools/config":
            if self.require_session():
                self.json_response({"apiKey": DESMOS_API_KEY})
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
            self.json_response({"state": merged_state(group, monitoring=session["role"] == "teacher"), "role": session["role"]})
            return
        if route.path == "/api/knowledge":
            session = self.require_teacher()
            if not session:
                return
            self.json_response(knowledge_snapshot())
            return
        if route.path == "/api/files":
            session = self.require_session()
            if not session:
                return
            with database() as db:
                if session["role"] == "student":
                    rows = db.execute(
                        "SELECT id,original_name AS name,mime,size,uploaded_at,uploaded_by,extracted_text<>'' AS indexed "
                        "FROM files WHERE group_no=? OR uploaded_by IN ('teacher','builtin-knowledge') ORDER BY uploaded_at DESC",
                        (session["groupId"],),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT id,original_name AS name,mime,size,uploaded_at,uploaded_by,extracted_text<>'' AS indexed "
                        "FROM files ORDER BY uploaded_at DESC"
                    ).fetchall()
            self.json_response({"files": [dict(row) for row in rows]})
            return
        if route.path.startswith("/api/files/"):
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
                        "WHERE id=? AND (group_no=? OR uploaded_by IN ('teacher','builtin-knowledge'))",
                        (file_id, session["groupId"]),
                    ).fetchone()
                else:
                    row = db.execute("SELECT original_name,stored_name,mime,size FROM files WHERE id=?", (file_id,)).fetchone()
            target = UPLOAD_DIR / row["stored_name"] if row else None
            if not row or not target.is_file() or target.resolve().parent != UPLOAD_DIR.resolve():
                self.json_response({"error": "文件不存在"}, HTTPStatus.NOT_FOUND)
                return
            raw = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", row["mime"])
            disposition = "attachment" if parse_qs(route.query).get("download") == ["1"] else "inline"
            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(row['original_name'])}")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
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
        session = self.current_session()
        if student_stage_page_locked(route.path, session, class_current_stage()):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/student/dashboard.html")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.end_headers()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if not self.check_origin():
            return
        route = urlparse(self.path).path
        if lesson_plans.handle(sys.modules[__name__], self, 'POST', route):
            return
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
            elif route == "/api/ai/compare":
                session = self.require_session("student")
                if session:
                    self.compare_solutions(session, self.read_json())
            elif route == "/api/stages/submit":
                session = self.require_session("student")
                if session:
                    self.submit_stage(session, self.read_json())
            elif route == "/api/knowledge/remove":
                session = self.require_session("teacher")
                if session:
                    self.remove_knowledge_material(session, self.read_json())
            elif route == "/api/knowledge/retry":
                session = self.require_session("teacher")
                if session and not session.get("isAdmin"):
                    self.json_response({"error": "只有管理员教师可以重新识别资料"}, HTTPStatus.FORBIDDEN)
                elif session:
                    if KNOWLEDGE_SYNC_RUNNING.is_set():
                        self.json_response({"error": "资料正在识别，请等待本次处理完成"}, HTTPStatus.CONFLICT)
                    elif not os.environ.get("ARK_API_KEY") or not KNOWLEDGE_SOURCE_DIR.is_dir():
                        self.json_response({"error": "识别服务尚未开通或资料目录不可用"}, HTTPStatus.SERVICE_UNAVAILABLE)
                    else:
                        with database() as db:
                            chunks = db.execute(
                                "UPDATE knowledge_chunks SET status='queued',error='',updated_at=? "
                                "WHERE status='failed'", (utc_now(),)
                            ).rowcount
                            db.execute(
                                "UPDATE knowledge_provider_files SET provider_file_id='',status='',expires_at=0,error='' "
                                "WHERE status='failed'"
                            )
                        started = start_ark_knowledge_sync()
                        if not started:
                            self.json_response({"error": "识别服务尚未开通或资料目录不可用"}, HTTPStatus.SERVICE_UNAVAILABLE)
                        else:
                            record_event("teacher", None, "knowledge_retry", None, f"failed_chunks={chunks}")
                            self.json_response({"ok": True, "queuedChunks": chunks, "message": "已开始重新识别未完成资料"})
            elif route == "/api/files":
                session = self.require_session()
                if session:
                    content_type = self.headers.get("Content-Type", "").lower()
                    if content_type.startswith("application/json"):
                        self.upload(session, self.read_json(MAX_UPLOAD_BYTES * 2))
                    else:
                        self.upload_binary(session)
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

    def do_DELETE(self) -> None:
        if not self.check_origin():
            return
        route = urlparse(self.path).path
        if lesson_plans.handle(sys.modules[__name__], self, 'DELETE', route):
            return
        if not route.startswith("/api/files/"):
            self.json_response({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        session = self.require_session()
        if not session:
            return
        file_id = route.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[A-Za-z0-9]+", file_id):
            self.json_response({"error": "文件标识无效"}, HTTPStatus.BAD_REQUEST)
            return
        with database() as db:
            if session["role"] == "student":
                row = db.execute(
                    "SELECT original_name,stored_name FROM files WHERE id=? AND uploaded_by IN ('student-evidence','student-math-stage','student-math-lab') AND group_no=?",
                    (file_id, session["groupId"]),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT original_name,stored_name FROM files WHERE id=? AND uploaded_by<>'builtin-knowledge'",
                    (file_id,),
                ).fetchone()
            if not row:
                self.json_response({"error": "文件不存在或无权删除"}, HTTPStatus.NOT_FOUND)
                return
            target = UPLOAD_DIR / row["stored_name"]
            if target.resolve().parent == UPLOAD_DIR.resolve() and target.exists():
                target.unlink()
            db.execute("DELETE FROM files WHERE id=?", (file_id,))
        if session["role"] == "student":
            group = state_get(f"group:{session['groupId']}")
            evidence_before = group.get("evidence", [])
            evidence_after = [
                item for item in group.get("evidence", [])
                if not (
                    isinstance(item, dict)
                    and isinstance(item.get("file"), dict)
                    and item["file"].get("id") == file_id
                )
            ]
            math_tools = group.get("mathTools", {}) if isinstance(group.get("mathTools"), dict) else {}
            screenshots_before = math_tools.get("screenshots", [])
            screenshots_after = [
                item for item in math_tools.get("screenshots", [])
                if not isinstance(item, dict) or item.get("id") != file_id
            ]
            if evidence_after != evidence_before or screenshots_after != screenshots_before:
                group["evidence"] = evidence_after
                math_tools["screenshots"] = screenshots_after
                group["mathTools"] = math_tools
                state_put(f"group:{session['groupId']}", group)
        else:
            class_state = state_get("class")
            config = class_state.get("teacherConfig", {}) if isinstance(class_state.get("teacherConfig"), dict) else {}
            config["knowledge"] = [name for name in config.get("knowledge", []) if name != row["original_name"]]
            config["knowledgePending"] = [name for name in config.get("knowledgePending", []) if name != row["original_name"]]
            class_state["teacherConfig"] = config
            state_put("class", class_state)
        record_event(session["role"], session.get("groupId"), "file_delete", 4 if session["role"] == "student" else None, row["original_name"])
        self.json_response({"ok": True})

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
            saved_submissions = group_state.get("stageSubmitted", {}) if isinstance(group_state.get("stageSubmitted"), dict) else {}
            if bool(saved_submissions.get("5", saved_submissions.get(5, False))):
                saved_product = group_state.get("product", {}) if isinstance(group_state.get("product"), dict) else {}
                incoming_product = incoming.get("product", saved_product)
                saved_workspace = group_state.get("workspace", {}) if isinstance(group_state.get("workspace"), dict) else {}
                incoming_workspace = incoming.get("workspace", saved_workspace)
                product_changed = incoming_product != saved_product
                outcome_changed = any(
                    str(incoming_workspace.get(key, saved_workspace.get(key, ""))) != str(saved_workspace.get(key, ""))
                    for key in ("problem", "model")
                ) if isinstance(incoming_workspace, dict) else True
                if product_changed or outcome_changed:
                    self.json_response({"error": "阶段5已提交，成果内容已经锁定"}, HTTPStatus.CONFLICT)
                    return
        candidate = dict(group_state)
        candidate.update({key: incoming[key] for key in GROUP_KEYS if key in incoming})
        # Version history is append-only and derived from the model actually saved.
        # Old clients cannot erase history or complete a task with empty versions.
        candidate["versions"] = group_state.get("versions", [])
        ensure_model_version(candidate, utc_now())
        incoming["versions"] = candidate.get("versions", [])
        requested = incoming.get("stageSubmitted", {}) if isinstance(incoming.get("stageSubmitted"), dict) else {}
        saved_submissions = group_state.get("stageSubmitted", {}) if isinstance(group_state.get("stageSubmitted"), dict) else {}
        valid_submissions = validated_stage_submissions(candidate)
        for number in range(1, 6):
            wants_submit = bool(requested.get(str(number), requested.get(number, False)))
            was_submitted = bool(saved_submissions.get(str(number), saved_submissions.get(number, False)))
            if session["role"] == "student" and wants_submit and not was_submitted:
                classroom = state_get("class")
                if classroom.get("stagePaused") or number != int(classroom.get("currentStage", 1)):
                    self.json_response({"error": "只能提交当前开放且未暂停的阶段"}, HTTPStatus.CONFLICT)
                    return
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
                handled = []
                for item in incoming_queue:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("id"))
                    if key in existing:
                        was_resolved = bool(existing[key].get("resolved"))
                        existing[key]["resolved"] = bool(item.get("resolved"))
                        if existing[key]["resolved"]:
                            existing[key]["feedback"] = str(item.get("feedback", "")).strip()[:800]
                            existing[key]["resolvedAt"] = str(item.get("resolvedAt") or utc_now())
                            existing[key]["resolvedBy"] = str(session.get("username", "教师"))[:80]
                            if not was_resolved:
                                handled.append(existing[key])
                    else:
                        existing[key] = item
                class_state["queue"] = list(existing.values())[-100:]
                for item in handled:
                    item_group = int(item.get("group", 0) or 0)
                    thread_id = str(item.get("threadId", ""))
                    if not item_group or not thread_id:
                        continue
                    item_state = state_get(f"group:{item_group}")
                    threads = item_state.get("helpThreads", []) if isinstance(item_state.get("helpThreads"), list) else []
                    thread = next((row for row in threads if isinstance(row, dict) and str(row.get("id")) == thread_id), None)
                    if thread:
                        thread["status"] = "resolved"
                        thread["resolvedAt"] = item["resolvedAt"]
                        thread["teacherFeedback"] = item["feedback"] or "教师已处理，请根据课堂中的反馈继续完成。"
                        thread["resolvedBy"] = item["resolvedBy"]
                        if item_state.get("activeHelpThreadId") == thread_id:
                            item_state["activeHelpThreadId"] = ""
                        item_state["helpThreads"] = threads
                        state_put(f"group:{item_group}", item_state)
                        self.sync_group_summary(item_group, item_state)
            state_put("class", class_state)
        self.sync_group_summary(group, group_state)
        record_event(session["role"], group, "state_save", incoming.get("currentStage"), "workspace")
        self.json_response({"ok": True, "updatedAt": utc_now(), "state": merged_state(group, monitoring=session["role"] == "teacher")})

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
            db.execute("DELETE FROM lesson_jobs")
            db.execute("DELETE FROM course_material")
            db.execute("DELETE FROM course_resources")
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
            self.json_response({"ok": True, "members": group_state.get("members", []), "membersLocked": False, "state": merged_state(group, monitoring=True)})
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
        self.json_response({"ok": True, "members": members, "roles": group_state["roles"], "membersLocked": group_state["membersLocked"], "state": merged_state(group, monitoring=session["role"] == "teacher")})

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
        state = merged_state(group, monitoring=False)
        enabled, quota = free_ai_settings(state)
        if not enabled or not state.get("aiOpen", True):
            raise UpstreamServiceError("自由工作区已由教师暂停", HTTPStatus.CONFLICT)
        prompt = str(body.get("prompt", "")).strip()[:1800]
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

    def compare_solutions(self, session: dict, body: dict) -> None:
        group = int(session["groupId"])
        state = merged_state(group, monitoring=False)
        if not state.get("aiOpen", True):
            raise UpstreamServiceError("AI已由教师暂停", HTTPStatus.CONFLICT)
        try:
            stage = int(body.get("stage", 3))
        except (TypeError, ValueError) as exc:
            raise ValueError("阶段参数无效") from exc
        if stage not in {3, 4}:
            raise ValueError("方案比较仅支持阶段3或阶段4")
        raw_viewpoints = body.get("viewpoints")
        if not isinstance(raw_viewpoints, list) or len(raw_viewpoints) != 2:
            raise ValueError("请提供两个待比较方案")
        viewpoints = []
        for index, raw in enumerate(raw_viewpoints):
            if not isinstance(raw, dict):
                raise ValueError("方案内容格式无效")
            item = {
                "who": str(raw.get("who", f"方案{'AB'[index]}"))[:40],
                "claim": str(raw.get("claim", "")).strip()[:1200],
                "basis": str(raw.get("basis", "")).strip()[:1200],
                "risk": str(raw.get("risk", "")).strip()[:1200],
            }
            if not item["claim"] or not item["basis"]:
                raise ValueError("两个方案都需要填写方案内容和依据")
            viewpoints.append(item)
        prompt = (
            f"请协助初中学生比较阶段{stage}中的两个项目方案。"
            "只根据学生提供的信息分析，不虚构数据，也不替小组直接决定最终方案。"
            "请依次给出：1. 可比性检查；2. 各方案依据的强项与不足；"
            "3. 风险、限制和缺失证据；4. 小组下一步应核验的2至4个具体问题。"
            f"两个方案如下：{json.dumps(viewpoints, ensure_ascii=False)}"
        )
        answer, latency, status, model = ark_free_chat(state, group, prompt, [])
        result = "returned" if answer else "failed"
        with database() as db:
            db.execute(
                "INSERT INTO ai_calls(ts,group_no,level,model,latency_ms,status,detail) VALUES(?,?,?,?,?,?,?)",
                (utc_now(), group, "COMPARE", model, latency, result, status[:1000]),
            )
        if not answer:
            if status == "not_configured":
                raise UpstreamServiceError("方案比较AI尚未配置", HTTPStatus.SERVICE_UNAVAILABLE)
            raise UpstreamServiceError("方案比较AI暂时不可用，请稍后重试", HTTPStatus.BAD_GATEWAY)
        record_event("student", group, "ai_compare", stage, "方案A/B")
        self.json_response({"ok": True, "answer": answer, "model": model, "latencyMs": latency})

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
        self.send_header("X-Accel-Buffering", "no")
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
        state = merged_state(group, monitoring=False)
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
            thread["type"] = issue_type
            thread["currentLevel"] = next_level_no
            thread.setdefault("transitions", []).append({"time": utc_now(), "from": f"L{level_no}", "to": next_level, "attempt": attempt})
            reply, latency, status = deepseek_hint(
                state, next_level, attempt, issue_type, issue, messages,
                bool(body.get("checkedWork")),
            )
            result = "returned" if reply else "fallback"
            if not reply:
                reply = static_hint(state, next_level_no)
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
            reply = static_hint(state, level_no)
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
            "submitted": submitted_count,
            "currentSubmitted": validated_stage_submissions(group_state).get(class_current_stage(class_state), False),
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

    def submit_stage(self, session: dict, body: dict) -> None:
        group = int(session["groupId"])
        class_state = state_get("class")
        stage_no = class_current_stage(class_state)
        try:
            requested_stage = int(body.get("stage", stage_no))
        except (TypeError, ValueError) as exc:
            raise ValueError("阶段编号无效") from exc
        if requested_stage != stage_no or class_state.get("stagePaused"):
            self.json_response({"error": "只能提交当前开放且未暂停的阶段"}, HTTPStatus.CONFLICT)
            return
        group_state = state_get(f"group:{group}")
        if not all(stage_task_status(group_state)[stage_no - 1]):
            self.json_response({"error": f"阶段{stage_no}任务尚未全部完成，不能提交"}, HTTPStatus.CONFLICT)
            return
        submissions = group_state.get("stageSubmitted", {})
        submissions = dict(submissions) if isinstance(submissions, dict) else {}
        submissions[str(stage_no)] = True
        group_state["stageSubmitted"] = submissions
        state_put(f"group:{group}", group_state)
        self.sync_group_summary(group, group_state)
        record_event("student", group, "stage_submit", stage_no, f"阶段{stage_no}产出")
        self.json_response({"ok": True, "state": merged_state(group, monitoring=False)})

    def remove_knowledge_material(self, session: dict, body: dict) -> None:
        name = Path(str(body.get("name", ""))).name[:180]
        if not name:
            raise ValueError("材料名称无效")
        with database() as db:
            rows = db.execute(
                "SELECT id,stored_name FROM files WHERE original_name=? AND uploaded_by='teacher'", (name,)
            ).fetchall()
            for row in rows:
                target = UPLOAD_DIR / row["stored_name"]
                if target.resolve().parent == UPLOAD_DIR.resolve() and target.exists():
                    target.unlink()
            db.execute("DELETE FROM files WHERE original_name=? AND uploaded_by='teacher'", (name,))
        class_state = state_get("class")
        config = class_state.get("teacherConfig", {}) if isinstance(class_state.get("teacherConfig"), dict) else {}
        config["knowledge"] = [item for item in config.get("knowledge", []) if item != name]
        config["knowledgePending"] = [item for item in config.get("knowledgePending", []) if item != name]
        class_state["teacherConfig"] = config
        state_put("class", class_state)
        record_event("teacher", None, "file_delete", None, name)
        self.json_response({"ok": True, "state": merged_state(1)})

    def upload(self, session: dict, body: dict) -> None:
        name = Path(str(body.get("name", ""))).name[:180]
        try:
            raw = base64.b64decode(str(body.get("content", "")), validate=True)
        except ValueError as exc:
            raise ValueError("文件内容无效") from exc
        self.store_upload(session, name, raw)

    def upload_binary(self, session: dict) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("文件长度无效") from exc
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError("文件为空或超过上传限制")
        name = Path(unquote(self.headers.get("X-File-Name", ""))).name[:180]
        raw = self.rfile.read(length)
        self._body_read = True
        self.store_upload(session, name, raw)

    def store_upload(self, session: dict, name: str, raw: bytes) -> None:
        suffix = Path(name).suffix.lower()
        allowed = TEACHER_KNOWLEDGE_UPLOADS if session["role"] == "teacher" else ALLOWED_UPLOADS
        if not name or suffix not in allowed:
            if session["role"] == "teacher":
                raise ValueError("教师补充材料仅支持 PDF、Word（.docx）和 Markdown（.md）文件")
            raise ValueError("不支持此文件类型")
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError("文件为空或超过上传限制")
        file_id = secrets.token_hex(12)
        stored = f"{file_id}{suffix}"
        (UPLOAD_DIR / stored).write_bytes(raw)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        extracted = extract_document(name, raw)
        purpose = self.headers.get("X-File-Purpose", "")
        student_purposes = {"math-stage": "student-math-stage", "math-lab": "student-math-lab"}
        uploaded_by = "teacher" if session["role"] == "teacher" else student_purposes.get(purpose, "student-evidence")
        group_no = None if session["role"] == "teacher" else session.get("groupId")
        with database() as db:
            db.execute("INSERT INTO files(id,original_name,stored_name,mime,size,extracted_text,uploaded_at,uploaded_by,group_no) VALUES(?,?,?,?,?,?,?,?,?)", (file_id, name, stored, mime, len(raw), extracted, utc_now(), uploaded_by, group_no))
        if session["role"] == "teacher":
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
        record_event(session["role"], group_no, "file_upload", 4 if session["role"] == "student" else None, name)
        self.json_response({"file": {"id": file_id, "name": name, "mime": mime, "size": len(raw), "uploadedBy": uploaded_by, "uploadedAt": utc_now(), "indexed": bool(extracted)}})

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
    knowledge_sync = sync_builtin_knowledge()
    start_ark_knowledge_sync()
    env = os.environ.get("MPT_ENV", "development").lower()
    browser_host = "127.0.0.1" if HOST in {"0.0.0.0", "::"} else HOST
    print(f"迈谱 Maple 已启动：http://{browser_host}:{PORT}/")
    if browser_host != HOST:
        print(f"监听地址：{HOST}:{PORT}（浏览器请使用上面的本地地址）")
    print(f"服务根目录：{ROOT}")
    print(f"数据目录：{DATA_DIR}")
    if knowledge_sync["found"]:
        print(
            f"知识库资料：发现{knowledge_sync['found']}份，新增{knowledge_sync['imported']}份，"
            f"可检索{knowledge_sync['indexed']}份"
        )
    print(f"运行模式：{env}；DeepSeek：{'已配置' if os.environ.get('DEEPSEEK_API_KEY') else '未配置，将使用教师静态支架'}")
    if not is_initialized():
        print("平台尚未初始化，请在登录页完成首位教师注册。")
        if SETUP_TOKEN_DISPLAY:
            print(f"本机一次性教师注册码：{SETUP_TOKEN_DISPLAY}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
