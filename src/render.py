"""Renderowanie: karuzela (PNG 1080x1350) i rolka (MP4 1080x1920) z tego samego szablonu HTML."""
from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from common import ASSETS, ROOT, TEMPLATES, apps, brand

CAROUSEL_W, CAROUSEL_H = 1080, 1350
REEL_W, REEL_H = 1080, 1920
FPS = 30


# ---------- HTML ----------

def _theme(app_key: str) -> dict:
    b = brand()
    st = b["apps_style"][app_key]
    fonts = {f"font_{k}": (ASSETS / "fonts" / v).as_uri() for k, v in b["font"]["files"].items()}
    return {
        "bg": b["colors"]["background"], "surface": b["colors"]["surface"],
        "text": b["colors"]["text"], "muted": b["colors"]["muted"],
        "accent": st["accent"], "accent_soft": st["accent_soft"], **fonts,
    }


def _brand_html() -> str:
    b = brand()
    logo = ROOT / b.get("logo", "")
    if b.get("logo") and logo.exists():
        return f'<div class="brand"><img src="{logo.as_uri()}"><span>{html.escape(b["name"])}</span></div>'
    return f'<div class="brand"><span>{html.escape(b["name"])}</span></div>'


def _fill(template: str, values: dict) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def _page(app_key: str, content: str, w: int, h: int, body_class: str, sizes: dict, page_bg: str | None = None) -> str:
    tpl = (TEMPLATES / "slide.html").read_text(encoding="utf-8")
    th = _theme(app_key)
    return _fill(tpl, {**th, **sizes, "w": w, "h": h, "content": content, "body_class": body_class,
                       "page_bg": page_bg or th["bg"]})


def _slide_inner(app_key: str, kind: str, title: str, body: str, idx: int, total: int, footer_hint: str) -> str:
    name = html.escape(apps()[app_key]["name"])
    t, b = html.escape(title), html.escape(body or "")
    body_html = f"<p>{b}</p>" if b else ""
    if kind == "point":
        main = f'<div class="main"><div class="card"><div class="bar"></div><h1>{t}</h1>{body_html}</div></div>'
    else:
        main = f'<div class="main"><div class="bar"></div><h1>{t}</h1>{body_html}</div>'
    counter = f'<span class="counter">{idx}/{total}</span>' if total else ""
    return (f'<div class="top"><span class="tag">{name}</span>{counter}</div>{main}'
            f'<div class="bottom">{_brand_html()}<span class="swipe">{html.escape(footer_hint)}</span></div>')


def _screenshot(pages: list[tuple[str, Path]], w: int, h: int, transparent: bool = False) -> None:
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        for i, (doc, out) in enumerate(pages):
            f = Path(tmp) / f"p{i}.html"
            f.write_text(doc, encoding="utf-8")
            page.goto(f.as_uri())
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts.ready")
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), omit_background=transparent)
        browser.close()


# ---------- karuzela ----------

def render_carousel(app_key: str, content: dict, out_dir: Path) -> list[Path]:
    slides = content["slides"]
    total = len(slides)
    sizes = {"pad": 88, "title_size": 108, "point_title_size": 74, "body_size": 40}
    pages = []
    for i, s in enumerate(slides, 1):
        hint = "Swipe →" if i < total else "Link in bio"
        inner = _slide_inner(app_key, s["kind"], s["title"], s.get("body", ""), i, total, hint)
        pages.append((_page(app_key, inner, CAROUSEL_W, CAROUSEL_H, f"kind-{s['kind']}", sizes),
                      out_dir / f"slide-{i:02d}.png"))
    _screenshot(pages, CAROUSEL_W, CAROUSEL_H)
    return [p for _, p in pages]


# ---------- rolka ----------

