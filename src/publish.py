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


def platform_accounts(platform: str) -> list[dict]:
    data = api("GET", "accounts").json()
    accounts = data.get("accounts", []) if isinstance(data, dict) else data
    return [a for a in accounts if a.get("platform") == platform and a.get("isActive", True)]


def instagram_accounts() -> list[dict]:
    return platform_accounts("instagram")


def account_id(platform: str = "instagram") -> str:
    if platform == "instagram" and (v := os.environ.get("ZERNIO_ACCOUNT_ID")):
        return v
    accs = platform_accounts(platform)
    if len(accs) != 1:
        names = [a.get("username") for a in accs]
        raise RuntimeError(f"Oczekiwano jednego konta {platform} w Zernio, jest {len(accs)}: {names}.")
    return accs[0]["_id"]


def platform_for(post: dict) -> str:
    """Gdzie idzie post: pole 'platform' w post.json, inaczej mapowanie apka → platforma z harmonogramu."""
    if post.get("platform"):
        return post["platform"]
    from common import schedule
    return (schedule().get("platforms") or {}).get(post.get("app"), "instagram")


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
    """Wynik publikacji na platformie posta (Instagram albo TikTok — każdy post idzie na jedną)."""
    plats = post.get("platforms", [])
    return next((p for p in plats if p.get("platform") == "instagram"), plats[0] if plats else {})


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
    target = platform_for(post)
    platform = {"platform": target, "accountId": account_id(target), "platformSpecificData": {}}
    if target == "tiktok":
        platform["platformSpecificData"] = {
            "privacyLevel": "PUBLIC_TO_EVERYONE", "allowComment": True, "allowDuet": True, "allowStitch": True,
            "commercialContentType": "brand_organic", "contentPreviewConfirmed": True, "expressConsentGiven": True,
            "videoCoverImageUrl": upload(folder / media["cover"])}
        items = [{"type": "video", "url": upload(folder / media["video"])}]
    elif post["format"] == "carousel":
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


