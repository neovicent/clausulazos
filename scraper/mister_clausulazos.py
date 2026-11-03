#!/usr/bin/env python3
"""Registra los clausulazos de una liga Mister y los guarda en un JSON.

Hace lo mismo que el navegador al abrir el feed y bajar para cargar más:
1. Pide /feed con tu sesión y lee de la página el token de acceso (_FG_cfg.auth).
2. Pide /ajax/feed de 20 en 20 enviando ese token, y se queda con los fichajes
   de tipo "clause".

Uso normal (con tu sesión de Mister en la variable MISTER_COOKIE):
    MISTER_COOKIE="..." python scraper/mister_clausulazos.py

Forzar la lectura de todo el histórico del feed:
    MISTER_COOKIE="..." python scraper/mister_clausulazos.py --full
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "data" / "clausulazos.json"
HOST = "mister.mundodeportivo.com"
ORIGIN = f"https://{HOST}"
FEED_URL = f"{ORIGIN}/feed"
AJAX_URL = f"{ORIGIN}/ajax/feed"
LEAGUE_NAME = os.environ.get("MISTER_LEAGUE_NAME", "Los liguis 2.0")
SITE_TZ = ZoneInfo("Europe/Madrid")  # las fechas del feed vienen en hora de España
PAGE_SIZE = 20
MAX_PAGES = 500          # tope de seguridad: 10.000 entradas del feed
PAUSE_SECONDS = 0.5      # pausa entre páginas para no saturar el servicio
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class FeedError(Exception):
    """Problema al leer el feed (cookie caducada, liga equivocada, formato inesperado...)."""


def same_text(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


# --- Acceso a Mister ----------------------------------------------------------

def extract_cfg(html: str) -> dict:
    """Lee el objeto `var _FG_cfg = {...};` que la página trae incrustado."""
    m = re.search(r"var\s+_FG_cfg\s*=\s*", html)
    if not m:
        raise FeedError("No se encontró la configuración de la página (_FG_cfg): "
                        "lo más probable es que la cookie haya caducado o la sesión esté cerrada.")
    try:
        cfg, _ = json.JSONDecoder().raw_decode(html[m.end():])
    except ValueError:
        raise FeedError("No se pudo leer la configuración de la página (_FG_cfg).")
    if not isinstance(cfg, dict) or not cfg.get("auth"):
        raise FeedError("La página no trae el token de acceso (auth): ¿sesión cerrada?")
    return cfg


def check_league(cfg: dict, expected: str) -> int | None:
    """Comprueba que la liga activa de la sesión es la esperada. Devuelve el id de la comunidad."""
    user = cfg.get("user") or {}
    active = user.get("community")
    if active and not same_text(str(active), expected):
        raise FeedError(f"La liga activa de tu sesión es «{active}», no «{expected}». "
                        f"Cambia de liga en Mister y copia de nuevo la cookie, "
                        f"o define MISTER_LEAGUE_NAME con el nombre correcto.")
    community = ((cfg.get("context") or {}).get("community") or {}).get("id") or user.get("id_community")
    return int(community) if community else None


def make_fetcher(cookie: str) -> tuple[Callable[[int], list[dict]], dict]:
    """Abre la sesión, lee el token de /feed y devuelve (función_que_pide_páginas, configuración)."""
    session = requests.Session()
    for part in cookie.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            session.cookies.set(name, value.strip(), domain=HOST)

    page = session.get(
        FEED_URL,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9",
                 "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        timeout=30,
    )
    page.raise_for_status()
    cfg = extract_cfg(page.text)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "es-ES,es;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "X-Auth": str(cfg["auth"]),
        "Origin": ORIGIN,
        "Referer": FEED_URL,
    }

    def fetch_page(offset: int) -> list[dict]:
        resp = session.post(
            AJAX_URL,
            data={"end": "false", "loading": "true", "offset": offset, "cardsPerPage": PAGE_SIZE},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            raise FeedError("La respuesta del feed no es JSON: lo más probable es que la sesión haya caducado.")
        if body.get("status") != "ok":
            raise FeedError(f"Mister respondió status={body.get('status')!r}: ¿sesión caducada?")
        return body.get("data") or []

    return fetch_page, cfg


# --- Lectura del feed ---------------------------------------------------------

def to_utc_iso(created: str | None) -> str | None:
    """'2026-10-12 20:02:24' (hora de España) -> '2026-10-12T18:02:24+00:00'."""
    if not created:
        return None
    local = datetime.strptime(created, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SITE_TZ)
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_items(items: list[dict], community_id: int | None = None) -> list[dict]:
    """Extrae los clausulazos de una página del feed."""
    events: list[dict] = []
    for item in items:
        if item.get("category") != "transfer" or not isinstance(item.get("data"), list):
            continue
        item_community = item.get("id_community")
        if community_id and item_community and int(item_community) != community_id:
            continue  # fichaje de otra liga
        for t in item["data"]:
            if not isinstance(t, dict) or t.get("type") != "clause":
                continue
            try:
                events.append({
                    "id": f"t{int(t['id_transfer'])}",
                    "player_id": str(t["id"]),
                    "player": str(t["name"]).strip(),
                    "from_id": str(t["id_uc_from"]),
                    "from": str(t["from"]).strip(),    # vendedor (a quien le pagan la cláusula)
                    "to_id": str(t["id_uc_to"]),
                    "to": str(t["to"]).strip(),        # comprador (quien paga la cláusula)
                    "price": int(t["price"]),
                    "created": to_utc_iso(item.get("created")),
                })
            except (KeyError, TypeError, ValueError) as err:
                print(f"AVISO: clausulazo con formato inesperado ({err!r}), se ignora.", file=sys.stderr)
    return events


def collect(fetch_page: Callable[[int], list[dict]], known_ids: set[str], full: bool,
            pause: float = PAUSE_SECONDS, community_id: int | None = None) -> tuple[list[dict], bool, int]:
    """Pide páginas del feed hacia atrás.

    Devuelve (clausulazos, llegó_al_final, páginas_leídas). En modo incremental
    se detiene en cuanto una página contiene un clausulazo que ya conocíamos.
    """
    found: list[dict] = []
    seen_feed_ids: set = set()
    reached_end = False
    pages = 0
    for page in range(MAX_PAGES):
        items = fetch_page(page * PAGE_SIZE)
        if not items:
            reached_end = True
            break
        fresh = {it.get("id") for it in items} - seen_feed_ids
        if not fresh:              # el servidor repite la última página: no hay más
            reached_end = True
            break
        pages += 1
        seen_feed_ids |= fresh
        events = parse_items(items, community_id)
        found.extend(events)
        if not full and any(ev["id"] in known_ids for ev in events):
            break                  # ya hemos enlazado con lo que teníamos guardado
        time.sleep(pause)
    return found, reached_end, pages


# --- Almacenamiento -----------------------------------------------------------

def load_store(path: Path) -> dict:
    if path.exists():
        store = json.loads(path.read_text(encoding="utf-8"))
        store.setdefault("events", [])
        return store
    return {"league": LEAGUE_NAME, "updated": None, "complete_history": False, "events": []}


def legacy_key(ev: dict) -> str:
    """Formato de id de la primera versión del script (leía el HTML del feed)."""
    return f"{ev['player_id']}-{ev['from_id']}-{ev['to_id']}-{ev['price']}"


def merge(store: dict, found: list[dict]) -> tuple[int, int]:
    """Añade lo nuevo. Los registros antiguos (sin fecha real) se sustituyen por el nuevo."""
    events = store["events"]
    known = {e["id"] for e in events}
    legacy_pos = {e["id"]: i for i, e in enumerate(events) if not e["id"].startswith("t")}
    added = upgraded = 0
    for ev in found:
        if ev["id"] in known:
            continue
        pos = legacy_pos.pop(legacy_key(ev), None)
        if pos is not None:
            events[pos] = ev
            upgraded += 1
        else:
            events.append(ev)
            added += 1
        known.add(ev["id"])
    events.sort(key=lambda e: e.get("created") or e.get("first_seen") or "", reverse=True)
    return added, upgraded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="Leer todo el histórico del feed, no solo lo nuevo")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Archivo JSON de salida")
    args = ap.parse_args()

    cookie = os.environ.get("MISTER_COOKIE", "").strip()
    if not cookie:
        print("ERROR: falta la variable de entorno MISTER_COOKIE.", file=sys.stderr)
        return 2

    out = Path(args.out)
    store = load_store(out)
    known_ids = {e["id"] for e in store["events"] if e["id"].startswith("t")}
    full = args.full or not store.get("complete_history")

    try:
        fetch_page, cfg = make_fetcher(cookie)
        community_id = check_league(cfg, LEAGUE_NAME)
        found, reached_end, pages = collect(fetch_page, known_ids, full, community_id=community_id)
    except FeedError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 3
    except requests.RequestException as err:
        print(f"ERROR de red al leer el feed: {err}", file=sys.stderr)
        return 4

    if pages == 0:
        print("ERROR: el feed no devolvió ninguna entrada. Revisa la cookie.", file=sys.stderr)
        return 3

    added, upgraded = merge(store, found)
    if reached_end:
        store["complete_history"] = True
    store["updated"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Modo {'completo' if full else 'incremental'}: {pages} páginas leídas, "
          f"{len(found)} clausulazos vistos, {added} nuevos, {upgraded} actualizados con su fecha real. "
          f"Total registrados: {len(store['events'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