def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def _card_segment(png: Path, seconds: float, out: Path) -> None:
    frames = int(seconds * FPS)
    vf = (f"scale={REEL_W * 2}:{REEL_H * 2},"
          f"zoompan=z='1+0.035*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={REEL_W}x{REEL_H}:fps={FPS},"
          f"fade=t=in:st=0:d=0.25,fade=t=out:st={seconds - 0.25:.2f}:d=0.25,format=yuv420p")
    _ffmpeg("-loop", "1", "-i", str(png), "-t", f"{seconds:.2f}", "-vf", vf, "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out))


def _clip_segment(clip: Path, overlay_png: Path, seconds: float, out: Path) -> None:
    fc = (f"[0:v]scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,crop={REEL_W}:{REEL_H},setsar=1,fps={FPS}[v];"
          f"[v][1:v]overlay=0:0,fade=t=in:st=0:d=0.25,fade=t=out:st={seconds - 0.25:.2f}:d=0.25,format=yuv420p")
    _ffmpeg("-stream_loop", "-1", "-i", str(clip), "-i", str(overlay_png), "-t", f"{seconds:.2f}",
            "-filter_complex", fc, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out))


def available_clips(app_key: str) -> list[str]:
    d = ASSETS / "clips" / app_key
    return sorted(p.name for p in d.glob("*") if p.suffix.lower() in {".mp4", ".mov", ".webm"}) if d.exists() else []


def _music(index: int) -> Path | None:
    tracks = sorted(p for p in (ASSETS / "music").glob("*") if p.suffix.lower() in {".mp3", ".m4a", ".wav"})
    return tracks[index % len(tracks)] if tracks else None


def render_reel(app_key: str, content: dict, out_dir: Path, music_index: int = 0) -> dict:
    scenes = content["scenes"]
    clips = set(available_clips(app_key))
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    total = len(scenes)

    card_sizes = {"pad": 96, "title_size": 112, "point_title_size": 92, "body_size": 44}
    card_pages, overlay_pages = [], []
    for i, sc in enumerate(scenes, 1):
        kind = "cover" if i == 1 else ("cta" if i == total else "point")
        if sc.get("visual") == "clip" and sc.get("clip") in clips:
            inner = f'<div class="caption">{html.escape(sc["text"])}</div>'
            overlay_pages.append((_page(app_key, inner, REEL_W, REEL_H, "overlay", card_sizes, "transparent"),
                                  work / f"scene-{i:02d}.png"))
        else:
            hint = "Link in bio" if kind == "cta" else ""
            inner = _slide_inner(app_key, kind, sc["text"], "", 0, 0, hint)
            card_pages.append((_page(app_key, inner, REEL_W, REEL_H, f"kind-{kind} reel", card_sizes),
                               work / f"scene-{i:02d}.png"))
    if card_pages:
        _screenshot(card_pages, REEL_W, REEL_H)
    if overlay_pages:
        _screenshot(overlay_pages, REEL_W, REEL_H, transparent=True)

    segments = []
    for i, sc in enumerate(scenes, 1):
        secs = max(2.5, min(5.0, float(sc.get("seconds", 3.5))))
        png, seg = work / f"scene-{i:02d}.png", work / f"seg-{i:02d}.mp4"
        if sc.get("visual") == "clip" and sc.get("clip") in clips:
            _clip_segment(ASSETS / "clips" / app_key / sc["clip"], png, secs, seg)
        else:
            _card_segment(png, secs, seg)
        segments.append((seg, secs))

    concat_list = work / "list.txt"
    concat_list.write_text("".join(f"file '{s.name}'\n" for s, _ in segments), encoding="utf-8")
    silent = work / "video.mp4"
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(silent))

    duration = sum(s for _, s in segments)
    reel = out_dir / "reel.mp4"
    track = _music(music_index)
    if track:
        _ffmpeg("-i", str(silent), "-stream_loop", "-1", "-i", str(track), "-t", f"{duration:.2f}",
                "-filter_complex", f"[1:a]volume=0.6,afade=t=in:st=0:d=0.5,afade=t=out:st={duration - 1.5:.2f}:d=1.5[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(reel))
    else:
        _ffmpeg("-i", str(silent), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{duration:.2f}",
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest",
                "-movflags", "+faststart", str(reel))

    cover = out_dir / "cover.jpg"
    first_png = work / "scene-01.png"
    _ffmpeg("-i", str(first_png), "-q:v", "3", str(cover))
    shutil.rmtree(work)
    return {"video": reel, "cover": cover, "duration": round(duration, 1), "music": track.name if track else None}
