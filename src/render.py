"""Renderowanie w stylu filmów Ready72h / BikepackPilot.

Karuzela: PNG 1080x1350. Rolka: MP4 1080x1920, sceny = karta z nagłówkiem, telefon z nagraniem apki + panel tekstu,
karta końcowa z logo i przyciskiem "Link in bio". Szablon: templates/slide.html, kolory/czcionki: config/brand.yaml.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

from common import ASSETS, TEMPLATES, apps, brand

CAROUSEL_W, CAROUSEL_H = 1080, 1350
REEL_W, REEL_H = 1080, 1920
FPS = 30

# okno telefonu: (x, y, szerokość, wysokość, promień)
REEL_PHONE = (230, 290, 620, 1040, 37)
CAROUSEL_PHONE = (320, 140, 440, 680, 30)


# ---------- klipy ----------

def clip_catalog(app_key: str) -> dict[str, str]:
    """Klipy apki z opisami (assets/clips/<apka>/clips.yaml); tylko istniejące pliki."""
    d = ASSETS / "clips" / app_key
    if not d.exists():
        return {}
    meta_file = d / "clips.yaml"
    meta = yaml.safe_load(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    files = sorted(p.name for p in d.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".webm"})
    return {f: (meta or {}).get(f, "App screen recording.") for f in files}


def available_clips(app_key: str) -> list[str]:
    return list(clip_catalog(app_key))


def _clip_path(app_key: str, name: str | None) -> Path | None:
    if not name:
        return None
    p = ASSETS / "clips" / app_key / name
    return p if p.exists() else None


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip() or 0)


# ---------- HTML ----------

def _style(app_key: str) -> dict:
    return brand()["apps_style"][app_key]


def logo_svg(app_key: str, full: bool = False) -> str:
    st = _style(app_key)
    if st["logo"] == "tent72h":
        extra = ('<line x1="50" y1="4" x2="50" y2="96" stroke="#fff" stroke-width="1.2"/>'
                 '<text x="50" y="72" text-anchor="middle" font-family="BrandSans" font-weight="700" font-size="21" fill="#fff">72h</text>'
                 if full else "")
        return (f'<svg viewBox="0 0 100 100"><polygon points="50,4 4,96 50,96" fill="#14504c"/>'
                f'<polygon points="50,4 96,96 50,96" fill="{st["accent"]}"/>{extra}</svg>')
    return (f'<svg viewBox="0 0 48 32"><polyline points="3,27 14,10 21,19 29,5 45,27" fill="none" '
            f'stroke="{st["accent"]}" stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def _decor(app_key: str, w: int, h: int) -> str:
    if not _style(app_key).get("decor"):
        return ""
    wave_y = h * 0.11
    a = h * 0.025
    wave = f"M0 {wave_y} " + " ".join(
        f"Q {w * (i + 0.25) / 2} {wave_y + (a if i % 2 == 0 else -a) * 2} {w * (i + 0.5) / 2} {wave_y}" for i in range(4))
    cx, cy = w * 0.86, h * 0.84
    circles = "".join(f'<circle cx="{cx}" cy="{cy}" r="{w * r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="2"/>'
                      for r in (0.14, 0.21, 0.29))
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none"><path d="{wave}" fill="none" '
            f'stroke="rgba(255,255,255,.12)" stroke-width="2" stroke-dasharray="3 5"/>{circles}</svg>')


def _hole_mask(w: int, h: int, hole: tuple | None) -> str:
    if not hole:
        return ""
    x, y, hw, hh, r = hole
    path = (f"M0 0H{w}V{h}H0Z M{x + r} {y}H{x + hw - r}A{r} {r} 0 0 1 {x + hw} {y + r}V{y + hh - r}"
            f"A{r} {r} 0 0 1 {x + hw - r} {y + hh}H{x + r}A{r} {r} 0 0 1 {x} {y + hh - r}V{y + r}A{r} {r} 0 0 1 {x + r} {y}Z")
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"><path fill-rule="evenodd" fill="black" d="{path}"/></svg>'
    return "data:image/svg+xml," + urllib.parse.quote(svg)


def rich(text: str) -> str:
    """*słowa* -> pomarańczowe wyróżnienie."""
    return re.sub(r"\*(.+?)\*", r"<em>\1</em>", html.escape(text or ""))


def plain(text: str) -> str:
    return (text or "").replace("*", "")


def _page(app_key: str, w: int, h: int, content: str, hole: tuple | None = None, hero_size: float = 7.2) -> str:
    st, b = _style(app_key), brand()
    fonts = {f"font_{k}": (ASSETS / "fonts" / v).as_uri() for k, v in b["fonts"].items()}
    families = {"serif_bold": "BrandSerif, serif", "sans_bold": "BrandSans, sans-serif"}
    values = {
        **fonts, "bg1": st["background"][0], "bg2": st["background"][1], "panel": st["panel"], "text": st["text"],
        "muted": st["muted"], "accent": st["accent"], "headline_family": families[st["headline_font"]],
        "w": w, "h": h, "unit": w / 100, "header_top": 4.6 if h > 1500 else 4.0, "hero_size": hero_size,
        "hole_mask": _hole_mask(w, h, hole), "content": content,
    }
    tpl = (TEMPLATES / "slide.html").read_text(encoding="utf-8")
    for k, v in values.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def _bg(app_key: str, w: int, h: int, holed: bool = False) -> str:
    return f'<div class="bg{" holed" if holed else ""}">{_decor(app_key, w, h)}</div>'


def _header(app_key: str) -> str:
    return f'<div class="header">{logo_svg(app_key)}<span class="word">{html.escape(apps()[app_key]["name"])}</span></div>'


def _end(app_key: str, line: str, big: bool) -> str:
    st = _style(app_key)
    name = html.escape(apps()[app_key]["name"])
    first = f'<div class="line big">{rich(line)}</div>' if big and line else ""
    return (f'<div class="end">{logo_svg(app_key, full=True)}<div class="name">{name}</div>{first}'
            f'<div class="line">{html.escape(st["tagline"])}</div><div class="pill">Link in bio</div></div>')


def _px(v: float) -> str:
    return f"{v:.0f}px"


def _screenshot(pages: list[tuple[str, Path]], w: int, h: int, transparent: bool = False) -> None:
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        for i, (doc, out) in enumerate(pages):
            f = Path(tmp) / f"p{i}.html"
            f.write_text(doc, encoding="utf-8")
            page.goto(f.as_uri())
            page.wait_for_function("window.__ready === true", timeout=15000)
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), omit_background=transparent)
        browser.close()


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


# ---------- karuzela ----------

def _still(app_key: str, clip: str, out: Path) -> Path | None:
    src = _clip_path(app_key, clip)
    if not src:
        return None
    _ffmpeg("-ss", f"{min(1.5, _duration(src) / 2):.2f}", "-i", str(src), "-frames:v", "1", str(out))
    return out


def render_carousel(app_key: str, content: dict, out_dir: Path) -> list[Path]:
    slides = content["slides"]
    total, W, H = len(slides), CAROUSEL_W, CAROUSEL_H
    pages = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(slides, 1):
            count = f'<span class="count">{i:02d}/{total:02d}</span>'
            foot_swipe = f'<div class="foot">{count}<span class="swipe">Swipe →</span></div>'
            if s["kind"] == "cover":
                sub = f"<p>{html.escape(plain(s.get('body', '')))}</p>" if s.get("body") else ""
                body = (f'{_bg(app_key, W, H)}{_header(app_key)}<div class="hero">'
                        f'<div class="headline" data-fit="560">{rich(s["title"])}</div>{sub}</div>{foot_swipe}')
                pages.append((_page(app_key, W, H, body, hero_size=8.2), out_dir / f"slide-{i:02d}.png"))
                continue
            if s["kind"] == "cta":
                body = f'{_bg(app_key, W, H)}{_end(app_key, s["title"], big=True)}'
                pages.append((_page(app_key, W, H, body), out_dir / f"slide-{i:02d}.png"))
                continue
            kicker = f'<div class="kicker">{i - 1:02d}</div>'
            text = (f'{kicker}<div class="headline">{rich(s["title"])}</div>'
                    + (f"<p>{html.escape(plain(s.get('body', '')))}</p>" if s.get("body") else ""))
            still = _still(app_key, s.get("clip"), Path(tmp) / f"still-{i}.png")
            if still:
                x, y, pw, ph, r = CAROUSEL_PHONE
                phone = (f'<div class="phone" style="left:{x}px;top:{y}px;width:{pw}px;height:{ph}px;border-radius:{r}px">'
                         f'<img src="{still.as_uri()}"></div>')
                panel = f'<div class="panel" data-fit="320" style="left:70px;right:70px;top:860px">{text}</div>'
                body = f"{_bg(app_key, W, H)}{_header(app_key)}{phone}{panel}{foot_swipe}"
            else:
                panel = (f'<div class="panel" data-fit="820" style="left:70px;right:70px;top:50%;'
                         f'transform:translateY(-46%)">{text}</div>')
                body = f"{_bg(app_key, W, H)}{_header(app_key)}{panel}{foot_swipe}"
            pages.append((_page(app_key, W, H, body), out_dir / f"slide-{i:02d}.png"))
        _screenshot(pages, W, H)
    return [p for _, p in pages]


# ---------- rolka ----------

def _music(index: int) -> Path | None:
    tracks = sorted(p for p in (ASSETS / "music").glob("*") if p.suffix.lower() in {".mp3", ".m4a", ".wav"})
    return tracks[index % len(tracks)] if tracks else None


def _fade(secs: float) -> str:
    return f"fade=t=in:st=0:d=0.2,fade=t=out:st={secs - 0.2:.2f}:d=0.2"


def render_reel(app_key: str, content: dict, out_dir: Path, music_index: int = 0) -> dict:
    scenes = content["scenes"]
    W, H, total = REEL_W, REEL_H, len(scenes)
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)

    pages_opaque, pages_holed, plan = [], [], []
    for i, sc in enumerate(scenes, 1):
        png = work / f"scene-{i:02d}.png"
        clip = _clip_path(app_key, sc.get("clip")) if sc.get("visual") == "clip" else None
        if i == total:  # karta końcowa
            body = f'{_bg(app_key, W, H)}{_end(app_key, sc["text"], big=True)}'
            pages_opaque.append((_page(app_key, W, H, body), png))
        elif clip:
            x, y, pw, ph, r = REEL_PHONE
            frame = (f'<div class="phone" style="left:{x}px;top:{y}px;width:{pw}px;height:{ph}px;border-radius:{r}px;'
                     f'box-shadow:0 0 0 3px rgba(255,255,255,.16)"></div>')
            panel = (f'<div class="panel" data-fit="330" style="left:70px;right:70px;top:1410px;min-height:290px">'
                     f'<div class="headline" style="font-size:1.76em">{rich(sc["text"])}</div></div>')
            body = f"{_bg(app_key, W, H, holed=True)}{_header(app_key)}{frame}{panel}"
            pages_holed.append((_page(app_key, W, H, body, hole=REEL_PHONE), png))
        elif i == 1:  # hak
            body = f'{_bg(app_key, W, H)}{_header(app_key)}<div class="hero"><div class="headline" data-fit="900">{rich(sc["text"])}</div></div>'
            pages_opaque.append((_page(app_key, W, H, body, hero_size=7.4), png))
        else:
            panel = (f'<div class="panel" data-fit="760" style="left:70px;right:70px;top:46%;transform:translateY(-50%);'
                     f'min-height:300px"><div class="kicker">{i - 1:02d}</div>'
                     f'<div class="headline" style="font-size:2em">{rich(sc["text"])}</div></div>')
            body = f"{_bg(app_key, W, H)}{_header(app_key)}{panel}"
            pages_opaque.append((_page(app_key, W, H, body), png))
        plan.append((png, clip, max(2.5, min(6.0, float(sc.get("seconds", 3.5))))))
    if pages_opaque:
        _screenshot(pages_opaque, W, H)
    if pages_holed:
        _screenshot(pages_holed, W, H, transparent=True)

    segments = []
    for i, (png, clip, secs) in enumerate(plan, 1):
        seg = work / f"seg-{i:02d}.mp4"
        if clip:
            x, y, pw, ph, _ = REEL_PHONE
            fc = (f"color=c=black:s={W}x{H}:r={FPS}[base];"
                  f"[0:v]fps={FPS},scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph}:(iw-{pw})/2:0,setsar=1[clip];"
                  f"[base][clip]overlay={x}:{y}:shortest=1[v1];[v1][1:v]overlay=0:0,{_fade(secs)},format=yuv420p")
            _ffmpeg("-stream_loop", "-1", "-i", str(clip), "-loop", "1", "-i", str(png), "-t", f"{secs:.2f}",
                    "-filter_complex", fc, "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-an", str(seg))
        else:
            _ffmpeg("-loop", "1", "-i", str(png), "-t", f"{secs:.2f}", "-vf", f"fps={FPS},{_fade(secs)},format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-an", str(seg))
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
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(reel))
    else:  # Instagram wymaga ścieżki audio — dokładamy ciszę
        _ffmpeg("-i", str(silent), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{duration:.2f}",
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(reel))

    cover = out_dir / "cover.jpg"
    _ffmpeg("-i", str(plan[0][0]), "-q:v", "3", str(cover))
    shutil.rmtree(work)
    return {"video": reel, "cover": cover, "duration": round(duration, 1), "music": track.name if track else None}
