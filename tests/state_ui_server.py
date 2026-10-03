"""Disposable server for browser state regression; never uses the classroom DB."""
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platform_server as app

with tempfile.TemporaryDirectory(prefix="maple-state-test-") as directory:
    app.DATA_DIR = Path(directory)
    app.UPLOAD_DIR = app.DATA_DIR / "uploads"
    app.DB_PATH = app.DATA_DIR / "platform.db"
    app.SETUP_TOKEN_FILE = app.DATA_DIR / "setup-token.txt"
    app.init_database()
    app.initialize_platform("state_teacher", "StateTeacher#2026", "STATE-RECOVERY-2026", "STATECLASS", "TEAM", 1)
    app.state_put("class", {"project": "状态回归项目", "currentStage": 1, "aiOpen": True, "teacherConfig": {"staticHints": ["打开任务理解表填写已知信息", "静态提示二", "静态提示三"]}})
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    app.Handler.log_message = lambda *args: None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    try:
        sys.stdin.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
