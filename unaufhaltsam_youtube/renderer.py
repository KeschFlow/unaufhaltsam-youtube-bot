"""FFmpeg-based vertical Shorts renderer."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from .metadata import HUMAN_RECORD, WALL, result_hook
from .models import RunResult


class RenderError(RuntimeError):
    pass


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windows = Path("C:/Windows/Fonts")
    names = ["arialbd.ttf", "segoeuib.ttf"] if bold else ["arial.ttf", "segoeui.ttf"]
    for name in names:
        candidate = windows / name
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font, fill: str = "white") -> None:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((1080 - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)


def _overlay(
    run: RunResult, attempt_number: int, path: Path, overlay_variant: str = "score_first",
    content_format: str | None = None,
) -> None:
    image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((48, 42, 1032, 290), radius=30, fill=(8, 8, 13, 218), outline=(160, 75, 255, 255), width=5)
    _center(draw, f"BOT ATTEMPT #{attempt_number}", 72, _font(54, True))
    if content_format:
        draw.text((72, 300), f"UNAUFHALTSAM · {content_format}", font=_font(24, True), fill="#d69cff")
    score_line = f"BOT SCORE: {run.reached_boxes}"
    record_line = f"HUMAN RECORD: {HUMAN_RECORD}   |   WALL: {WALL}"
    if overlay_variant == "record_first":
        _center(draw, record_line, 145, _font(36, True))
        _center(draw, score_line, 205, _font(58, True), "#d69cff")
    elif overlay_variant == "hook_first":
        _center(draw, result_hook(run.reached_boxes, run.completed).split(".")[0], 145, _font(38, True), "#ffd34d")
        _center(draw, score_line, 205, _font(52, True), "#d69cff")
    else:
        _center(draw, score_line, 145, _font(64, True), "#d69cff")
        _center(draw, record_line, 228, _font(34, True))
    draw.rounded_rectangle((48, 1575, 1032, 1874), radius=30, fill=(8, 8, 13, 230), outline=(255, 255, 255, 220), width=3)
    hook = result_hook(run.reached_boxes, run.completed)
    lines = hook.replace(". ", ".\n").splitlines()
    for index, line in enumerate(lines[:2]):
        _center(draw, line, 1610 + index * 58, _font(42, True), "#ffd34d")
    _center(draw, "PLAY UNAUFHALTSAM", 1742, _font(42, True))
    _center(draw, "eddiesworld.de/unaufhaltsam/", 1805, _font(30))
    image.save(path)


def render_short(
    run: RunResult, attempt_number: int, output_path: Path,
    overlay_variant: str = "score_first", content_format: str | None = None,
) -> Path:
    source = Path(run.recording_path)
    if not source.is_file() or source.stat().st_size == 0:
        raise RenderError("Run-Aufzeichnung fehlt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay = output_path.with_suffix(".overlay.png")
    _overlay(run, attempt_number, overlay, overlay_variant, content_format)
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(source), "-i", str(overlay),
        "-filter_complex",
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease," 
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black[game];[game][1:v]overlay=0:0,format=yuv420p[out]",
        "-map", "[out]", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-movflags", "+faststart", "-r", "30", str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    overlay.unlink(missing_ok=True)
    if completed.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        raise RenderError(completed.stderr[-2000:] or "FFmpeg-Render fehlgeschlagen")
    return output_path
