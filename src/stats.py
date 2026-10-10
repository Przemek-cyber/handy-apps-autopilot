"""Cotygodniowy raport statystyk opublikowanych postów -> reports/stats.md (+ podsumowanie w GitHub Actions)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOT, apps, existing_posts  # noqa: E402
from publish import graph  # noqa: E402

METRICS = ["reach", "views", "likes", "comments", "saved", "shares"]


def insights(media_id: str) -> dict:
    out = {}
    for m in METRICS:  # pojedynczo: niektóre metryki nie istnieją dla danego typu posta
        try:
            data = graph("GET", f"{media_id}/insights", metric=m).get("data", [])
            out[m] = data[0]["values"][0]["value"] if data else None
        except Exception:
            out[m] = None
    return out


def main() -> None:
    rows = []
    for p in existing_posts():
        if not p["_published"]:
            continue
        pub = json.loads((p["_dir"] / "published.json").read_text())
        rows.append((p, pub, insights(pub["media_id"])))
    rows.sort(key=lambda r: r[0]["publish_on"], reverse=True)

    def fmt(v):
        return "–" if v is None else f"{v:,}".replace(",", " ")

    lines = [f"# Statystyki Handy Apps · {dt.date.today().isoformat()}", "",
             "| Data | Apka | Format | Temat | Zasięg | Wyśw. | Polub. | Kom. | Zapis. | Udost. |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for p, pub, s in rows:
        topic = f"[{p['topic']}]({pub.get('permalink', '')})"
        lines.append(f"| {p['publish_on']} | {apps()[p['app']]['name']} | {p['format']} | {topic} | "
                     + " | ".join(fmt(s[m]) for m in METRICS) + " |")

    # podsumowanie: średni zasięg wg apki i formatu
    lines += ["", "## Średni zasięg", "", "| Apka | Format | Postów | Śr. zasięg |", "|---|---|---|---|"]
    groups: dict[tuple, list[int]] = {}
    for p, _, s in rows:
        if s["reach"] is not None:
            groups.setdefault((p["app"], p["format"]), []).append(s["reach"])
    for (a, f), vals in sorted(groups.items()):
        lines.append(f"| {apps()[a]['name']} | {f} | {len(vals)} | {round(sum(vals) / len(vals))} |")

    report = "\n".join(lines) + "\n"
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "stats.md").write_text(report, encoding="utf-8")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(report)
    print(report)


if __name__ == "__main__":
    main()
