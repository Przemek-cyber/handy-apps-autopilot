"""Reakcja na Twój komentarz pod zgłoszeniem posta (GitHub Issue).

Komentarz ze słowem akceptacji (config/schedule.yaml → approve_words) niczego nie zmienia.
Każdy inny komentarz wstrzymuje post: usuwa go z kolejki Zernio i zapisuje cancelled.json.

Zmienne środowiskowe (ustawia workflow): ISSUE_BODY, COMMENT_BODY, ZERNIO_API_KEY.
Wynik: plik reply.md (odpowiedź do zgłoszenia) i GITHUB_OUTPUT action=approved|cancelled|too_late|none.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOT, schedule, write_json  # noqa: E402
from publish import _safe, cancel_post, get_post  # noqa: E402


def out(action: str, reply: str) -> None:
    (ROOT / "reply.md").write_text(reply + "\n", encoding="utf-8")
    if o := os.environ.get("GITHUB_OUTPUT"):
        with open(o, "a") as f:
            f.write(f"action={action}\n")
    print(f"{action}: {reply}")


def main() -> None:
    m = re.search(r"<!-- post: (posts/[\w.-]+) -->", os.environ.get("ISSUE_BODY", ""))
    if not m:
        return out("none", "Nie znalazłem posta powiązanego z tym zgłoszeniem.")
    folder = ROOT / m.group(1)
    comment = os.environ.get("COMMENT_BODY", "").strip()
    words = [w.lower() for w in schedule().get("approve_words", [])]
    if comment.lower().strip(" .!") in words:
        return out("approved", "👍 OK — post wychodzi zgodnie z planem.")

    if (folder / "cancelled.json").exists():
        return out("none", "Ten post jest już wstrzymany.")
    if (folder / "published.json").exists():
        return out("too_late", "Ten post jest już opublikowany — nie da się go wstrzymać. "
                               "Jeśli trzeba, usuń go ręcznie w aplikacji Instagram.")
    sched = json.loads((folder / "scheduled.json").read_text()) if (folder / "scheduled.json").exists() else None
    if not sched:
        return out("none", "Ten post nie był zaplanowany w Zernio — nic do wstrzymania.")
    try:
        status = get_post(sched["zernio_post_id"]).get("status")
        if status == "published":
            return out("too_late", "Za późno — post właśnie się opublikował. Usuń go ręcznie w aplikacji Instagram, jeśli trzeba.")
        cancel_post(sched["zernio_post_id"])
    except Exception as e:
        return out("none", f"⚠️ Nie udało się wstrzymać posta: {_safe(e)}")
    write_json(folder / "cancelled.json", {"cancelled_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                           "comment": comment[:500]})
    out("cancelled", "⏸️ Wstrzymane — post usunięty z kolejki i **nie zostanie opublikowany**. "
                     "Temat wraca do puli. Chcesz nową wersję? Uruchom „Generuj posty” ręcznie dla tej apki i daty.")


if __name__ == "__main__":
    main()
