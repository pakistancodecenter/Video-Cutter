import sys
import os
import shutil
import traceback
import logging
from datetime import datetime

APP_VERSION = "1.1.0"

# True when running as a PyInstaller-built executable, False under `python
# Video_Cutter.py`. Nothing below should assume one or the other.
IS_FROZEN = bool(getattr(sys, "frozen", False))


def _app_dir() -> str:
    """Directory the executable lives in (frozen), or this script's directory (dev).

    This is where we look for a bundled ffmpeg/ folder — it's next to
    VideoCutter.exe in a PyInstaller one-folder build, not inside the
    temporary _MEIPASS extraction dir.
    """
    if IS_FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _bundle_dir() -> str:
    """PyInstaller's bundled-resource dir: _MEIPASS for one-file builds,
    otherwise the same as _app_dir() (one-folder builds and dev mode)."""
    return getattr(sys, "_MEIPASS", _app_dir())


# ─── FFmpeg discovery & bundling ────────────────────────────────────────────
#
# THIS MUST RUN BEFORE `import moviepy` ANYWHERE IN THIS PROCESS.
#
# moviepy.config resolves its FFMPEG_BINARY exactly once, at import time, by
# calling imageio_ffmpeg.get_ffmpeg_exe() (which itself checks the
# IMAGEIO_FFMPEG_EXE environment variable). Every moviepy submodule that
# needs it then does `from moviepy.config import FFMPEG_BINARY`, which binds
# a private snapshot of that value into its own namespace. Setting
# IMAGEIO_FFMPEG_EXE *after* moviepy has already been imported — e.g. later
# in __main__, or lazily on first export — has no effect whatsoever: every
# already-imported moviepy module keeps using whatever ffmpeg it resolved at
# import time, silently ignoring a bundled copy we point at afterward. That
# was tested and confirmed while building this app, which is why this whole
# block sits above every PySide6/moviepy import in the file and why
# resolve_ffmpeg() is called immediately below, at module load, rather than
# from __main__.
#
# Priority order, so the packaged app never depends on the target machine
# having FFmpeg installed:
#   1. A copy we bundled ourselves at ffmpeg/ffmpeg.exe next to the built
#      executable (see video_cutter.spec / BUILD.md).
#   2. The copy imageio-ffmpeg ships/downloads on its own (a dependency of
#      MoviePy, present in both dev installs and PyInstaller builds that
#      collect it — see the .spec file).
#   3. `ffmpeg` on the system PATH, purely as a last resort for developers
#      running from source with neither of the above.
#
# Note: logging isn't configured yet at this point in the startup sequence
# (that needs Qt-adjacent state we don't want to load this early), so the
# logging.info/.warning calls below are inert until _init_logging() runs
# further down — which then re-logs the final FFMPEG_PATH so it still ends
# up in the log file.

FFMPEG_PATH = None  # set by resolve_ffmpeg(); None means "not found"


def _bundled_ffmpeg_candidates():
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    # Check next to the executable first (real layout of a built app), then
    # the extraction dir (only differs from the above for one-file builds).
    seen = []
    for d in (_app_dir(), _bundle_dir()):
        c = os.path.join(d, "ffmpeg", exe_name)
        if c not in seen:
            seen.append(c)
    return seen


def _find_bundled_ffmpeg():
    for candidate in _bundled_ffmpeg_candidates():
        if os.path.isfile(candidate):
            return candidate
    return None


def resolve_ffmpeg():
    """Find the best available FFmpeg, configure MoviePy to use it, and
    cache the result in FFMPEG_PATH. Idempotent, but must be called before
    `moviepy` is imported for the MoviePy side of it to take effect — see
    the block comment above."""
    global FFMPEG_PATH

    bundled = _find_bundled_ffmpeg()
    if bundled:
        os.environ["IMAGEIO_FFMPEG_EXE"] = bundled
        FFMPEG_PATH = bundled
        logging.info(f"FFmpeg: using bundled copy at {bundled}")
        return FFMPEG_PATH

    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            os.environ.setdefault("IMAGEIO_FFMPEG_EXE", path)
            FFMPEG_PATH = path
            logging.info(f"FFmpeg: using imageio-ffmpeg's copy at {path}")
            return FFMPEG_PATH
    except Exception as e:
        logging.warning(f"FFmpeg: imageio-ffmpeg lookup failed: {e}")

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        FFMPEG_PATH = system_ffmpeg
        logging.info(f"FFmpeg: using system PATH copy at {system_ffmpeg}")
        return FFMPEG_PATH

    FFMPEG_PATH = None
    logging.warning("FFmpeg: no usable copy found (bundled, imageio-ffmpeg, or PATH)")
    return None


def ffmpeg_available() -> bool:
    """True if a working FFmpeg has been (or can be) resolved."""
    path = FFMPEG_PATH or resolve_ffmpeg()
    return bool(path and os.path.isfile(path))


