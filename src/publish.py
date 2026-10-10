"""Publikuje zaakceptowane posty (scalone do main), których termin publikacji minął.

Media trafiają najpierw do Cloudinary (Instagram pobiera je z publicznego adresu),
potem przez Meta Graph API na Instagram. Po publikacji powstaje published.json.

Zmienne środowiskowe:
  IG_USER_ID, IG_ACCESS_TOKEN                         — konto Instagram (Graph API)
  CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
  GRAPH_API_VERSION (domyślnie v23.0), GRAPH_HOST (domyślnie graph.facebook.com)

Użycie: python src/publish.py [--dry-run] [--now]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from common import existing_posts, schedule, write_json  # noqa: E402

API_VER = os.environ.get("GRAPH_API_VERSION", "v23.0")
HOST = os.environ.get("GRAPH_HOST", "graph.facebook.com")


# ---------- Cloudinary ----------

def cloudinary_upload(path, folder: str, resource: str) -> str:
    cloud = os.environ["CLOUDINARY_CLOUD_NAME"]
    key, secret = os.environ["CLOUDINARY_API_KEY"], os.environ["CLOUDINARY_API_SECRET"]
    params = {"folder": folder, "public_id": path.stem, "timestamp": str(int(time.time()))}
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params)) + secret
    params["signature"] = hashlib.sha1(to_sign.encode()).hexdigest()
    params["api_key"] = key
    with open(path, "rb") as f:
        r = requests.post(f"https://api.cloudinary.com/v1_1/{cloud}/{resource}/upload",
                          data=params, files={"file": f}, timeout=300)
    r.raise_for_status()
    return r.json()["secure_url"]


def as_jpeg(url: str) -> str:
    """Instagram przyjmuje tylko JPEG — Cloudinary konwertuje po zmianie rozszerzenia."""
    return url.rsplit(".", 1)[0] + ".jpg"


# ---------- Graph API ----------

def graph(method: str, path: str, **params) -> dict:
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    r = requests.request(method, f"https://{HOST}/{API_VER}/{path}", params=params, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Graph API {path}: {r.status_code} {r.text}")
    return r.json()


def wait_ready(container_id: str, timeout_s: int = 900) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        status = graph("GET", container_id, fields="status_code,status").get("status_code")
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Kontener {container_id}: {status}")
        time.sleep(10)
    raise TimeoutError(f"Kontener {container_id} nie był gotowy w {timeout_s} s")


def full_caption(content: dict) -> str:
    tags = " ".join(content.get("hashtags", [])[:30])
    return f"{content['caption'].strip()}\n\n{tags}"[:2200]


def publish_post(post: dict, dry: bool) -> dict:
    ig = os.environ.get("IG_USER_ID", "<IG_USER_ID>")
    folder = post["_dir"]
    cfolder = f"handy-apps/{folder.name}"
    caption = full_caption(post["content"])
    if dry:
        print(f"[dry-run] {folder.name}: {post['format']}, {len(caption)} znaków podpisu")
        return {}

    if post["format"] == "carousel":
        children = []
        for name in post["media"]["images"]:
            url = as_jpeg(cloudinary_upload(folder / name, cfolder, "image"))
            children.append(graph("POST", f"{ig}/media", image_url=url, is_carousel_item="true")["id"])
        for c in children:
            wait_ready(c)
        container = graph("POST", f"{ig}/media", media_type="CAROUSEL", children=",".join(children),
                          caption=caption)["id"]
    else:
        video = cloudinary_upload(folder / post["media"]["video"], cfolder, "video")
        cover = as_jpeg(cloudinary_upload(folder / post["media"]["cover"], cfolder, "image"))
        container = graph("POST", f"{ig}/media", media_type="REELS", video_url=video, cover_url=cover,
                          caption=caption, share_to_feed="true")["id"]
    wait_ready(container)
    media_id = graph("POST", f"{ig}/media_publish", creation_id=container)["id"]
    permalink = graph("GET", media_id, fields="permalink").get("permalink")
    return {"media_id": media_id, "permalink": permalink,
            "published_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def due(post: dict, now: dt.datetime, force_now: bool) -> bool:
    if post["_published"]:
        return False
    day = dt.date.fromisoformat(post["publish_on"])
    if day < now.date():
        return True
    return day == now.date() and (force_now or now.hour >= schedule()["publish_hour_utc"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", action="store_true", help="publikuj dzisiejsze posty bez czekania na godzinę")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    todo = [p for p in existing_posts() if due(p, now, args.now)]
    if not todo:
        print("Brak postów do publikacji.")
        return
    failed = 0
    for post in todo:
        try:
            result = publish_post(post, args.dry_run)
            if result:
                write_json(post["_dir"] / "published.json", result)
                print(f"Opublikowano {post['_dir'].name}: {result['permalink']}")
        except Exception as e:  # jeden błędny post nie blokuje pozostałych
            failed += 1
            print(f"BŁĄD {post['_dir'].name}: {e}", file=sys.stderr)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
