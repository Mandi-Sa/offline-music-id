# music-id

> 说明
> 本项目代码与文档由 AI 辅助生成并整理，适合作为可运行的参考实现与工程起点，但不应默认视为经过充分人工审计、完整测试或可直接用于生产环境。
> 在实际使用前，请自行评估其可用性、正确性、性能、安全性以及潜在风险，并根据你的具体场景完成必要的测试、复核与修改。

一个本地离线“听歌识曲”命令行工具，面向如下场景设计：

- 用户用手机录下一段正在播放的音乐
- 工具在本地扫描已有曲库
- 为曲库歌曲建立音频指纹索引
- 对录音做本地匹配
- 输出最可能的原曲路径、得分、命中统计、偏移统计和置信度

整个流程完全离线，不依赖云服务，不调用在线 API，不引入深度学习。

---

## 1. 设计方案总览

本项目采用经典的**频谱峰值配对音频指纹**方案，目标不是追求学术最强，而是优先实现：

- 工程上可运行
- 代码结构清晰
- 后续便于调参和优化
- 对“手机录制外放音乐”场景具备更好的基础鲁棒性

本项目当前实现重点包括：

- 峰值检测鲁棒性
- 峰值密度控制
- 噪声峰抑制
- fingerprint target zone 可配置
- hash 离散度增强
- 匹配评分综合考虑 offset 投票、命中数、覆盖率和集中度
- 增加查询调试输出，方便分析误识别和漏识别

核心流程如下：

1. **音频预处理**
   - 重采样到统一采样率（默认 11025 Hz）
   - 转单声道
   - 去直流
   - 轻量高通滤波，抑制手机录音低频轰鸣/ handling noise
   - 峰值归一化
   - 预加重

2. **频谱分析**
   - 对音频做 STFT
   - 得到 dB 频谱图

3. **峰值提取**
   - 在频谱图上寻找局部极大值
   - 限制分析频带范围
   - 使用帧内自适应阈值
   - 限制每帧峰值数
   - 限制每秒总峰值密度

4. **指纹构造**
   - 采用 anchor-target 配对
   - 每个 anchor 只在指定 target zone 中选择后续峰值
   - 控制每个 anchor 的 target 数量
   - hash 字段包含：
     - `freq1_bin`
     - `freq2_bin`
     - `delta_t_bin`
     - 可选 `freq_delta`

5. **建立倒排索引**
   - `key = hash_value`
   - `value = (song_id, anchor_time)`

6. **查询匹配**
   - 对录音生成 fingerprints
   - 查倒排索引命中
   - 统计 `(song_id, db_time - query_time)` 的 offset 投票
   - 计算：
     - `best_offset_votes`
     - `matched_hashes`
     - `coverage_ratio`
     - `offset_concentration`
   - 用加权综合分数排序候选

---

## 2. 项目目录结构

```text
.
├── main.py
├── requirements.txt
├── README.md
└── music_id/
    ├── __init__.py
    ├── config.py
    ├── utils.py
    ├── audio.py
    ├── fingerprint.py
    ├── index_db.py
    ├── matcher.py
    ├── service.py
    └── cli.py
```

---

## 3. 每个模块职责

### `main.py`
程序入口，执行 CLI。

### `music_id/config.py`
集中管理所有默认参数，避免魔法数字，包括：

- 音频预处理参数
- STFT 参数
- 峰值检测参数
- 指纹构造参数
- 匹配参数
- 建库策略参数
- 调试输出参数
- 索引路径参数

### `music_id/utils.py`
通用工具函数：

- 扫描音频文件
- 创建目录
- JSON 读写
- 批量切分
- 时间换算

### `music_id/audio.py`
音频加载与预处理：

- 使用 `librosa.load` 读取音频
- 统一采样率
- 转单声道
- 去直流
- 轻量高通滤波
- 归一化
- 预加重
- 查询音频长度检查

### `music_id/fingerprint.py`
核心指纹算法：

- STFT 频谱计算
- 频带限制
- 局部峰值提取
- 峰值密度控制
- anchor-target 指纹配对
- fingerprint 哈希生成
- 输出查询峰值数 / 指纹数等统计信息

