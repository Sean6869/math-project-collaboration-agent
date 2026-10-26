"""Disposable real API server for classroom browser acceptance."""
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platform_server as app

for key in ('ARK_API_KEY', 'MPT_TEACHER_USERNAME', 'MPT_TEACHER_PASSWORD', 'MPT_GROUP_COUNT'):
    os.environ.pop(key, None)
os.environ['MPT_ENV'] = 'development'
if '--camera-ui' in sys.argv:
    # Only this isolated fixture uses deterministic visual responses.
    app.ark_recognize_images = lambda urls: '预算100元；单价8元；数量10个。'
    app.ark_free_chat_stream = lambda state, group, prompt, history, on_delta: ('请核对照片中的单位。', 1, 'ok', 'test-vision')
with tempfile.TemporaryDirectory(prefix='classroom-ui-') as folder:
    app.DATA_DIR = Path(folder); app.UPLOAD_DIR = app.DATA_DIR / 'uploads'
    app.DB_PATH = app.DATA_DIR / 'test.db'; app.SETUP_TOKEN_FILE = app.DATA_DIR / 'setup-token'
    app.init_database()
    app.initialize_platform('ui_teacher', 'ClassroomTeacher#2026', count=3)
    with app.database() as db:
        db.execute("UPDATE teachers SET student_login_code='9512' WHERE username='ui_teacher'")
    with app.database() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?,?)', ('ui-project', '小小采购员', app.utc_now(), 1))
    app.PROJECT_CONTEXT.set('ui-project')
    app.state_put('class', {'project': '小小采购员', 'currentStage': 5, 'groupCount': 3, 'aiOpen': True, 'teacherConfig': {'mathTopic': '用数据选择方案', 'staticHints': ['检查数量与单位']}})
    for n in range(1, 4):
        app.state_put(f'group:{n}', {'groupId': n, 'members': ['同学甲', '同学乙', '同学丙', '同学丁'], 'membersLocked': True,
            'roles': ['建模者', '质疑者', '记录者', '解释者'], 'roleRound': 1,
            'workspace': {'model': '100元预算', 'variables': '金额，单位元', 'relation': '预算不少于支出', 'constraints': '总预算100元'},
            'firstThoughts': {}, 'preparationChecks': {'goal': True, 'materials': True, 'rules': True}})
    app.Handler.log_message = lambda *args: None
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    try:
        sys.stdin.read()
    finally:
        server.shutdown(); server.server_close(); worker.join()
