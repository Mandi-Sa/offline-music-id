from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence

import gradio as gr

from music_id.config import BUILD, MATCH, SUPPORTED_EXTENSIONS
from music_id.service import (
    LibraryBuildError,
    QueryError,
    build_library,
    query_library,
    summarize_build_metadata,
)
from music_id.utils import get_index_paths, relative_to_or_self

APP_TITLE = "Musin-ID 本地离线听歌识曲"
APP_DESCRIPTION = (
    "围绕现有音频指纹服务层构建的本地 Web UI，可完成曲库建库、自动建库查询、"
    "候选结果查看与调试分析。"
)

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "none": "无",
}

CUSTOM_CSS = """
:root {
  color-scheme: dark;
}

.gradio-container {
  background:
    radial-gradient(circle at top, rgba(34, 211, 238, 0.15), transparent 32%),
    linear-gradient(180deg, #0f172a 0%, #111827 100%);
}

.app-shell {
  max-width: 1120px;
  margin: 0 auto;
  padding-bottom: 28px;
}

.stack-layout {
  gap: 18px;
}

.hero {
  padding: 24px 28px;
  border-radius: 24px;
  background: linear-gradient(135deg, rgba(15, 23, 42, 0.96), rgba(30, 41, 59, 0.9));
  border: 1px solid rgba(148, 163, 184, 0.16);
  box-shadow: 0 24px 80px rgba(15, 23, 42, 0.42);
  margin-bottom: 18px;
}

.hero h1 {
  margin: 0 0 10px 0;
  font-size: 32px;
  font-weight: 800;
  color: #f8fafc;
}

.hero p {
  margin: 0;
  color: #cbd5e1;
  line-height: 1.65;
}

.hero .hero-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}

.hero .badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.18);
  color: #e2e8f0;
  font-size: 13px;
}

.panel {
  border-radius: 22px;
  background: linear-gradient(180deg, rgba(15, 23, 42, 0.88), rgba(15, 23, 42, 0.76));
  border: 1px solid rgba(148, 163, 184, 0.18);
  box-shadow: 0 20px 60px rgba(2, 6, 23, 0.28);
  padding: 16px 18px;
}

.panel + .panel {
  margin-top: 18px;
}

.section-title {
  margin: 2px 0 4px 0;
  font-size: 21px;
  font-weight: 700;
  color: #f8fafc;
}

.section-caption {
  margin: 0 0 14px 0;
  color: #b5c2d3;
  font-size: 14px;
  line-height: 1.6;
}

.status-overview {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.status-overview .status-card + .status-card {
  margin-top: 0;
}

.panel-actions {
  gap: 12px;
  flex-wrap: wrap;
}

.status-card {
  border-radius: 20px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  background: linear-gradient(180deg, rgba(15, 23, 42, 0.98), rgba(17, 24, 39, 0.96));
  padding: 18px 20px;
  min-height: 122px;
  color: #f8fafc;
  box-shadow: 0 14px 34px rgba(2, 6, 23, 0.32);
}

.status-card__title {
  font-size: 17px;
  font-weight: 800;
  letter-spacing: 0.01em;
  color: #f8fafc;
  line-height: 1.5;
  margin-bottom: 12px;
}

.status-card__list {
  margin: 0;
  padding-left: 20px;
  color: #f8fafc;
  line-height: 1.7;
}

.status-card__list li {
  color: inherit;
}

.status-card__list li::marker {
  color: rgba(226, 232, 240, 0.85);
}

.status-card__list li + li {
  margin-top: 6px;
}

.tone-info {
  border-color: rgba(125, 211, 252, 0.5);
  background: linear-gradient(180deg, rgba(7, 32, 54, 0.98), rgba(15, 23, 42, 0.98));
  color: #eff6ff;
  box-shadow: inset 0 0 0 1px rgba(125, 211, 252, 0.22), 0 14px 34px rgba(2, 6, 23, 0.32);
}

.tone-info .status-card__list,
.tone-info .status-card__list li {
  color: #eff6ff;
}

.tone-success {
  border-color: rgba(74, 222, 128, 0.34);
  background: linear-gradient(180deg, rgba(20, 83, 45, 0.88), rgba(15, 23, 42, 0.96));
  box-shadow: inset 0 0 0 1px rgba(34, 197, 94, 0.18), 0 14px 34px rgba(2, 6, 23, 0.32);
}

.tone-warning {
  border-color: rgba(250, 204, 21, 0.34);
  background: linear-gradient(180deg, rgba(120, 53, 15, 0.88), rgba(15, 23, 42, 0.96));
  box-shadow: inset 0 0 0 1px rgba(251, 191, 36, 0.18), 0 14px 34px rgba(2, 6, 23, 0.32);
}

.tone-error {
  border-color: rgba(248, 113, 113, 0.34);
  background: linear-gradient(180deg, rgba(127, 29, 29, 0.88), rgba(15, 23, 42, 0.96));
  box-shadow: inset 0 0 0 1px rgba(248, 113, 113, 0.2), 0 14px 34px rgba(2, 6, 23, 0.32);
}

.tone-info .status-card__title {
  color: #e0f2fe;
  text-shadow: 0 1px 0 rgba(2, 6, 23, 0.4);
}

.tone-info .status-card__list li::marker {
  color: #7dd3fc;
}

.tone-success .status-card__title {
  color: #dcfce7;
}

.tone-warning .status-card__title {
  color: #fef3c7;
}

.tone-error .status-card__title {
  color: #fee2e2;
}

.note-box {
  border-radius: 16px;
  padding: 14px 16px;
  background: rgba(30, 41, 59, 0.74);
  border: 1px dashed rgba(148, 163, 184, 0.22);
  color: #dbe4ef;
  line-height: 1.7;
}

footer {
  display: none !important;
}
"""