### `music_id/index_db.py`
本地 SQLite 索引层：

- 歌曲元数据表
- 指纹倒排索引表
- 索引写入与读取
- 元数据保存

### `music_id/matcher.py`
匹配逻辑：

- 对每个查询 hash 查库
- 统计 `(song_id, offset)` 投票
- 计算 Top-K 候选
- 结合多项指标综合评分
- 输出置信度和调试信息

### `music_id/service.py`
业务编排层：

- 全量建库
- 增量建库
- 低并发受控建库
- 自动建库
- 查询
- 元数据汇总

### `music_id/cli.py`
命令行解析与调度：

- `build`
- `query`
- 默认直接查询模式

---

## 4. 数据结构设计

### 4.1 峰值结构

```python
@dataclass(frozen=True)
class Peak:
    freq_bin: int
    time_bin: int
    magnitude_db: float
```

表示时频图上的一个局部峰值。

### 4.2 指纹结构

```python
@dataclass(frozen=True)
class Fingerprint:
    hash_value: int
    anchor_time: int
    freq1_bin: int
    freq2_bin: int
    delta_t_bin: int
```

其中：

- `hash_value`：指纹哈希，用作倒排索引 key
- `anchor_time`：anchor 在歌曲中的时间位置（帧索引）
- `freq1_bin`：anchor 频率 bin
- `freq2_bin`：target 频率 bin
- `delta_t_bin`：时间差 bin

### 4.3 指纹提取统计结构

```python
@dataclass(frozen=True)
class FingerprintExtractionStats:
    peak_count: int
    fingerprint_count: int
    spectrogram_frames: int
    spectrogram_freq_bins: int
```

用于调试查询质量，例如：

- 提取了多少峰值
- 生成了多少 fingerprints
- 频谱图尺寸大概是多少

### 4.4 SQLite 表设计

#### songs
保存歌曲元信息：

- `id`
- `path`
- `file_size`
- `mtime`
- `duration`
- `fingerprint_count`

#### fingerprints
保存倒排索引：

- `hash_value`
- `song_id`
- `anchor_time`

#### metadata
保存索引元数据：

- 配置快照
- 曲库统计
- 建库结果
- 失败文件记录

---

## 5. fingerprint hash 的构成方式

这里使用增强后的紧凑整数哈希。

### 基本字段

- `freq1_bin`
- `freq2_bin`
- `delta_t_bin`

### 增强字段
为了提升手机录音噪声场景下的离散度，当前默认还会把：

- `freq_delta = freq2_bin - freq1_bin`

编码进 hash。

### 逻辑示意

```text
hash = combine(freq1_bin, freq2_bin, freq_delta, delta_t_bin)
```

再对大整数上界取模，保证在 SQLite 中稳定存储。

### 为什么这样设计
优点：

- 仍保持经典频谱峰配对思路
- 比只用 `(freq1, freq2, delta_t)` 更容易拉开相似指纹
- 在手机录音、混响、轻微失真场景下更稳一些
- 仍然可解释，便于后续继续优化

---

## 6. 索引存储格式

### 为什么使用 SQLite，而不是 pickle
本项目选择 SQLite，原因如下：

1. **标准库自带**
   - 无需额外依赖
   - 跨平台更稳定

2. **适合倒排索引**
   - 支持对 `hash_value` 建索引
   - 查询效率比整包反序列化更可控

3. **更适合较大曲库**
   - pickle 对几千首歌和大量 fingerprints 时会变得臃肿
   - SQLite 更方便后续做增量更新和统计分析

4. **调试方便**
   - 可直接查看数据库内容
   - 更利于工程维护

索引默认保存在曲库目录下：

```text
songs/
└── .fingerprint_index/
    ├── index.db
    └── metadata.json
```

### 建库提醒
当你修改以下内容后，建议重新建库：

- 峰值检测规则
- fingerprint hash 规则
- 匹配评分策略

推荐命令：

```bash
python main.py build -d "songs/" --rebuild
```

---

## 7. 匹配评分策略

查询时，流程如下：

1. 为查询录音提取 fingerprints
2. 对每个 `hash_value` 到倒排索引中查找命中项
3. 对每个命中项，计算：

