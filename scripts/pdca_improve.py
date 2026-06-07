#!/usr/bin/env python3
"""
pdca_improve.py — PDCA 进化引擎
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Plan → Do → Check → Act → Verify 自动闭环
输入 (stdin JSON):  {"perf_stats": {...}, "mode": "single|batch"}
输出 (stdout JSON): {"iteration": N, "auto_applied": [...], "suggestions": [...],
                     "version_bump": "patch|minor|major", "score_delta": float}
契约版本: 0.1.1
"""

import json, sys, os, datetime, statistics

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_PATH = os.path.join(SKILL_ROOT, "assets", "version.json")
LOG_PATH = os.path.join(SKILL_ROOT, "assets", "pdca.log")
QUEUE_PATH = os.path.join(SKILL_ROOT, "assets", "improve_queue.json")
BASELINE_PATH = os.path.join(SKILL_ROOT, "assets", "baseline.json")

# 低风险参数（可自动调整）
LOW_RISK_RANGES = {
    "vad_parameters.min_silence_duration_ms": [300, 400, 500, 600, 700],
    "vad_parameters.speech_pad_ms": [200, 300, 400, 500],
    "beam_size": [3, 5],
}

# 自动改进阈值
AUTO_IMPROVE_INTERVAL = 10  # 每 10 次迭代尝试自动改进
NO_SPEECH_HIGH_THRESHOLD = 0.15  # no_speech_ratio 超此值触发告警
REGRESSION_QUALITY_DROP = 0.15   # 相对 baseline 下降 >15% 触发回归告警
HISTORY_MAX = 100                # history 截断前保留条数


def load_version():
    if os.path.exists(VERSION_PATH):
        with open(VERSION_PATH, "r") as f:
            return json.load(f)
    return {
        "version": "0.1.0",
        "iteration_count": 0,
        "defaults": {},
        "history": [],
        "aggregates": None,
    }


def save_version(v):
    os.makedirs(os.path.dirname(VERSION_PATH), exist_ok=True)
    with open(VERSION_PATH, "w") as f:
        json.dump(v, f, ensure_ascii=False, indent=2)


def load_baseline():
    """加载回归基线"""
    if os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, "r") as f:
            return json.load(f)
    return None


def save_baseline(b):
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, "w") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)


def update_baseline(perf_stats: dict, iteration: int):
    """首次非 trivial 迭代时建立 baseline，或每 50 次迭代更新一次"""
    baseline = load_baseline()

    if baseline is None:
        baseline = {
            "established_at_iteration": iteration,
            "quality_score": perf_stats.get("quality_score"),
            "avg_logprob": perf_stats.get("avg_logprob"),
            "no_speech_ratio": perf_stats.get("no_speech_ratio"),
            "realtime_factor": perf_stats.get("realtime_factor"),
            "model": perf_stats.get("model"),
        }
        save_baseline(baseline)
        return {"action": "baseline_established", "baseline": baseline}

    # 每 50 次迭代更新 baseline
    if iteration % 50 == 0:
        baseline["quality_score"] = perf_stats.get("quality_score")
        baseline["avg_logprob"] = perf_stats.get("avg_logprob")
        baseline["no_speech_ratio"] = perf_stats.get("no_speech_ratio")
        baseline["updated_at_iteration"] = iteration
        save_baseline(baseline)
        return {"action": "baseline_updated", "baseline": baseline}

    return {"action": "baseline_unchanged", "baseline": baseline}


def regression_check(perf_stats: dict) -> dict:
    """检测质量是否相对 baseline 显著下降"""
    baseline = load_baseline()
    if baseline is None:
        return {"regression": False, "reason": "no_baseline"}

    current_q = perf_stats.get("quality_score", 0)
    baseline_q = baseline.get("quality_score", 0)

    if baseline_q == 0:
        return {"regression": False, "reason": "zero_baseline"}

    relative_drop = (baseline_q - current_q) / abs(baseline_q)

    if relative_drop > REGRESSION_QUALITY_DROP:
        return {
            "regression": True,
            "severity": "high" if relative_drop > 0.3 else "medium",
            "baseline_quality": baseline_q,
            "current_quality": current_q,
            "relative_drop_pct": round(relative_drop * 100, 1),
            "baseline_iteration": baseline.get("established_at_iteration"),
            "recommendation": "建议回滚到 baseline 参数或人工审查转写结果",
        }

    return {"regression": False, "relative_drop_pct": round(max(0, relative_drop) * 100, 1)}


def load_recent_logs(n=50):
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


def compact_history(version: dict):
    """在截断 history 前，将旧数据聚合为统计摘要"""
    history = version.get("history", [])
    if len(history) <= HISTORY_MAX:
        return  # 不需要截断

    # 被截断的部分
    to_compact = history[:-HISTORY_MAX]
    scores = [h.get("quality_score", 0) for h in to_compact if h.get("quality_score") is not None]

    aggregate = {
        "compacted_count": len(to_compact),
        "compacted_range": f"iteration {to_compact[0].get('iteration', '?')}–{to_compact[-1].get('iteration', '?')}",
        "quality_mean": round(statistics.mean(scores), 4) if scores else None,
        "quality_stdev": round(statistics.stdev(scores), 4) if len(scores) >= 2 else None,
        "quality_min": round(min(scores), 4) if scores else None,
        "quality_max": round(max(scores), 4) if scores else None,
        "trends": {
            "improving": sum(1 for h in to_compact if h.get("trend") == "improving"),
            "stable": sum(1 for h in to_compact if h.get("trend") == "stable"),
            "declining": sum(1 for h in to_compact if h.get("trend") == "declining"),
        },
        "auto_improvements_total": sum(h.get("auto_applied_count", 0) for h in to_compact),
        "compacted_at": datetime.datetime.now().isoformat(),
    }

    # 合并已有聚合数据
    existing = version.get("aggregates", [])
    if not isinstance(existing, list):
        existing = []
    existing.append(aggregate)
    version["aggregates"] = existing

    # 截断
    version["history"] = history[-HISTORY_MAX:]


