"""Publikuje zaakceptowane posty (scalone do main), których termin publikacji minął — przez Zernio.

Zernio ma własną, zatwierdzoną przez Metę aplikację; konto Instagram jest połączone w panelu Zernio
(logowanie Instagramem, bez Facebooka). Pliki wysyłamy do Zernio (presign + PUT), więc repo nie musi być publiczne.
Po publikacji powstaje posts/<post>/published.json.

Zmienne środowiskowe:
  ZERNIO_API_KEY       — klucz API (sk_...)
  ZERNIO_ACCOUNT_ID    — opcjonalnie; domyślnie wykrywane jedyne aktywne konto Instagram

Użycie: python src/publish.py [--dry-run] [--now] [--check]
"""
from __future__ import annotations

import argparse
import datetime as dt
import mimetypes
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from common import existing_posts, schedule, write_json  # noqa: E402

API = os.environ.get("ZERNIO_API_BASE") or "https://zernio.com/api/v1"


# ---------- Zernio API ----------

def api(method: str, path: str, **kwargs) -> requests.Response:
    r = requests.request(method, f"{API}/{path.lstrip('/')}", timeout=120,
                         headers={"Authorization": f"Bearer {os.environ['ZERNIO_API_KEY']}"}, **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(f"Zernio {method} /{path}: {r.status_code} {r.text[:600]}")
    return r


def instagram_accounts() -> list[dict]:
    data = api("GET", "accounts").json()
    accounts = data.get("accounts", []) if isinstance(data, dict) else data
    return [a for a in accounts if a.get("platform") == "instagram" and a.get("isActive", True)]


def account_id() -> str:
    if v := os.environ.get("ZERNIO_ACCOUNT_ID"):
        return v
    accs = instagram_accounts()
    if len(accs) != 1:
        names = [a.get("username") for a in accs]
        raise RuntimeError(f"Oczekiwano jednego konta Instagram w Zernio, jest {len(accs)}: {names}. "
                           "Ustaw sekret ZERNIO_ACCOUNT_ID.")
    return accs[0]["_id"]


def upload(path) -> str:
    """Wysyła plik do magazynu Zernio i zwraca jego publiczny adres."""
    ctype = mimetypes.guess_type(path.name)[0] or {".mp4": "video/mp4"}.get(path.suffix.lower(), "")
    if not ctype:
        raise RuntimeError(f"Nieznany typ pliku: {path.name}")
    size = path.stat().st_size
    pre = api("POST", "media/presign", json={"filename": path.name, "contentType": ctype, "size": size}).json()
    with open(path, "rb") as f:
        r = requests.put(pre["uploadUrl"], data=f, headers={"Content-Type": ctype}, timeout=600)
    if r.status_code >= 400:
        raise RuntimeError(f"Wysyłanie {path.name}: {r.status_code} {r.text[:300]}")
    return pre["publicUrl"]


def full_caption(content: dict) -> str:
    tags = " ".join(content.get("hashtags", [])[:30])
    return f"{content['caption'].strip()}\n\n{tags}"[:2200]


def _ig_result(post: dict) -> dict:
    return next((p for p in post.get("platforms", []) if p.get("platform") == "instagram"), {})


def wait_published(post_id: str, timeout_s: int = 900) -> dict:
    start = time.time()
    while time.time() - start < timeout_s:
        post = api("GET", f"posts/{post_id}").json().get("post", {})
        status, ig = post.get("status"), _ig_result(post)
        if status == "published" or ig.get("status") == "published":
            return post
        if status in {"failed", "partial"} or ig.get("status") == "failed":
            raise RuntimeError(f"Instagram odrzucił post: {ig.get('errorMessage') or post}")
        time.sleep(15)
    raise TimeoutError(f"Post {post_id} nie opublikował się w {timeout_s} s")


def publish_post(post: dict, dry: bool) -> dict:
    folder, media = post["_dir"], post["media"]
    caption = full_caption(post["content"])
    if dry:
        print(f"[dry-run] {folder.name}: {post['format']}, {len(caption)} znaków podpisu")
        return {}

    platform = {"platform": "instagram", "accountId": account_id(), "platformSpecificData": {}}
    if post["format"] == "carousel":
        items = [{"type": "image", "url": upload(folder / name)} for name in media["images"]]
    else:
        items = [{"type": "video", "url": upload(folder / media["video"])}]
        platform["platformSpecificData"] = {"shareToFeed": True, "instagramThumbnail": upload(folder / media["cover"])}

    r = api("POST", "posts", json={"content": caption, "mediaItems": items, "platforms": [platform], "publishNow": True})
    created = r.json().get("post", {})
    ig = _ig_result(created)
    if r.status_code == 207 or created.get("status") in {"failed", "partial"} or ig.get("status") == "failed":
        raise RuntimeError(f"Instagram odrzucił post: {ig.get('errorMessage') or created}")
    if created.get("status") != "published":
        created = wait_published(created["_id"])
    return {"zernio_post_id": created["_id"], "permalink": _ig_result(created).get("platformPostUrl"),
            "published_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def check() -> None:
    """Test połączenia: klucz API i konto Instagram w Zernio. Nic nie publikuje."""
    for a in instagram_accounts():
        print(f"::notice title=Instagram połączony przez Zernio::@{a.get('username')} (accountId {a['_id']})")
    account_id()


# ---------- harmonogram ----------

def due(post: dict, now: dt.datetime, force_now: bool) -> bool:
    if post["_published"]:
        return False
    day = dt.date.fromisoformat(post["publish_on"])
    if day < now.date():
        return True
    return day == now.date() and (force_now or now.hour >= schedule()["publish_hour_utc"])


def _safe(e: Exception) -> str:
    return str(e).replace(os.environ.get("ZERNIO_API_KEY", "") or "\0", "***").replace("\n", " ")[:900]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", action="store_true", help="publikuj dzisiejsze posty bez czekania na godzinę")
    ap.add_argument("--check", action="store_true", help="tylko sprawdź połączenie z Instagramem")
    args = ap.parse_args()
    if args.check:
        try:
            check()
        except Exception as e:
            print(f"::error title=Połączenie z Instagramem::{_safe(e)}")
            sys.exit(1)
        return
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
                print(f"::notice title=Opublikowano {post['_dir'].name}::{result['permalink']}")
        except Exception as e:  # jeden błędny post nie blokuje pozostałych
            failed += 1
            print(f"::error title=Publikacja {post['_dir'].name}::{_safe(e)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
