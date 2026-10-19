"""Przenosi gotowe posty na inną platformę (np. stare rolki BikepackPilot z Instagrama na TikToka).

Dla każdego posta: nowy folder na dany dzień, podpis z CTA dla nowej platformy, rolka renderowana ponownie
(ten sam utwór, nowa plakietka na końcu, np. "bikepackpilot.com"), zaplanowanie w Zernio.

Użycie: python src/repost.py --platform tiktok --start 2026-10-19 --first-time 19:00 POST [POST ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from common import POSTS, apps, post_dir, schedule, write_json  # noqa: E402
from render import render_reel  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def new_caption(caption: str, app_key: str) -> str:
    """Usuwa stare wezwanie do działania (komentarz/DM/link in bio) i dodaje CTA apki."""
    paras = [p for p in caption.strip().split("\n\n") if p.strip()]
    if paras and re.search(r"link in bio|comment|\bDM\b", paras[-1], re.I):
        paras = paras[:-1]
    return "\n\n".join(paras + [apps()[app_key]["cta"]])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("posts", nargs="+")
    ap.add_argument("--platform", default="tiktok")
    ap.add_argument("--start", required=True, help="data pierwszej publikacji RRRR-MM-DD")
    ap.add_argument("--first-time", help="godzina pierwszej publikacji HH:MM (domyślnie wg harmonogramu)")
    ap.add_argument("--no-schedule", action="store_true")
    args = ap.parse_args()

    sched = schedule()
    tz = sched["timezone"]
    start = dt.date.fromisoformat(args.start)
    created, failed = [], []
    for i, name in enumerate(args.posts):
        src = POSTS / name
        try:
            post = json.loads((src / "post.json").read_text())
            app_key = post["app"]
            day = start + dt.timedelta(days=i)
            folder = post_dir(app_key, day)
            if folder.exists():
                folder = folder.with_name(f"{folder.name}-{args.platform}")
            if folder.exists():
                print(f"::notice title={folder.name}::już istnieje — pomijam")
                continue
            folder.mkdir(parents=True)
            content = {**post["content"], "caption": new_caption(post["content"]["caption"], app_key)}
            track = ROOT / "assets" / "music" / post["media"]["music"]
            r = render_reel(app_key, content, folder, track)
            new = {**{k: v for k, v in post.items() if k not in ("qa",)},
                   "platform": args.platform, "reposted_from": name, "publish_on": day.isoformat(),
                   "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "content": content,
                   "media": {"video": r["video"].name, "cover": r["cover"].name, "duration": r["duration"],
                             "music": r["music"]},
                   "qa": []}
            write_json(folder / "post.json", new)
            new["_dir"] = folder
            sched_info = None
            if not args.no_schedule:
                from publish import schedule_post
                hhmm = args.first_time if (i == 0 and args.first_time) else sched["publish_times"][app_key]
                sched_info = schedule_post(new, f"{day.isoformat()}T{hhmm}:00", tz)
                write_json(folder / "scheduled.json", sched_info)
            from generate import issue_body
            (folder / "issue_body.md").write_text(
                f"♻️ Przeniesione z {name} na {args.platform}.\n\n" + issue_body(new, folder, sched_info), encoding="utf-8")
            created.append(folder.name)
            print(f"::notice title=Gotowe: {folder.name}::{args.platform}"
                  + (f" · {sched_info['scheduled_for']}" if sched_info else ""))
        except Exception as e:
            failed.append(name)
            print(f"::error title=Przeniesienie {name} nie powiodło się::{type(e).__name__}: {str(e)[:800]}")

    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"created={' '.join(created)}\n")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
