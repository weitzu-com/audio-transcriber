#!/usr/bin/env python3
"""
transcribe.py — Whisper 转写引擎（含 PDCA 数据采集）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
输入 (stdin JSON):  {"file_path": "...", "language": "ja", "model": "medium",
                     "output_dir": "...", "auto_improve": false}
输出 (stdout JSON): {"segments": [...], "lang": "...", "duration": float,
                     "model": "...", "perf_stats": {...}}
契约版本: 0.1.0
"""

import json, sys, os, time, subprocess
from faster_whisper import WhisperModel

IMPROVE_QUEUE = None  # 由调用方通过 output_dir 确定


def find_queue(output_dir: str) -> str:
    """定位 improve_queue.json"""
    # 向上查找 assets/improve_queue.json
    candidates = [
        os.path.join(output_dir, "assets", "improve_queue.json"),
        os.path.join(output_dir, "..", "assets", "improve_queue.json"),
    ]
    # 也查找 skill 根目录
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(skill_root, "assets", "improve_queue.json"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return os.path.join(skill_root, "assets", "improve_queue.json")


def check_improve_queue(output_dir: str):
    """I2: 启动时检查 improve_queue，≥3 条高风险建议则打断用户"""
    queue_path = find_queue(output_dir)
    if not os.path.exists(queue_path):
        return None

    try:
        with open(queue_path, "r") as f:
            queue = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None

    high_risk = [item for item in queue.get("pending", [])
                 if item.get("risk") == "high"]
    if len(high_risk) >= 3:
        print(f"\n⚠️  {len(high_risk)} 条高风险改进建议待处理:", file=sys.stderr)
        for item in high_risk[-3:]:
            print(f"   • {item.get('action', '未知')}: {item.get('reason', '')}", file=sys.stderr)
        print(f"   查看: {queue_path}\n", file=sys.stderr)
        return high_risk
    return None


def get_model_params(model_name: str) -> dict:
    """根据模型名返回 WhisperModel 参数"""
    params = {
        "tiny": {"compute_type": "int8"},
        "medium": {"compute_type": "int8"},
        "large-v3": {"compute_type": "auto"},
    }
    return params.get(model_name, {"compute_type": "int8"})


def transcribe_file(file_path: str, language: str, model_name: str) -> dict:
    """执行转写并收集性能数据"""
    t0 = time.time()

    # 如果有 improvement 建议的参数覆盖，先加载
    overrides = load_param_overrides()

    # 如果语言是 unknown 或混合，使用自动检测
    lang_param = None if language in ("unknown", "mixed", "auto") else language

    model_params = get_model_params(model_name)
    model = WhisperModel(model_name, device="cpu", cpu_threads=8, **model_params)

    # 加载转写参数（可能被 pdca_improve 自动调整过）
    vad_params = overrides.get("vad_parameters", {
        "min_silence_duration_ms": 500,
        "speech_pad_ms": 400,
    })

    beam_size = overrides.get("beam_size", 5 if model_name == "large-v3" else 3)

    t_load = time.time() - t0

    segments, info = model.transcribe(
        file_path,
        language=lang_param,
        vad_filter=True,
        vad_parameters=vad_params,
        beam_size=beam_size,
        initial_prompt=overrides.get("initial_prompt", ""),
    )

    t_transcribe = time.time() - t0 - t_load

    results = []
    total_logprob = 0.0
    no_speech_count = 0
    total_segments = 0

    for seg in segments:
        results.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
            # I1: WER 代理指标
            "avg_logprob": round(seg.avg_logprob, 4) if hasattr(seg, 'avg_logprob') else None,
            "no_speech_prob": round(seg.no_speech_prob, 4) if hasattr(seg, 'no_speech_prob') else None,
        })
        total_logprob += getattr(seg, 'avg_logprob', 0) or 0
        no_speech_count += 1 if (getattr(seg, 'no_speech_prob', 0) or 0) > 0.5 else 0
        total_segments += 1

    t_total = time.time() - t0

    # I1: 用 avg_logprob 和 no_speech_prob 作为 WER 代理指标
    avg_logprob = total_logprob / max(total_segments, 1)
    no_speech_ratio = no_speech_count / max(total_segments, 1)

    perf_stats = {
        "model": model_name,
        "language": info.language,
        "language_prob": info.language_probability,
        "duration_sec": info.duration,
        "total_segments": total_segments,
        "time_load_sec": round(t_load, 2),
        "time_transcribe_sec": round(t_transcribe, 2),
        "time_total_sec": round(t_total, 2),
        "realtime_factor": round(info.duration / t_transcribe, 1) if t_transcribe > 0 else 0,
        # I1: WER 代理指标
        "avg_logprob": round(avg_logprob, 4),
        "no_speech_ratio": round(no_speech_ratio, 4),
        "quality_score": round(avg_logprob * (1 - no_speech_ratio), 4),  # 综合质量分
    }

    return {
        "segments": results,
        "lang": info.language,
        "duration": info.duration,
        "model": model_name,
        "perf_stats": perf_stats,
        "overrides_applied": overrides,
    }


