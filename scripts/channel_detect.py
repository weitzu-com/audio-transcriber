#!/usr/bin/env python3
"""
channel_detect.py — 声道发现 + 分段语言识别
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
输入 (stdin JSON):  {"audio_path": "/path/to/file.mp3"}
输出 (stdout JSON): {"channels": [...], "total_channels": N, "duration_sec": float}
契约版本: 0.1.0
"""

import json, sys, subprocess, tempfile, os, math
from faster_whisper import WhisperModel

SEGMENT_WINDOW = 30      # 语言检测段长度（秒）
LANG_CONFIDENCE = 0.80   # 低于此置信度时切更细粒度
MIN_DURATION = 0.5       # 最短有效音频（秒）


def ffprobe(path: str) -> dict:
    """获取音频元数据"""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"ffprobe 失败: {result.stderr.strip()}")

    data = json.loads(result.stdout)

    streams = [s for s in data.get("streams", []) if s["codec_type"] == "audio"]
    if not streams:
        raise ValueError("未找到音频流")

    s = streams[0]
    duration = float(s.get("duration", 0) or 0)

    return {
        "channels": s.get("channels", 1),
        "channel_layout": s.get("channel_layout", "unknown"),
        "duration_sec": duration,
        "sample_rate": int(s.get("sample_rate", 0)),
        "codec": s.get("codec_name", "unknown"),
    }


def extract_channel_sample(audio_path: str, channel_idx: int, total_channels: int,
                           start: float, duration: float, output: str) -> bool:
    """提取指定声道的某个时间段样本。
    单声道：直接裁剪。2-8声道：channelsplit。>8声道：pan 滤镜回退。"""
    if duration <= 0:
        return False

    if total_channels == 1:
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-t", str(duration),
            "-i", audio_path, "-ac", "1", "-ar", "16000", output,
        ]
    elif total_channels <= 8:
        # channelsplit: 可靠且高效
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-t", str(duration),
            "-i", audio_path, "-filter_complex",
            f"[0:a]channelsplit=channel_layout={total_channels}c[ch{channel_idx}]",
            "-map", f"[ch{channel_idx}]",
            "-ac", "1", "-ar", "16000", output,
        ]
    else:
        # >8声道: pan 滤镜回退
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-t", str(duration),
            "-i", audio_path, "-filter_complex",
            f"[0:a]pan=mono|c0=c{channel_idx}[ch{channel_idx}]",
            "-map", "[ch{channel_idx}]",
            "-ac", "1", "-ar", "16000", output,
        ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and os.path.exists(output) and os.path.getsize(output) > 1000


def detect_language(model: WhisperModel, audio_path: str) -> tuple:
    """检测音频样本的主要语言"""
    try:
        segments, info = model.transcribe(audio_path, vad_filter=True)
        text_sample = ""
        for i, seg in enumerate(segments):
            if i < 3:
                text_sample += seg.text
        return info.language, info.language_probability, text_sample[:200]
    except Exception as e:
        return "unknown", 0.0, str(e)


def detect_channel_languages(model: WhisperModel, audio_path: str,
                             info: dict) -> list:
    """对每个声道进行分段语言检测"""
    total_ch = info["channels"]
    duration = info["duration_sec"]
    channels = []

    # 空音频回退: duration <= 0 时返回占位声道
    if duration <= MIN_DURATION:
        return [{"index": i, "language": "unknown", "lang_prob": 0.0,
                 "mixed": False, "segments": [],
                 "warning": f"音频时长 {duration}s < {MIN_DURATION}s 阈值"}
                for i in range(total_ch)]

    with tempfile.TemporaryDirectory() as tmpdir:
        for ch_idx in range(total_ch):
            ch_data = {
                "index": ch_idx,
                "language": "unknown",
                "lang_prob": 0.0,
                "mixed": False,
                "segments": [],
            }

            # Phase 1: 提取前 SEGMENT_WINDOW 秒样本检测
            sample_path = os.path.join(tmpdir, f"ch{ch_idx}_sample.wav")
            ok = extract_channel_sample(audio_path, ch_idx, total_ch, 0,
                                        min(SEGMENT_WINDOW, duration), sample_path)
            if not ok:
                channels.append(ch_data)
                continue

            lang, prob, text = detect_language(model, sample_path)
            ch_data["language"] = lang
            ch_data["lang_prob"] = round(prob, 4)
            ch_data["segments"].append({
                "start": 0, "end": min(SEGMENT_WINDOW, duration),
                "lang": lang, "lang_prob": round(prob, 4), "text_sample": text,
            })

            # Phase 2: 如果置信度低，进行更细粒度的分段检测
            if prob < LANG_CONFIDENCE and duration > SEGMENT_WINDOW * 2:
                ch_data["mixed"] = True
                num_windows = min(6, int(duration / SEGMENT_WINDOW))
                for w in range(1, num_windows):
                    w_start = w * (duration / num_windows)
                    w_path = os.path.join(tmpdir, f"ch{ch_idx}_w{w}.wav")
                    ok2 = extract_channel_sample(audio_path, ch_idx, total_ch,
                                                 w_start, SEGMENT_WINDOW, w_path)
                    if not ok2:
                        continue
                    w_lang, w_prob, w_text = detect_language(model, w_path)
                    if w_lang != lang:
                        ch_data["mixed"] = True
                    ch_data["segments"].append({
                        "start": round(w_start, 1),
                        "end": round(w_start + SEGMENT_WINDOW, 1),
                        "lang": w_lang,
                        "lang_prob": round(w_prob, 4),
                        "text_sample": w_text,
                    })

            channels.append(ch_data)

    return channels


def main():
    input_data = json.load(sys.stdin)
    audio_path = input_data["audio_path"]

    if not os.path.exists(audio_path):
        print(json.dumps({"error": f"文件不存在: {audio_path}"}, ensure_ascii=False))
        sys.exit(1)

    # 加载 tiny 模型（仅用于语言检测）
    model = WhisperModel("tiny", device="cpu", compute_type="int8")

    # 获取元数据
    try:
        info = ffprobe(audio_path)
    except ValueError as e:
        print(json.dumps({"error": str(e), "audio_path": audio_path}, ensure_ascii=False))
        sys.exit(1)

    # 空音频回退
    if info["duration_sec"] <= MIN_DURATION:
        print(json.dumps({
            "audio_path": audio_path,
            "total_channels": info["channels"],
            "channel_layout": info["channel_layout"],
            "duration_sec": info["duration_sec"],
            "sample_rate": info["sample_rate"],
            "codec": info["codec"],
            "channels": [{"index": i, "language": "unknown", "lang_prob": 0.0,
                          "mixed": False, "segments": [],
                          "warning": f"音频过短 ({info['duration_sec']}s)"}
                         for i in range(info["channels"])],
            "warning": "AUDIO_TOO_SHORT",
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    # 检测每个声道的语言
    channels = detect_channel_languages(model, audio_path, info)

    output = {
        "audio_path": audio_path,
        "total_channels": info["channels"],
        "channel_layout": info["channel_layout"],
        "duration_sec": info["duration_sec"],
        "sample_rate": info["sample_rate"],
        "codec": info["codec"],
        "channels": channels,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
