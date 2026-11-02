#!/usr/bin/env python3
"""Registra los clausulazos de una liga Mister y los guarda en un JSON.

Usa el mismo servicio que el feed cuando se baja para cargar más entradas
(POST /ajax/feed, de 20 en 20) y se queda con los fichajes de tipo "clause".

Uso normal (con tu sesión de Mister en la variable MISTER_COOKIE):
    MISTER_COOKIE="..." python scraper/mister_clausulazos.py

Forzar la lectura de todo el histórico del feed:
    MISTER_COOKIE="..." python scraper/mister_clausulazos.py --full
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "data" / "clausulazos.json"
ORIGIN = "https://mister.mundodeportivo.com"
FEED_URL = f"{ORIGIN}/feed"
AJAX_URL = f"{ORIGIN}/ajax/feed"
LEAGUE_NAME = os.environ.get("MISTER_LEAGUE_NAME", "Los liguis 2.0")
SITE_TZ = ZoneInfo("Europe/Madrid")  # las fechas del feed vienen en hora de España
PAGE_SIZE = 20
MAX_PAGES = 500          # tope de seguridad: 10.000 entradas del feed
PAUSE_SECONDS = 0.5      # pausa entre páginas para no saturar el servicio


class FeedError(Exception):
    """Problema al leer el feed (cookie caducada, formato inesperado...)."""


# --- Lectura del feed ---------------------------------------------------------

def to_utc_iso(created: str | None) -> str | None:
    """'2026-10-12 20:02:24' (hora de España) -> '2026-10-12T18:02:24+00:00'."""
    if not created:
        return None
    local = datetime.strptime(created, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SITE_TZ)
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_items(items: list[dict]) -> list[dict]:
    """Extrae los clausulazos de una página del feed."""
    events: list[dict] = []
    for item in items:
        if item.get("category") != "transfer" or not isinstance(item.get("data"), list):
            continue
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


def make_fetcher(cookie: str) -> Callable[[int], list[dict]]:
    session = requests.Session()
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "es-ES,es;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
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
            raise FeedError("La respuesta no es JSON: lo más probable es que la cookie haya caducado.")
        if body.get("status") != "ok":
            raise FeedError(f"Mister respondió status={body.get('status')!r}: ¿cookie caducada?")
        return body.get("data") or []

    return fetch_page


def collect(fetch_page: Callable[[int], list[dict]], known_ids: set[str], full: bool,
            pause: float = PAUSE_SECONDS) -> tuple[list[dict], bool, int]:
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
        events = parse_items(items)
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
        found, reached_end, pages = collect(make_fetcher(cookie), known_ids, full)
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
