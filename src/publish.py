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
from common import existing_posts, write_json  # noqa: E402

API = os.environ.get("ZERNIO_API_BASE") or "https://zernio.com/api/v1"


# ---------- Zernio API ----------

def api(method: str, path: str, **kwargs) -> requests.Response:
    r = requests.request(method, f"{API}/{path.lstrip('/')}", timeout=120,
                         headers={"Authorization": f"Bearer {os.environ['ZERNIO_API_KEY'].strip()}"}, **kwargs)
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


def upload(path, attempts: int = 4) -> str:
    """Wysyła plik do magazynu Zernio i zwraca jego publiczny adres (z ponawianiem przy chwilowych błędach)."""
    ctype = mimetypes.guess_type(path.name)[0] or {".mp4": "video/mp4"}.get(path.suffix.lower(), "")
    if not ctype:
        raise RuntimeError(f"Nieznany typ pliku: {path.name}")
    size = path.stat().st_size
    last = ""
    for n in range(attempts):
        if n:
            time.sleep(5 * 2 ** n)
        try:
            pre = api("POST", "media/presign", json={"filename": path.name, "contentType": ctype, "size": size}).json()
            with open(path, "rb") as f:
                r = requests.put(pre["uploadUrl"], data=f, headers={"Content-Type": ctype}, timeout=600)
            if r.status_code < 400:
                return pre["publicUrl"]
            last = f"{r.status_code} {r.text[:200]}"
        except requests.RequestException as e:
            last = str(e)[:200]
    raise RuntimeError(f"Wysyłanie {path.name} nie powiodło się po {attempts} próbach: {last}")


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


def _create(post: dict, extra: dict) -> requests.Response:
    """Wysyła media do Zernio i tworzy post (natychmiast albo zaplanowany — zależnie od `extra`)."""
    folder, media = post["_dir"], post["media"]
    platform = {"platform": "instagram", "accountId": account_id(), "platformSpecificData": {}}
    if post["format"] == "carousel":
        items = [{"type": "image", "url": upload(folder / name)} for name in media["images"]]
    else:
        items = [{"type": "video", "url": upload(folder / media["video"])}]
        platform["platformSpecificData"] = {"shareToFeed": True, "instagramThumbnail": upload(folder / media["cover"])}
    return api("POST", "posts", json={"content": full_caption(post["content"]), "mediaItems": items,
                                      "platforms": [platform], **extra})


def schedule_post(post: dict, local_time: str, timezone: str) -> dict:
    """Planuje post w Zernio na `local_time` (RRRR-MM-DDTHH:MM:SS, czas lokalny strefy `timezone`)."""
    r = _create(post, {"scheduledFor": local_time, "timezone": timezone})
    created = r.json().get("post", {})
    if r.status_code == 207 or created.get("status") in {"failed", "partial"}:
        raise RuntimeError(f"Zernio nie zaplanował posta: {_ig_result(created).get('errorMessage') or created}")
    return {"zernio_post_id": created["_id"], "scheduled_for": local_time, "timezone": timezone,
            "status": created.get("status", "scheduled")}


def get_post(post_id: str) -> dict:
    return api("GET", f"posts/{post_id}").json().get("post", {})


def cancel_post(post_id: str) -> None:
    api("DELETE", f"posts/{post_id}")


def publish_post(post: dict, dry: bool) -> dict:
    """Publikacja natychmiastowa (ręczna, awaryjna)."""
    if dry:
        print(f"[dry-run] {post['_dir'].name}: {post['format']}, {len(full_caption(post['content']))} znaków podpisu")
        return {}
    r = _create(post, {"publishNow": True})
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
    raw = os.environ.get("ZERNIO_API_KEY", "")
    k = raw.strip()
    print(f"::notice title=Diagnostyka klucza::początek='{k[:3]}', długość={len(k)}, "
          f"białe znaki={'tak' if raw != k or ' ' in k else 'nie'}, cudzysłowy={'tak' if any(c in k for c in '\"\'') else 'nie'}, "
          f"'Bearer' w środku={'tak' if 'bearer' in k.lower() else 'nie'}, API={API}")
    for a in instagram_accounts():
        print(f"::notice title=Instagram połączony przez Zernio::@{a.get('username')} (accountId {a['_id']})")
    account_id()


def publish_now(name: str, dry: bool = False) -> None:
    """Publikuje post od razu. Jeśli był zaplanowany w Zernio — najpierw zdejmuje go z kolejki,
    a folder przenosi na dzisiejszą datę (zwalnia termin dla nowego posta)."""
    import json
    import shutil
    from zoneinfo import ZoneInfo

    from common import POSTS, schedule
    post = next(p for p in existing_posts() if p["_dir"].name == name)
    if post["_published"]:
        raise RuntimeError(f"{name} jest już opublikowany")
    if dry:
        publish_post(post, True)
        return
    folder = post["_dir"]
    if (folder / "scheduled.json").exists():
        sched = json.loads((folder / "scheduled.json").read_text())
        try:
            cancel_post(sched["zernio_post_id"])
        except RuntimeError as e:
            if "404" not in str(e):
                raise
        (folder / "scheduled.json").unlink()
    result = publish_post(post, False)
    today = dt.datetime.now(ZoneInfo(schedule()["timezone"])).date().isoformat()
    if post["publish_on"] != today:
        target = POSTS / f"{today}-{post['app']}"
        n = 2
        while target.exists():
            target = POSTS / f"{today}-{post['app']}-{n}"
            n += 1
        shutil.move(str(folder), str(target))
        folder = target
        data = json.loads((folder / "post.json").read_text(encoding="utf-8"))
        data["publish_on"] = today
        write_json(folder / "post.json", data)
    write_json(folder / "published.json", result)
    with open("published_now.txt", "a") as f:
        f.write(f"{name}\t{folder.name}\t{result['permalink']}\n")
    print(f"::notice title=Opublikowano {folder.name}::{result['permalink']}")


def _safe(e: Exception) -> str:
    return str(e).replace(os.environ.get("ZERNIO_API_KEY", "") or "\0", "***").replace("\n", " ")[:900]


def main() -> None:
    """Narzędzia ręczne. Zwykła publikacja odbywa się przez planowanie w Zernio (generate.py)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="sprawdź połączenie z Instagramem przez Zernio")
    ap.add_argument("--post", help="folder(y) w posts/ do natychmiastowej publikacji, oddzielone spacją")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        if args.check:
            check()
        elif args.post:
            for name in args.post.split():
                publish_now(name, args.dry_run)
        else:
            ap.print_help()
    except Exception as e:
        print(f"::error title=Publikacja / połączenie::{_safe(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
