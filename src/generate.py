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
from render import available_clips, render_carousel, render_reel


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
    content = write_content(app_key, fmt, topic, available_clips(app_key), recent)
    reviews = []
    for _ in range(2):
        qa = review_content(app_key, fmt, content)
        reviews.append({"verdict": qa["verdict"], "issues": qa.get("issues", [])})
        if qa["verdict"] == "pass":
            return content, reviews
        if qa["verdict"] == "reject":
            break
        if qa.get("fixed"):
            content = qa["fixed"]
    raise RuntimeError(f"Kontrola jakości nie przepuściła posta: {reviews}")


def sample_content(fmt: str) -> dict:
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
    if fmt == "carousel":
        base["slides"] = [
            {"kind": "cover", "title": "How much water do you really need?", "body": "For 72 hours. For your whole household."},
            {"kind": "point", "title": "Water comes first", "body": "MSB, BBK and RCB all put drinking water at the top of the list."},
            {"kind": "point", "title": "Rule of thumb", "body": "Around 3 litres per person per day, for drinking and basic cooking."},
            {"kind": "point", "title": "Do the maths once", "body": "4 people × 3 days × 3 litres = 36 litres. Write the number down."},
            {"kind": "point", "title": "Store it smart", "body": "Sealed bottles, in a cool dark place you can reach without moving furniture."},
            {"kind": "point", "title": "Swap it on a schedule", "body": "Put a date on it. Rotate it into daily use before it expires."},
            {"kind": "cta", "title": "Get the full 72h plan", "body": "Checklist, family comms plan and a 15-minute go-bag list. Pay what you want."},
        ]
    else:
        base["scenes"] = [
            {"text": "How much water for 72 hours?", "seconds": 3.5, "visual": "card"},
            {"text": "Official guides agree: water comes first", "seconds": 3.5, "visual": "card"},
            {"text": "About 3 litres per person per day", "seconds": 3.5, "visual": "card"},
            {"text": "Family of 4 = 36 litres", "seconds": 3.5, "visual": "card"},
            {"text": "Date it. Rotate it.", "seconds": 3, "visual": "card"},
            {"text": "Full 72h checklist — link in bio", "seconds": 3.5, "visual": "card"},
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
        imgs = sorted(p.name for p in folder.glob("slide-*.png"))
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
    topic = pick_topic(app_key) if not sample else {"topic": "How much water a household really needs for 72 hours", "pillar": "value"}
    folder = post_dir(app_key, publish_on)
    if folder.exists():
        print(f"Pomijam: {folder.name} już istnieje")
        return ""
    if sample:
        content, reviews = sample_content(fmt), []
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
    main()
