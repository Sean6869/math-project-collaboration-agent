"""State contract regressions: old models, real versions, all five task matrices."""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import platform_server as app


def complete_state():
    return {
        "members": ["甲", "乙"], "membersLocked": True,
        "preparationChecks": dict(goal=True, materials=True, rules=True),
        "firstThoughts": {"0": "比较价格", "1": "预算约束"},
        "workspace": {key: "本组记录" for key in ("problem", "known", "unknown", "constraints", "variables", "relation", "model")},
        "questionTree": ["核心问题", "子问题一", "子问题二"],
        "versions": [{"v": "V1", "model": "初稿"}, {"v": "V2", "model": "本组记录"}],
        "graphConfig": dict(a=0, b=1, c=0, min=0, max=10, note="已核验边界"),
        "viewpoints": [dict(claim="A", basis="计算", risk="单价变化"), dict(claim="B", basis="测量", risk="误差")],
        "evidence": [dict(text="计算过程", verified=True)],
        "product": dict(audience="同学", claim="结论", limitations="范围", evidenceValidation="计算与实测一致", rehearsal="预演记录", rehearsalChecks=[True]*4, reflection="数学工作\n提示帮助\n迁移计划"),
        "stageSubmitted": {str(n): True for n in range(1, 6)},
    }


class StateConsistencyTest(unittest.TestCase):
    def test_teacher_stage_is_single_source_for_student_page_access(self):
        student = {"role": "student", "groupId": 1}
        teacher = {"role": "teacher"}
        self.assertFalse(app.student_stage_page_locked("/student/stage2.html", student, 2))
        self.assertFalse(app.student_stage_page_locked("/student/stage2-model.html", student, 2))
        self.assertTrue(app.student_stage_page_locked("/student/stage3.html", student, 2))
        self.assertTrue(app.student_stage_page_locked("/student/stage5-reflection.html", student, 2))
        self.assertFalse(app.student_stage_page_locked("/student/stage5.html", teacher, 2))
        self.assertFalse(app.student_stage_page_locked("/teacher/stages.html", student, 2))

    def test_frontend_backend_task_matrix_matches(self):
        states = [{}, complete_state()]
        changes = [
            ("versions", []), ("versions", [{}]),
            ("graphConfig", dict(a=0, b=0, c=0, min=3, max=1, note="无效范围")),
            ("graphConfig", dict(a="", b=0, c=0, min=0, max=1, note="缺系数")),
            ("viewpoints", [{}, *complete_state()["viewpoints"]]),
            ("product", {}), ("membersLocked", False), ("questionTree", ["", "a", "b"]),
            ("firstThoughts", {"0": "只有一个成员"}), ("evidence", []),
        ]
        for key, value in changes:
            state = complete_state(); state[key] = value; states.append(state)
        for checks in ({str(i): True for i in range(6)}, {"old": False, **{str(i): True for i in range(6)}}, {str(i): True for i in range(1, 7)}):
            state = complete_state(); state["graphConfig"] = {}; state["checklist"] = checks; states.append(state)
        script = """
const vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('assets/app.js','utf8').replace('window.MPT={boot,load,save,reset,toast}', 'window.MPT={stageTaskReady,normalizeState}');
const context={window:{MPT_DATA:{stages:[]}},document:{addEventListener(){}},setInterval(){}};
vm.runInNewContext(source,context);
const states=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(states.map(s=>Array.from({length:5},(_,n)=>Array.from({length:4},(_,i)=>context.window.MPT.stageTaskReady(context.window.MPT.normalizeState(s),n+1,i))))));
"""
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, input=json.dumps(states), text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [app.stage_task_status(s) for s in states])
        self.assertEqual(app.stage_task_status(complete_state()), [[True]*4]*5)

    def test_versions_are_real_snapshots_and_unchanged_save_is_idempotent(self):
        state = {"workspace": {"model": "预算=单价×数量", "constraints": "不超过100元"}}
        app.ensure_model_version(state, "first")
        self.assertEqual(state["versions"][0]["model"], state["workspace"]["model"])
        app.ensure_model_version(state, "repeat")
        self.assertEqual(len(state["versions"]), 1)
        state["workspace"]["constraints"] = "不超过80元"
        app.ensure_model_version(state, "second")
        self.assertEqual(len(state["versions"]), 2)
        self.assertEqual(state["versions"][0]["constraints"], "不超过100元")
        empty = {"workspace": {"model": "  "}, "versions": [{}]}
        app.ensure_model_version(empty, "ignored")
        self.assertEqual(app.model_versions(empty), [])

    def test_old_saved_model_recovers_once_without_fabricated_submissions(self):
        original = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.DB_PATH = Path(folder)/"old.db"
                with app.database() as db:
                    db.execute("CREATE TABLE states(scope TEXT PRIMARY KEY,data TEXT,updated_at TEXT)")
                    db.execute("INSERT INTO states VALUES(?,?,?)", ("group:1", json.dumps({"workspace": {"model": "旧模型"}, "versions": []}), "2026-09-13T12:00:00Z"))
                state = app.state_get("group:1")
                self.assertEqual(len(state["versions"]), 1)
                self.assertEqual(state["versions"][0]["time"], "2026-09-13T12:00:00Z")
                self.assertTrue(app.stage_task_status(state)[2][2])
                self.assertFalse(any(app.validated_stage_submissions(state).values()))
                app.state_put("group:1", state)
                self.assertEqual(app.state_get("group:1")["versions"], state["versions"])
        finally:
            app.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
