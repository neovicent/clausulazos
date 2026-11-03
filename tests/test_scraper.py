"""Pruebas del lector del feed. Ejecutar con:  python tests/test_scraper.py"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scraper"))
import mister_clausulazos as m  # noqa: E402

PAGES = json.loads((HERE / "sample_feed.json").read_text(encoding="utf-8"))["pages"]
PAGE_HTML = (HERE / "sample_page.html").read_text(encoding="utf-8")


def fake_fetcher():
    calls = []

    def fetch(offset):
        calls.append(offset)
        index = offset // m.PAGE_SIZE
        return (PAGES[index] if index < len(PAGES) else []), False

    fetch.calls = calls
    return fetch


class FakeResponse:
    def __init__(self, text="", body=None, status=200):
        self.text, self._body, self.status_code = text, body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self):
        if self._body is None:
            raise ValueError("no es JSON")
        return self._body


class FakeSession:
    """Simula la web: /feed con el token y /ajax/feed, que exige el token en X-Auth."""
    instances = []

    def __init__(self):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.gets, self.posts = [], []
        FakeSession.instances.append(self)

    def get(self, url, headers=None, timeout=None):
        self.gets.append((url, headers))
        return FakeResponse(text=PAGE_HTML)

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append((url, data, headers))
        if (headers or {}).get("X-Auth") != "abc123def456":
            return FakeResponse(status=401)
        index = data["offset"] // m.PAGE_SIZE
        data = PAGES[index] if index < len(PAGES) else []
        return FakeResponse(body={"status": "ok" if data else "end", "data": data})


class ParseTests(unittest.TestCase):
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

    def test_filtra_por_comunidad(self):
        item = {"id": 1, "category": "transfer", "created": "2026-10-12 20:02:24", "id_community": 999,
                "data": [{"id_transfer": 1, "id_uc_from": 1, "id_uc_to": 2, "type": "clause", "price": 5,
                          "from": "A", "to": "B", "id": 7, "name": "J"}]}
        self.assertEqual(m.parse_items([item], community_id=2337019), [])
        self.assertEqual(len(m.parse_items([item], community_id=999)), 1)
        self.assertEqual(len(m.parse_items([item], community_id=None)), 1)


class CollectTests(unittest.TestCase):
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

    def test_status_end_con_datos_se_aprovecha(self):
        # Si Mister marca 'end' en una página con entradas, se guardan y se para.
        calls = []

        def fetch(offset):
            calls.append(offset)
            return PAGES[offset // m.PAGE_SIZE], offset // m.PAGE_SIZE == 1

        found, reached_end, pages = m.collect(fetch, set(), full=True, pause=0)
        self.assertEqual((len(found), reached_end, pages, calls), (3, True, 2, [0, 20]))

    def test_pagina_repetida_cuenta_como_final(self):
        _, reached_end, pages = m.collect(lambda offset: (PAGES[0], False), set(), full=True, pause=0)
        self.assertTrue(reached_end)
        self.assertEqual(pages, 1)


class MergeTests(unittest.TestCase):
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


class SessionTests(unittest.TestCase):
    def test_extrae_token_de_la_pagina(self):
        cfg = m.extract_cfg(PAGE_HTML)
        self.assertEqual(cfg["auth"], "abc123def456")  # no se confunde con firebase "auth_domain"

    def test_pagina_sin_configuracion(self):
        with self.assertRaises(m.FeedError):
            m.extract_cfg("<html><body>Inicia sesión</body></html>")

    def test_pagina_sin_token(self):
        with self.assertRaises(m.FeedError):
            m.extract_cfg('<script>var _FG_cfg = {"auth":""};</script>')

    def test_liga_activa(self):
        cfg = m.extract_cfg(PAGE_HTML)
        self.assertEqual(m.check_league(cfg, "Los liguis 2.0"), 2337019)  # sin distinguir mayúsculas
        with self.assertRaises(m.FeedError):
            m.check_league(cfg, "Otra liga")

    def test_envia_cookie_y_token(self):
        FakeSession.instances.clear()
        with mock.patch.object(m.requests, "Session", FakeSession):
            fetch, cfg = m.make_fetcher("sid=aaa; otra=b=c; ; basura")
            self.assertEqual(len(fetch(0)[0]), 3)
            self.assertEqual(len(fetch(20)[0]), 2)
            self.assertEqual(fetch(40), ([], True))   # status 'end': no es un error
        s = FakeSession.instances[-1]
        self.assertEqual(s.gets[0][0], m.FEED_URL)
        self.assertEqual({c.name: c.value for c in s.cookies}, {"sid": "aaa", "otra": "b=c"})
        url, data, headers = s.posts[0]
        self.assertEqual(url, m.AJAX_URL)
        self.assertEqual(data, {"end": "false", "loading": "true", "offset": 0, "cardsPerPage": 20})
        self.assertEqual(headers["X-Auth"], "abc123def456")
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")

    def test_error_http_se_propaga(self):
        class SinPermiso(FakeSession):
            def post(self, *a, **k):
                return FakeResponse(status=401)
        with mock.patch.object(m.requests, "Session", SinPermiso):
            fetch, _ = m.make_fetcher("sid=aaa")
            with self.assertRaises(requests.HTTPError):
                fetch(0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
