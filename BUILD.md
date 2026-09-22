# Windows Build Guide

## 1. Prepare Python

Use Python 3.10 or newer.

```bat
python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

## 2. Optional bundled FFmpeg

The application checks for `ffmpeg/ffmpeg.exe` next to the built executable first.

To bundle your own copy:

1. Create an `ffmpeg` folder in the project root.
2. Put `ffmpeg.exe` inside it.
3. Run `build.bat`.

If the folder is not present, the app can still use the FFmpeg binary provided by `imageio-ffmpeg`, or a system FFmpeg installation on PATH.

## 3. Build

```bat
build.bat
```

Output will be created under:

```text
dist\VideoCutter\
```

## 4. Test before distribution

Test at least:

- Application starts on a clean Windows machine
- MP4/H.264 playback
- Audio playback
- Timeline seek and selection
- Multiple saved segments
- Segment deletion and renumbering
- Export destination selection
- H.264/AAC export
- Export cancellation
- No overwrite when output names already exist
- Log file creation under the user's local application data directory

## Notes

One-folder builds are used intentionally because multimedia applications with Qt and FFmpeg are typically easier to diagnose and maintain this way than very large one-file bundles.
