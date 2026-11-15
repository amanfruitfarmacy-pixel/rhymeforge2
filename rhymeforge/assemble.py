"""Assemble the final video with ffmpeg: shots cut to the lyric timing,
animated stills, reused chorus shots, karaoke captions and loudness at -14 LUFS."""
from __future__ import annotations

from pathlib import Path

from .library import ClipLibrary
from .util import log, media_duration, run, write_json


def shot_times(shots: list[dict], lines: list[dict], song_duration: float) -> list[dict]:
    """Each shot runs from its line's start until the next line starts."""
    out = []
    for i, (s, ln) in enumerate(zip(shots, lines)):
        start = 0.0 if i == 0 else ln["start"]
        end = lines[i + 1]["start"] if i + 1 < len(lines) else song_duration
        out.append(dict(s, start=round(start, 3), end=round(max(end, start + 0.5), 3)))
    return out


def _still_filter(motion: str, frames: int, w: int, h: int, fps: int, bpm: float) -> str:
    beat_frames = fps * 60 / bpm
    z = {
        "push_in": f"1+0.12*on/{frames}",
        "pull_out": f"1.12-0.12*on/{frames}",
        "bounce": f"1.06+0.03*abs(sin(PI*on/{beat_frames:.3f}))",
        "pan_left": "1.15",
        "pan_right": "1.15",
    }.get(motion, f"1+0.1*on/{frames}")
    x = {"pan_left": f"(iw-iw/zoom)*(1-on/{frames})", "pan_right": f"(iw-iw/zoom)*on/{frames}"}.get(
        motion, "iw/2-(iw/zoom/2)")
    return (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
            f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},setsar=1")


def render_segment(shot: dict, src: Path, dest: Path, cfg: dict, is_clip: bool) -> None:
    v = cfg["video"]
    w, h, fps = v["width"], v["height"], v["fps"]
    dur = shot["end"] - shot["start"]
    frames = max(1, round(dur * fps))
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-frames:v", str(frames), "-r", str(fps)]
    if is_clip:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1"
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src), "-vf", vf, *enc, str(dest)])
    else:
        vf = _still_filter(shot.get("motion", "push_in"), frames, w, h, fps, v["bpm"])
        run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(src), "-vf", vf, *enc, str(dest)])


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def write_ass(lines: list[dict], path: Path, w: int, h: int) -> None:
    """Karaoke captions: each word lights up yellow as it is sung."""
    size = int(h * 0.075)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Kara,DejaVu Sans,{size},&H0000E5FF,&H00FFFFFF,&H00402010,&H64000000,-1,0,0,0,100,100,1,0,1,{max(3, size // 12)},2,2,60,60,{int(h * 0.06)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for ln in lines:
        start, end = ln["start"], ln["end"] + 0.4
        shown = ln["text"].split()  # display words keep their capitals and punctuation
        ws = ln.get("words")
        if ws and len(ws) == len(shown):
            parts, cursor = [], start
            for wd, label in zip(ws, shown):
                gap = max(0.0, wd["start"] - cursor)
                if gap > 0.02:
                    parts.append(f"{{\\k{round(gap * 100)}}}")
                parts.append(f"{{\\kf{max(1, round((wd['end'] - wd['start']) * 100))}}}{label} ")
                cursor = wd["end"]
            text = "".join(parts).strip()
        else:
            per = (ln["end"] - ln["start"]) / max(len(shown), 1)
            text = " ".join(f"{{\\kf{round(per * 100)}}}{w}" for w in shown)
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Kara,,0,0,0,,{text}")
    path.write_text(header + "\n".join(events) + "\n")


def write_srt(lines: list[dict], path: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    blocks = [f"{i}\n{ts(l['start'])} --> {ts(l['end'] + 0.4)}\n{l['text']}\n" for i, l in enumerate(lines, 1)]
    path.write_text("\n".join(blocks))


def assemble(video_dir: Path, storyboard: dict, lines: list[dict], gpu_out: Path, slug: str, cfg: dict,
             library: ClipLibrary | None = None, user_song: Path | None = None) -> Path:
    v = cfg["video"]
    song = user_song if user_song and user_song.exists() else next(iter(sorted((gpu_out / "songs").glob(f"{slug}.*"))), None)
    if not song:
        raise RuntimeError(f"no song for {slug}: GPU job failed for it (see {gpu_out/'errors.json'}). "
                           f"You can drop your own song at {video_dir/'song.wav'} and re-run.")
    song_dur = media_duration(song)
    shots = shot_times(storyboard["shots"], lines, song_dur)
    seg_dir = video_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    by_id = {s["id"]: s for s in shots}
    fallbacks = []
    for s in shots:
        src_shot = by_id[s["reuse_of"]] if s["kind"] == "reuse" else s
        clip = gpu_out / "clips" / f"{slug}__{src_shot['id']}.mp4"
        image = gpu_out / "images" / f"{slug}__{src_shot['id']}.png"
        is_clip = False
        if src_shot["kind"] == "library" and library:
            item = next((i for i in library.items if i["id"] == src_shot.get("library_id")), None)
            src, is_clip = (library.path(item), True) if item else (None, False)
        elif clip.exists():
            src, is_clip = clip, True
        elif image.exists():
            src = image
            if src_shot["kind"] == "ai_clip":
                fallbacks.append(s["id"])
        else:
            src = next(iter(sorted((gpu_out / "images").glob(f"{slug}__*.png"))), None)
            fallbacks.append(s["id"])
        if src is None:
            raise RuntimeError(f"no visual available for shot {s['id']} of {slug}")
        render_segment(s, src, seg_dir / f"{s['id']}.mp4", cfg, is_clip)
        # Add newly generated clips to the library for future weeks.
        if library and is_clip and src_shot["kind"] == "ai_clip" and s["kind"] == "ai_clip":
            library.add(clip, f"{slug}__{s['id']}", s["characters"], s["line"], s.get("motion_prompt", ""))
    if fallbacks:
        log(f"{slug}: {len(fallbacks)} shots fell back to stills/other images: {fallbacks}")

    concat = seg_dir / "concat.txt"
    concat.write_text("".join(f"file '{(seg_dir / (s['id'] + '.mp4')).resolve()}'\n" for s in shots))
    silent = video_dir / "video_silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)])

    ass, srt = video_dir / "captions.ass", video_dir / "captions.srt"
    write_ass(lines, ass, v["width"], v["height"])
    write_srt(lines, srt)
    final = video_dir / "final.mp4"
    ass_arg = str(ass.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    run(["ffmpeg", "-y", "-i", str(silent), "-i", str(song),
         "-vf", f"subtitles='{ass_arg}'", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(final)])
    write_json(video_dir / "shots_timed.json", shots)
    return final
