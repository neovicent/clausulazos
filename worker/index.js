// Cloudflare Worker: comprueba la contraseña y guarda/edita clausulazos manuales
// en docs/data/manual.json del repositorio (commit vía API de GitHub).
//
// Rutas (todas POST con JSON): /check, /add, /update
// Variables de texto:  ALLOWED_ORIGIN (https://usuario.github.io), GITHUB_REPO (usuario/repo),
//                      GITHUB_BRANCH (opcional, por defecto "main")
// Secretos:            ADMIN_PASSWORD, GITHUB_TOKEN (permiso Contents: Read and write, solo ese repo)

const FILE_PATH = "docs/data/manual.json";
const MAX_EVENTS = 100;
const MAX_ATTEMPTS = 3;
const enc = new TextEncoder();

class UserError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

const corsHeaders = (env) => ({
  "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Vary": "Origin",
});

const json = (env, status, body) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...corsHeaders(env) },
  });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(text)));
}

// Comparación en tiempo constante (sobre los hashes, que tienen longitud fija).
async function passwordOk(given, expected) {
  if (typeof given !== "string" || !expected) return false;
  const [a, b] = await Promise.all([sha256(given), sha256(expected)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

const norm = (s) =>
  String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();

const eventKey = (e) => [e.date, norm(e.player), norm(e.from), norm(e.to), e.price].join("|");

// Devuelve { event } o { error }. `where` abre el mensaje de error ("Clausulazo 2", "El clausulazo"...).
function cleanEvent(raw, where) {
  const text = (v) => (typeof v === "string" ? v.replace(/\s+/g, " ").trim() : "");
  const player = text(raw?.player);
  const from = text(raw?.from);
  const to = text(raw?.to);
  const date = typeof raw?.date === "string" ? raw.date.trim() : "";
  const price = raw?.price;

  for (const [label, value] of [["jugador", player], ["vendedor", from], ["comprador", to]]) {
    if (!value) return { error: `${where}: falta el ${label}.` };
    if (value.length > 60) return { error: `${where}: el ${label} es demasiado largo.` };
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date) {
    return { error: `${where}: fecha no válida (AAAA-MM-DD).` };
  }
  const year = Number(date.slice(0, 4));
  if (year < 2015 || Date.parse(`${date}T00:00:00Z`) > Date.now() + 86400000) {
    return { error: `${where}: la fecha no puede ser futura ni anterior a 2015.` };
  }
  if (!Number.isInteger(price) || price < 1 || price > 2_000_000_000) {
    return { error: `${where}: importe no válido.` };
  }
  return { event: { player, from, to, price, date } };
}

// --- GitHub -----------------------------------------------------------------

const ghHeaders = (env) => ({
  Authorization: `Bearer ${env.GITHUB_TOKEN}`,
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": "clausulazos-worker",
});

const b64ToUtf8 = (b64) =>
  new TextDecoder().decode(Uint8Array.from(atob(b64.replace(/\s/g, "")), (c) => c.charCodeAt(0)));

function utf8ToB64(text) {
  let bin = "";
  for (const byte of enc.encode(text)) bin += String.fromCharCode(byte);
  return btoa(bin);
}

// Lee manual.json, aplica `mutate(store)` y lo guarda con un commit.
// mutate devuelve { changed, result, message } o lanza UserError.
// Si alguien escribe a la vez (409), se vuelve a leer y a aplicar.
async function modifyManual(env, mutate) {
  const repo = env.GITHUB_REPO;
  const branch = env.GITHUB_BRANCH || "main";
  const url = `https://api.github.com/repos/${repo}/contents/${FILE_PATH}`;

  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    const getRes = await fetch(`${url}?ref=${encodeURIComponent(branch)}`, { headers: ghHeaders(env) });
    let store = { events: [] };
    let sha;
    if (getRes.status === 200) {
      const file = await getRes.json();
      sha = file.sha;
      store = JSON.parse(b64ToUtf8(file.content));
      if (!Array.isArray(store.events)) store.events = [];
    } else if (getRes.status !== 404) {
      throw new Error(`GitHub respondió ${getRes.status} al leer el archivo.`);
    }

    const out = mutate(store);
    if (!out.changed) return out.result;

    const putRes = await fetch(url, {
      method: "PUT",
      headers: { ...ghHeaders(env), "Content-Type": "application/json" },
      body: JSON.stringify({
        message: out.message,
        content: utf8ToB64(JSON.stringify(store, null, 2) + "\n"),
        branch,
        ...(sha ? { sha } : {}),
      }),
    });
    if (putRes.ok) return out.result;
    if (putRes.status === 409 && attempt < MAX_ATTEMPTS - 1) continue;
    throw new Error(`GitHub respondió ${putRes.status} al guardar.`);
  }
  throw new Error("No se pudo guardar tras varios intentos.");
}

const addEvents = (env, incoming) =>
  modifyManual(env, (store) => {
    const known = new Set(store.events.map(eventKey));
    const added = [];
    for (const ev of incoming) {
      const k = eventKey(ev);
      if (known.has(k)) continue;
      known.add(k);
      added.push({ id: `manual-${crypto.randomUUID()}`, ...ev });
    }
    store.events = [...added, ...store.events];
    return {
      changed: added.length > 0,
      result: { added, skipped: incoming.length - added.length },
      message: `Añade ${added.length} clausulazo(s) manual(es)`,
    };
  });

const updateEvent = (env, id, event) =>
  modifyManual(env, (store) => {
    const i = store.events.findIndex((e) => e.id === id);
    if (i < 0) throw new UserError("No se encontró ese clausulazo (¿se ha borrado?).", 404);
    const key = eventKey(event);
    if (store.events.some((e, j) => j !== i && eventKey(e) === key)) {
      throw new UserError("Ya existe otro clausulazo manual igual.");
    }
    const updated = { id, ...event };
    const same = eventKey(store.events[i]) === key;
    if (!same) store.events[i] = updated;
    return { changed: !same, result: { updated }, message: "Edita un clausulazo manual" };
  });

// --- Rutas ------------------------------------------------------------------

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(env) });
    if (request.method !== "POST") return json(env, 405, { error: "Método no permitido." });

    let body;
    try {
      body = await request.json();
    } catch {
      return json(env, 400, { error: "Petición no válida." });
    }

    if (!(await passwordOk(body?.password, env.ADMIN_PASSWORD))) {
      await sleep(1000); // frena los intentos de adivinar la contraseña
      return json(env, 401, { error: "Contraseña incorrecta." });
    }

    const path = new URL(request.url).pathname;
    try {
      if (path === "/check") return json(env, 200, { ok: true });

      if (path === "/add") {
        if (!Array.isArray(body.events) || !body.events.length) throw new UserError("No hay clausulazos que añadir.");
        if (body.events.length > MAX_EVENTS) throw new UserError(`Máximo ${MAX_EVENTS} clausulazos por envío.`);
        const cleaned = [];
        for (const [i, raw] of body.events.entries()) {
          const { event, error } = cleanEvent(raw, `Clausulazo ${i + 1}`);
          if (error) throw new UserError(error);
          cleaned.push(event);
        }
        return json(env, 200, await addEvents(env, cleaned));
      }

      if (path === "/update") {
        const id = typeof body.id === "string" ? body.id : "";
        if (!/^manual-[\w-]{1,64}$/.test(id)) throw new UserError("Identificador no válido.");
        const { event, error } = cleanEvent(body.event, "El clausulazo");
        if (error) throw new UserError(error);
        return json(env, 200, await updateEvent(env, id, event));
      }
    } catch (err) {
      if (err instanceof UserError) return json(env, err.status, { error: err.message });
      return json(env, 502, { error: err.message });
    }
    return json(env, 404, { error: "No encontrado." });
  },
};
