"""Publikuje zaakceptowane posty (scalone do main), których termin publikacji minął.

Bez zewnętrznego hostingu: zdjęcia Instagram pobiera z publicznego repo (raw.githubusercontent.com),
a filmy wysyłamy prosto do Instagrama (resumable upload). Po publikacji powstaje published.json.

Zmienne środowiskowe:
  IG_USER_ID, IG_ACCESS_TOKEN          — konto Instagram (Graph API)
  GITHUB_REPOSITORY, GITHUB_SHA        — ustawia GitHub Actions (skąd brać linki do zdjęć)
  GRAPH_API_VERSION (domyślnie v23.0), GRAPH_HOST (domyślnie graph.facebook.com)

Użycie: python src/publish.py [--dry-run] [--now]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOT, existing_posts, schedule, write_json  # noqa: E402

API_VER = os.environ.get("GRAPH_API_VERSION") or "v23.0"
HOST = os.environ.get("GRAPH_HOST") or "graph.facebook.com"


# ---------- linki do mediów ----------

def public_url(path) -> str:
    """Publiczny link do pliku w repo, przypięty do konkretnego commita (nie zmieni się po publikacji)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "Przemek-cyber/handy-apps-autopilot")
    ref = os.environ.get("GITHUB_SHA") or "main"
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path.relative_to(ROOT).as_posix()}"
    r = requests.head(url, timeout=30, allow_redirects=True)
    if r.status_code != 200:
        raise RuntimeError(f"Plik niedostępny publicznie ({r.status_code}): {url} — czy repo jest publiczne?")
    return url


# ---------- Graph API ----------

def graph(method: str, path: str, **params) -> dict:
    token = os.environ["IG_ACCESS_TOKEN"]
    url = f"https://{HOST}/{API_VER}/{path}"
    if method == "POST":  # długie podpisy w treści żądania, nie w adresie
        r = requests.post(url, data={**params, "access_token": token}, timeout=120)
    else:
        r = requests.request(method, url, params={**params, "access_token": token}, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Graph API {path}: {r.status_code} {r.text}")
    return r.json()


def upload_video(ig: str, video, **params) -> str:
    """Kontener rolki + wysłanie pliku prosto do Instagrama (bez hostingu)."""
    created = graph("POST", f"{ig}/media", media_type="REELS", upload_type="resumable", **params)
    size = video.stat().st_size
    with open(video, "rb") as f:
        r = requests.post(created["uri"], data=f, timeout=600, headers={
            "Authorization": f"OAuth {os.environ['IG_ACCESS_TOKEN']}", "offset": "0", "file_size": str(size)})
    if r.status_code >= 400:
        raise RuntimeError(f"Wysyłanie filmu: {r.status_code} {r.text}")
    return created["id"]


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
    ig = ig_user_id() if not dry else "<IG_USER_ID>"
    folder = post["_dir"]
    caption = full_caption(post["content"])
    if dry:
        print(f"[dry-run] {folder.name}: {post['format']}, {len(caption)} znaków podpisu")
        return {}

    if post["format"] == "carousel":
        children = []
        for name in post["media"]["images"]:
            url = public_url(folder / name)
            children.append(graph("POST", f"{ig}/media", image_url=url, is_carousel_item="true")["id"])
        for c in children:
            wait_ready(c)
        container = graph("POST", f"{ig}/media", media_type="CAROUSEL", children=",".join(children),
                          caption=caption)["id"]
    else:
        cover = public_url(folder / post["media"]["cover"])
        container = upload_video(ig, folder / post["media"]["video"], cover_url=cover,
                                 caption=caption, share_to_feed="true")
    wait_ready(container)
    media_id = graph("POST", f"{ig}/media_publish", creation_id=container)["id"]
    permalink = graph("GET", media_id, fields="permalink").get("permalink")
    return {"media_id": media_id, "permalink": permalink,
            "published_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def ig_accounts() -> list[dict]:
    """Konta Instagram dostępne dla tokenu (przez strony na Facebooku)."""
    data = graph("GET", "me/accounts", fields="name,instagram_business_account{id,username}").get("data", [])
    return [{"page": d.get("name"), **d["instagram_business_account"]} for d in data if d.get("instagram_business_account")]


def ig_user_id() -> str:
    """IG_USER_ID z sekretu albo — jeśli pusty — wykryty automatycznie z tokenu."""
    if v := os.environ.get("IG_USER_ID"):
        return v
    accounts = ig_accounts()
    if len(accounts) != 1:
        raise RuntimeError(f"Nie da się jednoznacznie wykryć konta IG (znaleziono {len(accounts)}): {accounts}. "
                           "Dodaj sekret IG_USER_ID.")
    return accounts[0]["id"]


def check() -> None:
    """Test połączenia: token, konto IG, limit publikacji. Nic nie publikuje."""
    me = graph("GET", "me", fields="id,name")
    print(f"Token OK: {me.get('name')} ({me.get('id')})")
    for a in ig_accounts():
        print(f"Konto IG: @{a.get('username')} id={a['id']} (strona: {a['page']})")
        print(f"::notice title=Instagram połączony::@{a.get('username')} (id {a['id']}), strona FB: {a['page']}")
    ig = ig_user_id()
    lim = graph("GET", f"{ig}/content_publishing_limit", fields="quota_usage,config")
    print(f"Limit publikacji: {lim}")


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
    ap.add_argument("--check", action="store_true", help="tylko sprawdź połączenie z Instagramem")
    args = ap.parse_args()
    if args.check:
        try:
            check()
        except Exception as e:
            msg = str(e).replace(os.environ.get("IG_ACCESS_TOKEN", "") or "\0", "***").replace("\n", " ")[:900]
            print(f"::error title=Połączenie z Instagramem::{msg}")
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
                print(f"Opublikowano {post['_dir'].name}: {result['permalink']}")
        except Exception as e:  # jeden błędny post nie blokuje pozostałych
            failed += 1
            msg = str(e).replace(os.environ.get("IG_ACCESS_TOKEN", "") or "\0", "***").replace("\n", " ")[:900]
            print(f"BŁĄD {post['_dir'].name}: {msg}", file=sys.stderr)
            print(f"::error title=Publikacja {post['_dir'].name}::{msg}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
