"""Generuje post dla apki, która ma termin publikacji za `lead_days` dni.

Użycie:
  python src/generate.py                      # tryb harmonogramu (GitHub Actions)
  python src/generate.py --app ready72h --format reel --date 2026-10-12   # ręcznie
  python src/generate.py --sample             # próbny post bez Claude API (test renderowania)

Wynik: posts/<data>-<apka>/ z post.json, grafikami lub rolką i pr_body.md.
Na wyjściu ustawia GITHUB_OUTPUT: created=<lista folderów>.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

from common import POSTS, apps, existing_posts, is_slot, post_dir, schedule, topics, write_json
from render import clip_catalog, render_carousel, render_reel


def open_pr_apps() -> set[str]:
    """Apki, które czekają już na akceptację w otwartym PR (żeby nie dublować)."""
    try:
        out = subprocess.run(["gh", "pr", "list", "--label", "autopost", "--state", "open",
                              "--json", "headRefName"], capture_output=True, text=True, check=True).stdout
        return {r["headRefName"].split("/")[-1].split("-", 3)[-1] for r in json.loads(out)}
    except Exception:
        return set()


def pick_topic(app_key: str) -> dict:
    used = [p["topic"] for p in existing_posts(app_key)]
    for t in topics().get(app_key, []):
        if t["topic"] not in used:
            return t
    from agents import new_topic
    return new_topic(app_key, used)


def pick_format(app_key: str) -> str:
    rot = schedule()["format_rotation"]
    return rot[len(existing_posts(app_key)) % len(rot)]


def produce_content(app_key: str, fmt: str, topic: dict) -> tuple[dict, list[dict]]:
    from agents import review_content, write_content
    recent = [p["topic"] for p in existing_posts(app_key)][-6:]
    content = write_content(app_key, fmt, topic, clip_catalog(app_key), recent)
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
            content = qa["fixed"]
            if n == rounds:  # ostatnia runda: przyjmujemy wersję poprawioną przez kontrolera (i tak akceptujesz ją w PR)
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


def pr_body(app_key: str, fmt: str, topic: dict, content: dict, publish_on: dt.date, folder, reviews) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = f"autopost/{folder.name}"  # ta sama nazwa gałęzi tworzy workflow
    rel = folder.relative_to(POSTS.parent).as_posix()
    raw = lambda f: f"https://github.com/{repo}/blob/{branch}/{rel}/{f}?raw=true"  # noqa: E731
    lines = [f"## {apps()[app_key]['name']} · {fmt} · publikacja {publish_on.isoformat()}",
             f"**Temat:** {topic['topic']} ({topic['pillar']})", "",
             "**Merge** = akceptuję i publikuję w dniu publikacji. **Close** = odrzucam (temat wraca do puli).",
             "Poprawki: edytuj `post.json` w tym PR przed scaleniem (podpis, hashtagi).", ""]
    if fmt == "carousel":
        imgs = sorted(p.name for p in folder.glob("slide-*.jpg"))
        lines += ["### Slajdy", " ".join(f'<img src="{raw(i)}" width="240">' for i in imgs)]
    else:
        lines += ["### Rolka", f'<img src="{raw("cover.jpg")}" width="240">',
                  f"[▶ Obejrzyj reel.mp4]({raw('reel.mp4')})", "",
                  "| # | Tekst | s |", "|---|---|---|"]
        lines += [f"| {i} | {s['text']} | {s['seconds']} |" for i, s in enumerate(content["scenes"], 1)]
    lines += ["", "### Podpis", "```", content["caption"], "", " ".join(content["hashtags"]), "```",
              "", f"Kontrola jakości: {' → '.join(r['verdict'] for r in reviews) or 'próbka'}"]
    for r in reviews:
        for issue in r["issues"]:
            lines.append(f"- {issue}")
    return "\n".join(lines) + "\n"


def make_post(app_key: str, publish_on: dt.date, fmt: str | None, sample: bool) -> str:
    fmt = fmt or pick_format(app_key)
    topic = pick_topic(app_key) if not sample else {"topic": f"Sample {fmt}", "pillar": "value"}
    folder = post_dir(app_key, publish_on)
    if folder.exists():
        print(f"Pomijam: {folder.name} już istnieje")
        return ""
    if sample:
        content, reviews = sample_content(app_key, fmt), []
    else:
        content, reviews = produce_content(app_key, fmt, topic)
    folder.mkdir(parents=True)
    media = {}
    if fmt == "carousel":
        media["images"] = [p.name for p in render_carousel(app_key, content, folder)]
    else:
        r = render_reel(app_key, content, folder, music_index=len(existing_posts(app_key)))
        media = {"video": r["video"].name, "cover": r["cover"].name, "duration": r["duration"], "music": r["music"]}
    post = {"app": app_key, "format": fmt, "topic": topic["topic"], "pillar": topic["pillar"],
            "publish_on": publish_on.isoformat(), "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "content": content, "media": media, "qa": reviews}
    write_json(folder / "post.json", post)
    (folder / "pr_body.md").write_text(pr_body(app_key, fmt, topic, content, publish_on, folder, reviews), encoding="utf-8")
    print(f"Utworzono {folder}")
    return folder.name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app")
    ap.add_argument("--format", choices=["carousel", "reel"])
    ap.add_argument("--date", help="data publikacji RRRR-MM-DD")
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()

    sched = schedule()
    if args.app:
        day = dt.date.fromisoformat(args.date) if args.date else dt.date.today() + dt.timedelta(days=sched["lead_days"])
        targets = [(args.app, day)]
    else:
        day = dt.date.today() + dt.timedelta(days=sched["lead_days"])
        waiting = open_pr_apps()
        targets = [(a, day) for a in sched["apps"] if is_slot(a, day, sched) and a not in waiting]

    created = [n for a, d in targets if (n := make_post(a, d, args.format, args.sample))]
    if not created:
        print("Nic do wygenerowania dzisiaj.")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"created={' '.join(created)}\n")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        main()
    except Exception as e:  # komunikat jako adnotacja GitHub (widoczna w podsumowaniu uruchomienia)
        msg = f"{type(e).__name__}: {e}".replace("\n", " ")[:900]
        print(f"::error title=Generowanie nie powiodło się::{msg}")
        raise