```text
offset = db_anchor_time - query_anchor_time
```

4. 对 `(song_id, offset_bin)` 计数投票
5. 对每首歌找到 offset 票数最高的 bin
6. 进一步统计多个指标并综合评分

### 当前输出指标

- `score`
  - 综合加权分数
- `matched_hashes`
  - 总命中数
- `unique_matched_hashes`
  - 去重后的命中 hash 数
- `best_offset_votes`
  - 最优偏移聚类下的票数
- `best_offset`
  - 最优偏移对应的时间
- `coverage_ratio`
  - 查询唯一 hash 中，有多少比例在该候选中出现
- `offset_concentration`
  - 命中中有多少比例集中在最佳 offset 上
- `confidence`
  - `high / medium / low / none`
- `confident`
  - 是否达到可信阈值

### 当前综合评分思路

综合考虑：

- `best_offset_votes`
- `matched_hashes`
- `coverage_ratio`
- `offset_concentration`

即不再只看 offset 峰值票数，而是更关注：

- 命中是否足够多
- 命中是否集中
- 查询指纹覆盖是否足够广

这对“手机录制外放音乐”场景更稳，因为这类音频往往存在：

- 环境噪声
- 混响
- 音量变化
- 录音设备频响偏差

### 当前置信度判定
基于以下条件综合判断：

- `score >= min_confident_score`
- `matched_hashes >= min_confident_matched_hashes`
- `coverage_ratio >= min_confident_coverage_ratio`
- `offset_concentration >= min_confident_offset_ratio`

---

## 8. 参数默认值建议

所有参数集中在 `music_id/config.py` 中。

### 音频参数
- `sample_rate = 11025`
- `mono = True`
- `normalize = True`
- `pre_emphasis = 0.97`
- `highpass_cutoff_hz = 80.0`

### 频谱参数
- `n_fft = 2048`
- `hop_length = 256`
- `window = "hann"`

### 峰值检测参数
- `amp_min_db = 18.0`
- `neighborhood_freq_bins = 17`
- `neighborhood_time_bins = 17`
- `max_peaks_per_frame = 5`
- `max_peaks_per_second = 32`
- `min_freq_hz = 120.0`
- `max_freq_hz = 5000.0`
- `min_frame_peak_percentile = 75.0`

### 指纹参数
- `fan_value = 10`
- `target_zone_start_s = 0.35`
- `target_zone_end_s = 2.8`
- `max_targets_per_anchor = 10`
- `anchor_step = 1`
- `delta_t_quantization = 2`
- `freq_quantization_hz = 30`
- `include_freq_delta = True`

### 匹配参数
- `top_k = 3`
- `min_query_duration_s = 3.0`
- `min_confident_score = 10.0`
- `min_confident_matched_hashes = 18`
- `min_confident_coverage_ratio = 0.08`
- `min_confident_offset_ratio = 0.18`
- `offset_bin_size_frames = 2`

### 调试参数
- `enabled = True`
- `top_candidate_details = 3`

---

## 9. 安装方法

## 9.1 Python 版本建议
建议使用：

- Python 3.10+
- Python 3.11 / 3.12 也可

## 9.2 安装依赖

```bash
pip install -r requirements.txt
```

---

## 10. 音频依赖说明

项目使用 `librosa + soundfile` 读取音频。

### 已直接支持较稳定的格式
- wav
- flac

### mp3 / m4a 注意事项
不同系统环境下，`mp3` 和 `m4a` 的读取能力可能受后端依赖影响。

如果出现以下问题：

- 某些 mp3 无法读取
- m4a 无法读取
- 报解码器相关错误

建议安装 **ffmpeg**。

### ffmpeg 安装建议

#### macOS
```bash
brew install ffmpeg
```

#### Ubuntu / Debian
```bash
sudo apt-get update
sudo apt-get install ffmpeg
```

#### Windows
可通过以下方式之一安装：

- `winget install ffmpeg`
- `choco install ffmpeg`
- 手动下载 ffmpeg 并加入 PATH

如果环境中没有合适的解码器，建议先将音频转成 wav 再测试。

