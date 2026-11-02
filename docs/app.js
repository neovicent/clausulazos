"use strict";

const CFG = window.MISTER_CONFIG || {};
const $ = (id) => document.getElementById(id);

const eur = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const fmtDateTime = (iso) => new Date(iso).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
const fmtDay = (iso) => new Date(iso).toLocaleDateString("es-ES", { dateStyle: "short", timeZone: "UTC" });

// Los nombres los escribe cada manager (o quien añade a mano): se escapan antes de pintarlos.
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Para agrupar nombres escritos de forma ligeramente distinta ("Muñoz" / "munoz ").
const norm = (s) =>
  String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();

const eventKey = (e) => [norm(e.player), norm(e.from), norm(e.to), e.price].join("|");

const state = { league: "Liga", updated: null, feed: [], manual: [], all: [], password: null, filter: "" };

// --- Datos ------------------------------------------------------------------

async function loadJson(path) {
  try {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function buildAll() {
  const seen = new Set(state.feed.map(eventKey));
  const feed = state.feed.map((e) => ({ ...e, when: e.created || e.first_seen, manual: false }));
  const manual = state.manual
    .filter((e) => !seen.has(eventKey(e)))
    .map((e) => ({ ...e, when: e.date, manual: true }));
  // Orden estable: lo más reciente primero.
  state.all = [...feed, ...manual].sort((a, b) => Date.parse(b.when) - Date.parse(a.when));
}

// --- Pintado ------------------------------------------------------------------

function ranking(events, nameKey) {
  const map = new Map();
  for (const e of events) {
    const k = norm(e[nameKey]);
    const cur = map.get(k) || { name: e[nameKey], count: 0, total: 0 }; // gana el nombre más reciente
    cur.count += 1;
    cur.total += e.price;
    map.set(k, cur);
  }
  return [...map.values()].sort((a, b) => b.count - a.count || b.total - a.total).slice(0, 10);
}

function renderRanking(elId, list, withTotal) {
  const el = $(elId);
  if (!list.length) { el.innerHTML = '<p class="empty">Sin datos todavía.</p>'; return; }
  el.innerHTML =
    "<table><tbody>" +
    list.map((r, i) =>
      `<tr><td>${i + 1}</td><td>${esc(r.name)}</td><td class="num">${r.count}</td>` +
      (withTotal ? `<td class="num">${eur.format(r.total)}</td>` : "") + "</tr>"
    ).join("") +
    "</tbody></table>";
}

function renderRows() {
  const needle = norm(state.filter);
  const rows = state.all.filter((e) =>
    !needle || [e.player, e.from, e.to].some((t) => norm(t).includes(needle)));
  $("rows").innerHTML = rows.length
    ? rows.map((e) =>
        `<tr><td>${e.manual ? `${esc(fmtDay(e.when))} <span class="tag">manual</span>` : esc(fmtDateTime(e.when))}</td>` +
        `<td>${esc(e.player)}</td><td>${esc(e.from)}</td><td>${esc(e.to)}</td>` +
        `<td class="num">${eur.format(e.price)}</td></tr>`
      ).join("")
    : '<tr><td colspan="5" class="empty">Ningún clausulazo coincide.</td></tr>';
}

function renderDatalists() {
  const options = (values) =>
    [...new Map(values.map((v) => [norm(v), v])).values()].sort((a, b) => a.localeCompare(b, "es"))
      .map((v) => `<option value="${esc(v)}">`).join("");
  $("dl-players").innerHTML = options(state.all.map((e) => e.player));
  $("dl-managers").innerHTML = options(state.all.flatMap((e) => [e.from, e.to]));
}

function render() {
  const events = state.all;
  const total = events.reduce((s, e) => s + e.price, 0);
  const top = events.reduce((m, e) => (e.price > (m ? m.price : 0) ? e : m), null);

  $("subtitle").textContent =
    `${state.league} · última lectura del feed: ${state.updated ? fmtDateTime(state.updated) : "—"}`;
  $("kpis").innerHTML =
    `<div class="kpi"><b>${events.length}</b>clausulazos</div>` +
    `<div class="kpi"><b>${eur.format(total)}</b>movido en cláusulas</div>` +
    `<div class="kpi"><b>${top ? eur.format(top.price) : "—"}</b>el más caro${top ? ` (${esc(top.player)})` : ""}</div>`;

  renderRanking("buyers", ranking(events, "to"), true);
  renderRanking("sellers", ranking(events, "from"), true);
  renderRanking("players", ranking(events, "player"), false);
  renderRows();
  renderDatalists();
}

// --- Añadir a mano ------------------------------------------------------------

const parsePrice = (t) => {
  const digits = String(t || "").replace(/\D/g, "");
  return digits ? Number(digits) : null;
};

function normDate(text) {
  const t = String(text).trim();
  let m = t.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return t;
  m = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  return m ? `${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}` : null;
}

function parseBulk(text) {
  const events = [];
  const errors = [];
  text.split(/\r?\n/).forEach((raw, i) => {
    const line = raw.trim();
    if (!line) return;
    const p = line.split(/\s*[;\t]\s*/);
    if (p.length !== 5) { errors.push(`Línea ${i + 1}: se esperan 5 campos separados por «;».`); return; }
    const date = normDate(p[0]);
    const price = parsePrice(p[4]);
    if (!date) { errors.push(`Línea ${i + 1}: fecha no válida (usa AAAA-MM-DD o DD/MM/AAAA).`); return; }
    if (!price) { errors.push(`Línea ${i + 1}: importe no válido.`); return; }
    events.push({ date, player: p[1], from: p[2], to: p[3], price });
  });
  if (events.length > 100) errors.push("Máximo 100 líneas por envío.");
  return { events, errors };
}

function setStatus(id, msg, isError = false) {
  const el = $(id);
  el.textContent = msg;
  el.classList.toggle("error", isError);
}

function lock(message = "") {
  state.password = null;
  $("editor").hidden = true;
  $("unlock").hidden = false;
  setStatus("unlock-status", message, Boolean(message));
}

async function callWorker(path, payload) {
  let res;
  try {
    res = await fetch(CFG.addEndpoint.replace(/\/$/, "") + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error("No se pudo contactar con el servicio. Inténtalo de nuevo.");
  }
  let body = {};
  try { body = await res.json(); } catch { /* respuesta sin JSON */ }
  if (res.status === 401) { lock("Contraseña incorrecta."); throw new Error("Contraseña incorrecta."); }
  if (!res.ok) throw new Error(body.error || `Error ${res.status}`);
  return body;
}

async function addEvents(events) {
  setStatus("add-status", "Guardando…");
  try {
    const r = await callWorker("/add", { password: state.password, events });
    state.manual = [...r.added, ...state.manual];
    buildAll();
    render();
    const dup = r.skipped ? ` (${r.skipped} ya existía${r.skipped > 1 ? "n" : ""})` : "";
    setStatus("add-status", `Añadido${r.added.length === 1 ? "" : "s"}: ${r.added.length}${dup}. Los demás lo verán en un minuto aproximadamente.`);
    return true;
  } catch (err) {
    if (state.password) setStatus("add-status", err.message, true);
    return false;
  }
}

function setupAdmin() {
  if (!CFG.addEndpoint) return;
  $("admin").hidden = false;

  $("unlock").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const pw = $("password").value;
    setStatus("unlock-status", "Comprobando…");
    try {
      await callWorker("/check", { password: pw });
      state.password = pw;
      $("password").value = "";
      $("unlock").hidden = true;
      $("editor").hidden = false;
      setStatus("unlock-status", "");
      setStatus("add-status", "");
    } catch (err) {
      setStatus("unlock-status", err.message, true);
    }
  });

  $("lock").addEventListener("click", () => lock());

  $("add-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const price = parsePrice($("f-price").value);
    if (!price) { setStatus("add-status", "Importe no válido.", true); return; }
    const ok = await addEvents([{
      date: $("f-date").value,
      player: $("f-player").value,
      from: $("f-from").value,
      to: $("f-to").value,
      price,
    }]);
    if (ok) { $("f-player").value = ""; $("f-price").value = ""; $("f-player").focus(); }
  });

  $("bulk-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const { events, errors } = parseBulk($("bulk-text").value);
    if (errors.length) { setStatus("add-status", errors.slice(0, 3).join(" "), true); return; }
    if (!events.length) { setStatus("add-status", "No hay líneas que añadir.", true); return; }
    if (await addEvents(events)) $("bulk-text").value = "";
  });
}

// --- Arranque -----------------------------------------------------------------

async function main() {
  const [feed, manual] = await Promise.all([
    loadJson("data/clausulazos.json"),
    loadJson("data/manual.json"),
  ]);
  if (!feed && !manual) {
    $("subtitle").textContent = "No se pudieron cargar los datos.";
    return;
  }
  state.league = (feed && feed.league) || "Liga";
  state.updated = feed ? feed.updated : null;
  state.feed = (feed && feed.events) || [];
  state.manual = (manual && manual.events) || [];
  buildAll();
  render();

  $("filter").addEventListener("input", (ev) => { state.filter = ev.target.value; renderRows(); });
  setupAdmin();
}

main();
