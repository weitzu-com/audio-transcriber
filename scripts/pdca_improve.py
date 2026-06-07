#!/usr/bin/env python3
"""
pdca_improve.py — PDCA 进化引擎
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Plan → Do → Check → Act → Verify 自动闭环
输入 (stdin JSON):  {"perf_stats": {...}, "mode": "single|batch", "iteration_count": N,
                     "quality_score": float}
输出 (stdout JSON): {"iteration": N, "auto_applied": [...], "suggestions": [...],
                     "version_bump": "patch|minor|major", "score_delta": float}
契约版本: 0.1.0
"""

import json, sys, os, datetime

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_PATH = os.path.join(SKILL_ROOT, "assets", "version.json")
LOG_PATH = os.path.join(SKILL_ROOT, "assets", "pdca.log")
QUEUE_PATH = os.path.join(SKILL_ROOT, "assets", "improve_queue.json")

# 低风险参数（可自动调整）
LOW_RISK_PARAMS = ["vad_parameters.min_silence_duration_ms",
                   "vad_parameters.speech_pad_ms", "beam_size"]
LOW_RISK_RANGES = {
    "vad_parameters.min_silence_duration_ms": [300, 400, 500, 600, 700],
    "vad_parameters.speech_pad_ms": [200, 300, 400, 500],
    "beam_size": [3, 5],
}

# 自动改进阈值
AUTO_IMPROVE_INTERVAL = 10  # 每 10 次迭代尝试自动改进
QUALITY_DROP_THRESHOLD = 0.1  # 质量下降超过此值回滚
NO_SPEECH_HIGH_THRESHOLD = 0.15  # no_speech_ratio 超此值触发告警


def load_version():
    if os.path.exists(VERSION_PATH):
        with open(VERSION_PATH, "r") as f:
            return json.load(f)
    return {
        "version": "0.1.0",
        "iteration_count": 0,
        "defaults": {},
        "history": [],
    }


def save_version(v):
    os.makedirs(os.path.dirname(VERSION_PATH), exist_ok=True)
    with open(VERSION_PATH, "w") as f:
        json.dump(v, f, ensure_ascii=False, indent=2)


def load_recent_logs(n=20):
    """加载最近的性能日志"""
    if not os.path.exists(LOG_PATH):
        return []
    logs = []
    with open(LOG_PATH, "r") as f:
        for line in f:
            try:
                logs.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
    return logs[-n:]


def analyze_trend(logs: list) -> dict:
    """分析性能趋势"""
    if len(logs) < 3:
        return {"trend": "insufficient_data", "direction": "neutral"}

    recent_q = [l.get("quality_score", 0) for l in logs[-5:]]
    older_q = [l.get("quality_score", 0) for l in logs[:-5]] if len(logs) > 5 else recent_q

    avg_recent = sum(recent_q) / len(recent_q) if recent_q else 0
    avg_older = sum(older_q) / len(older_q) if older_q else avg_recent

    delta = avg_recent - avg_older
    direction = "improving" if delta > 0.02 else ("declining" if delta < -0.02 else "stable")

    return {
        "trend": "analyzed",
        "direction": direction,
        "avg_quality_recent": round(avg_recent, 4),
        "avg_quality_older": round(avg_older, 4),
        "delta": round(delta, 4),
        "sample_size": len(logs),
    }


def generate_auto_improvements(version: dict, perf_stats: dict, logs: list) -> list:
    """生成自动改进建议（仅低风险参数）"""
    auto = []

    no_speech = perf_stats.get("no_speech_ratio", 0)

    # 如果 no_speech 过高，调低 VAD 静音阈值
    if no_speech > NO_SPEECH_HIGH_THRESHOLD:
        current = version.get("defaults", {}).get("vad_parameters", {}).get(
            "min_silence_duration_ms", 500)
        idx = LOW_RISK_RANGES["vad_parameters.min_silence_duration_ms"].index(current) if current in LOW_RISK_RANGES["vad_parameters.min_silence_duration_ms"] else 2
        new_val = LOW_RISK_RANGES["vad_parameters.min_silence_duration_ms"][min(idx + 1, 4)]
        auto.append({
            "param": "vad_parameters.min_silence_duration_ms",
            "from": current,
            "to": new_val,
            "reason": f"no_speech_ratio ({no_speech:.2%}) 过高，放宽 VAD 静音检测",
        })

    # 如果 avg_logprob 低（质量差），尝试提高 beam_size
    avg_logprob = perf_stats.get("avg_logprob", -1)
    if avg_logprob < -0.8:
        current = version.get("defaults", {}).get("beam_size", 3)
        if current < 5:
            auto.append({
                "param": "beam_size",
                "from": current,
                "to": 5,
                "reason": f"avg_logprob ({avg_logprob}) 偏低，提高 beam_size 改善质量",
            })

    return auto