---

## 11. 如何建库

### 方案 A：显式建库
```bash
python main.py build -d "songs/"
```

强制重建：

```bash
python main.py build -d "songs/" --rebuild
```

### 建库输出示例
```text
Build finished:
  library: songs
  songs_indexed: 128
  fingerprints: 842193
  processed_files: 128
  failed_files: 0
```

### 建议
当你调整过以下任一参数后，建议重建索引：

- `sample_rate`
- `n_fft`
- `hop_length`
- 峰值检测参数
- fingerprint target zone 参数
- hash 相关参数

---

## 12. 如何查询

### 方案 A：首次运行自动建库
如果索引不存在，会自动建库：

```bash
python main.py -d "songs/" query.mp3
```

### 方案 B：先建库，再查询
```bash
python main.py build -d "songs/"
python main.py query -d "songs/" query.mp3
```

### 禁止自动建库
```bash
python main.py query -d "songs/" query.mp3 --no-auto-build
```

统一使用以下方式：

```bash
python main.py -d "songs/" query.mp3
```

---

## 13. 查询调试输出说明

当前默认会输出额外调试信息，便于定位问题。

### 查询级统计
- `query_peaks`
  - 查询音频提取出的峰值数量
- `query_fingerprints`
  - 查询音频生成的 fingerprint 数量
- `query_unique_hashes`
  - 查询音频中去重后的 hash 数量
- `candidate_count`
  - 本次产生了多少候选歌曲

### 候选级统计
每个候选会输出：

- `matched_hashes`
- `unique_matched_hashes`
- `best_offset_votes`
- `coverage`
- `concentration`

其中：

- `coverage`
  - 越高通常表示查询指纹覆盖越广
- `concentration`
  - 越高通常表示 offset 聚类越集中，误报可能性越低

---

## 14. 输出示例

### 高置信匹配
```text
Best match:
  file: songs/周杰伦/稻香.mp3
  score: 166.42
  matched_hashes: 420
  unique_matched_hashes: 211
  best_offset_votes: 183
  best_offset: 12.54s
  coverage_ratio: 0.356
  offset_concentration: 0.436
  confidence: high
  query_fingerprints: 1137
  confident: yes

Top candidates:
  1. songs/周杰伦/稻香.mp3    score=166.42 matched_hashes=420 best_offset_votes=183 coverage=0.356 concentration=0.436 offset=12.54s confidence=high
  2. songs/周杰伦/七里香.mp3  score=54.13 matched_hashes=98 best_offset_votes=41 coverage=0.102 concentration=0.418 offset=13.06s confidence=low
  3. songs/五月天/倔强.mp3    score=23.77 matched_hashes=50 best_offset_votes=18 coverage=0.051 concentration=0.360 offset=10.92s confidence=low

Debug:
  query_peaks: 173
  query_fingerprints: 1137
  query_unique_hashes: 593
  candidate_count: 3
  candidate_1: path=songs/周杰伦/稻香.mp3 score=166.42 matched_hashes=420 unique_matched_hashes=211 best_offset_votes=183 coverage=0.356 concentration=0.436
```

### 低置信结果
```text
Best match:
  file: songs/unknown_candidate.mp3
  score: 8.73
  matched_hashes: 12
  unique_matched_hashes: 10
  best_offset_votes: 7
  best_offset: 3.48s
  coverage_ratio: 0.021
  offset_concentration: 0.583
  confidence: low
  query_fingerprints: 430
  confident: no

Top candidates:
  1. songs/unknown_candidate.mp3    score=8.73 matched_hashes=12 best_offset_votes=7 coverage=0.021 concentration=0.583 offset=3.48s confidence=low
  2. songs/another_song.mp3         score=6.11 matched_hashes=9 best_offset_votes=5 coverage=0.017 concentration=0.556 offset=1.62s confidence=low
  3. songs/third_song.mp3           score=4.39 matched_hashes=5 best_offset_votes=3 coverage=0.010 concentration=0.600 offset=8.12s confidence=low

Debug:
  query_peaks: 88
  query_fingerprints: 430
  query_unique_hashes: 271
  candidate_count: 7

No confident match found.
```

