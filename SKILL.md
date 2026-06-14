---
name: make-vertical-into-horizontal
description: Convert vertical or portrait videos into horizontal 16:9 MP4 highlights with a blurred background fill. Use when Codex needs to reframe phone/social videos for YouTube, classroom, presentation, or desktop playback; find a short highlight such as a 10-second clip; preserve the original video centered over a blurred widescreen background; validate the output; or optionally hand the finished clip to a subtitle workflow.
---

# Make Vertical Into Horizontal

## Overview

Use this skill to turn a vertical video into a horizontal 16:9 highlight. Prefer the bundled script for repeatability: it can select a highlight window from audio and frame-change activity, render a blurred-background widescreen MP4, and create a preview frame for inspection.

## Quick Start

Run the bundled script from the folder containing the video. Use the absolute path to this skill's `scripts/make_horizontal_highlight.py` when needed:

```powershell
python "<path-to-this-skill>\scripts\make_horizontal_highlight.py" "input.mp4" --duration 10 --output "highlight_16x9_blur.mp4"
```

If the user gives a specific start time, skip auto-selection:

```powershell
python "<path-to-this-skill>\scripts\make_horizontal_highlight.py" "input.mp4" --start 83 --duration 10 --output "highlight_16x9_blur.mp4"
```

If exactly one supported video is in the current folder, the script can infer it:

```powershell
python "<path-to-this-skill>\scripts\make_horizontal_highlight.py" --duration 10
```

## Workflow

1. Identify the source video. If multiple video files exist, pass the intended file explicitly.
2. Use `scripts/make_horizontal_highlight.py` to create the horizontal clip. Let the script choose the start time unless the user specified one.
3. Inspect the generated preview PNG. Check that the subject is centered, the background is blurred, and no important content is cropped.
4. Validate with FFprobe or the script's printed validation summary. Confirm `1920x1080`, expected duration, H.264 video, and audio if present.
5. Deliver the final MP4 path and mention the selected source time range.

## Highlight Selection

The script scores candidate windows using:

- audio RMS level, when an audio stream is present
- visual scene/frame-change scores sampled at low resolution
- a sliding window, defaulting to 10 seconds

This is a heuristic for "most energetic" rather than a semantic understanding of the scene. If the chosen clip feels wrong, rerun with `--start <seconds>` using a better moment.

## Subtitles

If the user asks for subtitles, run the horizontal highlight first, then use a subtitle skill or workflow on that finished clip. When `video-subtitle-creator` is available, read and follow that skill before making API calls.

Recommended subtitle sequence:

```powershell
python "<path-to-this-skill>\scripts\make_horizontal_highlight.py" "input.mp4" --duration 10 --output "highlight_16x9_blur.mp4"
python "C:\Users\Leo\.codex\skills\video-subtitle-creator\scripts\video_subtitle_creator.py" "highlight_16x9_blur.mp4" --burn bilingual
```

If transcription creates one long cue, split `*.subtitles.json` into shorter timed segments and rerun:

```powershell
python "C:\Users\Leo\.codex\skills\video-subtitle-creator\scripts\video_subtitle_creator.py" "highlight_16x9_blur.mp4" --skip-api --burn bilingual
```

## FFmpeg Notes

Require `ffmpeg` and `ffprobe` on `PATH` or in a normal system install location. On Windows, check common install paths before giving up. Do not overwrite the source video; write a new output file and keep the generated `.preview.png` for visual QA.