# Resolve (and pin via IMAGEIO_FFMPEG_EXE) *before* moviepy is imported below.
resolve_ffmpeg()


# ─── Qt / MoviePy imports ───────────────────────────────────────────────────
# (safe now that FFmpeg has been resolved and pinned above)

from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel,
    QVBoxLayout, QFileDialog, QHBoxLayout, QSlider,
    QSizePolicy, QFrame, QListWidget, QListWidgetItem,
    QMessageBox, QProgressDialog
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import QUrl, Qt, QTimer, QThread, Signal, QObject, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QCursor, QPalette
from moviepy import VideoFileClip


# ─── Logging setup ─────────────────────────────────────────────────────────

def _user_data_dir() -> str:
    """A writable per-user directory for logs, independent of where the app
    is installed (e.g. Program Files is often not writable without admin)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return os.path.join(base, "VideoCutter")
    # Dev / non-Windows fallback: keep the old behavior (logs/ next to the app).
    return os.path.join(_app_dir(), "logs")


LOG_DIR = _user_data_dir() if os.name == "nt" else os.path.join(_app_dir(), "logs")


def _init_logging():
    # force=True is required here: resolve_ffmpeg() above already called
    # logging.info()/.warning() before this function runs (deliberately —
    # it has to resolve FFmpeg before `import moviepy`, see the comment on
    # that block). Any logging.<level>() call on a completely unconfigured
    # root logger implicitly triggers Python's own basicConfig() with a
    # bare stderr StreamHandler — and basicConfig() silently does nothing
    # on later calls once the root logger already has a handler, unless
    # force=True. Without this, the file handler below was never actually
    # installed and nothing was ever written to disk in any run mode; this
    # was only caught by launching the built app and checking for a log
    # file, not by reading source or the build log.
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(
            LOG_DIR, f"video_cutter_{datetime.now():%Y%m%d}.log"
        )
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            force=True,
        )
    except Exception:
        # If we can't create a log file (e.g. read-only folder), fall back
        # to console logging so the app still starts.
        logging.basicConfig(level=logging.INFO, force=True)
    logging.info(f"=== Video Cutter v{APP_VERSION} starting ===")
    logging.info(f"Frozen (PyInstaller build): {IS_FROZEN}")
    logging.info(f"Python: {sys.version.split()[0]}  Platform: {sys.platform}")
    logging.info(f"App dir: {_app_dir()}")
    logging.info(f"Log dir: {LOG_DIR}")
    # Resolved before logging existed (see block above) — re-log it now so
    # it's actually captured in the log file, not just lost to a no-op call.
    logging.info(f"FFmpeg resolved at startup: {FFMPEG_PATH or 'NOT FOUND'}")


def install_global_exception_hook():
    """Catch anything that would otherwise silently crash the app."""

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        tb_text = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )
        logging.error("Unhandled exception:\n%s", tb_text)

        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Unexpected Error")
            box.setText(
                "Something went wrong and the error has been logged.\n"
                "You can usually keep working — if the app misbehaves, "
                "please restart it."
            )
            box.setDetailedText(tb_text)
            box.exec()
        except Exception:
            # If even the dialog fails, don't let that raise further.
            pass

    sys.excepthook = handle_exception


# ─── Background thread for video export ───────────────────────────────────────

class ExportWorker(QObject):
    progress = Signal(int, int)   # current, total
    finished = Signal()
    error    = Signal(str)
    cancelled = Signal()

    def __init__(self, file_path, segments, out_dir):
        super().__init__()
        self.file_path = file_path
        self.segments  = segments   # list of (start, end)
        self.out_dir   = out_dir
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True
        logging.info("Export cancellation requested")

    @staticmethod
    def _unique_output_path(out_dir, index):
        """Never silently overwrite an existing file."""
        base = f"video_part_{index}"
        candidate = os.path.join(out_dir, f"{base}.mp4")
        suffix = 1
        while os.path.exists(candidate):
            candidate = os.path.join(out_dir, f"{base}_{suffix}.mp4")
            suffix += 1
        return candidate

    def run(self):
        video = None
        total = len(self.segments)
        completed = 0
        try:
            logging.info(f"Export starting: {self.file_path}, {total} segment(s)")
            video = VideoFileClip(self.file_path)

            for i, (start, end) in enumerate(self.segments):
                if self._cancel_requested:
                    logging.info("Export cancelled before segment %d", i + 1)
                    self.cancelled.emit()
                    return

                out_path = self._unique_output_path(self.out_dir, i + 1)
                clip = None
                try:
                    clip = video.subclipped(start, end)
                    clip.write_videofile(
                        out_path,
                        codec="libx264",
                        audio_codec="aac",
                        logger=None,
                    )
                finally:
                    if clip is not None:
                        try:
                            clip.close()
                        except Exception:
                            pass

                completed += 1
                self.progress.emit(completed, total)
                logging.info(f"Exported segment {completed}/{total} -> {out_path}")

                if self._cancel_requested:
                    logging.info("Export cancelled after segment %d", completed)
                    self.cancelled.emit()
                    return

            self.finished.emit()
            logging.info("Export finished successfully")
        except Exception as e:
            logging.error("Export failed: %s", e, exc_info=True)
            self.error.emit(str(e))
        finally:
            if video is not None:
                try:
                    video.close()
                except Exception:
                    pass


# ─── Custom timeline widget ────────────────────────────────────────────────────

class TimelineWidget(QWidget):
    """
    Interactive timeline that supports:
      - Click to seek
      - Click-drag to create a selection region
      - Visual display of all saved markers / regions
    """
    seekRequested    = Signal(float)   # seconds
    selectionChanged = Signal(float, float)  # start_sec, end_sec

    HANDLE_W  = 8    # px width of drag handle
    TICK_H    = 6    # px height of tick marks
    MIN_SELECTION_SEC = 0.05  # ignore tiny accidental drags

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setMouseTracking(True)

        self.duration      = 0.0
        self.position      = 0.0          # current play head (sec)
        self.sel_start     = None         # selection start (sec)
        self.sel_end       = None         # selection end   (sec)
        self.regions       = []           # list of (start, end) saved regions
        self._drag_origin  = None         # pixel x where drag started
        self._dragging     = False
        self._hover_x      = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _sec_to_x(self, sec):
        if self.duration <= 0:
            return self.HANDLE_W
        w = max(self.width() - 2 * self.HANDLE_W, 1)
        return int(self.HANDLE_W + (sec / self.duration) * w)

    def _x_to_sec(self, x):
        if self.duration <= 0:
            return 0.0
        w = self.width() - 2 * self.HANDLE_W
        frac = (x - self.HANDLE_W) / max(w, 1)
        return max(0.0, min(self.duration, frac * self.duration))

    # ── public slots ─────────────────────────────────────────────────────────

    def set_duration(self, sec: float):
        # Guard against NaN/negative/garbage duration values.
        if sec is None or sec != sec or sec < 0:
            sec = 0.0
        self.duration   = sec
        self.position   = 0.0
        self.sel_start  = None
        self.sel_end    = None
        self.regions    = []
        self._drag_origin = None
        self._dragging = False
        self.update()

    def set_position(self, sec: float):
        if sec is None or sec != sec:
            return
        self.position = max(0.0, sec)
        self.update()

    def clear_selection(self):
        self.sel_start = None
        self.sel_end   = None
        self.update()

    def commit_selection(self):
        """Save current rubber-band selection to the regions list."""
        if self.sel_start is not None and self.sel_end is not None:
            s = min(self.sel_start, self.sel_end)
            e = max(self.sel_start, self.sel_end)
            s = max(0.0, s)
            e = min(self.duration, e)
            if e - s > self.MIN_SELECTION_SEC:
                self.regions.append((s, e))
                self.update()
                return (s, e)
        return None

    def remove_region(self, index: int):
        if 0 <= index < len(self.regions):
            self.regions.pop(index)
            self.update()

    def get_split_points(self):
        """Return sorted unique split points from all saved regions."""
        pts = set()
        for s, e in self.regions:
            pts.add(s)
            pts.add(e)
        return sorted(pts)

    # ── mouse events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.duration > 0:
            self._drag_origin = event.position().x()
            sec = self._x_to_sec(self._drag_origin)
            self.sel_start = sec
            self.sel_end   = sec
            self._dragging = False
            self.seekRequested.emit(sec)

    def mouseMoveEvent(self, event):
        self._hover_x = event.position().x()
        if self._drag_origin is not None and self.duration > 0:
            # Clamp drag position to the widget bounds so dragging outside
            # the timeline doesn't produce out-of-range selections.
            x = min(max(event.position().x(), 0), self.width())
            dx = abs(x - self._drag_origin)
            if dx > 3:
                self._dragging = True
            if self._dragging:
                self.sel_end = self._x_to_sec(x)
                self.selectionChanged.emit(
                    min(self.sel_start, self.sel_end),
                    max(self.sel_start, self.sel_end)
                )
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._dragging and self.duration > 0:
                x = min(max(event.position().x(), 0), self.width())
                self.sel_end = self._x_to_sec(x)
                self.selectionChanged.emit(
                    min(self.sel_start, self.sel_end),
                    max(self.sel_start, self.sel_end)
                )
            self._drag_origin = None
            self._dragging    = False
        self.update()

    def leaveEvent(self, event):
        self._hover_x = None
        self.update()

    # ── paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        W, H = self.width(), self.height()

        # Background track
        track_y  = H // 2 - 4
        track_h  = 8
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2a2a2a"))
        p.drawRoundedRect(self.HANDLE_W, track_y, W - 2 * self.HANDLE_W, track_h, 4, 4)

        # Saved regions (green bands)
        for s, e in self.regions:
            xs = self._sec_to_x(s)
            xe = self._sec_to_x(e)
            p.setBrush(QColor(60, 180, 100, 140))
            p.setPen(QPen(QColor(60, 200, 100), 1))
            p.drawRoundedRect(xs, track_y - 4, xe - xs, track_h + 8, 3, 3)

        # Active rubber-band selection (blue band)
        if self.sel_start is not None and self.sel_end is not None:
            xs = self._sec_to_x(self.sel_start)
            xe = self._sec_to_x(self.sel_end)
            if xs > xe:
                xs, xe = xe, xs
            p.setBrush(QColor(80, 160, 255, 120))
            p.setPen(QPen(QColor(100, 180, 255), 1))
            p.drawRoundedRect(xs, track_y - 6, max(xe - xs, 2), track_h + 12, 3, 3)

            # End handles
            for hx in (xs, xe):
                p.setBrush(QColor("#5aa0ff"))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(hx - self.HANDLE_W // 2, track_y - 8,
                                  self.HANDLE_W, track_h + 16, 3, 3)

        # Play head
        if self.duration > 0:
            ph_x = self._sec_to_x(self.position)
            p.setPen(QPen(QColor("#ff4f4f"), 2))
            p.drawLine(ph_x, 4, ph_x, H - 4)
            # Triangle head
            p.setBrush(QColor("#ff4f4f"))
            p.setPen(Qt.NoPen)
            p.drawPolygon([
                QPoint(ph_x - 5, 4),
                QPoint(ph_x + 5, 4),
                QPoint(ph_x, 14),
            ])

        # Hover ghost line
        if self._hover_x is not None and self.duration > 0:
            p.setPen(QPen(QColor(200, 200, 200, 80), 1, Qt.DashLine))
            p.drawLine(int(self._hover_x), 0, int(self._hover_x), H)

        # Tick marks + time labels
        if self.duration > 0:
            p.setFont(QFont("Courier New", 8))
            p.setPen(QColor(120, 120, 120))
            step = self._nice_step()
            t = 0.0
            while t <= self.duration + 0.001:
                tx = self._sec_to_x(t)
                p.drawLine(tx, H - self.TICK_H - 2, tx, H - 2)
                label = f"{int(t // 60):02}:{int(t % 60):02}"
                p.drawText(tx - 18, H - self.TICK_H - 4, 36, 12,
                           Qt.AlignCenter, label)
                t += step

        p.end()

    def _nice_step(self):
        """Choose a human-friendly tick interval based on duration."""
        candidates = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        target_ticks = max(self.width() // 80, 4)
        for c in candidates:
            if self.duration / c <= target_ticks:
                return c
        return candidates[-1]


# ─── Main window ──────────────────────────────────────────────────────────────

DARK_BG   = "#1a1a1e"
PANEL_BG  = "#23232a"
ACCENT    = "#5aa0ff"
ACCENT2   = "#3cdc78"
BTN_BG    = "#2e2e38"
BTN_HOV   = "#3a3a48"
TEXT_PRI  = "#e8e8f0"
TEXT_SEC  = "#8888a0"
DANGER    = "#ff4f4f"
WARN      = "#ffb74f"

STYLE = f"""
QWidget {{
    background-color: {DARK_BG};
    color: {TEXT_PRI};
    font-family: 'Segoe UI', 'SF Pro Display', sans-serif;
    font-size: 13px;
}}
QPushButton {{
    background-color: {BTN_BG};
    color: {TEXT_PRI};
    border: 1px solid #3a3a4a;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {BTN_HOV};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: #222230;
}}
QPushButton:disabled {{
    background-color: #232328;
    color: #55555f;
    border-color: #2a2a34;
}}
QPushButton#btn_play {{
    background-color: {ACCENT};
    color: #fff;
    font-weight: 600;
    border: none;
}}
QPushButton#btn_play:hover {{
    background-color: #4490ff;
}}
QPushButton#btn_export {{
    background-color: {ACCENT2};
    color: #0a1a10;
    font-weight: 600;
    border: none;
}}
QPushButton#btn_export:hover {{
    background-color: #30c060;
}}
QPushButton#btn_export:disabled {{
    background-color: #1e2e24;
    color: #4a6a54;
    border: none;
}}
QPushButton#btn_danger {{
    background-color: #3a2020;
    color: {DANGER};
    border-color: #5a2020;
}}
QPushButton#btn_danger:hover {{
    background-color: #502020;
}}
QLabel#title_label {{
    font-size: 20px;
    font-weight: 700;
    color: {TEXT_PRI};
    letter-spacing: 1px;
}}
QLabel#file_label {{
    color: {TEXT_SEC};
    font-size: 12px;
    padding: 4px 0;
}}
QLabel#time_label {{
    color: {ACCENT};
    font-family: 'Courier New', monospace;
    font-size: 13px;
    font-weight: 600;
}}
QLabel#hint_label {{
    color: {TEXT_SEC};
    font-size: 11px;
    padding: 2px 0;
}}
QLabel#warn_label {{
    color: {WARN};
    font-size: 11px;
    padding: 2px 0;
}}
QListWidget {{
    background-color: {PANEL_BG};
    border: 1px solid #2e2e3e;
    border-radius: 6px;
    padding: 4px;
    color: {TEXT_PRI};
    font-size: 12px;
}}
QListWidget::item:selected {{
    background-color: #2a3a5a;
    color: #ffffff;
    border-radius: 4px;
}}
QFrame#separator {{
    background-color: #2e2e3e;
    max-height: 1px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: #2a2a3a;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
