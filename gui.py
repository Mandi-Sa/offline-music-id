import flet as ft
import threading
import os
import time
import librosa
import pygame
from pathlib import Path
from typing import Optional

from music_id.service import build_library, query_library, get_library_info, LibraryBuildError, QueryError, EmptyIndexError
from music_id.config import AUDIO, SPECTROGRAM, PEAKS, FINGERPRINT, MATCH, BUILD, INDEX
from music_id.config import load_config, save_config

class DebugConsole(ft.Container):
    def __init__(self, color_text_main="#FFFFFF", color_text_sec="#B0B0B0"):
        self.color_text_main = color_text_main
        self.color_text_sec = color_text_sec
        self.logs = ft.ListView(expand=True, spacing=2, auto_scroll=True)
        
        super().__init__(
            content=ft.Column([
                ft.Row([
                    ft.Text("调试控制台", size=14, weight=ft.FontWeight.BOLD, color=self.color_text_main),
                    ft.TextButton("清除", on_click=lambda _: self.clear_logs(), style=ft.ButtonStyle(color=self.color_text_sec)),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.logs,
            ], spacing=10, expand=True),
            bgcolor="#1A1A1A",
            padding=10,
            border_radius=16,
            height=200,
            width=400,
        )

    def add_log(self, level: str, message: str):
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        color = {
            "INFO": self.color_text_main,
            "DEBUG": self.color_text_sec,
            "WARN": "orange",
            "ERROR": "red",
        }.get(level, self.color_text_sec)
        
        self.logs.controls.append(
            ft.Text(f"[{timestamp}] [{level}] {message}",
                    size=12, color=color, font_family="Consolas")
        )
        self.update()

    def clear_logs(self):
        self.logs.controls.clear()
        self.update()

class MusicIDGUI:
    def __init__(self, page: ft.Page):
        self.page = page
        load_config()
        
        # Debug State
        self.debug_visible = False
        
        # State
        self.current_library_dir: Optional[Path] = None
        self.current_query_file: Optional[Path] = None
        self.is_building = False
        self.is_querying = False
        self.is_library_ready = False
        self.query_status = "准备就绪"
        self.query_result_data = None
        self.stop_event = threading.Event()
        self.is_playing_audio = False
        self.audio_duration = 0
        self.is_seeking = False
        self.playback_thread = None
        self.current_offset = 0
        self.current_playing_path: Optional[Path] = None
        
        # File Picker
        self.picker = ft.FilePicker()
        self.picker.on_result = self.handle_picker_result
        
        print("[DEBUG] Initializing MusicIDGUI...")
        self.setup_page()

    def create_player_bar(self):
        # Initialize playback components as class attributes for global access
        self.progress_slider = ft.Slider(
            min=0, max=100, value=0,
            on_change=self.handle_seek,
            active_color=self.color_primary,
        )
        self.time_text = ft.Text("00:00 / 00:00", size=12, color=self.color_text_sec)
        self.volume_slider = ft.Slider(
            min=0, max=1, value=1.0,
            on_change=self.handle_volume,
            active_color=self.color_primary,
            width=120,
        )
        self.current_song_text = ft.Text("未在播放", size=14, color=self.color_text_sec, italic=True)

        self.audio_controls = ft.Row([
            ft.IconButton("pause", icon_color=self.color_text_main, on_click=self.pause_audio, tooltip="暂停"),
            ft.IconButton("play_arrow", icon_color=self.color_text_main, on_click=self.resume_audio, tooltip="继续"),
            ft.IconButton("stop", icon_color=self.color_text_main, on_click=self.stop_audio, tooltip="停止"),
        ], alignment=ft.MainAxisAlignment.CENTER, spacing=10)

        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon("music_note", color=self.color_primary, size=20),
                    self.current_song_text,
                    ft.Row([
                        ft.Icon("volume_up", size=16, color=self.color_text_sec),
                        self.volume_slider,
                    ], alignment=ft.MainAxisAlignment.END),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Row([
                    self.audio_controls,
                    self.time_text,
                ], alignment=ft.MainAxisAlignment.CENTER),
                self.progress_slider,
            ], spacing=10),
            bgcolor=self.color_surface,
            padding=15,
            border_radius=16,
            height=200,
        )

    def setup_page(self):
        self.page.title = "Music-ID 离线识曲"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 0
        self.page.spacing = 0
        self.page.vertical_alignment = ft.MainAxisAlignment.START
        
        # Font Configuration
        self.font_family = "Microsoft YaHei"
        self.page.theme = ft.Theme(font_family=self.font_family)
        
        # Theme Colors
        self.color_bg = "#121212"
        self.color_surface = "#1E1E1E"
        self.color_card = "#2C2C2C"
        self.color_primary = "#BB86FC"
        self.color_text_main = "#FFFFFF"
        self.color_text_sec = "#B0B0B0"

        self.page.bgcolor = self.color_bg

        # Navigation Rail
        self.nav_rail = ft.NavigationRail(
            group_alignment=ft.MainAxisAlignment.START,
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=100,
            bgcolor=self.color_surface,
            destinations=[
                ft.NavigationRailDestination(icon="home", label="首页"),
                ft.NavigationRailDestination(icon="library_books", label="曲库管理"),
                ft.NavigationRailDestination(icon="search", label="识曲查询"),
                ft.NavigationRailDestination(icon="settings", label="系统配置"),
            ],
            on_change=self.handle_nav_change,
        )

        # Main Content Area
        self.content_area = ft.Column(
            controls=[self.get_home_view()],
            expand=True,
        )

        # Global Player Bar
        self.player_bar = self.create_player_bar()

        main_layout = ft.Row(
            [self.nav_rail, self.content_area],
            expand=True,
        )
        
        # Debug Console
        self.debug_console = DebugConsole(
            color_text_main=self.color_text_main,
            color_text_sec=self.color_text_sec
        )
        self.debug_console.visible = self.debug_visible

        self.player_bar.expand = True
        self.page.controls = [
            ft.Column([
                main_layout,
                ft.Row([
                    self.debug_console,
                    self.player_bar,
                ], alignment=ft.MainAxisAlignment.END),
            ], expand=True)
        ]
        self.page.overlay.append(self.picker)
        print("[DEBUG] Page layout and FilePicker overlay added. Updating page...")
        self.page.update()

    def log_debug(self, level: str, message: str):
        self.debug_console.add_log(level, message)
        self.page.update()

    def toggle_debug_console(self, e):
        self.debug_visible = not self.debug_visible
        self.debug_console.visible = self.debug_visible
        self.page.update()

    def handle_nav_change(self, e):
        index = self.nav_rail.selected_index
        
        if index == 0:
            self.content_area.controls = [self.get_home_view()]
        elif index == 1:
            self.content_area.controls = [self.get_library_view()]
        elif index == 2:
            self.content_area.controls = [self.get_query_view()]
        elif index == 3:
            self.content_area.controls = [self.get_settings_view()]
        
        self.page.update()

    def get_home_view(self):
        print("[DEBUG] Generating home view...")
        return ft.Column(
            [
                ft.Text("Music-ID", size=40, weight=ft.FontWeight.BOLD, color=self.color_primary),
                ft.Text("现代化离线音频指纹识别系统", size=18, weight=ft.FontWeight.W_400, color=self.color_text_sec),
                ft.Divider(height=20, color="transparent"),
                ft.Container(
                    content=ft.Column([
                        ft.Text("当前状态", size=20, weight=ft.FontWeight.W_500),
                        ft.Text(f"曲库路径: {self.current_library_dir or '未设置'}", weight=ft.FontWeight.W_400, color=self.color_text_sec),
                    ]),
                    bgcolor=self.color_card,
                    padding=20,
                    border_radius=16,
                ),
            ],
            expand=True,
        )

    def check_library_status(self):
        if not self.current_library_dir:
            self.is_library_ready = False
            return
 
        try:
            info = get_library_info(self.current_library_dir)
            self.is_library_ready = info["index_exists"] and info["song_count"] > 0
            
            if not self.is_library_ready:
                print(f"[DEBUG] Library not ready: {info}")
            else:
                if info["needs_update"]:
                    self.page.snackbar = ft.SnackBar(
                        content=ft.Text(f"检测到曲库文件({info['file_count']})与索引记录({info['song_count']})不匹配，建议更新"),
                        bgcolor="orange"
                    )
                    self.page.update()
        except Exception as e:
            print(f"[DEBUG] Library check failed: {e}")
            self.is_library_ready = False

    def handle_picker_result(self, e):
        if e.path:
            path = Path(e.path)
            if path.is_dir():
                self.current_library_dir = path
                print(f"[DEBUG] Selected directory: {path}")
                self.check_library_status()
                self.update_home_status()
                self.update_library_view()
                return

        if e.files and len(e.files) > 0:
            path_str = e.files[0].path
            if path_str:
                path = Path(path_str)
                self.current_query_file = path
                print(f"[DEBUG] Selected file: {path}")
                if self.nav_rail.selected_index == 2:
                    self.content_area.controls = [self.get_query_view()]
                    self.page.update()

    def update_home_status(self):
        if self.nav_rail.selected_index == 0:
            self.content_area.controls = [self.get_home_view()]
            self.page.update()

    def update_library_view(self):
        if self.nav_rail.selected_index == 1:
            self.content_area.controls = [self.get_library_view()]
            self.page.update()

    def get_library_view(self):
        self.lib_path_text = ft.Text(f"当前路径: {self.current_library_dir or '未选择'}", weight=ft.FontWeight.W_400, color=self.color_text_sec)
        self.lib_rebuild_switch = ft.Switch(label="强制重建索引", value=False, active_color=self.color_primary)
        self.lib_progress_bar = ft.ProgressBar(value=0, width=400, color=self.color_primary)
        self.lib_status_text = ft.Text("准备就绪", weight=ft.FontWeight.W_400, color=self.color_text_sec)
        self.lib_build_btn = ft.ElevatedButton(
            text="开始构建索引",
            on_click=self.start_build,
            disabled=self.is_building,
            style=ft.ButtonStyle(bgcolor=self.color_primary, color=self.color_bg)
        )
        self.lib_stop_btn = ft.ElevatedButton(
            text="停止",
            on_click=self.stop_task,
            disabled=not self.is_building,
            style=ft.ButtonStyle(bgcolor="red", color="white")
        )

        def build_status_panel():
            if not self.current_library_dir:
                return ft.Container(
                    content=ft.Text("请先选择曲库文件夹以查看状态", color=self.color_text_sec),
                    bgcolor=self.color_card, padding=20, border_radius=16
                )
            if self.is_building:
                return ft.Container(
                    content=ft.Text("正在构建索引中，请稍候...", color=self.color_primary),
                    bgcolor=self.color_card, padding=20, border_radius=16
                )
            info = get_library_info(self.current_library_dir)
            if not info["index_exists"]:
                status_color, status_text = "red", "缺失索引"
            elif info["needs_update"]:
                status_color, status_text = "orange", "建议更新"
            else:
                status_color, status_text = "green", "状态正常"
            return ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Text("曲库状态", size=18, weight=ft.FontWeight.BOLD, color=self.color_text_main),
                        ft.Container(
                            content=ft.Text(status_text, size=12, color="white"),
                            bgcolor=status_color, padding=ft.padding.symmetric(horizontal=10, vertical=2), border_radius=10
                        ),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Divider(height=10, color="transparent"),
                    ft.Row([
                        ft.Text(f"文件夹文件数: {info['file_count']}", color=self.color_text_sec),
                        ft.Text(f"索引记录数: {info['song_count']}", color=self.color_text_sec),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Row([
                        ft.Text(f"指纹总数: {info['fingerprint_count']}", color=self.color_text_sec),
                        ft.Text(f"索引状态: {'已存在' if info['index_exists'] else '不存在'}", color=self.color_text_sec),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ]),
                bgcolor=self.color_card, padding=20, border_radius=16,
            )

        return ft.Column(
            [
                ft.Text("曲库管理", size=24, weight=ft.FontWeight.BOLD, color=self.color_text_main),
                ft.Divider(height=20, color="transparent"),
                ft.Row([
                    ft.ElevatedButton("选择文件夹", icon="folder_open", on_click=lambda _: self.picker.get_directory_path()),
                    self.lib_path_text,
                ], alignment=ft.MainAxisAlignment.START),
                ft.Divider(height=10, color="transparent"),
                build_status_panel(),
                ft.Divider(height=20, color="transparent"),
                ft.Row([
                    self.lib_rebuild_switch,
                    self.lib_build_btn,
                    self.lib_stop_btn,
                ], alignment=ft.MainAxisAlignment.START),
                ft.Divider(height=20, color="transparent"),
                ft.Column([
                    ft.Text("构建进度", size=16, weight=ft.FontWeight.W_400, color=self.color_text_main),
                    self.lib_progress_bar,
                    self.lib_status_text,
                ], spacing=10),
            ],
            expand=True,
        )

    def start_build(self, e):
        if not self.current_library_dir:
            self.lib_status_text.value = "错误: 请先选择文件夹"
            self.lib_status_text.color = "red"
            self.page.update()
            return
        if self.is_building: return
        self.is_building = True
        self.stop_event.clear()
        self.lib_build_btn.disabled = True
        self.lib_stop_btn.disabled = False
        self.lib_status_text.value = "正在初始化..."
        self.lib_status_text.color = self.color_text_sec
        self.page.update()

        def build_thread():
            try:
                def progress_cb(current, total, msg):
                    self.lib_progress_bar.value = current / total
                    self.lib_status_text.value = f"{msg} ({current}/{total})"
                    self.page.update()
                result = build_library(
                    library_dir=self.current_library_dir, # type: ignore
                    rebuild=self.lib_rebuild_switch.value,
                    progress_callback=progress_cb,
                    log_callback=self.log_debug,
                    stop_event=self.stop_event
                )
                self.lib_status_text.value = f"构建完成! 索引了 {result['song_count']} 首歌曲"
                self.lib_status_text.color = "green"
            except Exception as exc:
                self.lib_status_text.value = f"构建失败: {str(exc)}"
                self.lib_status_text.color = "red"
            finally:
                self.is_building = False
                self.lib_build_btn.disabled = False
                self.lib_stop_btn.disabled = True
                self.page.update()
        threading.Thread(target=build_thread, daemon=True).start()

    def get_query_view(self):
        self.query_file_text = ft.Text(f"已选择: {self.current_query_file or '未选择'}", weight=ft.FontWeight.W_400, color=self.color_text_sec)
        self.query_progress_bar = ft.ProgressBar(width=400, color=self.color_primary, visible=self.is_querying)
        self.query_status_text = ft.Text(self.query_status, weight=ft.FontWeight.W_400, color=self.color_text_sec, visible=self.is_querying)
        self.query_start_btn = ft.ElevatedButton(
            "开始识别", icon="search", on_click=self.start_query,
            style=ft.ButtonStyle(bgcolor=self.color_primary, color=self.color_bg),
            disabled=self.is_querying or not self.is_library_ready
        )
        self.query_stop_btn = ft.ElevatedButton(
            text="停止", on_click=self.stop_task, disabled=not self.is_querying,
            style=ft.ButtonStyle(bgcolor="red", color="white")
        )
        self.query_result_container = ft.Column(spacing=20)
        if self.query_result_data:
            self.display_query_results(self.query_result_data)
        return ft.Column([
            ft.Text("识曲查询", size=24, weight=ft.FontWeight.BOLD, color=self.color_text_main),
            ft.Divider(height=20, color="transparent"),
            ft.Column([
                ft.Container(
                    content=ft.Column([
                        ft.Text("上传音频片段", size=18, weight=ft.FontWeight.W_500),
                        ft.Row([
                            ft.ElevatedButton("选择文件", icon="upload_file", on_click=lambda _: self.picker.pick_files(),
                                style=ft.ButtonStyle(bgcolor=self.color_primary, color=self.color_bg)),
                            self.query_file_text,
                        ], alignment=ft.MainAxisAlignment.START),
                        ft.Row([self.query_start_btn, self.query_stop_btn], alignment=ft.MainAxisAlignment.START),
                    ], spacing=20), bgcolor=self.color_card, padding=20, border_radius=16,
                ),
                ft.Divider(height=30, color="transparent", visible=not self.is_querying),
                ft.Column([
                    ft.Text("识别进度", size=20, weight=ft.FontWeight.W_500, visible=self.is_querying),
                    self.query_progress_bar,
                    self.query_status_text,
                ], spacing=10, visible=self.is_querying),
                ft.Divider(height=30, color="transparent", visible=not self.is_querying),
                ft.Text("识别结果", size=20, weight=ft.FontWeight.W_500, visible=not self.is_querying),
                ft.Column([self.query_result_container], visible=not self.is_querying),
            ], scroll=ft.ScrollMode.AUTO, expand=True),
        ], expand=True)

    def start_query(self, e):
        if not self.current_library_dir:
            self.query_result_container.controls = [ft.Text("错误: 请先在曲库管理中设置曲库路径", weight=ft.FontWeight.W_400, color="red")]
            self.page.update()
            return
        if not self.current_query_file:
            self.query_result_container.controls = [ft.Text("错误: 请先选择要识别的音频文件", weight=ft.FontWeight.W_400, color="red")]
            self.page.update()
            return
        self.is_querying = True
        self.stop_event.clear()
        self.query_status = "初始化..."
        self.query_result_data = None
        self.query_result_container.controls = []
        self.query_start_btn.disabled = True
        self.query_stop_btn.disabled = False
        self.page.update()

        def query_thread():
            try:
                def progress_cb(msg):
                    self.query_status = msg
                    if self.nav_rail.selected_index == 2:
                        if hasattr(self, 'query_status_text'):
                            self.query_status_text.value = msg
                            self.page.update()
                result = query_library(
                    library_dir=self.current_library_dir, # type: ignore
                    query_file=self.current_query_file, # type: ignore
                    progress_callback=progress_cb,
                    log_callback=self.log_debug,
                    stop_event=self.stop_event
                )
                self.query_result_data = result
                self.display_query_results(result)
            except EmptyIndexError as exc:
                self.query_result_container.controls = [ft.Text("索引库为空，请先在‘曲库管理’页面构建索引", weight=ft.FontWeight.W_400)]
                self.page.snackbar = ft.SnackBar(content=ft.Text("索引库为空，请先在‘曲库管理’页面构建索引"))
                self.page.update()
            except Exception as exc:
                self.query_result_container.controls = [ft.Text(f"识别失败: {str(exc)}", weight=ft.FontWeight.W_400, color="red")]
                self.page.update()
            finally:
                self.is_querying = False
                self.query_status = "准备就绪"
                self.query_start_btn.disabled = not self.is_library_ready
                self.query_stop_btn.disabled = True
                self.page.update()
        threading.Thread(target=query_thread, daemon=True).start()

    def stop_task(self, e):
        self.stop_event.set()
        if self.is_building:
            self.is_building = False
            self.lib_status_text.value = "任务已停止"
            self.lib_status_text.color = "orange"
            self.lib_build_btn.disabled = False
            self.lib_stop_btn.disabled = True
        elif self.is_querying:
            self.is_querying = False
            self.query_status = "任务已停止"
            if hasattr(self, 'query_status_text'):
                self.query_status_text.value = "任务已停止"
            self.query_start_btn.disabled = not self.is_library_ready
            self.query_stop_btn.disabled = True
        self.page.update()

    def format_time(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def handle_seek(self, e):
        self.is_seeking = True
        try:
            if pygame.mixer.music.get_busy():
                # pygame.mixer.music.set_pos is in seconds for some formats, but behavior varies.
                # For MP3 it might not work as expected.
                pygame.mixer.music.set_pos(e.control.value)
                self.current_offset = e.control.value
            else:
                print("[DEBUG] Seek ignored: Music isn't playing")
        except Exception as e:
            print(f"Seek error: {e}")
        self.is_seeking = False

    def handle_volume(self, e):
        try:
            pygame.mixer.music.set_volume(e.control.value)
        except Exception as e:
            print(f"Volume error: {e}")

    def update_playback_progress(self):
        while self.is_playing_audio:
            if not pygame.mixer.music.get_busy():
                # Music finished or stopped naturally
                self.is_playing_audio = False
                self.current_song_text.value = "未在播放"
                self.progress_slider.value = 0
                self.time_text.value = "00:00 / 00:00"
                self.page.update()
                break

            if not self.is_seeking and self.progress_slider and self.time_text:
                try:
                    # get_pos() returns ms since play() or set_pos() was called
                    current_pos = self.current_offset + (pygame.mixer.music.get_pos() / 1000)
                    if self.audio_duration > 0:
                        # Clamp value to avoid "value must be less than or equal to max" error
                        clamped_pos = min(current_pos, self.audio_duration)
                        self.progress_slider.value = clamped_pos
                        self.time_text.value = f"{self.format_time(clamped_pos)} / {self.format_time(self.audio_duration)}"
                        self.page.update()
                except Exception as e:
                    print(f"Update progress error: {e}")
            time.sleep(0.5)

    def display_query_results(self, result):
        if not hasattr(self, 'query_result_container') or result is None: return
        top = result.best
        if not top:
            self.query_result_container.controls = [ft.Text("未找到匹配结果", weight=ft.FontWeight.W_400, color=self.color_text_sec)]
            self.page.update()
            return
        
        # File size formatting
        try:
            file_size = os.path.getsize(top.path)
            if file_size < 1024 * 1024:
                size_str = f"{file_size / 1024:.1f} KB"
            else:
                size_str = f"{file_size / (1024 * 1024):.1f} MB"
        except Exception:
            size_str = "未知大小"
        
        top_card = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon("music_note", color=self.color_primary, size=30),
                    ft.Column([
                        ft.Text(f"最佳匹配: {top.path}", size=18, weight=ft.FontWeight.BOLD, color=self.color_text_main),
                        ft.Row([
                            ft.Text(f"置信度得分: {top.score:.2f}", weight=ft.FontWeight.W_400, color=self.color_text_sec),
                            ft.Text(f" | 文件大小: {size_str}", weight=ft.FontWeight.W_400, color=self.color_text_sec),
                        ]),
                    ], expand=True),
                    ft.IconButton("play_arrow", icon_color=self.color_primary, on_click=lambda _: self.play_audio(top.path)),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ]), bgcolor=self.color_surface, padding=20, border_radius=16, border=ft.border.all(1, self.color_primary), animate_offset=500, offset=ft.Offset(0, 0.1),
        )
        candidates_list = ft.Column([ft.Text("其他候选结果", size=16, weight=ft.FontWeight.W_400, color=self.color_text_sec)], spacing=10)
        for cand in result.top_candidates[1:]:
            candidates_list.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(f"{cand.path}", expand=True, weight=ft.FontWeight.W_400, color=self.color_text_main),
                        ft.Text(f"得分: {cand.score:.2f}", weight=ft.FontWeight.W_400, color=self.color_text_sec),
                        ft.IconButton("play_arrow", icon_color=self.color_text_sec, on_click=lambda _, p=cand.path: self.play_audio(p)),
                    ]), bgcolor=self.color_card, padding=10, border_radius=8, animate_opacity=500, opacity=0,
                )
            )
        self.query_result_container.controls = [top_card, candidates_list]
        self.page.update()
        top_card.offset = ft.Offset(0, 0)
        for control in candidates_list.controls:
            if isinstance(control, ft.Container): control.opacity = 1
        self.page.update()

    def play_audio(self, path):
        try:
            if not pygame.mixer.get_init(): pygame.mixer.init()
            
            # Update current playing state
            self.current_playing_path = Path(path)
            self.current_song_text.value = f"正在播放: {self.current_playing_path.name}"
            
            # Get duration using librosa for accuracy
            self.audio_duration = librosa.get_duration(path=str(path))
            self.current_offset = 0
            
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()
            
            self.is_playing_audio = True
            self.progress_slider.max = self.audio_duration
            self.progress_slider.value = 0
            self.time_text.value = f"00:00 / {self.format_time(self.audio_duration)}"
            
            # The player bar is always visible now, but we can update its appearance if needed
            # self.player_bar.visible = True # It's already in the layout
            
            # Start update thread
            if self.playback_thread and self.playback_thread.is_alive():
                pass # already running
            else:
                self.playback_thread = threading.Thread(target=self.update_playback_progress, daemon=True)
                self.playback_thread.start()
                
            self.page.update()
        except Exception as e:
            print(f"Audio playback error: {e}")

    def pause_audio(self, e):
        import pygame
        try: pygame.mixer.music.pause()
        except Exception as e: print(f"Pause audio error: {e}")

    def resume_audio(self, e):
        import pygame
        try: pygame.mixer.music.unpause()
        except Exception as e: print(f"Resume audio error: {e}")

    def stop_audio(self, e):
        try:
            pygame.mixer.music.stop()
            self.is_playing_audio = False
            self.current_song_text.value = "未在播放"
            self.progress_slider.value = 0
            self.time_text.value = "00:00 / 00:00"
            self.page.update()
        except Exception as e: print(f"Stop audio error: {e}")

    def get_settings_view(self):
        from music_id.config import AUDIO, SPECTROGRAM, PEAKS, FINGERPRINT, MATCH, BUILD, DEBUG, INDEX
        groups = {"基础音频音频处理": [AUDIO, SPECTROGRAM], "指纹提取策略": [PEAKS, FINGERPRINT], "匹配算法调优": [MATCH], "系统性能与存储": [BUILD, DEBUG, INDEX]}
        label_map = {
            "sample_rate": "音频采样率", "mono": "强制单声道", "normalize": "幅度归一化", "pre_emphasis": "预加重系数", "highpass_cutoff_hz": "高通滤波截止频率",
            "n_fft": "频谱分析精度 (FFT)", "hop_length": "帧移 (Hop Length)", "window": "窗函数类型", "top_db": "频谱峰值阈值 (dB)",
            "amp_min_db": "噪声过滤阈值", "neighborhood_freq_bins": "频率邻域范围", "neighborhood_time_bins": "时间邻域范围", "max_peaks_per_frame": "每帧最大峰值数", "max_peaks_per_second": "每秒最大峰值数", "min_freq_hz": "最小分析频率 (Hz)", "max_freq_hz": "最大分析频率 (Hz)", "min_frame_peak_percentile": "峰值百分比阈值",
            "fan_value": "扇出出值 (Fan Value)", "target_zone_start_s": "目标区起始时间 (s)", "target_zone_end_s": "目标区结束时间 (s)", "max_targets_per_anchor": "每个锚点最大目标数", "anchor_step": "锚点步 l步长", "delta_t_quantization": "时间量化间隔", "freq_quantization_hz": "频率量化间隔 (Hz)", "hash_mod": "哈希模数", "include_freq_delta": "包含频率偏移量 (增强鲁棒性)",
            "top_k": "返回结果数量", "min_query_duration_s": "最短查询时长 (s)", "min_confident_score": "置信度得分阈值", "min_confident_matched_hashes": "最少匹配哈希数", "min_confident_coverage_ratio": "最低覆盖率", "min_confident_offset_ratio": "偏移量容差比", "offset_bin_size_frames": "偏移量量化步长", "score_offset_weight": "偏移权重", "score_hash_weight": "哈希权重", "score_coverage_weight": "覆盖率权重", "score_concentration_weight": "集中度权重",
            "sequential_scan": "顺序扫描模式", "max_workers": "并行处理线程数", "prefetch_window": "预取窗口大小", "commit_every_n_files": "提交频率 (文件数)", "prefer_locality_order": "优先本地化排序",
            "enabled": "启用调试模式", "top_candidate_details": "候选详情数量",
            "index_dir_name": "索引目录名", "db_name": "数据库文件名", "metadata_name": "元数据文件名", "fingerprints_batch_size": "指纹写入批次大小",
        }
        settings_list = ft.Column(spacing=30, scroll=ft.ScrollMode.AUTO, expand=True)
        
        # Add header and debug toggle to the scrollable list
        settings_list.controls.append(ft.Text("系统配置", size=24, weight=ft.FontWeight.BOLD, color=self.color_text_main))
        settings_list.controls.append(ft.Divider(height=20, color="transparent"))
        
        debug_toggle = ft.Switch(
            label="显示调试控制台",
            value=self.debug_visible,
            on_change=self.toggle_debug_console,
            active_color=self.color_primary
        )
        settings_list.controls.append(ft.Row([debug_toggle], alignment=ft.MainAxisAlignment.START))
        settings_list.controls.append(ft.Divider(height=20, color="transparent"))
        
        for group_name, config_objs in groups.items():
            group_controls = []
            for config_obj in config_objs:
                section_controls = []
                for field_name, field_def in config_obj.__dataclass_fields__.items():
                    if not hasattr(config_obj, field_name) or callable(getattr(config_obj, field_name)): continue
                    val = getattr(config_obj, field_name)
                    display_name = label_map.get(field_name, field_name)
                    if isinstance(val, bool):
                        control = ft.Switch(label=display_name, value=val, on_change=lambda e, obj=config_obj, name=field_name: self.update_config_val(obj, name, e.control.value))
                    elif isinstance(val, (int, float)):
                        control = ft.TextField(label=display_name, value=str(val), width=200, on_change=lambda e, obj=config_obj, name=field_name: self.update_config_num(obj, name, e.control.value))
                    else:
                        control = ft.TextField(label=display_name, value=str(val), width=200, on_change=lambda e, obj=config_obj, name=field_name: self.update_config_val(obj, name, e.control.value))
                    section_controls.append(control)
                group_controls.append(ft.Column([ft.Text(f"{config_obj.__class__.__name__}", size=14, weight=ft.FontWeight.W_500, color=self.color_text_sec), ft.Row(section_controls, wrap=True, spacing=20)], spacing=10))
            settings_list.controls.append(ft.Container(content=ft.Column([ft.Text(group_name, size=18, weight=ft.FontWeight.BOLD, color=self.color_primary), ft.Divider(height=10, color="transparent"), ft.Column(group_controls, spacing=20)]), bgcolor=self.color_card, padding=20, border_radius=16, animate_opacity=300))
        return settings_list

    def update_config_val(self, obj, name, value):
        setattr(obj, name, value)
        save_config()
        self.page.snackbar = ft.SnackBar(content=ft.Text(f"已更新 {name} 为 {value}", weight=ft.FontWeight.W_400))
        self.page.update()

    def update_config_num(self, obj, name, value):
        try:
            current_val = getattr(obj, name)
            if isinstance(current_val, int): setattr(obj, name, int(value))
            elif isinstance(current_val, float): setattr(obj, name, float(value))
            else: setattr(obj, name, value)
            save_config()
            self.page.snackbar = ft.SnackBar(content=ft.Text(f"已更新 {name} 为 {value}", weight=ft.FontWeight.W_400))
            self.page.update()
        except ValueError:
            self.page.snackbar = ft.SnackBar(content=ft.Text(f"无效的数值输入: {value}", bgcolor="red"))
            self.page.update()

def app_main(page: ft.Page):
    MusicIDGUI(page)

if __name__ == "__main__":
    ft.app(target=app_main)