def _render_card_html(title: str, lines: Sequence[str], tone: str = "info") -> str:
    icons = {
        "info": "ℹ️",
        "success": "✅",
        "warning": "⚠️",
        "error": "❌",
    }
    safe_title = html.escape(title)
    items = "".join(
        f"<li>{html.escape(str(line))}</li>" for line in lines if str(line).strip()
    )
    if not items:
        items = "<li>暂无内容。</li>"

    return (
        f'<div class="status-card tone-{tone}">'
        f'<div class="status-card__title">{icons.get(tone, "ℹ️")} {safe_title}</div>'
        f'<ul class="status-card__list">{items}</ul>'
        "</div>"
    )


def _empty_build_outputs() -> tuple[str, str, list[list[str]]]:
    return (
        _render_card_html(
            "等待建库",
            [
                "请输入曲库目录后，可手动建立或更新索引。",
                "若直接发起识别并启用自动建库，系统会在索引不存在时自动完成建库。",
            ],
            tone="info",
        ),
        "尚未执行建库操作。",
        [],
    )


def _empty_query_outputs() -> tuple[str, str, list[list[Any]], Dict[str, Any]]:
    return (
        _render_card_html(
            "等待识别",
            [
                "请选择查询音频后点击“开始识别”。",
                "识别结果会显示最佳匹配、Top-K 候选以及调试信息。",
            ],
            tone="info",
        ),
        _render_card_html(
            "暂无识别结果",
            [
                "当前还没有查询结果。",
                "支持在本地上传查询音频，或直接输入文件路径进行识别。",
            ],
            tone="info",
        ),
        [],
        {
            "status": "idle",
            "message": "尚未开始识别。",
        },
    )


def _normalize_path(value: str | None) -> str:
    return (value or "").strip().strip('"')



