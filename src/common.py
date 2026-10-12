"""Wspólne funkcje: ścieżki, konfiguracja, harmonogram, historia postów."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
POSTS = ROOT / "posts"
ASSETS = ROOT / "assets"
TEMPLATES = ROOT / "templates"


def load_yaml(name: str) -> dict:
    with open(CONFIG / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def brand() -> dict:
    return load_yaml("brand.yaml")


def apps() -> dict:
    return load_yaml("apps.yaml")


def topics() -> dict:
    return load_yaml("topics.yaml")


def schedule() -> dict:
    return load_yaml("schedule.yaml")


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def post_dir(app: str, publish_on: dt.date) -> Path:
    return POSTS / f"{publish_on.isoformat()}-{app}"


def existing_posts(app: str | None = None) -> list[dict]:
    """Wszystkie posty w repo (przyjęte), najstarsze pierwsze."""
    out = []
    if not POSTS.exists():
        return out
    for d in sorted(POSTS.iterdir()):
        pj = d / "post.json"
        if not pj.exists():
            continue
        data = json.loads(pj.read_text(encoding="utf-8"))
        if app and data.get("app") != app:
            continue
        data["_dir"] = d
        data["_published"] = (d / "published.json").exists()
        data["_scheduled"] = (d / "scheduled.json").exists()
        data["_cancelled"] = (d / "cancelled.json").exists()
        out.append(data)
    return out


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
