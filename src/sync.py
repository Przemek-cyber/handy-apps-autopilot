"""Po godzinie publikacji sprawdza w Zernio, czy zaplanowane posty wyszły.

Opublikowany → published.json z linkiem do posta. Błąd → adnotacja ::error (workflow kończy się błędem,
GitHub wysyła maila). Wynik do zamknięcia zgłoszeń: plik sync_results.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOT, existing_posts, write_json  # noqa: E402
from publish import _ig_result, _safe, get_post  # noqa: E402

GRACE = dt.timedelta(minutes=20)


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    results, failed = [], 0
    for p in existing_posts():
        if p["_published"] or p["_cancelled"] or not p["_scheduled"]:
            continue
        sched = json.loads((p["_dir"] / "scheduled.json").read_text())
        when = dt.datetime.fromisoformat(sched["scheduled_for"]).replace(tzinfo=ZoneInfo(sched["timezone"]))
        if now < when + GRACE:
            continue
        name = p["_dir"].name
        try:
            post = get_post(sched["zernio_post_id"])
        except Exception as e:
            failed += 1
            print(f"::error title=Nie mogę sprawdzić {name}::{_safe(e)}")
            continue
        ig = _ig_result(post)
        if post.get("status") == "published" or ig.get("status") == "published":
            link = ig.get("platformPostUrl")
            write_json(p["_dir"] / "published.json", {"zernio_post_id": sched["zernio_post_id"], "permalink": link,
                                                      "published_at": now.isoformat(timespec="seconds")})
            results.append({"post": name, "status": "published", "link": link})
            print(f"::notice title=Opublikowano {name}::{link}")
        elif post.get("status") in {"failed", "partial"} or ig.get("status") == "failed":
            failed += 1
            msg = ig.get("errorMessage") or post.get("status")
            results.append({"post": name, "status": "failed", "error": msg})
            print(f"::error title=Publikacja {name} nie powiodła się::{msg}")
        else:
            print(f"::warning title={name} jeszcze nie opublikowany::status {post.get('status')}")
    (ROOT / "sync_results.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
