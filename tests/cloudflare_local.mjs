/**
 * Simulation locale des Workers Cloudflare (collecte + normalisation) avec KV, Queues et Azure Blob simulés.
 * Les Workers appellent la partie Azure simulée par tests/azure_local.py (port 7071).
 * Usage : node tests/cloudflare_local.mjs <collecte.mjs> <normalisation.mjs>   (fichiers produits par esbuild)
 */
const [collecteMod, normMod] = await Promise.all([import(process.argv[2]), import(process.argv[3])]);
const collecte = collecteMod.default, normalisation = normMod.default;

class KV {
  constructor() { this.m = new Map(); }
  async get(k) { return this.m.has(k) ? this.m.get(k) : null; }
  async put(k, v) { this.m.set(k, String(v)); }
  async delete(k) { this.m.delete(k); }
}
const queue = [];
const blobs = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init = {}) => {
  url = String(url);
  if (url.startsWith("https://compte.blob.core.windows.net/")) {      // Azure Blob simulé
    if (!url.includes("sig=")) return new Response("SAS manquant", { status: 403 });
    blobs.push(url.split("?")[0].replace("https://compte.blob.core.windows.net/raw/", ""));
    return new Response("", { status: 201 });
  }
  return realFetch(url, init);
};

const envC = { CURSORS: new KV(), POSTS: { sendBatch: async (msgs) => queue.push(...msgs.map((m) => m.body)) },
  MODE: "replay", BATCH_SIZE: "4", ADMIN_TOKEN: "admin-test",
  AZURE_BLOB_SAS_URL: "https://compte.blob.core.windows.net/raw?sv=2024&sig=abc",
  RAPPORT_URL: "http://127.0.0.1:7071/api/rapport" };
const envN = { SEEN: new KV(), PSEUDO_SECRET: "sel", ANALYZE_URL: "http://127.0.0.1:7071/api/analyze" };

async function runCron(cron) {
  const waits = [];
  await collecte.scheduled({ cron }, envC, { waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
}
async function drainQueue() {
  const logs = [];
  const orig = console.log; console.log = (s) => logs.push(JSON.parse(s));
  while (queue.length) {
    const batch = queue.splice(0, 5).map((body) => ({ body, ack() {}, retry() { queue.push(body); } }));
    await normalisation.queue({ messages: batch }, envN);
  }
  console.log = orig;
  return logs;
}

// Routes HTTP protégées
let r = await collecte.fetch(new Request("https://w/etat"), envC);
if (r.status !== 401) throw new Error("la route /etat doit exiger le jeton");

const resultats = [];
for (let tour = 1; tour <= 8; tour++) {
  await runCron("*/2 * * * *");
  const n = queue.length;
  const logs = await drainQueue();
  resultats.push(...logs);
  console.log(`Passage ${tour} du déclencheur planifié : ${n} publication(s)`);
  if (!n) break;
}
r = await collecte.fetch(new Request("https://w/etat", { headers: { Authorization: "Bearer admin-test" } }), envC);
console.log("Curseurs KV :", JSON.stringify(await r.json()));

// Doublon
queue.push({ id: "dup1", source: "x", author: "@autre", text: "Il va pleuvoir demain sur Rabat et Kénitra, prenez vos parapluies !" });
const dup = await drainQueue();
console.log("Doublon :", dup[0].statut);

// Déclencheur du soir -> rapport
await runCron("0 23 * * *");

console.log(JSON.stringify({
  blobs: blobs.length, envoyees: resultats.filter((x) => x.statut === "envoyé à Azure").length,
  langues: [...new Set(resultats.map((x) => x.lang))].sort(),
  pseudonymes_exemple: resultats[0]?.auteur, erreurs: resultats.filter((x) => x.erreur).length,
}));