def _expiry_days(acc: dict) -> float | None:
    """Dni do wygaśnięcia tokenu Instagrama (szuka pola z datą wygaśnięcia w danych konta)."""
    def walk(d, prefix=""):
        for k, v in (d.items() if isinstance(d, dict) else []):
            if isinstance(v, dict):
                yield from walk(v, prefix + k + ".")
            elif "expir" in k.lower() and isinstance(v, str):
                yield prefix + k, v
    for key, val in walk(acc):
        try:
            when = dt.datetime.fromisoformat(val.replace("Z", "+00:00"))
            return (when - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400
        except ValueError:
            continue
    return None


def check() -> None:
    """Test połączenia: konto Instagram w Zernio, ważność tokenu, stan zaplanowanych postów. Nic nie publikuje."""
    import json
    current = account_id()
    for a in instagram_accounts():
        days = _expiry_days(a)
        exp = f", token ważny jeszcze {days:.0f} dni" if days is not None else ""
        print(f"::notice title=Instagram połączony przez Zernio::@{a.get('username')} (accountId {a['_id']}){exp}")
        print("Pola konta:", sorted(a.keys()))
        if days is not None and days < 10:
            print(f"::error title=Połączenie Instagrama wygasa za {days:.0f} dni::W Zernio: Connections → Instagram → Disconnect → Connect.")
            sys.exit(1)
    for p in existing_posts():
        if p["_scheduled"] and not (p["_published"] or p["_cancelled"]):
            sched = json.loads((p["_dir"] / "scheduled.json").read_text())
            try:
                post = get_post(sched["zernio_post_id"])
                acc = [x.get("accountId") for x in post.get("platforms", [])]
                acc = [x.get("_id") if isinstance(x, dict) else x for x in acc]
                print(f"::notice title=Zaplanowany {p['_dir'].name}::status {post.get('status')}, konto {acc}, aktualne {current}")
            except Exception as e:
                print(f"::warning title=Zaplanowany {p['_dir'].name}::nie znaleziono w Zernio ({_safe(e)[:200]})")


def profile_id() -> str:
    """Profil Zernio konta Instagram (wymagany przez automaty komentarzy)."""
    accs = instagram_accounts()
    acc = next((a for a in accs if a["_id"] == account_id()), accs[0])
    pid = acc.get("profileId")
    if isinstance(pid, dict):
        pid = pid.get("_id")
    if pid:
        return pid
    profiles = api("GET", "profiles").json()
    profiles = profiles.get("profiles", profiles) if isinstance(profiles, dict) else profiles
    return profiles[0]["_id"]


def ensure_automations() -> None:
    """Automaty komentarz → DM dla każdej apki z dm_keyword (na całe konto). Tworzy brakujące, aktualizuje istniejące."""
    from common import apps
    pid, aid = profile_id(), account_id()
    existing = api("GET", "comment-automations", params={"profileId": pid}).json()
    existing = existing.get("automations", existing.get("data", [])) if isinstance(existing, dict) else existing
    by_name = {a.get("name"): a for a in existing}
    for key, a in apps().items():
        kw = a.get("dm_keyword")
        if not kw or not a.get("link"):
            continue
        name = f"Handy Apps – {kw} → {a['name']}"
        body = {
            "profileId": pid, "accountId": aid, "name": name, "trigger": "comment",
            "keywords": [kw], "matchMode": "word", "typoTolerance": True,
            "dmMessage": a["dm_message"].format(link=a["link"]),
            "commentReply": "Sent! Check your DMs 📩",
            "commentReplyVariations": ["Done — it's in your DMs 📩", "Sent you the link in DM ✅", "Check your inbox 📩"],
            "publicReplyPolicy": "after_dm",
            "isActive": True,
        }
        if name in by_name:
            aut_id = by_name[name].get("id") or by_name[name].get("_id")
            upd = {k: v for k, v in body.items() if k not in ("profileId", "accountId")}
            api("PATCH", f"comment-automations/{aut_id}", json=upd)
            print(f"::notice title=Zaktualizowano automat {kw}::{name} ({aut_id})")
        else:
            r = api("POST", "comment-automations", json=body).json()
            aut = r.get("automation", {})
            print(f"::notice title=Utworzono automat {kw}::{name} ({aut.get('id')})")


def automation_logs() -> None:
    """Podgląd automatów DM; błąd (mail z GitHuba), jeśli w ostatnich 2 dniach któraś wiadomość się nie wysłała."""
    failures: list[str] = []
    pid = profile_id()
    existing = api("GET", "comment-automations", params={"profileId": pid}).json()
    existing = existing.get("automations", existing.get("data", [])) if isinstance(existing, dict) else existing
    for a in existing:
        aut_id = a.get("id") or a.get("_id")
        logs = api("GET", f"comment-automations/{aut_id}/logs").json()
        items = logs.get("logs", logs.get("data", [])) if isinstance(logs, dict) else logs
        print(f"::notice title=Automat {a.get('name')}::aktywny={a.get('isActive')}, słowa={a.get('keywords')}, wpisów w logu={len(items)}")
        for it in items[:5]:
            print(f"::notice title=Log {a.get('name')}::{str({k: it.get(k) for k in ('status', 'commentText', 'createdAt', 'error', 'reason') if k in it})[:400]}")
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
        for it in items:
            st = str(it.get("status", "")).lower()
            when = str(it.get("createdAt", ""))
            try:
                recent = dt.datetime.fromisoformat(when.replace("Z", "+00:00")) >= since
            except ValueError:
                recent = True
            if recent and ("fail" in st or "error" in st):
                failures.append(f"{a.get('name')}: {st} {it.get('error') or it.get('reason') or ''}")
    if failures:
        for f in failures[:5]:
            print(f"::error title=Automat DM nie wysłał wiadomości::{str(f)[:400]}")
        sys.exit(1)


def reschedule() -> None:
    """Planuje ponownie wszystkie zaplanowane (nieopublikowane) posty — np. po ponownym połączeniu konta."""
    import json
    for p in existing_posts():
        if not p["_scheduled"] or p["_published"] or p["_cancelled"]:
            continue
        folder = p["_dir"]
        sched = json.loads((folder / "scheduled.json").read_text())
        when = sched["scheduled_for"]
        if os.environ.get("RETIME"):  # nowa godzina z harmonogramu (config/schedule.yaml → publish_times)
            from common import schedule
            if dt.datetime.fromisoformat(when) <= dt.datetime.now() + dt.timedelta(hours=3):
                continue  # dzisiejszy/już wychodzący post zostawiamy w spokoju
            when = f"{when[:10]}T{schedule()['publish_times'][p['app']]}:00"
        try:
            cancel_post(sched["zernio_post_id"])
        except RuntimeError:
            pass  # post mógł już zniknąć razem z odłączonym kontem
        new = schedule_post(p, when, sched["timezone"])
        write_json(folder / "scheduled.json", new)
        print(f"::notice title=Zaplanowano ponownie {folder.name}::{new['scheduled_for']} (id {new['zernio_post_id']})")


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


def debug_post(post_id: str) -> None:
    """Diagnostyka: szczegóły posta w Zernio i konta TikTok (bez tokenów)."""
    import json
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if not any(w in k.lower() for w in ("token", "secret"))}
        if isinstance(o, list):
            return [clean(x) for x in o]
        return o
    post = clean(get_post(post_id))
    for pl in post.get("platforms", []):
        print(f"::notice title=Platforma posta {post_id}::{json.dumps(pl)[:3500]}")
    print(f"::notice title=Post (bez platform)::{json.dumps({k: v for k, v in post.items() if k not in ('platforms', 'content')})[:3500]}")
    for a in platform_accounts("tiktok"):
        print(f"::notice title=Konto TikTok @{a.get('username')}::{json.dumps(clean(a))[:3500]}")
        for path in (f"accounts/{a['_id']}/tiktok/creator-info", f"accounts/{a['_id']}/tiktok/creator_info"):
            try:
                print(f"::notice title={path}::{json.dumps(api('GET', path).json())[:3500]}")
                break
            except RuntimeError as e:
                print(f"::warning title={path}::{str(e)[:300]}")
        try:
            r = api("GET", "analytics", params={"postId": post_id}).json()
            print(f"::notice title=Analytics {post_id}::{json.dumps(r)[:3500]}")
        except RuntimeError as e:
            print(f"::warning title=Analytics::{str(e)[:300]}")


def main() -> None:
    """Narzędzia ręczne. Zwykła publikacja odbywa się przez planowanie w Zernio (generate.py)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="sprawdź połączenie z Instagramem przez Zernio")
    ap.add_argument("--post", help="folder(y) w posts/ do natychmiastowej publikacji, oddzielone spacją")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reschedule", action="store_true", help="zaplanuj ponownie wszystkie zaplanowane posty")
    ap.add_argument("--automations", action="store_true", help="utwórz / zaktualizuj automaty komentarz → DM")
    ap.add_argument("--automation-logs", action="store_true", help="pokaż ostatnie zdarzenia automatów DM")
    ap.add_argument("--debug-post", help="diagnostyka posta Zernio (id)")
    args = ap.parse_args()
    if args.debug_post:
        return debug_post(args.debug_post)
    try:
        if args.automations:
            ensure_automations()
        elif args.automation_logs:
            automation_logs()
        elif args.reschedule:
            reschedule()
        elif args.check:
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