### 没有任何可用匹配
```text
No confident match found.

Debug:
  query_peaks: 51
  query_fingerprints: 196
  query_unique_hashes: 133
  candidate_count: 0
```

---

## 15. 参数说明

### 通用输入
- `-d, --directory`
  - 曲库目录路径

### 默认模式
```bash
python main.py -d "songs/" query.mp3
```

可选参数：
- `--rebuild`
  - 查询前强制重建索引
- `--no-auto-build`
  - 禁止自动建库

### build 子命令
```bash
python main.py build -d "songs/"
```

参数：
- `--rebuild`
  - 清空后重建
- `--thread N`
  - 建库并发度，例如 `--thread 2`
  - 每个 worker 进程会独立执行读取、计算流程
  - 写库由独立写入线程通过缓冲队列批量提交
  - 该参数会自动联动内部的任务窗口、写入批次和队列容量，不需要再手动调整其他并发相关参数

示例：
```bash
python main.py build -d "songs/" --thread 2
python main.py build -d "songs/" --rebuild --thread 2
```

### query 子命令
```bash
python main.py query -d "songs/" query.mp3
```

参数：
- `--no-auto-build`
  - 不自动建库
- `--thread N`
  - 当索引不存在并触发自动建库时使用的建库线程数，例如 `--thread 2`

---

## 16. 调参建议

### 16.1 安静环境录音
适合：

- 房间较安静
- 外放音量稳定
- 手机距离音源较近
- 环境回声不强

建议：

- 可适当降低 `amp_min_db`
  - 例如从 `18.0` 降到 `14.0 ~ 16.0`
- 可适当增大 `max_peaks_per_frame`
  - 例如从 `5` 调到 `6 ~ 8`
- `target_zone_end_s` 可略增
  - 例如 `2.8 -> 3.2`
- `fan_value` 可适当增加
  - 例如 `10 -> 12`

效果：
- 能保留更多细节峰值
- 对较短查询片段更友好
- 但误报风险会略微上升

### 16.2 嘈杂环境录音
适合：

- 周围有人声
- 有风噪、路噪、低频轰鸣
- 手机距离音源较远
- 外放混响较强

建议：

- 提高 `amp_min_db`
  - 例如 `18.0 -> 20.0 ~ 24.0`
- 提高 `min_frame_peak_percentile`
  - 例如 `75 -> 80 ~ 85`
- 减小 `max_peaks_per_frame`
  - 例如 `5 -> 4`
- 限制峰值频带
  - `min_freq_hz` 可提高到 `150 ~ 200`
- 缩短 `target_zone_end_s`
  - 例如 `2.8 -> 2.2 ~ 2.5`
- 降低 `fan_value`
  - 例如 `10 -> 6 ~ 8`

效果：
- 能减少噪声峰和偶然峰
- 通常会降低总 fingerprint 数
- 但匹配稳定性会更好

### 16.3 曲库规模较大时
适合：

- 几千首以上曲目
- 风格相近歌曲较多
- 查询速度和误报率都需要兼顾

建议：

- 提高 hash 离散度
  - 保持 `include_freq_delta = True`
- 增大 `freq_quantization_hz`
  - 例如 `30 -> 35 ~ 45`
- 适度减小 `fan_value`
  - 例如 `10 -> 6 ~ 8`
- 增大 `anchor_step`
  - 例如 `1 -> 2`
- 提高 `min_confident_score`
- 提高 `min_confident_coverage_ratio`

效果：
- 单首歌索引规模更小
- 查询更快
- 误报更少
- 但对极短查询片段可能会稍微不利

### 16.4 调参原则
每次只改一组参数，然后：

1. 重新建库
2. 用固定测试集评估
3. 观察：
   - 正确 Top1 数
   - 误报数
   - `query_peaks`
   - `query_fingerprints`
   - `coverage_ratio`
   - `offset_concentration`

---

## 17. 最小测试方案

建议至少做以下测试。

### 17.1 从曲库中随机截取 10 秒片段
测试方式：

- 从曲库歌曲中随机截取 10 秒音频
- 保存为独立查询文件
- 用工具查询
- 检查是否能回识别到原曲

