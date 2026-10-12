"""Agenci przygotowują posty na następny dzień i planują je w Zernio.

Użycie:
  python src/generate.py                       # tryb harmonogramu: obie apki, publikacja jutro (GitHub Actions)
  python src/generate.py --app ready72h --date 2026-10-13 --format reel
  python src/generate.py --sample --no-schedule  # próbne posty bez Claude API i bez Zernio

Wynik: posts/<data>-<apka>/ z post.json, grafikami lub rolką, scheduled.json i issue_body.md.
Na wyjściu ustawia GITHUB_OUTPUT: created=<lista folderów>.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from common import POSTS, apps, existing_posts, post_dir, schedule, topics, write_json
from render import clip_catalog, render_carousel, render_reel


def pick_topic(app_key: str, skip: set[str] | None = None) -> dict:
    used = {p["topic"] for p in existing_posts(app_key) if not p["_cancelled"]} | (skip or set())
    for t in topics().get(app_key, []):
        if t["topic"] not in used:
            return t
    from agents import new_topic
    return new_topic(app_key, sorted(used))


def produce_content(app_key: str, fmt: str, topic: dict) -> tuple[dict, list[dict]]:
    from agents import review_content, write_content
    history = [p for p in existing_posts(app_key) if not p["_cancelled"]]
    recent = [p["topic"] for p in history][-6:]
    content = write_content(app_key, fmt, topic, clip_catalog(app_key), recent, [p["format"] for p in history][-4:])
    fmt = content["format"]
    reviews = []
    rounds = 3
    for n in range(1, rounds + 1):
        qa = review_content(app_key, fmt, content, clip_catalog(app_key))
        reviews.append({"verdict": qa["verdict"], "issues": qa.get("issues", [])})
        if qa["verdict"] == "pass":
            return content, reviews
        if qa["verdict"] == "reject":
            break
        if qa.get("fixed"):
            content = {**qa["fixed"], "format": fmt}
            if n == rounds:  # ostatnia runda: przyjmujemy wersję poprawioną przez kontrolera
                return content, reviews
    raise RuntimeError(f"Kontrola jakości nie przepuściła posta: {reviews}")


def sample_content(app_key: str, fmt: str) -> dict:
    """Próbne treści do testu renderowania bez Claude API."""
    if app_key == "bikepackpilot":
        base = {
            "hook": "Kilometres lie. Riding days don't.",
            "caption": ("180 km looks like two easy days. Add gravel, 32°C and a headwind, and it's a different trip.\n\n"
                        "BikepackPilot splits your route into 50 km segments, lets you set surface, heat, wind and rain for each, "
                        "and estimates realistic riding days. Your gear list adapts to the conditions you entered.\n\n"
                        "Plan your next trip with BikepackPilot — link in bio."),
            "hashtags": ["#bikepacking", "#bikepackinglife", "#gravelbike", "#cycletouring", "#bikepackingplanner", "#adventurecycling"],
            "alt_text": "Reel showing how BikepackPilot turns a route into realistic riding days.",
            "sources_used": [],
        }
        base["scenes"] = [
            {"text": "Kilometres lie. *Riding days* don't.", "seconds": 3, "visual": "card"},
            {"text": "Enter your real route distance", "seconds": 3.2, "visual": "clip", "clip": "enter-route-distance.mp4"},
            {"text": "Set *surface, heat, wind and rain* for each 50 km", "seconds": 3.4, "visual": "clip", "clip": "set-surface-heat-wind-rain.mp4"},
            {"text": "Get *realistic riding days*, not just kilometres", "seconds": 3.6, "visual": "clip", "clip": "realistic-riding-days.mp4"},
            {"text": "Your *gear list* adapts to the conditions", "seconds": 3.8, "visual": "clip", "clip": "gear-adapts-to-conditions.mp4"},
            {"text": "Plan your next trip in minutes", "seconds": 3.5, "visual": "card"},
        ]
        base["slides"] = [
            {"kind": "cover", "title": "Kilometres lie. *Riding days* don't.", "body": "How to plan stages you can actually ride."},
            {"kind": "point", "title": "Start with the *real distance*", "body": "Take it from your map or GPX file, not from a guess.", "clip": "enter-route-distance.mp4"},
            {"kind": "point", "title": "Think in *50 km* blocks", "body": "Short segments make surface, heat and wind easy to judge."},
            {"kind": "point", "title": "Conditions change *everything*", "body": "Gravel, 32°C and a headwind can double the time of the same distance.", "clip": "set-surface-heat-wind-rain.mp4"},
            {"kind": "point", "title": "Plan in *riding days*", "body": "180 km on mixed terrain may be 2.1 days, not 1.5.", "clip": "realistic-riding-days.mp4"},
            {"kind": "cta", "title": "Plan your next trip *in minutes*", "body": ""},
        ]
        return base
    base = {
        "hook": "How much water do you really need?",
        "caption": ("Most plans start with water — and most of us underestimate it.\n\n"
                    "Official guidance from Sweden (MSB), Germany (BBK) and Poland (RCB) all put water first. "
                    "A common rule of thumb is about 3 litres per person per day for drinking and basic cooking.\n\n"
                    "For a family of four over 72 hours, that adds up fast. Store it where you can reach it, "
                    "and swap it regularly.\n\nWant the full checklist, offline? Get the planner — link in bio (pay what you want)."),
        "hashtags": ["#preparedness", "#emergencykit", "#72hours", "#blackout", "#familysafety", "#readyhome"],
        "alt_text": "Carousel explaining how much water a household needs for 72 hours.",
        "sources_used": ["MSB", "BBK", "RCB"],
    }
    base["slides"] = [
        {"kind": "cover", "title": "How much *water* do you really need?", "body": "For 72 hours. For your whole household."},
        {"kind": "point", "title": "Water comes *first*", "body": "MSB, BBK and RCB all put drinking water at the top of the list."},
        {"kind": "point", "title": "About *3 litres* per person, per day", "body": "For drinking and basic cooking. Add extra for hygiene if you have space.", "clip": "checklist-water-supplies.mp4"},
        {"kind": "point", "title": "Do the maths *once*", "body": "4 people × 3 days × 3 litres = 36 litres. Write the number down."},
        {"kind": "point", "title": "Store it *smart*", "body": "Sealed bottles, in a cool dark place you can reach without moving furniture.", "clip": "apartment-vs-house-advice.mp4"},
        {"kind": "point", "title": "Swap it on a *schedule*", "body": "Put a date on it. Rotate it into daily use before it expires."},
        {"kind": "cta", "title": "Get the full *72-hour* plan", "body": ""},
    ]
    base["scenes"] = [
        {"text": "Most homes couldn't last *72 hours* without power.", "seconds": 3.5, "visual": "card"},
        {"text": "A complete *72-hour checklist* for water, food, meds and cash.", "seconds": 4, "visual": "clip", "clip": "checklist-water-supplies.mp4"},
        {"text": "A family communication plan for when *phones go down*.", "seconds": 4, "visual": "clip", "clip": "family-communication-plan.mp4"},
        {"text": "A *15-minute grab-and-go bag*, checked and ready by the door.", "seconds": 4, "visual": "clip", "clip": "go-bag-15-minutes.mp4"},
        {"text": "Tailored advice for *where you live*.", "seconds": 4, "visual": "clip", "clip": "apartment-vs-house-advice.mp4"},
        {"text": "Pay what you want — *from 2 PLN*", "seconds": 3.5, "visual": "card"},
    ]
    return base


def issue_body(post: dict, folder, sched: dict | None) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "Przemek-cyber/handy-apps-autopilot")
    rel = folder.relative_to(POSTS.parent).as_posix()
    raw = lambda f: f"https://github.com/{repo}/blob/main/{rel}/{f}?raw=true"  # noqa: E731
    content, fmt = post["content"], post["format"]
    when = f"{sched['scheduled_for'].replace('T', ' ')[:16]} ({sched['timezone']})" if sched else "nie zaplanowano"
    lines = [f"<!-- post: {rel} -->",
             f"## {apps()[post['app']]['name']} · {fmt} · publikacja {when}",
             f"**Temat:** {post['topic']} ({post['pillar']})", "",
             "> **Brak komentarza = post wychodzi automatycznie.**",
             "> Napisz tu dowolny komentarz (np. „stop” albo uwagę), a post zostanie **wstrzymany** i usunięty z kolejki.",
             "> Komentarz „ok” niczego nie zmienia.", ""]
    if fmt == "carousel":
        imgs = sorted(p.name for p in folder.glob("slide-*.jpg"))
        lines += ["### Slajdy", " ".join(f'<img src="{raw(i)}" width="240">' for i in imgs)]
    else:
        lines += ["### Rolka", f'<img src="{raw("cover.jpg")}" width="240">',
                  f"[▶ Obejrzyj reel.mp4]({raw('reel.mp4')})", "", "| # | Tekst | s |", "|---|---|---|"]
        lines += [f"| {i} | {s['text']} | {s['seconds']} |" for i, s in enumerate(content["scenes"], 1)]
    lines += ["", "### Podpis", "```", content["caption"], "", " ".join(content["hashtags"]), "```", "",
              f"Kontrola jakości: {' → '.join(r['verdict'] for r in post['qa']) or 'próbka'}"]
    lines += [f"- {i}" for r in post["qa"] for i in r["issues"]]
    return "\n".join(lines) + "\n"


def make_post(app_key: str, publish_on: dt.date, fmt: str, sample: bool) -> dict:
    """Pisze, sprawdza i renderuje post. Przy odrzuceniu tematu przez kontrolę próbuje kolejnego."""
    folder = post_dir(app_key, publish_on)
    if folder.exists():
        raise FileExistsError(f"{folder.name} już istnieje")
    tried: set[str] = set()
    for attempt in range(2):
        topic = pick_topic(app_key, tried) if not sample else {"topic": f"Sample {fmt}", "pillar": "value"}
        try:
            if sample:
                f = "carousel" if fmt == "auto" else fmt
                content, reviews = {**sample_content(app_key, f), "format": f}, []
            else:
                content, reviews = produce_content(app_key, fmt, topic)
            break
        except RuntimeError as e:
            print(f"::warning title={app_key}: temat odrzucony::{topic['topic']} — {str(e)[:300]}")
            tried.add(topic["topic"])
    else:
        raise RuntimeError(f"{app_key}: kontrola jakości odrzuciła 2 tematy z rzędu")
    fmt = content["format"]
    folder.mkdir(parents=True)
    if fmt == "carousel":
        media = {"images": [p.name for p in render_carousel(app_key, content, folder)]}
    else:
        r = render_reel(app_key, content, folder, music_index=len(existing_posts(app_key)))
        media = {"video": r["video"].name, "cover": r["cover"].name, "duration": r["duration"], "music": r["music"]}
    post = {"app": app_key, "format": fmt, "topic": topic["topic"], "pillar": topic["pillar"],
            "publish_on": publish_on.isoformat(), "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "content": content, "media": media, "qa": reviews}
    write_json(folder / "post.json", post)
    post["_dir"] = folder
    return post


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", help="tylko ta apka (domyślnie wszystkie z config/schedule.yaml)")
    ap.add_argument("--format", choices=["carousel", "reel", "auto"], default="auto")
    ap.add_argument("--date", help="data publikacji RRRR-MM-DD (domyślnie: jutro wg strefy z harmonogramu)")
    ap.add_argument("--sample", action="store_true", help="próbne treści bez Claude API")
    ap.add_argument("--no-schedule", action="store_true", help="nie planuj w Zernio")
    args = ap.parse_args()

    sched = schedule()
    tz = sched["timezone"]
    today = dt.datetime.now(ZoneInfo(tz)).date()
    day = dt.date.fromisoformat(args.date) if args.date else today + dt.timedelta(days=sched["publish_after_days"])
    targets = [args.app] if args.app else list(sched["publish_times"])

    created, failed = [], []
    for app_key in targets:
        try:
            post = make_post(app_key, day, args.format, args.sample)
            sched_info = None
            if not args.no_schedule:
                from publish import schedule_post
                local = f"{day.isoformat()}T{sched['publish_times'][app_key]}:00"
                sched_info = schedule_post(post, local, tz)
                write_json(post["_dir"] / "scheduled.json", sched_info)
            (post["_dir"] / "issue_body.md").write_text(issue_body(post, post["_dir"], sched_info), encoding="utf-8")
            created.append(post["_dir"].name)
            print(f"::notice title=Gotowe: {post['_dir'].name}::{post['format']} · {post['topic']}"
                  + (f" · zaplanowano na {sched_info['scheduled_for']}" if sched_info else ""))
        except Exception as e:  # jedna apka nie blokuje drugiej
            failed.append(app_key)
            print(f"::error title=Generowanie {app_key} nie powiodło się::{type(e).__name__}: {str(e)[:800]}")

    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"created={' '.join(created)}\n")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(__file__))
    main()
