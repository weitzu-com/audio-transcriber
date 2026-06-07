# CLAUDE.md — Audio Transcriber Skill

> 多声道多语言音轨自动分离+转写。591 体系（五省九部一院）设计实现。
> v0.1.1 | GitHub: https://github.com/weitzu-com/audio-transcriber

## 目录结构

```
audio-transcriber/
├── SKILL.md                   ← Claude Code Skill 入口（YAML frontmatter + 编排逻辑）
├── README.md                  ← GitHub 开源文档（中英双语）
├── LICENSE                    ← MIT
├── scripts/
│   ├── channel_detect.py      ← ffprobe 声道发现 + tiny 分段语言识别
│   ├── channel_split.py       ← 声道分离引擎（1-N声道→独立文件）
│   ├── transcribe.py          ← faster-whisper 转写 + WER代理指标采集
│   └── pdca_improve.py        ← PDCA 进化引擎（自动触发的 P→D→C→A→V 闭环）
├── references/
│   ├── model_guide.md         ← Whisper 模型选型指南（含 M4 实测数据）
│   └── prompt_tuning.md       ← 按语言分类的 initial_prompt 调优指南
├── assets/
│   ├── version.json           ← 版本控制 + 当前最优参数
│   ├── pdca.log               ← 每次运行的性能日志
│   ├── improve_queue.json     ← 改进建议队列
│   └── baseline.json          ← 回归基线（首次转写自动建立）
└── .github/workflows/
    └── test.yml               ← CI 语法检查 + JSON 有效性
```

## 工作约定

### 接口契约（不可违反）

所有 scripts/ 脚本通过 **stdin JSON → stdout JSON** 通信。契约版本号在文件头部声明。

| 脚本              | 输入                                       | 输出                                              |
| ----------------- | ------------------------------------------ | ------------------------------------------------- |
| channel_detect.py | `{audio_path}`                             | `{channels, total_channels, duration_sec}`        |
| channel_split.py  | `{audio_path, channels, output_dir}`       | `{files: [{channel, language, path}]}`            |
| transcribe.py     | `{file_path, language, model, output_dir}` | `{segments, perf_stats}`                          |
| pdca_improve.py   | `{perf_stats, mode}`                       | `{iteration, version, auto_applied, suggestions}` |

### 模型选型

- 语言检测：永远用 `tiny` (40x 实时)
- 日常转写：默认 `medium` + `int8` (M4 约 8min/62min 音频)
- 正式出版：`large-v3` + `compute_type="auto"` (float32, 约 2h/62min)
- **禁止** `large-v3` + `int8`：会在 Apple Silicon 上溢出

### PDCA 自动触发

`transcribe.py` 完成后自动调用 `pdca_improve.py`。每 10 次迭代尝试自动参数调优。`baseline.json` 首次非 trivial 迭代建立，每 50 次更新。

### 版本号规则

- patch (0.1.N)：低风险参数自动调优
- minor (0.N.0)：模型切换
- major (N.0.0)：架构变更或高风险改进

## 活跃模块

| 模块           | 状态      | 说明                                        |
| -------------- | --------- | ------------------------------------------- |
| channel_detect | ✅ v0.1.1 | 支持 1-N 声道，空音频回退，>8 声道 pan 回退 |
| channel_split  | ✅ v0.1.1 | 99 种语言文件名后缀映射                     |
| transcribe     | ✅ v0.1.1 | WER 代理指标 + improve_queue 启动检查       |
| pdca_improve   | ✅ v0.1.1 | baseline 回归检测 + history 聚合统计        |

## 注意事项

1. 需要 `ffmpeg >= 5.0` + `pip3 install faster-whisper`
2. 仅支持 macOS (Apple Silicon) 和 Linux x86，Windows 未测试
3. pdca.log 以 append-only 方式写入，需注意磁盘空间（目前无自动 rollover）
4. improve_queue 中高风险建议需用户手动处理

## 禁止事项

- 禁止修改脚本间 JSON 契约字段名而不更新契约版本号
- 禁止在 `large-v3` 上使用 `compute_type="int8"`
- 禁止删除 `assets/baseline.json` 而不重新建立
- 禁止在 production 中使用 `tiny` 模型做最终转写（仅用于语言检测）