目标：
- Top 1 命中率尽量高

### 17.2 给片段加入少量白噪声
例如：

- SNR 20dB
- 或较小幅度高斯噪声

验证：
- 是否仍能识别
- 得分是否明显下降但仍保留正确 Top 1

### 17.3 模拟手机录音退化
可以对片段做以下处理后测试：

- 音量缩放
- 轻微裁剪失真
- 简单混响
- 加一点环境噪声
- 频响变化（如轻度低通/高通）

目标：
- 验证对手机录音场景的基础鲁棒性

### 17.4 完全不在库里的歌曲
使用不在库中的音乐或其他音频测试：

- 检查是否误报
- 检查 `No confident match found.` 是否能稳定触发

目标：
- 降低误报率

---

## 18. 已知限制

当前实现仍然存在以下局限：

1. **增量建库仍是基础版本**
   - 已支持新增 / 修改 / 删除文件检测
   - 但还没有做到更细粒度的缓存与调度优化

2. **读取能力受系统音频解码环境影响**
   - 尤其是 mp3 / m4a
   - 某些环境可能需要 ffmpeg

3. **极端噪声或极短查询片段仍可能识别失败**
   - 当前方案仍是传统指纹算法
   - 没有使用更复杂的学习式表示

4. **没有做更复杂的后处理**
   - 例如 second-best gap
   - offset 聚类平滑
   - song-level normalization
   - 多阶段重排

5. **查询速度仍有优化空间**
   - 当前已经改为批量 hash 查询
   - 但更大规模曲库下仍可继续优化聚合和缓存策略

6. **建库并发仍有继续优化空间**
   - 当前已经支持多进程读取与计算、缓冲批量写库
   - 但不同机器和不同曲库下仍可能需要进一步调优

---

## 19. 如何运行的步骤

### 第一步：安装依赖
```bash
pip install -r requirements.txt
```

### 第二步：准备曲库
假设目录结构如下：

```text
songs/
├── song1.mp3
├── song2.flac
└── artist/
    └── song3.wav
```

### 第三步：重建索引
如果你已经调整过指纹参数、匹配参数或建库策略，建议先重建索引：

```bash
python main.py build -d "songs/" --rebuild
```

### 第四步：查询
```bash
python main.py query -d "songs/" my_phone_recording.mp3
```

### 第五步：或使用默认模式
```bash
python main.py -d "songs/" my_phone_recording.mp3
```

## 20. 后续可优化方向

### 20.1 建库性能优化
- 多进程并行提取 fingerprints
- 批量插入进一步优化
- 对 SQLite 做更细致的事务控制

### 20.2 查询性能优化
- 使用批量 hash 查询替代逐 hash 查询
- 热数据缓存
- 对命中 song_id 进行更高效聚合

### 20.3 指纹质量优化
- 频带分层取峰
- 自适应 target zone
- 针对不同曲风自适应峰值密度
- 更稳健的频率 / 时间量化策略

### 20.4 匹配评分优化
- 引入 second-best gap 作为置信度因子
- 针对短查询做更稳健的阈值策略
- 增加候选重排策略

### 20.5 手机录音鲁棒性优化
- 增加带通滤波
- 简单去噪
- 动态范围压缩
- 抑制低频环境噪声
- 更贴近录音场景的前端预处理

### 20.6 索引管理优化
- 支持增量建库
- 检测新增/删除/修改文件
- 支持多个曲库 profile

### 20.7 工程化增强
- 增加单元测试
- 增加 benchmark 脚本
- 增加日志等级
- 打包为 pip 安装命令
- 增加 Windows `.bat` 启动脚本

---

## 21. 开源协议

本项目采用 **MIT License** 开源协议发布。
你可以自由使用、修改、分发本项目代码，但需要保留原始许可证声明。详细内容请参见仓库根目录下的 `LICENSE` 文件。

---

## 22. 备注

如果你保存本项目代码、安装依赖并准备好本地曲库后，即可执行：

```bash
python main.py -d "songs/" my_phone_recording.mp3
```

实现本地离线、面向手机录音场景优化过的“听歌识曲”工具。