def generate_auto_improvements(version: dict, perf_stats: dict) -> list:
    """生成自动改进建议（仅低风险参数）"""
    auto = []

    no_speech = perf_stats.get("no_speech_ratio", 0)

    if no_speech > NO_SPEECH_HIGH_THRESHOLD:
        current = version.get("defaults", {}).get("vad_parameters", {}).get(
            "min_silence_duration_ms", 500)
        idx = LOW_RISK_RANGES["vad_parameters.min_silence_duration_ms"].index(current) \
            if current in LOW_RISK_RANGES["vad_parameters.min_silence_duration_ms"] else 2
        new_val = LOW_RISK_RANGES["vad_parameters.min_silence_duration_ms"][min(idx + 1, 4)]
        auto.append({
            "param": "vad_parameters.min_silence_duration_ms",
            "from": current,
            "to": new_val,
            "reason": f"no_speech_ratio ({no_speech:.2%}) 过高，放宽 VAD 静音检测",
        })

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


def generate_suggestions(perf_stats: dict, trend: dict, regression: dict, version: dict) -> list:
    """生成改进建议（含中高风险，需人工确认）"""
    suggestions = []

    model = perf_stats.get("model", "medium")
    quality = perf_stats.get("quality_score", 0)

    # 回归告警（最高优先级）
    if regression.get("regression"):
        suggestions.append({
            "risk": "high",
            "action": f"质量回归检测触发! 相对 baseline 下降 {regression['relative_drop_pct']}%",
            "reason": f"当前质量分 {regression['current_quality']} vs baseline {regression['baseline_quality']} (iter {regression['baseline_iteration']})",
            "recommendation": regression.get("recommendation", ""),
            "files": [BASELINE_PATH, VERSION_PATH],
        })

    # 中风险：模型切换建议
    if model == "medium" and quality < -0.5:
        suggestions.append({
            "risk": "medium",
            "action": "切换到 large-v3 模型",
            "reason": f"medium 模型质量分 ({quality}) 低于阈值，large-v3 可改善日语等语言的识别准确率",
            "cost": "转写时间增加约 5-10x，内存占用增加约 3x",
            "next_iteration_test": "A/B 对比: medium vs large-v3",
        })

    if model == "tiny" and perf_stats.get("realtime_factor", 0) > 50:
        suggestions.append({
            "risk": "medium",
            "action": "从 tiny 升级到 medium 模型",
            "reason": "tiny 模型速度极快但质量可能不足",
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


def bump_version(auto_applied: list, suggestions: list) -> str:
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
    prev_score = version.get("history", [{}])[-1].get("quality_score", 0) \
        if version.get("history") else 0
    score_delta = round(perf_stats.get("quality_score", 0) - prev_score, 4)

    # Check: 分析趋势 + 回归检查
    logs = load_recent_logs()
    trend = analyze_trend(logs)

    # 建立/更新 baseline，检测回归
    baseline_result = update_baseline(perf_stats, iteration)
    regression = regression_check(perf_stats) if iteration > 1 else {"regression": False}

    # Act: 生成改进
    auto_applied = []
    suggestions = []

    # 仅每 AUTO_IMPROVE_INTERVAL 次迭代尝试自动改进（避免频繁抖动）
    if iteration % AUTO_IMPROVE_INTERVAL == 0:
        auto_applied = generate_auto_improvements(version, perf_stats)

    suggestions = generate_suggestions(perf_stats, trend, regression, version)

    # 应用自动改进（低风险）
    for improvement in auto_applied:
        if "vad_parameters" in improvement.get("param", ""):
            version.setdefault("defaults", {}).setdefault("vad_parameters", {})
            version["defaults"]["vad_parameters"]["min_silence_duration_ms"] = improvement.get("to")
        elif improvement.get("param") == "beam_size":
            version["defaults"]["beam_size"] = improvement.get("to")

    # 中高风险建议：写入 improve_queue
    queue = {"pending": [], "applied": [], "rejected": []}
    if os.path.exists(QUEUE_PATH):
        try:
            with open(QUEUE_PATH, "r") as f:
                queue = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    for s in suggestions:
        if s not in queue["pending"]:
            queue["pending"].append({
                **s,
                "iteration": iteration,
                "timestamp": datetime.datetime.now().isoformat(),
            })

    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    with open(QUEUE_PATH, "w") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    # 版本号升级
    version_bump = bump_version(auto_applied, suggestions)
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

    # 截断前聚合统计
    compact_history(version)

    save_version(version)

    output = {
        "iteration": iteration,
        "version": version["version"],
        "version_bump": version_bump,
        "score_delta": score_delta,
        "trend": trend,
        "baseline": baseline_result,
        "regression": regression,
        "auto_applied": auto_applied,
        "suggestions": suggestions,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
