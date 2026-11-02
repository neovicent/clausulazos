"""Pruebas del lector del feed. Ejecutar con:  python tests/test_scraper.py"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import mister_clausulazos as m  # noqa: E402

PAGES = json.loads((Path(__file__).with_name("sample_feed.json")).read_text(encoding="utf-8"))["pages"]


def fake_fetcher():
    calls = []

    def fetch(offset):
        calls.append(offset)
        index = offset // m.PAGE_SIZE
        return PAGES[index] if index < len(PAGES) else []

    fetch.calls = calls
    return fetch


class ScraperTests(unittest.TestCase):
    def test_parse_solo_clausulas(self):
        events = m.parse_items(PAGES[0])
        self.assertEqual(len(events), 1)  # ni inicio de jornada ni fichaje de mercado
        ev = events[0]
        self.assertEqual(ev["id"], "t553124758")
        self.assertEqual((ev["player"], ev["from"], ev["to"], ev["price"]), ("Denis Suárez", "Laporta FC", "Muñoz", 2429610))
        self.assertEqual(ev["created"], "2026-10-12T18:02:24+00:00")  # 20:02 en España (verano) = 18:02 UTC
        self.assertEqual(ev["from_id"], "14571878")

    def test_varios_fichajes_en_una_entrada(self):
        self.assertEqual(len(m.parse_items(PAGES[1])), 2)

    def test_recorrido_completo(self):
        fetch = fake_fetcher()
        found, reached_end, pages = m.collect(fetch, set(), full=True, pause=0)
        self.assertEqual(len(found), 3)
        self.assertTrue(reached_end)
        self.assertEqual(pages, 2)
        self.assertEqual(fetch.calls, [0, 20, 40])

    def test_incremental_se_detiene_al_enlazar(self):
        fetch = fake_fetcher()
        found, reached_end, pages = m.collect(fetch, {"t553124758"}, full=False, pause=0)
        self.assertEqual(fetch.calls, [0])
        self.assertFalse(reached_end)
        self.assertEqual(pages, 1)

    def test_pagina_repetida_cuenta_como_final(self):
        calls = []

        def repetidor(offset):
            calls.append(offset)
            return PAGES[0]

        _, reached_end, pages = m.collect(repetidor, set(), full=True, pause=0)
        self.assertTrue(reached_end)
        self.assertEqual(pages, 1)

    def test_merge_sin_duplicados_y_ordenado(self):
        store = {"events": []}
        found, _, _ = m.collect(fake_fetcher(), set(), full=True, pause=0)
        self.assertEqual(m.merge(store, found), (3, 0))
        self.assertEqual(m.merge(store, found), (0, 0))
        created = [e["created"] for e in store["events"]]
        self.assertEqual(created, sorted(created, reverse=True))

    def test_merge_actualiza_registros_antiguos(self):
        # Registro de la primera versión (leía el HTML): sin fecha real, id con el formato antiguo.
        legacy = {"id": "31355-14689225-15414760-6127500", "player_id": "31355", "player": "Alexander Sørloth",
                  "from_id": "14689225", "from": "Muñoz", "to_id": "15414760", "to": "Juan Carlos al sueloooo",
                  "price": 6127500, "first_seen": "2026-11-02T12:33:56+00:00"}
        store = {"events": [legacy]}
        found, _, _ = m.collect(fake_fetcher(), set(), full=True, pause=0)
        added, upgraded = m.merge(store, found)
        self.assertEqual((added, upgraded), (2, 1))
        self.assertEqual(len(store["events"]), 3)
        sorloth = next(e for e in store["events"] if e["player_id"] == "31355")
        self.assertEqual(sorloth["id"], "t540000002")
        self.assertEqual(sorloth["created"], "2026-09-14T10:30:00+00:00")
        self.assertNotIn("first_seen", sorloth)


if __name__ == "__main__":
    unittest.main(verbosity=2)