def _pick_library_directory(current_value: str | None) -> str:
    normalized = _normalize_path(current_value)
    initial_dir = Path(normalized).expanduser() if normalized else Path.cwd()
    if initial_dir.is_file():
        initial_dir = initial_dir.parent
    if not initial_dir.is_dir():
        initial_dir = Path.cwd()

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("当前环境无法打开系统文件夹选择对话框，请手动输入曲库目录。") from exc

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        selected = filedialog.askdirectory(
            title="选择曲库目录",
            initialdir=str(initial_dir),
            mustexist=True,
            parent=root,
        )
        root.update()
    except Exception as exc:
        raise RuntimeError("打开系统文件夹选择对话框失败，请手动输入曲库目录。") from exc
    finally:
        if root is not None:
            root.destroy()

    return selected or normalized


def _resolve_library_dir(library_dir: str) -> Path:
    normalized = _normalize_path(library_dir)
    if not normalized:
        raise LibraryBuildError("请先填写曲库目录路径。")
    return Path(normalized).resolve()


def _resolve_query_file(query_upload: str | None, query_path: str | None) -> Path:
    manual_path = _normalize_path(query_path)
    if manual_path:
        return Path(manual_path).resolve()

    if query_upload:
        return Path(query_upload).resolve()

    raise QueryError("请先选择查询音频文件，或手动输入查询音频路径。")


def _to_int(value: float | int | None, default: int = 1) -> int:
    if value is None:
        return default
    return max(1, int(value))



def _cpu_count() -> int:
    return max(1, os.cpu_count() or 1)



def _default_build_thread_count() -> int:
    return min(_cpu_count(), max(1, BUILD.max_workers))