def generate_suggestions(perf_stats: dict, trend: dict, version: dict) -> list:
    """生成改进建议（含中高风险，需人工确认）"""
    suggestions = []

    # 中风险：模型切换建议
    model = perf_stats.get("model", "medium")
    rt_factor = perf_stats.get("realtime_factor", 0)
    quality = perf_stats.get("quality_score", 0)

    if model == "medium" and quality < -0.5:
        suggestions.append({
            "risk": "medium",
            "action": "切换到 large-v3 模型",
            "reason": f"medium 模型质量分 ({quality}) 低于阈值，large-v3 可改善日语等语言的识别准确率",
            "cost": "转写时间增加约 5-10x，内存占用增加约 3x",
            "next_iteration_test": "A/B 对比: medium vs large-v3",
        })

    if model == "tiny" and rt_factor > 50:
        suggestions.append({
            "risk": "medium",
            "action": "从 tiny 升级到 medium 模型",
            "reason": f"tiny 模型速度快 ({rt_factor}x) 但质量可能不足",
            "next_iteration_test": "对比转写结果的前 100 段准确率",
        })

    # 高风险：趋势恶化
    if trend.get("direction") == "declining":
        suggestions.append({
            "risk": "high",
            "action": f"质量趋势恶化 (Δ={trend['delta']})，建议人工审查最近 {trend['sample_size']} 次转写结果",
            "reason": "连续质量下降可能表明音频源变化、模型退化或参数不当",
            "files": [LOG_PATH, VERSION_PATH],
        })

    return suggestions


def bump_version(version: dict, auto_applied: list, suggestions: list) -> str:
    """根据改进类型确定版本号升级"""
    has_high = any(s.get("risk") == "high" for s in suggestions)
    has_medium = any(s.get("risk") == "medium" for s in suggestions)
    has_low = len(auto_applied) > 0

    if has_high:
        return "major"
    elif has_medium:
        return "minor"
    elif has_low:
        return "patch"
    return "patch"


def main():
    input_data = json.load(sys.stdin)
    perf_stats = input_data.get("perf_stats", {})
    mode = input_data.get("mode", "single")

    version = load_version()
    iteration = version.get("iteration_count", 0) + 1
    prev_score = version.get("history", [{}])[-1].get("quality_score", 0) if version.get("history") else 0
    score_delta = round(perf_stats.get("quality_score", 0) - prev_score, 4)

    # Check: 分析趋势
    logs = load_recent_logs()
    trend = analyze_trend(logs)

    # Act: 生成改进
    auto_applied = []
    suggestions = []

    # 仅每 AUTO_IMPROVE_INTERVAL 次迭代尝试自动改进（避免频繁抖动）
    if iteration % AUTO_IMPROVE_INTERVAL == 0:
        auto_applied = generate_auto_improvements(version, perf_stats, logs)

    suggestions = generate_suggestions(perf_stats, trend, version)

    # 应用自动改进（低风险）
    for improvement in auto_applied:
        if "vad_parameters" in improvement.get("param", ""):
            version.setdefault("defaults", {}).setdefault("vad_parameters", {})
            version["defaults"]["vad_parameters"]["min_silence_duration_ms"] = improvement.get("to")
        elif improvement.get("param") == "beam_size":
            version["defaults"]["beam_size"] = improvement.get("to")

    # 中等风险建议：写入 improve_queue 等待 A/B 测试
    queue = {"pending": [], "applied": [], "rejected": []}
    if os.path.exists(QUEUE_PATH):
        try:
            with open(QUEUE_PATH, "r") as f:
                queue = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    for s in suggestions:
        if s["risk"] == "medium" and s not in queue["pending"]:
            queue["pending"].append({**s, "iteration": iteration, "timestamp": datetime.datetime.now().isoformat()})

    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    with open(QUEUE_PATH, "w") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    # 版本号升级
    version_bump = bump_version(version, auto_applied, suggestions)
    parts = version["version"].split(".")
    if version_bump == "major":
        parts[0] = str(int(parts[0]) + 1)
        parts[1] = "0"
    elif version_bump == "minor":
        parts[1] = str(int(parts[1]) + 1)
    else:
        parts[2] = str(int(parts[2]) + 1)
    version["version"] = ".".join(parts)

    # 更新版本文件
    version["iteration_count"] = iteration
    version["history"].append({
        "iteration": iteration,
        "version": version["version"],
        "quality_score": perf_stats.get("quality_score"),
        "score_delta": score_delta,
        "trend": trend["direction"],
        "auto_applied_count": len(auto_applied),
        "suggestions_count": len(suggestions),
        "timestamp": datetime.datetime.now().isoformat(),
    })

    # 保留最近 100 条历史（防膨胀）
    if len(version["history"]) > 100:
        version["history"] = version["history"][-100:]

    save_version(version)

    output = {
        "iteration": iteration,
        "version": version["version"],
        "version_bump": version_bump,
        "score_delta": score_delta,
        "trend": trend,
        "auto_applied": auto_applied,
        "suggestions": suggestions,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
