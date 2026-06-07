#!/usr/bin/env python3
"""
channel_split.py — 声道分离引擎
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
输入 (stdin JSON):  {"audio_path": "...", "channels": [...], "output_dir": "..."}
输出 (stdout JSON): {"files": [{"channel": 0, "lang": "ja", "path": "..."}, ...]}
契约版本: 0.1.0
"""

import json, sys, os, subprocess

LANG_CODE_MAP = {
    "ja": "JP", "zh": "ZH", "en": "EN", "ko": "KR",
    "fr": "FR", "de": "DE", "es": "ES", "pt": "PT",
    "ru": "RU", "ar": "AR", "hi": "IN", "it": "IT",
    "unknown": "XX",
}


def get_channel_filter(total_channels: int, channel_idx: int) -> str:
    """生成 ffmpeg channelsplit 滤镜"""
    if total_channels == 1:
        return None  # 单声道不需要分离
    # 生成 channel_layout: 如 stereo=2c
    if total_channels <= 8:
        # 使用显式 channelsplit
        return f"[0:a]channelsplit=channel_layout={total_channels}c[ch{channel_idx}]"
    else:
        # 超过 8 声道用 pan 滤镜
        return f"[0:a]pan=mono|c0=c{channel_idx}[ch{channel_idx}]"


def split_channels(audio_path: str, channels: list, output_dir: str, info: dict) -> list:
    """分离声道为独立音频文件"""
    os.makedirs(output_dir, exist_ok=True)
    basename = os.path.splitext(os.path.basename(audio_path))[0]
    total_ch = info.get("total_channels", len(channels))
    files = []

    for ch in channels:
        ch_idx = ch["index"]
        lang = ch.get("language", "unknown")
        lang_code = LANG_CODE_MAP.get(lang, lang[:2].upper())

        out_name = f"{basename}_{lang_code}.mp3"
        out_path = os.path.join(output_dir, out_name)

        if total_ch == 1:
            # 单声道直接复制
            cmd = [
                "ffmpeg", "-y", "-i", audio_path,
                "-ac", "1", "-b:a", "128k", out_path,
            ]
        else:
            filter_str = get_channel_filter(total_ch, ch_idx)
            cmd = [
                "ffmpeg", "-y", "-i", audio_path,
                "-filter_complex", filter_str,
                "-map", f"[ch{ch_idx}]",
                "-ac", "1", "-b:a", "128k", out_path,
            ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(out_path):
            files.append({
                "channel": ch_idx,
                "language": lang,
                "lang_code": lang_code,
                "path": out_path,
                "size_bytes": os.path.getsize(out_path),
            })

    return files


def main():
    input_data = json.load(sys.stdin)
    audio_path = input_data["audio_path"]

    # 从 channel_detect.py 的输出获取声道信息，或使用传入的 channels
    channels = input_data.get("channels", [{"index": 0, "language": "unknown"}])
    output_dir = input_data.get("output_dir", os.path.dirname(audio_path))

    # 如果 channels 为空，默认单声道原始文件
    if not channels:
        channels = [{"index": 0, "language": "unknown"}]

    info = {
        "total_channels": input_data.get("total_channels", len(channels)),
    }

    files = split_channels(audio_path, channels, output_dir, info)

    output = {
        "audio_path": audio_path,
        "output_dir": output_dir,
        "files": files,
        "count": len(files),
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