def _build_failures_table(metadata: Dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in metadata.get("failed_files", []):
        rows.append([str(item.get("file", "")), str(item.get("error", ""))])
    return rows


def _format_index_ready_summary(library_dir: Path, auto_build: bool, force_rebuild: bool) -> str:
    index_paths = get_index_paths(library_dir)
    return "\n".join(
        [
            "Index status:",
            f"  library: {library_dir}",
            f"  index_db: {index_paths['db_path']}",
            f"  auto_build_on_missing: {auto_build}",
            f"  force_rebuild_before_query: {force_rebuild}",
            "  build_triggered: false",
        ]
    )


def _build_status_from_metadata(
    metadata: Dict[str, Any],
    library_dir: Path,
    lead: str,
) -> tuple[str, str, list[list[str]]]:
    failed_files = metadata.get("failed_files", [])
    tone = "success" if not failed_files else "warning"
    lines = [
        lead,
        (
            f"已索引 {metadata.get('song_count', 0)} 首歌曲，"
            f"累计 {metadata.get('fingerprint_count', 0)} 条指纹。"
        ),
        (
            f"本次处理 {metadata.get('processed_files', 0)} 个文件，"
            f"更新 {metadata.get('updated_files', 0)} 个，"
            f"跳过 {metadata.get('skipped_files', 0)} 个。"
        ),
        f"建库线程数：{metadata.get('thread_count', 1)}。",
        f"索引数据库：{get_index_paths(library_dir)['db_path']}。",
    ]
    if metadata.get("removed_files", 0):
        lines.append(f"已从索引中清理 {metadata.get('removed_files', 0)} 个缺失文件记录。")
    if failed_files:
        lines.append(f"有 {len(failed_files)} 个文件处理失败，可在失败列表中查看详情。")

    return (
        _render_card_html("建库状态", lines, tone=tone),
        summarize_build_metadata(metadata, library_dir).strip(),
        _build_failures_table(metadata),
    )


def _format_confidence(value: str) -> str:
    return CONFIDENCE_LABELS.get(value, value)


def _format_candidate_rows(result, library_dir: Path) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for idx, candidate in enumerate(result.top_candidates, start=1):
        rows.append(
            [
                idx,
                relative_to_or_self(Path(candidate.path), library_dir),
                _format_confidence(candidate.confidence),
                round(candidate.score, 2),
                int(candidate.matched_hashes),
                int(candidate.unique_matched_hashes),
                int(candidate.best_offset_votes),
                round(candidate.best_offset_seconds, 2),
                round(candidate.coverage_ratio, 3),
                round(candidate.offset_concentration, 3),
                "是" if candidate.confident else "否",
            ]
        )
    return rows


def _build_best_match_card(result, library_dir: Path) -> str:
    if not result.best:
        return _render_card_html(
            "未找到可展示结果",
            [
                "当前查询没有产生可用候选。",
                "请确认曲库已正确建库，并尽量提供时长更长、噪声更少的查询音频。",
            ],
            tone="warning",
        )

    best = result.best
    title = "最佳匹配" if best.confident else "最佳候选（低置信）"
    tone = "success" if best.confident else "warning"
    return _render_card_html(
        title,
        [
            f"文件：{relative_to_or_self(Path(best.path), library_dir)}",
            f"综合得分：{best.score:.2f}",
            f"置信度：{_format_confidence(best.confidence)}",
            f"是否达到可信阈值：{'是' if best.confident else '否'}",
            f"命中哈希：{best.matched_hashes}，去重命中：{best.unique_matched_hashes}",
            f"最佳偏移票数：{best.best_offset_votes}，最佳偏移：{best.best_offset_seconds:.2f}s",
            f"覆盖率：{best.coverage_ratio:.3f}，偏移集中度：{best.offset_concentration:.3f}",
        ],
        tone=tone,
    )


def _build_query_status_card(result, query_file: Path, library_dir: Path) -> str:
    if not result.best:
        return _render_card_html(
            "识别完成：未命中",
            [
                f"查询音频：{query_file}",
                f"曲库目录：{library_dir}",
                "没有找到可用匹配，请检查音频质量、时长或曲库覆盖范围。",
            ],
            tone="warning",
        )

    if result.best.confident:
        return _render_card_html(
            "识别完成：可信命中",
            [
                f"查询音频：{query_file}",
                f"最佳匹配：{relative_to_or_self(Path(result.best.path), library_dir)}",
                f"置信度：{_format_confidence(result.best.confidence)}，结果可直接参考。",
            ],
            tone="success",
        )

    return _render_card_html(
        "识别完成：低置信候选",
        [
            f"查询音频：{query_file}",
            f"当前最佳候选：{relative_to_or_self(Path(result.best.path), library_dir)}",
            "结果未达到可信阈值，建议结合 Top-K 候选与调试信息人工判断。",
        ],
        tone="warning",
    )


def _build_debug_payload(result, query_file: Path, library_dir: Path) -> Dict[str, Any]:
    best = result.best
    candidate_summaries = []
    for item in result.debug.candidate_summaries:
        candidate_summaries.append(
            {
                **item,
                "path": relative_to_or_self(Path(str(item.get("path", ""))), library_dir),
            }
        )

    return {
        "query_file": str(query_file),
        "library_dir": str(library_dir),
        "query_stats": {
            "query_peak_count": int(result.debug.query_peak_count),
            "query_fingerprint_count": int(result.debug.query_fingerprint_count),
            "query_unique_hash_count": int(result.debug.query_unique_hash_count),
            "candidate_count": int(result.debug.candidate_count),
        },
        "best_candidate": (
            {
                "path": relative_to_or_self(Path(best.path), library_dir),
                "score": round(best.score, 4),
                "confidence": best.confidence,
                "confident": bool(best.confident),
                "matched_hashes": int(best.matched_hashes),
                "unique_matched_hashes": int(best.unique_matched_hashes),
                "best_offset_votes": int(best.best_offset_votes),
                "best_offset_seconds": round(best.best_offset_seconds, 4),
                "coverage_ratio": round(best.coverage_ratio, 6),
                "offset_concentration": round(best.offset_concentration, 6),
            }
            if best
            else None
        ),
        "candidate_summaries": candidate_summaries,
    }


def _format_error_card(context: str, exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    lower_message = message.lower()
    title = context
    lines = [message]

    if "too short" in lower_message:
        title = "查询音频过短"
        lines.append(f"当前最短建议时长约为 {MATCH.min_query_duration_s:.1f} 秒。")
    elif "ffmpeg" in lower_message and (
        "winerror 2" in lower_message
        or "not found" in lower_message
        or "no such file" in lower_message
        or "系统找不到指定的文件" in message
    ):
        title = "音频加载失败，可能缺少 ffmpeg"
        lines.append("请安装 ffmpeg 并加入 PATH，或先将音频转为 WAV / FLAC 后重试。")
    elif "no fingerprints extracted" in lower_message:
        title = "未能提取有效指纹"
        lines.append("请确认音频不是静音、过短，或严重失真。")
    elif "no supported audio files" in lower_message:
        title = "曲库中没有可用音频文件"
        lines.append(f"当前支持格式：{', '.join(sorted(SUPPORTED_EXTENSIONS))}。")
    elif "fingerprint index not found" in lower_message:
        title = "索引不存在"
        lines.append("可启用自动建库，或先点击“建立 / 更新索引”。")

    return _render_card_html(title, lines, tone="error")


def choose_library_dir_ui(current_value: str) -> str:
    try:
        return _pick_library_directory(current_value)
    except RuntimeError as exc:
        raise gr.Error(str(exc)) from exc



def build_index_ui(
    library_dir: str,
    force_rebuild: bool,
    thread_count: float,
) -> tuple[str, str, list[list[str]]]:
    try:
        library_path = _resolve_library_dir(library_dir)
        metadata = build_library(
            library_path,
            rebuild=bool(force_rebuild),
            thread_count=_to_int(thread_count),
        )
        lead = "已完成重建索引。" if force_rebuild else "已完成建库 / 增量更新。"
        return _build_status_from_metadata(metadata, library_path, lead)
    except (LibraryBuildError, FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
        return (
            _format_error_card("建库失败", exc),
            f"Build failed:\n{exc}",
            [],
        )


def recognize_ui(
    library_dir: str,
    auto_build: bool,
    force_rebuild: bool,
    thread_count: float,
    query_upload: str | None,
    query_path: str | None,
) -> tuple[str, str, list[list[str]], str, str, list[list[Any]], Dict[str, Any]]:
    build_status_html, build_summary_text, build_failures_rows = _empty_build_outputs()

    try:
        library_path = _resolve_library_dir(library_dir)
        query_file = _resolve_query_file(query_upload, query_path)
        threads = _to_int(thread_count)

        index_paths = get_index_paths(library_path)
        build_triggered = False

        if force_rebuild:
            metadata = build_library(library_path, rebuild=True, thread_count=threads)
            build_status_html, build_summary_text, build_failures_rows = _build_status_from_metadata(
                metadata,
                library_path,
                "识别前已按要求强制重建索引。",
            )
            build_triggered = True
        elif auto_build and not index_paths["db_path"].exists():
            metadata = build_library(library_path, rebuild=False, thread_count=threads)
            build_status_html, build_summary_text, build_failures_rows = _build_status_from_metadata(
                metadata,
                library_path,
                "检测到索引不存在，已自动建库后继续识别。",
            )
            build_triggered = True
        else:
            build_status_html = _render_card_html(
                "建库状态",
                [
                    "本次识别未触发新的建库流程。",
                    (
                        "将复用现有索引进行识别。"
                        if index_paths["db_path"].exists()
                        else "当前未检测到索引，且已关闭自动建库。"
                    ),
                    f"索引数据库：{index_paths['db_path']}。",
                ],
                tone="info" if index_paths["db_path"].exists() else "warning",
            )
            build_summary_text = _format_index_ready_summary(
                library_path,
                auto_build=bool(auto_build),
                force_rebuild=bool(force_rebuild),
            )

        result = query_library(
            library_dir=library_path,
            query_file=query_file,
            auto_build=bool(auto_build and not build_triggered),
            thread_count=threads,
        )
        return (
            build_status_html,
            build_summary_text,
            build_failures_rows,
            _build_query_status_card(result, query_file, library_path),
            _build_best_match_card(result, library_path),
            _format_candidate_rows(result, library_path),
            _build_debug_payload(result, query_file, library_path),
        )
    except (QueryError, LibraryBuildError, FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
        return (
            build_status_html,
            build_summary_text,
            build_failures_rows,
            _format_error_card("识别失败", exc),
            _render_card_html(
                "暂无可展示结果",
                ["当前识别流程未成功完成，请先处理上方错误后重试。"],
                tone="error",
            ),
            [],
            {
                "status": "error",
                "message": str(exc),
            },
        )


def create_ui() -> gr.Blocks:
    build_status_value, build_summary_value, build_failures_value = _empty_build_outputs()
    query_status_value, best_match_value, candidate_rows_value, debug_value = _empty_query_outputs()
    cpu_count = _cpu_count()
    default_thread_count = _default_build_thread_count()

    theme = gr.themes.Soft(
        primary_hue="sky",
        secondary_hue="slate",
        neutral_hue="slate",
    )

    with gr.Blocks(title=APP_TITLE, theme=theme, css=CUSTOM_CSS) as demo:
        with gr.Column(elem_classes="app-shell"):
            gr.HTML(
                """
                <div class="hero">
                  <h1>Musin-ID 本地离线听歌识曲</h1>
                  <p>
                    复用现有 Python 音频指纹服务层，以现代化本地 Web UI 完成曲库建库、
                    自动建库查询、最佳匹配展示、候选对比与调试分析。
                  </p>
                  <div class="hero-badges">
                    <span class="badge">🎵 本地离线识别</span>
                    <span class="badge">🧱 可手动建库 / 自动建库</span>
                    <span class="badge">🧪 低置信与调试信息可视化</span>
                    <span class="badge">🛠️ 兼容现有 CLI 主流程</span>
                  </div>
                </div>
                """
            )

            with gr.Column(elem_classes="stack-layout"):
                with gr.Column(elem_classes="panel"):
                    gr.HTML(
                        "<div class='section-title'>项目配置与查询输入</div>"
                        "<div class='section-caption'>页面上方集中放置曲库配置、查询输入与主操作按钮，先完成输入再查看下方输出结果。</div>"
                    )
                    gr.HTML(
                        "<div class='section-title'>曲库配置区</div>"
                        "<div class='section-caption'>配置曲库目录、自动建库策略与建库线程数。</div>"
                    )
                    with gr.Row():
                        library_dir = gr.Textbox(
                            label="曲库目录",
                            placeholder="例如：E:/MusicLibrary 或 ./songs",
                            lines=1,
                            scale=8,
                        )
                        choose_library_dir_button = gr.Button(
                            "选择文件夹",
                            variant="secondary",
                            scale=1,
                            min_width=132,
                        )
                    with gr.Row():
                        auto_build = gr.Checkbox(
                            label="索引不存在时自动建库",
                            value=True,
                        )
                        force_rebuild = gr.Checkbox(
                            label="强制重建索引",
                            value=False,
                        )
                    thread_count = gr.Slider(
                        minimum=1,
                        maximum=cpu_count,
                        step=1,
                        value=default_thread_count,
                        label="建库线程数",
                        info=(
                            "用于手动建库，或识别时自动建库 / 强制重建。"
                            f" 当前机器 CPU 数量：{cpu_count}，最大可设为 {cpu_count}。"
                        ),
                    )

                    gr.HTML(
                        "<div class='section-title'>查询区</div>"
                        "<div class='section-caption'>上传查询音频，或直接输入文件路径后发起识别。</div>"
                    )
                    query_upload = gr.File(
                        label="选择查询音频文件",
                        type="filepath",
                        file_types=sorted(SUPPORTED_EXTENSIONS),
                    )
                    query_path = gr.Textbox(
                        label="或手动输入查询音频路径（优先于上传文件）",
                        placeholder="例如：E:/recordings/query.mp3",
                        lines=1,
                    )

                    with gr.Row(elem_classes="panel-actions"):
                        build_button = gr.Button("建立 / 更新索引", variant="secondary")
                        query_button = gr.Button("开始识别", variant="primary")
                        gr.ClearButton(
                            components=[library_dir, query_upload, query_path],
                            value="清空输入",
                        )

                    gr.HTML(
                        "<div class='note-box'>"
                        "提示：若查询结果为低置信命中，请重点查看 Top-K 候选与调试详情；"
                        "若音频格式无法解码，请确认系统已安装 ffmpeg。"
                        "</div>"
                    )

                with gr.Column(elem_classes="panel"):
                    gr.HTML(
                        "<div class='section-title'>输出结果总览</div>"
                        "<div class='section-caption'>页面下方统一展示建库状态、识别状态与详细结果，减少左右跳读带来的凌乱感。</div>"
                    )
                    gr.HTML(
                        "<div class='section-title'>状态总览</div>"
                        "<div class='section-caption'>实时查看建库状态、识别状态与空状态提示。</div>"
                    )
                    with gr.Column(elem_classes="status-overview"):
                        build_status = gr.HTML(value=build_status_value)
                        query_status = gr.HTML(value=query_status_value)

                with gr.Column(elem_classes="panel"):
                    gr.HTML(
                        "<div class='section-title'>建库结果 / 状态区</div>"
                        "<div class='section-caption'>查看建库摘要、失败文件与索引更新信息。</div>"
                    )
                    build_summary = gr.Textbox(
                        label="建库摘要",
                        value=build_summary_value,
                        lines=12,
                        interactive=False,
                    )
                    build_failures = gr.Dataframe(
                        headers=["失败文件", "错误信息"],
                        datatype=["str", "str"],
                        value=build_failures_value,
                        interactive=False,
                        wrap=True,
                        label="失败文件 / 错误提示",
                        row_count=(0, "dynamic"),
                    )

                with gr.Column(elem_classes="panel"):
                    gr.HTML(
                        "<div class='section-title'>识别结果区</div>"
                        "<div class='section-caption'>展示最佳匹配、Top-K 候选、关键评分和置信度。</div>"
                    )
                    best_match = gr.HTML(value=best_match_value)
                    candidates = gr.Dataframe(
                        headers=[
                            "排名",
                            "候选文件",
                            "置信度",
                            "综合得分",
                            "命中哈希",
                            "去重命中",
                            "最佳偏移票数",
                            "最佳偏移(秒)",
                            "覆盖率",
                            "偏移集中度",
                            "可信结果",
                        ],
                        datatype=[
                            "number",
                            "str",
                            "str",
                            "number",
                            "number",
                            "number",
                            "number",
                            "number",
                            "number",
                            "number",
                            "str",
                        ],
                        value=candidate_rows_value,
                        interactive=False,
                        wrap=True,
                        label="Top-K 候选",
                        row_count=(0, "dynamic"),
                    )

                    with gr.Accordion("调试详情区", open=False):
                        debug_output = gr.JSON(value=debug_value, label="查询统计与候选摘要")

        choose_library_dir_button.click(
            fn=choose_library_dir_ui,
            inputs=[library_dir],
            outputs=[library_dir],
            queue=False,
        )

        build_button.click(
            fn=build_index_ui,
            inputs=[library_dir, force_rebuild, thread_count],
            outputs=[build_status, build_summary, build_failures],
        )

        query_button.click(
            fn=recognize_ui,
            inputs=[library_dir, auto_build, force_rebuild, thread_count, query_upload, query_path],
            outputs=[
                build_status,
                build_summary,
                build_failures,
                query_status,
                best_match,
                candidates,
                debug_output,
            ],
        )

    demo.queue(default_concurrency_limit=1)
    return demo


def launch_ui(
    server_name: str = "127.0.0.1",
    server_port: int = 7860,
    inbrowser: bool = True,
) -> None:
    app = create_ui()
    app.launch(
        server_name=server_name,
        server_port=server_port,
        inbrowser=inbrowser,
        show_api=False,
    )


if __name__ == "__main__":
    launch_ui()
