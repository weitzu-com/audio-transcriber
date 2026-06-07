---
name: audio-transcriber
description: |
  多声道多语言音轨自动分离+转写。支持双声道/多声道音频的声道发现、语言识别、
  faster-whisper 转写（日/中/英/韩/法/德/西/俄等 99 种语言），内建 PDCA 自动
  进化引擎，输出 MD+JSON。触发：用户提供音频文件要求分离声道、转写文字、制作字幕。
workflow: |
  发现→分离→转写→进化：ffprobe 声道发现 → tiny 分段语言识别 → 声道分离为独立
  文件 → medium/large-v3 转写 → MD 输出 → pdca_improve 自动质量改进
enabled: true
---

# Audio Transcriber — 多声道多语言音轨分离+转写 Skill

对任何包含多语言声道的音频文件，自动发现声道 → 识别每声道语言 → 分离 → 转写 → 输出 MD 文档，内建 PDCA 质量进化闭环。

## 触发条件

- 用户说「转写这个音频」「从音频中提取 XX 语言」「分离声道」「Whisper 转写」
- 用户提供 `.mp3`/`.wav`/`.m4a`/`.ogg`/`.flac` 音频文件路径
- 用户说「把这个 MP3 转成文字」

## 前置依赖

本 Skill 需要以下工具已安装（首次使用会自动检查）：

```bash
# 必须
ffmpeg >= 5.0
pip3 install faster-whisper

# 可选
pip3 install openai-whisper  # 如需 OpenAI 原版 Whisper
```

## 使用方式

### 默认模式（全自动）

用户只需提供文件路径，Skill 自动完成全部流程：

```
转写 /path/to/audio.mp3
```

流程：

1. `channel_detect.py` — ffprobe 分析声道 → tiny 模型识别每声道语言（含分段检测混合语言）
2. `channel_split.py` — 将不同语言声道分离为独立 MP3 文件
3. `transcribe.py` — 使用 medium 模型转写所有声道
4. `pdca_improve.py` — 自动记录性能数据，生成改进建议
5. 输出：`{文件名}_{语言}_转写.md` + `{文件名}_{语言}_转写.json`

### 仅分离模式

```
分离声道 /path/to/audio.mp3
```

→ 仅输出 `{文件名}_JP.mp3` / `{文件名}_ZH.mp3` 等独立音频文件，不转写。

### 指定语言模式

```
只转写日语 /path/to/audio.mp3
```

→ 跳过中文/其他语言声道，仅转写日语。

### 批量模式

```
批量转写 /path/to/audio/folder/
```

→ 扫描目录下所有音频文件，逐个自动处理。批量模式下的 PDCA 改进在所有文件处理完后聚合执行。

### 高级参数

| 参数             | 说明                                   | 默认           |
| ---------------- | -------------------------------------- | -------------- |
| `--model`        | 转写模型: tiny/medium/large-v3         | medium         |
| `--output-dir`   | 输出目录                               | 同音频文件目录 |
| `--auto-improve` | 启用自动参数调优（低风险建议自动应用） | false          |

## 目录结构

```
audio-transcriber/
├── SKILL.md                   ← 本文件（Skill 入口）
├── README.md                  ← GitHub 开源文档
├── scripts/
│   ├── channel_detect.py      ← 声道发现 + 分段语言识别
│   ├── channel_split.py       ← 声道分离引擎
│   ├── transcribe.py          ← Whisper 转写引擎 + PDCA 数据采集
│   └── pdca_improve.py        ← PDCA 进化引擎（自动质量改进）
├── references/
│   ├── model_guide.md         ← Whisper 模型选型指南
│   └── prompt_tuning.md       ← 转写 initial_prompt 调优指南
├── assets/
│   ├── version.json           ← 版本历史 + 当前参数
│   ├── pdca.log               ← 每次运行的性能日志
│   └── improve_queue.json     ← 改进建议队列
└── .github/
    └── workflows/
        └── test.yml           ← CI 导入检查
```

## 执行逻辑（SKILL.md 内部编排）

当用户提供音频文件路径时，按以下顺序执行：

### Step 1: 依赖检查

检查 `ffmpeg` 和 `faster-whisper` 是否可用。若不可用，提示用户安装。

### Step 2: 声道发现

```bash
echo '{"audio_path": "/path/to/audio.mp3"}' | python3 scripts/channel_detect.py
```

返回声道数、每声道语言、是否混合语言。

### Step 3: 声道分离（如有多个声道）

```bash
cat detect_output.json | python3 scripts/channel_split.py
```

输出独立的 `{文件名}_{LANG}.mp3` 文件。

### Step 4: 转写

对需要转写的每个文件：

```bash
echo '{"file_path": "...", "language": "ja", "model": "medium", "output_dir": "..."}' | python3 scripts/transcribe.py
```

输出 MD + JSON 转写文件。

### Step 5: PDCA 进化

```bash
echo '{"perf_stats": {...}, "mode": "single"}' | python3 scripts/pdca_improve.py
```

记录性能数据 → 分析趋势 → 自动应用低风险改进 → 更新版本号。

### Step 6: 汇报结果

汇总所有输出文件路径、转写统计、PDCA 建议给用户。

## PDCA 闭环机制

```
每次转写自动触发 PDCA 循环：

Plan   → 读取 version.json + improve_queue.json → 生成本次参数计划
Do     → 执行 transcribe.py（应用当前最优参数）
Check  → transcribe.py 输出 perf_stats.json（avg_logprob, no_speech_ratio 代理 WER）
Act    → pdca_improve.py 自动执行：
         ├── 低风险（VAD参数/beam_size）→ 自动应用到 version.json defaults
         ├── 中风险（模型切换）→ 写入 improve_queue，下次 A/B 对比
         └── 高风险（趋势恶化）→ 提示用户人工审查
Verify → 下次转写的 Check 对比本次指标，确认改进有效

每 10 次转写自动尝试 1 次参数调优（避免频繁抖动）
版本号自动递增：patch（参数调优）→ minor（模型切换）→ major（架构变更）
```

## 数据契约（脚本间接口）

所有脚本通过 stdin JSON → stdout JSON 通信：

| 脚本                | 输入字段                                 | 输出字段                                        |
| ------------------- | ---------------------------------------- | ----------------------------------------------- |
| `channel_detect.py` | `audio_path`                             | `channels, total_channels, duration_sec`        |
| `channel_split.py`  | `audio_path, channels, output_dir`       | `files[{channel, language, path}]`              |
| `transcribe.py`     | `file_path, language, model, output_dir` | `segments, perf_stats, output.{md,json}`        |
| `pdca_improve.py`   | `perf_stats, mode`                       | `iteration, version, auto_applied, suggestions` |

## 支持的语言

Whisper 支持 99 种语言。常用映射：

| ISO 639-1 | 语言     | 文件名后缀 |
| --------- | -------- | ---------- |
| ja        | 日语     | \_JP       |
| zh        | 中文     | \_ZH       |
| en        | 英语     | \_EN       |
| ko        | 韩语     | \_KR       |
| fr        | 法语     | \_FR       |
| de        | 德语     | \_DE       |
| es        | 西班牙语 | \_ES       |
| ...       | ...      | ...        |

## 许可

MIT License — 见 [LICENSE](LICENSE)
