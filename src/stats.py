"""Cotygodniowy raport statystyk opublikowanych postów -> reports/stats.md (+ podsumowanie w GitHub Actions)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOT, apps, existing_posts  # noqa: E402
from publish import api  # noqa: E402

METRICS = ["reach", "views", "likes", "comments", "saves", "shares"]


def insights(zernio_post_id: str) -> dict:
    """Metryki posta z Zernio (GET /v1/analytics?postId=...)."""
    try:
        data = api("GET", "analytics", params={"postId": zernio_post_id}).json()
        m = data.get("analytics") or {}
    except Exception as e:
        print(f"::warning title=Statystyki {zernio_post_id}::{str(e)[:300]}")
        m = {}
    return {k: m.get(k) for k in METRICS}


def main() -> None:
    rows = []
    for p in existing_posts():
        if not p["_published"]:
            continue
        pub = json.loads((p["_dir"] / "published.json").read_text())
        rows.append((p, pub, insights(pub["zernio_post_id"])))
    rows.sort(key=lambda r: r[0]["publish_on"], reverse=True)

    def fmt(v):
        return "–" if v is None else f"{v:,}".replace(",", " ")

    lines = [f"# Statystyki Handy Apps · {dt.date.today().isoformat()}", "",
             "| Data | Apka | Styl otwarcia | Temat | Zasięg | Wyśw. | Polub. | Kom. | Zapis. | Udost. |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for p, pub, s in rows:
        topic = f"[{p['topic']}]({pub.get('permalink', '')})"
        lines.append(f"| {p['publish_on']} | {apps()[p['app']]['name']} | {p.get('hook_style') or '–'} | {topic} | "
                     + " | ".join(fmt(s[m]) for m in METRICS) + " |")

    # podsumowanie: średni zasięg wg apki i formatu
    def summary(title: str, key) -> None:
        lines.extend(["", f"## Średni zasięg wg: {title}", "", f"| {title} | Postów | Śr. zasięg | Śr. reakcje |",
                      "|---|---|---|---|"])
        groups: dict[str, list[tuple[int, int]]] = {}
        for p, _, s in rows:
            if s["reach"] is not None:
                react = sum(s[m] or 0 for m in ("likes", "comments", "saves", "shares"))
                groups.setdefault(key(p), []).append((s["reach"], react))
        for g, vals in sorted(groups.items()):
            lines.append(f"| {g} | {len(vals)} | {round(sum(v[0] for v in vals) / len(vals))} | "
                         f"{sum(v[1] for v in vals) / len(vals):.1f} |")

    summary("apka", lambda p: apps()[p["app"]]["name"])
    summary("styl otwarcia", lambda p: p.get("hook_style") or "(przed testem)")
    summary("forma", lambda p: "pokaz slajdów" if p.get("style") == "slideshow" else "rolka")

    report = "\n".join(lines) + "\n"
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "stats.md").write_text(report, encoding="utf-8")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(report)
    print(report)


if __name__ == "__main__":
    main()
