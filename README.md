# Video Cutter

A desktop video cutting application by Pakistan Code Center, built with Python, PySide6, MoviePy and FFmpeg.

## Features

- Open and preview common video formats
- Interactive timeline with click-to-seek
- Drag to select video ranges
- Save and manage multiple segments
- Export selected segments as MP4 (H.264/AAC)
- Background export thread so the UI stays responsive
- Export cancellation and progress tracking
- Safe output naming to avoid overwriting files
- FFmpeg auto-discovery for development and packaged builds
- Application logging and global exception handling
- Dark desktop interface

## Requirements

- Python 3.10+
- PySide6
- MoviePy
- imageio-ffmpeg
- FFmpeg (bundled, supplied by imageio-ffmpeg, or available on PATH)

## Run from source

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python Video_Cutter.py
```

## Build for Windows

Install build dependencies:

```bash
pip install -r requirements-dev.txt
```

Then run:

```bat
build.bat
```

The build uses PyInstaller in one-folder mode. If you want to ship your own FFmpeg executable, put it at `ffmpeg/ffmpeg.exe` before building.

See [BUILD.md](BUILD.md) for details.

## Output

Exported segments are written as:

- `video_part_1.mp4`
- `video_part_2.mp4`
- etc.

Existing files are never silently overwritten.

## Project

Developed by **Pakistan Code Center (PCC)**.

Version: **1.1.0**
