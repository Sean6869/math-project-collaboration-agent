"""启动临时服务并检查全部页面、静态资源和健康接口。"""

import json
import gzip
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

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
            pages = sorted(path for path in ROOT.rglob("*.html") if "vendor" not in path.relative_to(ROOT).parts)
            for path in pages:
                route = "/" + quote(path.relative_to(ROOT).as_posix(), safe="/")
                with urllib.request.urlopen(base + route, timeout=3) as response:
                    body = response.read().decode("utf-8")
                assert response.status == 200 and 'id="root"' in body and "迈谱 Maple" in body, route
            vendor_pages = sorted((ROOT / "vendor" / "math-viz-kit").rglob("*.html"))
            for path in vendor_pages:
                route = "/" + quote(path.relative_to(ROOT).as_posix(), safe="/")
                with urllib.request.urlopen(base + route, timeout=3) as response:
                    assert response.status == 200 and response.read(), route
            resources = (
                "/assets/styles.css",
                "/assets/data.js",
                "/assets/app.js",
                "/assets/lesson-plan.js",
                "/assets/images/starry-night-login.webp",
                "/assets/images/maple-login-light.webp",
            )
            for route in resources:
                with urllib.request.urlopen(base + route, timeout=3) as response:
                    assert response.status == 200 and response.read(), route
            compressed_request = urllib.request.Request(base + "/assets/app.js", headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(compressed_request, timeout=3) as response:
                compressed_body = response.read()
                etag = response.headers["ETag"]
                assert response.headers["Content-Encoding"] == "gzip"
                assert b"function" in gzip.decompress(compressed_body)
            cached_request = urllib.request.Request(
                base + "/assets/app.js",
                headers={"Accept-Encoding": "gzip", "If-None-Match": etag},
            )
            try:
                urllib.request.urlopen(cached_request, timeout=3)
                raise AssertionError("静态资源 ETag 未生效")
            except urllib.error.HTTPError as error:
                assert error.code == 304
            with urllib.request.urlopen(base + "/api/health", timeout=3) as response:
                health = json.loads(response.read())
            assert health["status"] == "ok"
            for route in ("/teacher/versions.html", "/teacher/research.html", "/expert/overview.html"):
                try:
                    urllib.request.urlopen(base + route, timeout=3)
                    raise AssertionError(f"已删除页面仍可访问：{route}")
                except urllib.error.HTTPError as error:
                    assert error.code == 404, route
            print(
                f"通过：{len(pages)} 个平台页面、{len(vendor_pages)} 个可视化页面、"
                f"{len(resources)} 个静态资源、gzip/ETag 和健康接口"
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