def load_param_overrides() -> dict:
    """从 version.json 加载被 pdca_improve 自动调整的参数"""
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version_path = os.path.join(skill_root, "assets", "version.json")
    try:
        with open(version_path, "r") as f:
            v = json.load(f)
            return v.get("defaults", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_perf_log(perf_stats: dict, output_dir: str):
    """将性能数据追加到 pdca.log"""
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_path = os.path.join(skill_root, "assets", "pdca.log")

    import datetime
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        **perf_stats,
    }

    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Warning: 无法写入 pdca.log: {e}", file=sys.stderr)


def fmt_time(s: float) -> str:
    m, s = divmod(int(s), 60)
    return f"{m:02d}:{s:02d}"


def generate_md(file_path: str, result: dict, output_dir: str) -> str:
    """生成 Markdown 转写文件"""
    basename = os.path.splitext(os.path.basename(file_path))[0]
    lang = result["lang"]
    segments = result["segments"]

    lines = [
        f"# {basename} — {lang} 转写",
        "",
        f"> 模型: {result['model']} | 时长: {int(result['duration'] // 60)}分{int(result['duration'] % 60)}秒 | 段数: {len(segments)}",
        f"> 质量分: {result['perf_stats']['quality_score']} | 实时倍率: {result['perf_stats']['realtime_factor']}x",
        "",
        "---",
        "",
    ]

    # 按停顿 >1.5秒 分段
    current_para = []
    para_threshold = 1.5

    for i, seg in enumerate(segments):
        if i > 0:
            gap = seg["start"] - segments[i-1]["end"]
        else:
            gap = 0

        if gap > para_threshold and current_para:
            para_text = "".join(s["text"] for s in current_para)
            st = fmt_time(current_para[0]["start"])
            et = fmt_time(current_para[-1]["end"])
            lines.append(f"**[{st} - {et}]**")
            lines.append("")
            lines.append(para_text)
            lines.append("")
            current_para = [seg]
        else:
            current_para.append(seg)

    if current_para:
        para_text = "".join(s["text"] for s in current_para)
        st = fmt_time(current_para[0]["start"])
        et = fmt_time(current_para[-1]["end"])
        lines.append(f"**[{st} - {et}]**")
        lines.append("")
        lines.append(para_text)
        lines.append("")

    md_path = os.path.join(output_dir, f"{basename}_{lang}_转写.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return md_path


def main():
    input_data = json.load(sys.stdin)
    file_path = input_data["file_path"]
    language = input_data.get("language", "auto")
    model_name = input_data.get("model", "medium")
    output_dir = input_data.get("output_dir", os.path.dirname(file_path))

    os.makedirs(output_dir, exist_ok=True)

    # I2: 检查 improve_queue
    check_improve_queue(output_dir)

    # 执行转写
    result = transcribe_file(file_path, language, model_name)

    # 生成 MD
    md_path = generate_md(file_path, result, output_dir)

    # 保存 JSON
    json_path = os.path.join(output_dir,
                             f"{os.path.splitext(os.path.basename(file_path))[0]}_{result['lang']}_转写.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 记录性能日志
    save_perf_log(result["perf_stats"], output_dir)

    output = {
        **result,
        "output": {
            "md": md_path,
            "json": json_path,
        },
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
