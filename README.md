# Audio Transcriber — 多声道多语言音轨分离+自动转写

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![PDCA](https://img.shields.io/badge/PDCA-v0.1.0--∞-green.svg)](assets/version.json)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)]()

> 扫把一口，丢入任何多语言音频 → 自动发现声道 → 识别语言 → 分离 → Whisper 转写 → 输出 MD + JSON。
> 内建 PDCA 质量进化引擎，每一次转写都比上一次更准。

---

## 为什么需要这个？

中文/日语讲座录音、多语言会议、国际采访……这些音频常常是**双声道**（左声道中文翻译，右声道日语原声），手动分离+转写极其痛苦。

Audio Transcriber 用一个命令完成全流程：

```
用户丢入音频 → 自动识别声道+语言 → 分离 → 转写 → MD 文档
                                              ↓
                                       PDCA 自动进化
```

## 快速开始

### 安装依赖

```bash
# macOS
brew install ffmpeg
pip3 install faster-whisper

# Linux (Ubuntu/Debian)
sudo apt install ffmpeg
pip3 install faster-whisper
```

### 安装 Skill

```bash
# Claude Code 用户
cp -r audio-transcriber ~/.claude/skills/

# 独立使用（不依赖 Claude Code）
git clone https://github.com/weitzu-com/audio-transcriber.git
cd audio-transcriber
```

### 使用

#### Claude Code Skill 模式

在 Claude Code 对话中直接说：

```
转写 /path/to/audio.mp3
```

Skill 会自动触发并执行全流程。

#### 命令行独立模式

```bash
# Step 1: 发现声道
echo '{"audio_path": "lecture.mp3"}' | python3 scripts/channel_detect.py

# Step 2: 分离声道
echo '{"audio_path": "lecture.mp3", "channels": [...], "output_dir": "."}' | python3 scripts/channel_split.py

# Step 3: 转写
echo '{"file_path": "lecture_JP.mp3", "language": "ja", "model": "medium", "output_dir": "."}' | python3 scripts/transcribe.py

# Step 4: PDCA 进化
echo '{"perf_stats": {...}, "mode": "single"}' | python3 scripts/pdca_improve.py
```

## 功能特性

### 核心能力

- **自动声道发现** — ffprobe 解析任意声道数（1-N），不需要预先知道左声道/右声道
- **分段语言检测** — 每 30s 窗口检测语言，识别混合语言声道（如日语音频中夹杂英文术语）
- **声道分离** — 将不同语言声道分离为独立 MP3 文件
- **Whisper 转写** — 使用 faster-whisper（medium 默认，可选 large-v3），支持 99 种语言
- **MD + JSON 双输出** — 按停顿分段，带时间戳，可直接用于字幕制作

### PDCA 自动进化 🔄

不是一次性脚本。每次转写自动：

| 环节       | 做什么                                                        |
| ---------- | ------------------------------------------------------------- |
| **Plan**   | 读取 version.json 当前最优参数 + 待处理改进建议               |
| **Do**     | 执行转写（应用参数）                                          |
| **Check**  | 采集质量代理指标（avg_logprob + no_speech_ratio）             |
| **Act**    | 低风险参数自动优化 / 中风险写入 A/B 测试队列 / 高风险提示用户 |
| **Verify** | 下次转写对比本次指标，确认改进有效                            |

每 10 次转写自动尝试 1 次参数调优。版本号语义化递增。

详见 [PDCA 闭环机制](SKILL.md#pdca-闭环机制)

## 真实案例

### 京瓷哲学详解 79 讲

输入：`00.前言.mp3`（立体声双声道，左=中文翻译，右=稻盛和夫日语，62分钟）

```bash
# 全流程 2 分钟完成
echo '{"audio_path": "00.前言.mp3"}' | python3 scripts/channel_detect.py
# → 发现: 2声道, 左=zh(98%), 右=ja(97%)

echo '...' | python3 scripts/channel_split.py
# → 输出: 00.前言_ZH.mp3 + 00.前言_JP.mp3

echo '...' | python3 scripts/transcribe.py
# → 输出: 00.前言_ja_转写.md (350段, 质量分 0.82)
```

## 模型选型

| 场景          | 推荐模型      | 62分钟耗时 (M4) |
| ------------- | ------------- | --------------- |
| 快速语言检测  | tiny          | ~1.5min         |
| 日常转写      | **medium** ⭐ | ~8min           |
| 正式出版/存档 | large-v3      | ~2h             |

详见 [模型选型指南](references/model_guide.md)

## 目录结构

```
audio-transcriber/
├── SKILL.md                   # Claude Code Skill 入口
├── README.md                  # 本文件
├── LICENSE                    # MIT
├── scripts/
│   ├── channel_detect.py      # 声道发现 + 语言识别
│   ├── channel_split.py       # 声道分离
│   ├── transcribe.py          # 转写引擎 + 数据采集
│   └── pdca_improve.py        # PDCA 进化引擎
├── references/
│   ├── model_guide.md         # 模型选型参考
│   └── prompt_tuning.md       # 转写调优指南
├── assets/
│   ├── version.json           # 版本历史
│   ├── pdca.log               # 性能日志
│   └── improve_queue.json     # 改进队列
└── .github/workflows/
    └── test.yml               # CI
```

## 脚本间接口契约

所有脚本通过 stdin JSON → stdout JSON 通信，字段定义见各脚本文件头部的契约声明。

| 脚本                | 输入                                       | 输出                                              |
| ------------------- | ------------------------------------------ | ------------------------------------------------- |
| `channel_detect.py` | `{audio_path}`                             | `{channels, total_channels, duration_sec}`        |
| `channel_split.py`  | `{audio_path, channels, output_dir}`       | `{files: [{channel, language, path}]}`            |
| `transcribe.py`     | `{file_path, language, model, output_dir}` | `{segments, perf_stats}`                          |
| `pdca_improve.py`   | `{perf_stats, mode}`                       | `{iteration, version, auto_applied, suggestions}` |

## 支持的语言

Whisper 99 种语言全支持。文件名后缀自动映射：

`ja→JP, zh→ZH, en→EN, ko→KR, fr→FR, de→DE, es→ES, ru→RU, ar→AR, hi→IN, pt→PT, it→IT`

## 贡献

欢迎 PR！特别是：

- 新语言 initial_prompt 模板
- 性能基准测试
- PDCA 改进策略

## 许可

MIT License — 详见 [LICENSE](LICENSE)

---

🤖 由 [591 Agent 体系](https://github.com/weitzu-com/591-agent-system)（五省九部一院）设计并实现。