"""


class VideoCutterTool(QWidget):
    MIN_SEGMENT_SEC = 0.05

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Cutter")
        self.setGeometry(160, 80, 1020, 700)
        self.setStyleSheet(STYLE)
        self.setMinimumSize(780, 560)

        self.file_path  = ""
        self.duration   = 0.0
        self._export_thread = None
        self._export_worker = None
        self._export_running = False

        # ── Media player ──────────────────────────────────────────────────
        self.player       = QMediaPlayer()
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(280)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setStyleSheet("background:#000;")

        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(1.0)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        # Connect media player signals for reliable state tracking
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)

        # ── Timeline ──────────────────────────────────────────────────────
        self.timeline = TimelineWidget()
        self.timeline.seekRequested.connect(self._seek_to_sec)
        self.timeline.selectionChanged.connect(self._on_selection_changed)

        # ── Volume slider ─────────────────────────────────────────────────
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.setMaximumWidth(100)
        self.vol_slider.valueChanged.connect(
            lambda v: self.audio_output.setVolume(v / 100.0)
        )

        # ── Labels ────────────────────────────────────────────────────────
        self.title_lbl = QLabel("✂  Video Cutter")
        self.title_lbl.setObjectName("title_label")

        self.file_lbl = QLabel("No file loaded — click  Open Video  to begin")
        self.file_lbl.setObjectName("file_label")
        self.file_lbl.setWordWrap(True)

        self.time_lbl = QLabel("00:00 / 00:00")
        self.time_lbl.setObjectName("time_label")

        self.sel_lbl = QLabel("No selection  |  drag on timeline to select")
        self.sel_lbl.setObjectName("hint_label")

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("warn_label")
        self.status_lbl.setVisible(False)

        # ── Buttons ───────────────────────────────────────────────────────
        self.btn_open    = QPushButton("📂  Open Video")
        self.btn_play    = QPushButton("▶  Play"); self.btn_play.setObjectName("btn_play")
        self.btn_pause   = QPushButton("⏸  Pause")
        self.btn_rewind  = QPushButton("⏮  Rewind")
        self.btn_save_sel = QPushButton("➕  Save Selection")
        self.btn_clear_sel = QPushButton("✖  Clear Selection"); self.btn_clear_sel.setObjectName("btn_danger")
        self.btn_export  = QPushButton("✂  Export Segments"); self.btn_export.setObjectName("btn_export")
        self.btn_remove  = QPushButton("🗑  Remove"); self.btn_remove.setObjectName("btn_danger")

        # Segments list
        self.seg_list = QListWidget()
        self.seg_list.setMaximumHeight(160)
        self.seg_list.setAlternatingRowColors(True)

        # ── Connect signals ───────────────────────────────────────────────
        self.btn_open.clicked.connect(self.open_file)
        self.btn_play.clicked.connect(self._play)
        self.btn_pause.clicked.connect(self.player.pause)
        self.btn_rewind.clicked.connect(lambda: self._seek_to_sec(0))
        self.btn_save_sel.clicked.connect(self._save_selection)
        self.btn_clear_sel.clicked.connect(self._clear_selection)
        self.btn_export.clicked.connect(self._export_segments)
        self.btn_remove.clicked.connect(self._remove_selected_segment)

        self._update_controls_enabled()

        # ── Layout ────────────────────────────────────────────────────────
        self._build_layout()

        # Warn (non-blocking) up front if FFmpeg isn't reachable.
        QTimer.singleShot(300, self._check_ffmpeg_available)

    # ── Layout builder ────────────────────────────────────────────────────────

    def _build_layout(self):
        # Top bar
        top = QHBoxLayout()
        top.addWidget(self.title_lbl)
        top.addStretch()
        top.addWidget(self.btn_open)

        # Playback controls row
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        ctrl.addWidget(self.btn_rewind)
        ctrl.addWidget(self.btn_play)
        ctrl.addWidget(self.btn_pause)
        ctrl.addStretch()
        ctrl.addWidget(QLabel("🔊"))
        ctrl.addWidget(self.vol_slider)
        ctrl.addSpacing(12)
        ctrl.addWidget(self.time_lbl)

        # Selection controls row
        sel_ctrl = QHBoxLayout()
        sel_ctrl.setSpacing(6)
        sel_ctrl.addWidget(self.sel_lbl, stretch=1)
        sel_ctrl.addWidget(self.btn_save_sel)
        sel_ctrl.addWidget(self.btn_clear_sel)

        # Separator
        sep1 = QFrame(); sep1.setObjectName("separator"); sep1.setFrameShape(QFrame.HLine)
        sep2 = QFrame(); sep2.setObjectName("separator"); sep2.setFrameShape(QFrame.HLine)

        # Segments panel
        seg_top = QHBoxLayout()
        seg_top.addWidget(QLabel("Saved Segments:"), stretch=1)
        seg_top.addWidget(self.btn_remove)
        seg_top.addWidget(self.btn_export)

        # Main layout
        main = QVBoxLayout(self)
        main.setSpacing(8)
        main.setContentsMargins(16, 12, 16, 12)
        main.addLayout(top)
        main.addWidget(self.file_lbl)
        main.addWidget(self.status_lbl)
        main.addWidget(sep1)
        main.addWidget(self.video_widget, stretch=1)
        main.addWidget(self.timeline)
        main.addLayout(ctrl)
        main.addLayout(sel_ctrl)
        main.addWidget(sep2)
        main.addLayout(seg_top)
        main.addWidget(self.seg_list)

    # ── FFmpeg check ─────────────────────────────────────────────────────────

    def _check_ffmpeg_available(self):
        if not ffmpeg_available():
            if IS_FROZEN:
                msg = (
                    "⚠  FFmpeg is missing from this installation. Playback may still "
                    "work, but exporting segments will fail. Try reinstalling the "
                    "application."
                )
            else:
                msg = (
                    "⚠  FFmpeg was not found (no bundled copy, no imageio-ffmpeg, "
                    "not on PATH). Playback may work, but exporting will fail."
                )
            self.status_lbl.setText(msg)
            self.status_lbl.setVisible(True)
            logging.warning("FFmpeg not found at startup (frozen=%s)", IS_FROZEN)

    # ── Enable/disable helpers ────────────────────────────────────────────────

    def _update_controls_enabled(self):
        """Keep buttons in a state that matches what's actually safe to do."""
        has_file = bool(self.file_path) and self.duration > 0
        exporting = self._export_running

        self.btn_open.setEnabled(not exporting)
        self.btn_play.setEnabled(has_file and not exporting)
        self.btn_pause.setEnabled(has_file and not exporting)
        self.btn_rewind.setEnabled(has_file and not exporting)
        self.btn_save_sel.setEnabled(has_file and not exporting)
        self.btn_clear_sel.setEnabled(has_file and not exporting)
        self.btn_remove.setEnabled(not exporting)
        self.btn_export.setEnabled(
            has_file and not exporting and self.seg_list.count() > 0
        )

    # ── File open ─────────────────────────────────────────────────────────────

    def open_file(self):
        if self._export_running:
            QMessageBox.information(
                self, "Export in Progress",
                "Please wait for the current export to finish before opening another video."
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video File", "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm)"
        )
        if not path:
            return

        # Stop any current playback cleanly before touching state.
        self.player.stop()

        # Probe duration BEFORE committing any state, so a bad file never
        # corrupts the currently-loaded video's file_path/duration/segments.
        try:
            clip = VideoFileClip(path)
            new_duration = clip.duration
            clip.close()
        except Exception as e:
            logging.error("Failed to read metadata for %s: %s", path, e)
            QMessageBox.warning(
                self, "Error",
                f"Could not read video metadata:\n{e}\n\n"
                "The previously loaded video (if any) is unaffected."
            )
            return

        if not new_duration or new_duration != new_duration or new_duration <= 0:
            QMessageBox.warning(
                self, "Error",
                "This video reports an invalid or zero duration and can't be used."
            )
            return

        # Only now commit the new file as the active one.
        self.file_path = path
        self.duration = new_duration
        self.file_lbl.setText(f"📄  {os.path.basename(path)}   —   {path}")
        logging.info(f"Loaded video: {path} ({new_duration:.2f}s)")

        self.timeline.set_duration(self.duration)
        self.seg_list.clear()
        self.time_lbl.setText(
            f"00:00 / {int(self.duration//60):02}:{int(self.duration%60):02}"
        )
        self.sel_lbl.setText("No selection  |  drag on timeline to select a region")

        # Set source AFTER metadata is ready
        self.player.setSource(QUrl.fromLocalFile(path))
        self._update_controls_enabled()

    # ── Playback helpers ──────────────────────────────────────────────────────

    def _play(self):
        if not self.file_path:
            return
        self.player.play()

    def _seek_to_sec(self, sec: float):
        if self.duration > 0:
            self.player.setPosition(int(sec * 1000))

    # ── Player signal handlers ────────────────────────────────────────────────

    def _on_position_changed(self, ms: int):
        sec = ms / 1000.0
        self.timeline.set_position(sec)
        total = self.duration
        self.time_lbl.setText(
            f"{int(sec//60):02}:{int(sec%60):02} / "
            f"{int(total//60):02}:{int(total%60):02}"
        )

    def _on_duration_changed(self, ms: int):
        # Qt's own duration detection as a cross-check — only used as a
        # fallback if MoviePy couldn't determine a duration for some reason.
        if ms > 0 and self.duration <= 0:
            self.duration = ms / 1000.0
            self.timeline.set_duration(self.duration)
            self._update_controls_enabled()

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self.btn_play.setText("▶  Playing")
        else:
            self.btn_play.setText("▶  Play")

    def _on_player_error(self, error, error_string):
        if error == QMediaPlayer.NoError:
            return
        logging.error("QMediaPlayer error (%s): %s", error, error_string)

        friendly = {
            QMediaPlayer.ResourceError: "The video file could not be opened or found.",
            QMediaPlayer.FormatError: "This video's format/codec isn't supported by the playback engine.",
            QMediaPlayer.NetworkError: "A network error occurred while loading the video.",
            QMediaPlayer.AccessDeniedError: "Access to the video file was denied.",
        }.get(error, "This video could not be played.")

        self.file_lbl.setText(f"⚠  {friendly}")
        self.status_lbl.setText(
            f"⚠  {friendly} ({error_string}) — try an MP4/H.264 file, or another format. "
            "The rest of the app remains usable."
        )
        self.status_lbl.setVisible(True)

    # ── Selection handling ────────────────────────────────────────────────────

    def _on_selection_changed(self, start: float, end: float):
        self.sel_lbl.setText(
            f"Selection:  {self._fmt(start)}  →  {self._fmt(end)}   "
            f"( {self._fmt(end - start)} )   |  click  Save Selection  to keep"
        )

    def _save_selection(self):
        result = self.timeline.commit_selection()
        if result:
            s, e = result
            item = QListWidgetItem(
                f"  Segment {self.seg_list.count() + 1}:  "
                f"{self._fmt(s)}  →  {self._fmt(e)}   "
                f"( {self._fmt(e - s)} )"            )
            item.setData(Qt.UserRole, (s, e))
            self.seg_list.addItem(item)
            self.sel_lbl.setText(f"✓ Saved:  {self._fmt(s)} → {self._fmt(e)}")
            self._update_controls_enabled()
        else:
            self.sel_lbl.setText("⚠  Drag on the timeline first to make a selection")

    def _clear_selection(self):
        self.timeline.clear_selection()
        self.sel_lbl.setText("Selection cleared  |  drag on timeline to select")

    def _remove_selected_segment(self):
        if self._export_running:
            return
        row = self.seg_list.currentRow()
        if row < 0:
            self.sel_lbl.setText("⚠  Select a segment in the list first")
            return
        self.seg_list.takeItem(row)
        self.timeline.remove_region(row)
        # Renumber remaining items
        for i in range(self.seg_list.count()):
            it = self.seg_list.item(i)
            s, e = it.data(Qt.UserRole)
            it.setText(
                f"  Segment {i + 1}:  {self._fmt(s)}  →  {self._fmt(e)}  "
                f"( {self._fmt(e - s)} )"
            )
        self._update_controls_enabled()

    # ── Segment validation ───────────────────────────────────────────────────

    def _validate_segments(self, segments):
        """Reject anything that shouldn't reach MoviePy/FFmpeg. Returns
        (valid_segments, problems)."""
        valid = []
        problems = []
        for i, seg in enumerate(segments, start=1):
            try:
                s, e = seg
                s = float(s)
                e = float(e)
            except (TypeError, ValueError):
                problems.append(f"Segment {i}: not numeric")
                continue
            if s != s or e != e:  # NaN check
                problems.append(f"Segment {i}: invalid (NaN) values")
                continue
            if s < 0:
                problems.append(f"Segment {i}: starts before 0:00")
                continue
            if e <= s:
                problems.append(f"Segment {i}: end is not after start")
                continue
            if e - s < self.MIN_SEGMENT_SEC:
                problems.append(f"Segment {i}: too short to export")
                continue
            if self.duration > 0 and e > self.duration + 0.5:
                problems.append(f"Segment {i}: extends beyond the video's length")
                continue
            valid.append((max(0.0, s), min(self.duration, e) if self.duration > 0 else e))
        return valid, problems

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_segments(self):
        if self._export_running:
            return  # guard against double-clicks / re-entrancy

        if self.seg_list.count() == 0:
            QMessageBox.information(self, "No Segments",
                                    "Save at least one selection before exporting.")
            return

        if not self.file_path or self.duration <= 0:
            QMessageBox.warning(self, "No Video", "Load a valid video before exporting.")
            return

        if not ffmpeg_available():
            if IS_FROZEN:
                QMessageBox.critical(
                    self, "FFmpeg Not Found",
                    "FFmpeg is missing or unavailable. Please reinstall the "
                    "application."
                )
            else:
                QMessageBox.critical(
                    self, "FFmpeg Not Found",
                    "FFmpeg is missing or unavailable, so segments can't be exported.\n\n"
                    "Install FFmpeg and make sure it's available on your system PATH, "
                    "then try again."
                )
            return

        raw_segments = []
        for i in range(self.seg_list.count()):
            raw_segments.append(self.seg_list.item(i).data(Qt.UserRole))

        segments, problems = self._validate_segments(raw_segments)
        if problems:
            QMessageBox.warning(
                self, "Invalid Segments",
                "The following segment(s) can't be exported and were skipped:\n\n"
                + "\n".join(problems)
            )
        if not segments:
            QMessageBox.warning(self, "Nothing to Export", "No valid segments remain.")
            return

        out_dir = QFileDialog.getExistingDirectory(self, "Choose Output Folder")
        if not out_dir:
            return

        if not os.access(out_dir, os.W_OK):
            QMessageBox.critical(
                self, "Permission Denied",
                f"No write permission for:\n{out_dir}\n\nChoose a different folder."
            )
            return

        total = len(segments)
        self._progress = QProgressDialog(
            "Exporting segments…", "Cancel", 0, total, self
        )
        self._progress.setWindowTitle("Exporting")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setValue(0)
        self._progress.canceled.connect(self._on_export_cancel_requested)

        # Run export in background thread
        self._export_running = True
        self._update_controls_enabled()

        self._export_thread = QThread()
        self._export_worker = ExportWorker(self.file_path, segments, out_dir)
        self._export_worker.moveToThread(self._export_thread)

        self._export_thread.started.connect(self._export_worker.run)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.cancelled.connect(self._on_export_cancelled)

        self._export_thread.start()

    def _on_export_cancel_requested(self):
        if self._export_worker is not None:
            self._export_worker.request_cancel()
        self._progress.setLabelText("Cancelling… finishing current segment.")

    def _on_export_progress(self, current, total):
        self._progress.setValue(current)
        self._progress.setLabelText(f"Exporting segment {current} of {total}…")

    def _teardown_export_thread(self):
        if self._export_thread is not None:
            self._export_thread.quit()
            self._export_thread.wait()
            self._export_thread.deleteLater()
        if self._export_worker is not None:
            self._export_worker.deleteLater()
        self._export_thread = None
        self._export_worker = None
        self._export_running = False
        self._update_controls_enabled()

    def _on_export_done(self):
        self._progress.close()
        self._teardown_export_thread()
        QMessageBox.information(self, "Done", "All segments exported successfully! ✓")

    def _on_export_error(self, msg):
        self._progress.close()
        self._teardown_export_thread()
        QMessageBox.critical(self, "Export Error", f"An error occurred:\n{msg}")

    def _on_export_cancelled(self):
        self._progress.close()
        self._teardown_export_thread()
        QMessageBox.information(self, "Cancelled", "Export was cancelled.")

    # ── Safe shutdown ────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._export_running:
            reply = QMessageBox.question(
                self, "Export In Progress",
                "An export is currently running. Closing the application will "
                "stop the export. Do you want to continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return

            if self._export_worker is not None:
                self._export_worker.request_cancel()
            if self._export_thread is not None:
                self._export_thread.quit()
                self._export_thread.wait(5000)  # wait up to 5s for a clean stop
            logging.info("Application closed during export (user confirmed)")

        self.player.stop()
        event.accept()

    # ── Utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt(sec: float) -> str:
        sec = max(0.0, sec)
        m, s = divmod(int(sec), 60)
        return f"{m:02}:{s:02}.{int((sec % 1) * 10)}"


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # FFmpeg was already resolved at module import time, above — see the
    # block comment there for why it can't be done here instead.
    _init_logging()
    install_global_exception_hook()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark palette base so native widgets match
    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(26, 26, 30))
    pal.setColor(QPalette.WindowText,      QColor(232, 232, 240))
    pal.setColor(QPalette.Base,            QColor(35, 35, 42))
    pal.setColor(QPalette.AlternateBase,   QColor(28, 28, 36))
    pal.setColor(QPalette.ToolTipBase,     QColor(50, 50, 60))
    pal.setColor(QPalette.ToolTipText,     QColor(232, 232, 240))
    pal.setColor(QPalette.Text,            QColor(232, 232, 240))
    pal.setColor(QPalette.Button,          QColor(46, 46, 56))
    pal.setColor(QPalette.ButtonText,      QColor(232, 232, 240))
    pal.setColor(QPalette.BrightText,      QColor(255, 80, 80))
    pal.setColor(QPalette.Highlight,       QColor(90, 160, 255))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(pal)

    w = VideoCutterTool()
    w.show()
    sys.exit(app.exec())