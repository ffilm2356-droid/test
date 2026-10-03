"""FFmpeg compositing — async shot rendering, concat, mux.

All FFmpeg operations run as async subprocesses with concurrency control.
"""

from __future__ import annotations

import asyncio
import logging
import random

log = logging.getLogger("mediaforge.composer")

W, H, FPS = 1280, 720, 30
OVER = 1.14
X264 = "-c:v libx264 -preset veryfast -crf 21 -pix_fmt yuv420p -r 30 -an"

_ffmpeg_sem: asyncio.Semaphore | None = None


def _get_sem(max_workers: int = 6) -> asyncio.Semaphore:
    global _ffmpeg_sem
    if _ffmpeg_sem is None:
        _ffmpeg_sem = asyncio.Semaphore(max_workers)
    return _ffmpeg_sem


def init_composer(max_workers: int = 6):
    global _ffmpeg_sem
    _ffmpeg_sem = asyncio.Semaphore(max_workers)


async def _run(cmd: str):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {cmd[:300]}\n{stderr.decode()[-1500:]}")


async def get_duration(path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return float(stdout.decode().strip() or 0)


async def render_still(img_path: str, frames: int, out: str,
                       motion: str, overlay: str | None = None):
    """Render still image with Ken Burns motion to MP4."""
    async with _get_sem():
        D = frames / FPS
        ow, oh = int(W * OVER), int(H * OVER)

        if motion == "zin":
            vf = (f"scale={W}:{H},"
                  f"scale=w='{W}*(1.0+0.10*t/{D:.3f})':h=-2:eval=frame,"
                  f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2")
        elif motion == "zout":
            vf = (f"scale={W}:{H},"
                  f"scale=w='{W}*(1.10-0.10*t/{D:.3f})':h=-2:eval=frame,"
                  f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2")
        else:
            sx = ow - W
            sy = (oh - H) / 2
            xe = (f"{sx}*t/{D:.3f}" if motion == "pr"
                  else f"{sx}-{sx}*t/{D:.3f}")
            vf = f"crop={W}:{H}:'{xe}':{sy:.0f}"

        src = f"-loop 1 -framerate {FPS} -i {img_path}"

        if overlay:
            cmd = (f"ffmpeg -y -v error {src} -i {overlay} -filter_complex "
                   f"\"[0:v]{vf},setsar=1[b];[b][1:v]overlay=0:0,format=yuv420p\" "
                   f"-frames:v {frames} {X264} {out}")
        else:
            cmd = (f"ffmpeg -y -v error {src} "
                   f"-vf \"{vf},setsar=1,format=yuv420p\" "
                   f"-frames:v {frames} {X264} {out}")
        await _run(cmd)


async def render_video(url: str, frames: int, out: str, seed: int,
                       bw: bool = False, overlay: str | None = None):
    """Cut and scale a video clip to MP4."""
    async with _get_sem():
        D = frames / FPS
        try:
            dur = await get_duration(url)
        except Exception:
            dur = 0

        rnd = random.Random(seed)
        start = (rnd.uniform(dur * 0.08, max(dur * 0.08, dur * 0.75 - D))
                 if dur > D + 4 else 0)

        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},fps={FPS},setsar=1")
        if bw:
            vf += ",hue=s=0"

        if overlay:
            cmd = (f"ffmpeg -y -v error -ss {start:.2f} -i '{url}' -i {overlay} "
                   f"-filter_complex \"[0:v]{vf}[b];[b][1:v]overlay=0:0,"
                   f"format=yuv420p\" -frames:v {frames} {X264} {out}")
        else:
            cmd = (f"ffmpeg -y -v error -ss {start:.2f} -i '{url}' "
                   f"-vf \"{vf},format=yuv420p\" -frames:v {frames} {X264} {out}")
        await _run(cmd)

        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        n = int(stdout.decode().strip() or 0)
        if n < frames:
            raise RuntimeError(f"short video clip {n}<{frames}")


async def pad_audio(input_path: str, output_path: str, duration: float):
    """Pad/trim audio to exact duration."""
    await _run(
        f"ffmpeg -y -v error -i {input_path} -af apad "
        f"-t {duration:.4f} -ar 48000 -ac 2 {output_path}"
    )


async def concat_files(list_file: str, output: str, copy: bool = True):
    """Concatenate media files using ffmpeg concat demuxer."""
    codec = "-c copy" if copy else ""
    await _run(
        f"ffmpeg -y -v error -f concat -safe 0 -i {list_file} {codec} {output}"
    )


async def mux_final(video: str, voice: str, output: str,
                    music: str | None = None, music_volume: float = 0.10):
    """Mux video + voice + optional music into final MP4."""
    import os
    if music and os.path.exists(music):
        await _run(
            f"ffmpeg -y -v error -i {video} -i {voice} -stream_loop -1 -i {music} "
            f"-filter_complex \"[2:a]volume={music_volume}[m];"
            f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]\" "
            f"-map 0:v -map [a] -c:v copy -c:a aac -b:a 160k "
            f"-movflags +faststart {output}"
        )
    else:
        await _run(
            f"ffmpeg -y -v error -i {video} -i {voice} "
            f"-map 0:v -map 1:a -c:v copy -c:a aac -b:a 160k "
            f"-shortest -movflags +faststart {output}"
        )
