#!/usr/bin/env python3
"""Create a 16:9 horizontal highlight with a blurred background fill."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


@dataclass(frozen=True)
class ProbeInfo:
    duration: float
    width: int
    height: int
    has_audio: bool


@dataclass(frozen=True)
class Candidate:
    score: float
    start: float
    audio: float
    motion: float
    peak_motion: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Make a vertical video into a horizontal 16:9 highlight with a blurred background."
    )
    parser.add_argument("video", nargs="?", help="Input video. If omitted, infer the only video in the current folder.")
    parser.add_argument("--output", help="Output MP4 path. Defaults to <input>.horizontal-highlight.mp4.")
    parser.add_argument("--start", type=float, help="Start time in seconds. If omitted, auto-select a highlight.")
    parser.add_argument("--duration", type=float, default=10.0, help="Clip length in seconds.")
    parser.add_argument("--width", type=int, default=1920, help="Output width.")
    parser.add_argument("--height", type=int, default=1080, help="Output height.")
    parser.add_argument("--blur", type=float, default=32.0, help="Gaussian blur sigma for the background.")
    parser.add_argument("--crf", type=int, default=18, help="libx264 CRF quality value.")
    parser.add_argument("--preset", default="medium", help="libx264 preset.")
    parser.add_argument("--audio-bitrate", default="160k", help="AAC audio bitrate.")
    parser.add_argument("--preview-time", type=float, default=5.0, help="Seconds into output clip for preview PNG.")
    parser.add_argument("--no-preview", action="store_true", help="Do not create a preview PNG.")
    parser.add_argument("--analysis-dir", help="Keep audio/scene analysis files in this directory.")
    parser.add_argument("--ffmpeg", help="Path to ffmpeg executable.")
    parser.add_argument("--ffprobe", help="Path to ffprobe executable.")
    return parser.parse_args()


def locate_exe(name: str, override: str | None = None) -> str:
    if override:
        path = Path(override).expanduser()
        if path.exists():
            return str(path)
        raise SystemExit(f"{name} not found: {path}")

    found = shutil.which(name)
    if found:
        return found

    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", "")) / "Gyan" / "FFmpeg" / "bin" / f"{name}.exe",
            Path(os.environ.get("ProgramFiles", "")) / "ffmpeg" / "bin" / f"{name}.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        ]
        for candidate in candidates[:2]:
            if candidate.exists():
                return str(candidate)
        package_root = candidates[2]
        if package_root.exists():
            matches = sorted(package_root.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe"), reverse=True)
            if matches:
                return str(matches[0])

    raise SystemExit(f"{name} is required but was not found on PATH.")


def run(cmd: list[str], *, cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {
        "cwd": str(cwd) if cwd else None,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE

    try:
        return subprocess.run(cmd, check=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        raise


def choose_video(arg: str | None) -> Path:
    if arg:
        path = Path(arg).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Video not found: {path}")
        return path

    videos = sorted(
        path for path in Path.cwd().iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    if len(videos) == 1:
        return videos[0].resolve()
    if not videos:
        raise SystemExit("No supported video file found in the current folder.")
    names = ", ".join(path.name for path in videos)
    raise SystemExit(f"Multiple videos found; pass one explicitly. Found: {names}")


def probe_video(ffprobe: str, video_path: Path) -> ProbeInfo:
    result = run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration:stream=index,codec_type,width,height",
            "-of", "json",
            str(video_path),
        ],
        capture=True,
    )
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise SystemExit("Input has no video stream.")
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise SystemExit("Could not determine input duration.")
    return ProbeInfo(
        duration=duration,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def parse_metadata_values(path: Path, key: str) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    current_time: float | None = None
    time_re = re.compile(r"pts_time:([0-9.]+)")
    value_re = re.compile(re.escape(key) + r"=([-+0-9.infINF]+)")

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        time_match = time_re.search(line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        value_match = value_re.search(line)
        if value_match and current_time is not None:
            try:
                value = float(value_match.group(1))
            except ValueError:
                current_time = None
                continue
            if math.isfinite(value):
                rows.append((current_time, value))
            current_time = None
    return rows


def normalize(rows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    values = [value for _time, value in rows if math.isfinite(value)]
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return [(time, 0.0) for time, _value in rows]
    return [(time, (value - lo) / (hi - lo)) for time, value in rows if math.isfinite(value)]


def average_in(rows: list[tuple[float, float]], start: float, end: float) -> float:
    values = [value for time, value in rows if start <= time < end]
    return sum(values) / len(values) if values else 0.0


def max_in(rows: list[tuple[float, float]], start: float, end: float) -> float:
    values = [value for time, value in rows if start <= time < end]
    return max(values) if values else 0.0


def analyze_audio(ffmpeg: str, video_path: Path, tmpdir: Path, has_audio: bool) -> list[tuple[float, float]]:
    if not has_audio:
        return []
    out = tmpdir / "audio_rms.txt"
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i", str(video_path),
            "-af", "asetnsamples=n=44100:p=1,astats=metadata=1:reset=1,"
                   "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=audio_rms.txt",
            "-vn",
            "-f", "null",
            os.devnull,
        ],
        cwd=tmpdir,
        capture=True,
    )
    return parse_metadata_values(out, "lavfi.astats.Overall.RMS_level")


def analyze_motion(ffmpeg: str, video_path: Path, tmpdir: Path) -> list[tuple[float, float]]:
    out = tmpdir / "scene_scores.txt"
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i", str(video_path),
            "-vf", "scale=160:-1,fps=2,select=gte(scene\\,0),metadata=print:file=scene_scores.txt",
            "-an",
            "-f", "null",
            os.devnull,
        ],
        cwd=tmpdir,
        capture=True,
    )
    return parse_metadata_values(out, "lavfi.scene_score")


def score_windows(
    audio_rows: list[tuple[float, float]],
    motion_rows: list[tuple[float, float]],
    source_duration: float,
    clip_duration: float,
) -> list[Candidate]:
    audio = normalize(audio_rows)
    motion = normalize(motion_rows)
    max_start = max(0.0, source_duration - clip_duration)
    steps = int(max_start * 2) + 1
    candidates: list[Candidate] = []

    for index in range(steps + 1):
        start = min(index / 2.0, max_start)
        end = start + clip_duration
        audio_score = average_in(audio, start, end)
        motion_score = average_in(motion, start, end)
        peak_motion = max_in(motion, start, end)

        if audio and motion:
            score = audio_score * 0.65 + motion_score * 0.25 + peak_motion * 0.10
        elif audio:
            score = audio_score
        elif motion:
            score = motion_score * 0.80 + peak_motion * 0.20
        else:
            score = 0.0
        candidates.append(Candidate(score, start, audio_score, motion_score, peak_motion))

    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def select_highlight(
    ffmpeg: str,
    video_path: Path,
    probe: ProbeInfo,
    clip_duration: float,
    analysis_dir: str | None,
) -> tuple[float, list[Candidate]]:
    if analysis_dir:
        tmp_path = Path(analysis_dir).expanduser().resolve()
        tmp_path.mkdir(parents=True, exist_ok=True)
        audio = analyze_audio(ffmpeg, video_path, tmp_path, probe.has_audio)
        motion = analyze_motion(ffmpeg, video_path, tmp_path)
    else:
        with tempfile.TemporaryDirectory(prefix="horizontal-highlight-") as tmp:
            tmp_path = Path(tmp)
            audio = analyze_audio(ffmpeg, video_path, tmp_path, probe.has_audio)
            motion = analyze_motion(ffmpeg, video_path, tmp_path)

    candidates = score_windows(audio, motion, probe.duration, clip_duration)
    if not candidates:
        return 0.0, []
    return candidates[0].start, candidates[:5]


def format_seconds(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def render_horizontal(
    ffmpeg: str,
    video_path: Path,
    output_path: Path,
    start: float,
    duration: float,
    args: argparse.Namespace,
) -> None:
    filter_graph = (
        f"[0:v]scale={args.width}:{args.height}:force_original_aspect_ratio=increase,"
        f"crop={args.width}:{args.height},gblur=sigma={args.blur}:steps=2,"
        "eq=brightness=-0.06:saturation=1.12[bg];"
        f"[0:v]scale={args.width}:{args.height}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-ss", format_seconds(start),
            "-t", format_seconds(duration),
            "-i", str(video_path),
            "-filter_complex", filter_graph,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", str(args.preset),
            "-crf", str(args.crf),
            "-c:a", "aac",
            "-b:a", str(args.audio_bitrate),
            "-movflags", "+faststart",
            str(output_path),
        ]
    )


def make_preview(ffmpeg: str, output_path: Path, duration: float, preview_time: float) -> Path:
    preview_path = output_path.with_suffix(".preview.png")
    seek = max(0.0, min(preview_time, max(0.0, duration - 0.05)))
    run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-ss", format_seconds(seek),
            "-i", str(output_path),
            "-frames:v", "1",
            "-update", "1",
            str(preview_path),
        ],
        capture=True,
    )
    return preview_path


def validation_summary(ffprobe: str, output_path: Path) -> dict[str, object]:
    result = run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration,size:stream=index,codec_type,width,height,codec_name",
            "-of", "json",
            str(output_path),
        ],
        capture=True,
    )
    return json.loads(result.stdout or "{}")


def main() -> None:
    args = parse_args()
    ffmpeg = locate_exe("ffmpeg", args.ffmpeg)
    ffprobe = locate_exe("ffprobe", args.ffprobe)
    video_path = choose_video(args.video)
    probe = probe_video(ffprobe, video_path)

    clip_duration = min(max(0.1, args.duration), probe.duration)
    if args.start is None:
        start, top_candidates = select_highlight(ffmpeg, video_path, probe, clip_duration, args.analysis_dir)
        print("Selected highlight:", f"{format_seconds(start)}-{format_seconds(start + clip_duration)}")
        for candidate in top_candidates:
            print(
                "Candidate:",
                f"{format_seconds(candidate.start)}-{format_seconds(candidate.start + clip_duration)}",
                f"score={candidate.score:.4f}",
                f"audio={candidate.audio:.4f}",
                f"motion={candidate.motion:.4f}",
                f"peak_motion={candidate.peak_motion:.4f}",
            )
    else:
        start = max(0.0, min(args.start, max(0.0, probe.duration - clip_duration)))
        print("Using requested start:", f"{format_seconds(start)}-{format_seconds(start + clip_duration)}")

    output_path = Path(args.output).expanduser().resolve() if args.output else video_path.with_suffix(".horizontal-highlight.mp4")
    render_horizontal(ffmpeg, video_path, output_path, start, clip_duration, args)

    preview_path: Path | None = None
    if not args.no_preview:
        preview_path = make_preview(ffmpeg, output_path, clip_duration, args.preview_time)

    summary = {
        "source": str(video_path),
        "output": str(output_path),
        "selected_start": round(start, 3),
        "selected_end": round(start + clip_duration, 3),
        "preview": str(preview_path) if preview_path else None,
        "validation": validation_summary(ffprobe, output_path),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
