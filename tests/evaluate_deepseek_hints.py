"""使用当前服务器环境中的 DeepSeek 密钥验证 L1—L3 教学支架。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import platform_server as app


def has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def assess(level: str, reply: str) -> list[str]:
    failures = []
    limits = {"L1": (45, 320), "L2": (90, 450), "L3": (150, 650)}
    minimum, maximum = limits[level]
    if not minimum <= len(reply) <= maximum:
        failures.append(f"长度应在{minimum}—{maximum}字之间，实际{len(reply)}字")
    for section in ("诊断：", "支架：", "下一步："):
        if section not in reply:
            failures.append(f"缺少{section[:-1]}部分")
    if app.validate_hint(reply, level) is None:
        failures.append("触发答案越界或格式校验")
    if level == "L1":
        if has_any(reply, ("完整公式", "完整模型", "最优值", "最终答案", "写出函数", "写出表达式", "列出不等式")):
            failures.append("L1给出了超层级信息")
        if not has_any(reply, ("检查", "标出", "圈出", "区分", "写出", "确认", "问题")):
            failures.append("L1缺少聚焦检查动作")
    elif level == "L2":
        markers = sum(word in reply for word in ("先", "再", "然后", "第一", "第二", "子问题", "步骤", "分别"))
        if markers < 2:
            failures.append("L2未形成清晰的分步结构")
    else:
        if not has_any(reply, ("例如", "例子", "类比", "示范", "一步", "局部")):
            failures.append("L3缺少局部示范或类比")
        if not has_any(reply, ("由你完成", "请你完成", "学生完成", "小组完成", "留给你", "自行完成", "先停在这一步")):
            failures.append("L3没有明确保留学生继续完成的步骤")
    return failures


def main() -> int:
    if not app.os.environ.get("DEEPSEEK_API_KEY"):
        print(json.dumps({"passed": False, "error": "DEEPSEEK_API_KEY 未配置"}, ensure_ascii=False))
        return 2

    state = {
        "project": "校园种植基地最大面积",
        "currentStage": 3,
        "teacherConfig": {
            "mathTopic": "二次函数与面积优化",
            "standard": "建立面积函数，说明变量范围，通过图像、顶点或边界检验比较方案",
            "knowledge": [],
        },
        "workspace": {
            "problem": "在矩形场地内设置等宽小路，设计可种植面积最大的方案",
            "known": "外框周长固定，小路等宽",
            "unknown": "可种植面积最大时的尺寸",
            "constraints": "边长与小路宽度均为正，种植区尺寸必须可行",
            "variables": "尝试用x表示一条边长",
            "relation": "准备使用外框面积减去小路面积",
        },
    }
    cases = [
        ("L1", "已经画出外框和小路", "目标定位", "不知道应该优化可种植面积还是整个外框面积"),
        ("L1", "已把可种植面积圈出来", "条件定位", "仍不清楚小路宽度会同时影响哪些边"),
        ("L2", "已标出受小路宽度影响的边", "关系拆解", "怎样把现有条件分成可计算的数量关系"),
        ("L3", "已用x表示外框一条边，并尝试用外框减小路", "局部核验", "计算时总会重复扣除小路交叉区域"),
    ]
    history = []
    results = []
    for level, done, issue_type, issue in cases:
        reply, latency, status = app.deepseek_hint(
            state, level, done, issue_type, issue, history, peer_tried=True, checked_work=True,
        )
        failures = [f"接口状态：{status}"] if status != "ok" else []
        if reply:
            failures.extend(assess(level, reply))
            history.append({"level": level, "question": issue, "done": done, "reply": reply})
        else:
            failures.append("模型回答未通过平台结构或边界校验")
        results.append({
            "level": level,
            "latencyMs": latency,
            "reply": reply or "",
            "passed": not failures,
            "failures": failures,
        })

    l1_replies = [item["reply"] for item in results if item["level"] == "L1"]
    if len(l1_replies) == 2 and l1_replies[0] and l1_replies[0] == l1_replies[1]:
        results[1]["passed"] = False
        results[1]["failures"].append("同层连续提问得到完全相同的回答")
    passed = all(item["passed"] for item in results)
    print(json.dumps({"passed": passed, "model": app.DEEPSEEK_MODEL, "results": results}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
