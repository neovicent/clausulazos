#!/usr/bin/env python3
"""Registra los clausulazos de una liga Mister y los guarda en un JSON.

Uso normal (con tu sesión de Mister en la variable MISTER_COOKIE):
    MISTER_COOKIE="..." python scraper/mister_clausulazos.py

Prueba sin red, con un HTML guardado del feed:
    python scraper/mister_clausulazos.py --html tests/sample_feed.html --out /tmp/prueba.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "data" / "clausulazos.json"
FEED_URL = os.environ.get("MISTER_FEED_URL", "https://mister.mundodeportivo.com/feed")
LEAGUE_NAME = os.environ.get("MISTER_LEAGUE_NAME", "Los liguis 2.0")
CLAUSE_MARK = "por pago de clausula"  # comparado sin tildes ni mayúsculas


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fold(text: str) -> str:
    """Minúsculas y sin tildes, para comparar textos."""
    norm = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in norm if unicodedata.category(c) != "Mn")


def parse_price(text: str) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def id_from_href(href: str | None, kind: str) -> str | None:
    m = re.search(rf"{kind}/(\d+)", href or "")
    return m.group(1) if m else None


def parse_feed(html: str) -> tuple[int, list[dict]]:
    """Devuelve (entradas_totales_del_feed, clausulazos_encontrados)."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("div.item")
    events: list[dict] = []
    for item in items:
        title = item.select_one(".title")
        if not title or CLAUSE_MARK not in fold(clean(title.get_text(" "))):
            continue

        player_tag = title.find("strong")
        names = [clean(e.get_text()) for e in title.find_all("em")]
        player_link = item.select_one("a.player")
        user_links = item.select(".flow a.user")
        price_tag = item.select_one(".price")

        if not (player_tag and len(names) >= 2 and player_link
                and len(user_links) >= 2 and price_tag):
            print(f"AVISO: entrada de cláusula con formato inesperado: "
                  f"{clean(title.get_text(' '))[:80]}", file=sys.stderr)
            continue

        player_id = id_from_href(player_link.get("href"), "players")
        from_id = id_from_href(user_links[0].get("href"), "users")
        to_id = id_from_href(user_links[1].get("href"), "users")
        price = parse_price(price_tag.get_text())
        if not (player_id and from_id and to_id and price):
            print("AVISO: entrada de cláusula sin ids o importe, se ignora.", file=sys.stderr)
            continue

        events.append({
            "id": f"{player_id}-{from_id}-{to_id}-{price}",
            "player_id": player_id,
            "player": clean(player_tag.get_text()),
            "from_id": from_id,
            "from": names[0],   # vendedor (a quien le pagan la cláusula)
            "to_id": to_id,
            "to": names[1],     # comprador (quien paga la cláusula)
            "price": price,
        })
    return len(items), events


def fetch_feed(cookie: str) -> str:
    resp = requests.get(
        FEED_URL,
        headers={
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "es-ES,es;q=0.9",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def load_store(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"league": LEAGUE_NAME, "updated": None, "events": []}


def merge(store: dict, found: list[dict], now: str) -> int:
    known = {e["id"] for e in store["events"]}
    new = []
    for ev in found:
        if ev["id"] not in known:
            known.add(ev["id"])
            new.append({**ev, "first_seen": now})
    # El feed viene con lo más reciente arriba: las nuevas van delante.
    store["events"] = new + store["events"]
    store["updated"] = now
    return len(new)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", help="Leer este HTML local en vez de descargar el feed")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Archivo JSON de salida")
    args = ap.parse_args()

    if args.html:
        html = Path(args.html).read_text(encoding="utf-8")
    else:
        cookie = os.environ.get("MISTER_COOKIE", "").strip()
        if not cookie:
            print("ERROR: falta la variable de entorno MISTER_COOKIE.", file=sys.stderr)
            return 2
        html = fetch_feed(cookie)

    total, found = parse_feed(html)
    if total == 0:
        print("ERROR: no se encontró ninguna entrada en el feed. "
              "Lo más probable es que la cookie haya caducado: renuévala.", file=sys.stderr)
        return 3

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    store = load_store(out)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    added = merge(store, found, now)
    out.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Feed: {total} entradas, {len(found)} clausulazos visibles, "
          f"{added} nuevos. Total registrados: {len(store['events'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
