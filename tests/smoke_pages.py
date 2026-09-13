"""启动临时服务并检查全部页面、静态资源和健康接口。"""

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import platform_server as app


def main():
    with tempfile.TemporaryDirectory() as directory:
        app.DATA_DIR = Path(directory)
        app.UPLOAD_DIR = app.DATA_DIR / "uploads"
        app.DB_PATH = app.DATA_DIR / "smoke.db"
        app.SETUP_TOKEN_FILE = app.DATA_DIR / "setup-token.txt"
        app.SETUP_TOKEN_DISPLAY = ""
        app.init_database()
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            pages = sorted(ROOT.rglob("*.html"))
            for path in pages:
                route = "/" + path.relative_to(ROOT).as_posix()
                with urllib.request.urlopen(base + route, timeout=3) as response:
                    body = response.read().decode("utf-8")
                assert response.status == 200 and 'id="root"' in body and "迈谱 Maple" in body, route
            resources = (
                "/assets/styles.css",
                "/assets/data.js",
                "/assets/app.js",
                "/assets/lesson-plan.js",
                "/assets/images/starry-night-login.jpg",
                "/assets/images/maple-brand.png",
                "/assets/images/maple-horizontal.png",
                "/assets/images/maple-login-light.png",
                "/assets/images/maple-mark-light.png",
            )
            for route in resources:
                with urllib.request.urlopen(base + route, timeout=3) as response:
                    assert response.status == 200 and response.read(), route
            with urllib.request.urlopen(base + "/api/health", timeout=3) as response:
                health = json.loads(response.read())
            assert health["status"] == "ok"
            for route in ("/teacher/versions.html", "/teacher/research.html", "/expert/overview.html"):
                try:
                    urllib.request.urlopen(base + route, timeout=3)
                    raise AssertionError(f"已删除页面仍可访问：{route}")
                except urllib.error.HTTPError as error:
                    assert error.code == 404, route
            print(f"通过：{len(pages)} 个页面、{len(resources)} 个静态资源和健康接口")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
