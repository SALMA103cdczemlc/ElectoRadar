import dataset from "./publications_test.json";

const COLLECT_CRON = "*/2 * * * *";

function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function getCursor(env, source) {
  return await env.CURSORS.get(`cursor:${source}`);
}

async function setCursor(env, source, value, count) {
  await env.CURSORS.put(`cursor:${source}`, String(value));
  await env.CURSORS.put(`meta:${source}`, JSON.stringify({ updated_at: nowIso(), last_batch: count }));
}

async function storeRaw(env, post) {
  if (!env.AZURE_BLOB_SAS_URL) return null;
  const day = new Date().toISOString().slice(0, 10);
  const path = `${post.source}/${day}/${post.id}.json`;
  const [base, sas] = env.AZURE_BLOB_SAS_URL.split("?");
  const res = await fetch(`${base}/${path}?${sas}`, {
    method: "PUT",
    headers: { "x-ms-blob-type": "BlockBlob", "Content-Type": "application/json" },
    body: JSON.stringify(post),
  });
  if (!res.ok) throw new Error(`Azure Blob ${res.status}: ${await res.text()}`);
  return path;
}

async function publish(env, posts) {
  const stored = [];
  for (const p of posts) stored.push(await storeRaw(env, p));
  if (posts.length) await env.POSTS.sendBatch(posts.map((p) => ({ body: p })));
  return stored;
}

async function collectReplay(env, source) {
  const all = dataset.publications.filter((p) => p.source === source);
  const start = parseInt((await getCursor(env, source)) || "0", 10);
  const size = parseInt(env.BATCH_SIZE || "4", 10);
  const batch = all.slice(start, start + size).map((p) => ({
    ...p, created_at: nowIso(), collected_at: nowIso(), mode: "replay",
  }));
  const stored = await publish(env, batch);
  if (batch.length) await setCursor(env, source, start + batch.length, batch.length);
  return { nouvelles: batch.length, fichiers: stored };
}

async function collectXLive(env) {
  const params = new URLSearchParams({
    query: env.X_QUERY || "(élections OR \"bureau de vote\" OR urne OR انتخابات) -is:retweet",
    max_results: "50",
    "tweet.fields": "created_at,public_metrics,author_id",
    expansions: "author_id",
    "user.fields": "created_at,public_metrics",
  });
  const since = await getCursor(env, "x");
  if (since) params.set("since_id", since);
  const res = await fetch(`https://api.x.com/2/tweets/search/recent?${params}`, {
    headers: { Authorization: `Bearer ${env.X_BEARER_TOKEN}` },
  });
  if (!res.ok) throw new Error(`API X ${res.status}`);
  const data = await res.json();
  const users = Object.fromEntries((data.includes?.users || []).map((u) => [u.id, u]));
  const posts = (data.data || []).map((t) => {
    const u = users[t.author_id] || {};
    const age = u.created_at ? Math.floor((Date.now() - Date.parse(u.created_at)) / 86400000) : null;
    return {
      id: t.id, source: "x", author: "@" + (u.username || t.author_id), text: t.text,
      url: `https://x.com/i/status/${t.id}`, created_at: t.created_at || nowIso(),
      reach: u.public_metrics?.followers_count || 0, shares: t.public_metrics?.retweet_count || 0,
      author_account_age_days: age, collected_at: nowIso(), mode: "live",
    };
  });
  const stored = await publish(env, posts);
  if (data.meta?.newest_id) await setCursor(env, "x", data.meta.newest_id, posts.length);
  return { nouvelles: posts.length, fichiers: stored };
}

function tag(item, name) {
  const m = item.match(new RegExp(`<${name}[^>]*>(?:<!\\[CDATA\\[)?([\\s\\S]*?)(?:\\]\\]>)?</${name}>`, "i"));
  return m ? m[1].trim() : "";
}

async function collectRssLive(env) {
  const feeds = (env.RSS_FEEDS || "").split(",").map((s) => s.trim()).filter(Boolean);
  const keywords = (env.KEYWORDS || "élection,bureau de vote,urne,scrutin,candidat,انتخابات,التصويت,صندوق")
    .split(",").map((k) => k.trim().toLowerCase());
  const last = Date.parse((await getCursor(env, "rss")) || "2000-01-01T00:00:00Z");
  let newest = last;
  const posts = [];
  for (const feed of feeds) {
    const xml = await (await fetch(feed, { headers: { "User-Agent": "ElectoRadar/1.0" } })).text();
    for (const item of xml.split(/<item[\s>]/i).slice(1)) {
      const title = tag(item, "title");
      const pub = Date.parse(tag(item, "pubDate"));
      if (!pub || pub <= last) continue;
      if (!keywords.some((k) => (title + " " + tag(item, "description")).toLowerCase().includes(k))) continue;
      newest = Math.max(newest, pub);
      const link = tag(item, "link");
      posts.push({
        id: "rss-" + [...link].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7), source: "rss",
        author: new URL(feed).hostname, text: title, url: link, created_at: new Date(pub).toISOString(),
        reach: 0, shares: 0, author_account_age_days: 3650, collected_at: nowIso(), mode: "live",
      });
    }
  }
  const stored = await publish(env, posts);
  if (newest > last) await setCursor(env, "rss", new Date(newest).toISOString(), posts.length);
  return { nouvelles: posts.length, fichiers: stored };
}

export async function collect(env, source) {
  if (env.MODE === "live" && source === "x" && env.X_BEARER_TOKEN) return collectXLive(env);
  if (env.MODE === "live" && source === "rss") return collectRssLive(env);
  return collectReplay(env, source);
}

async function collectAll(env) {
  const out = {};
  for (const s of ["x", "rss"]) out[s] = await collect(env, s);
  console.log(JSON.stringify({ collecte: out }));
  return out;
}

async function triggerReport(env) {
  if (!env.RAPPORT_URL) return { rapport: "non configuré" };
  const res = await fetch(env.RAPPORT_URL);
  const out = { rapport: res.status };
  console.log(JSON.stringify(out));
  return out;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status, headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

export default {
  async scheduled(event, env, ctx) {
    if (event.cron === COLLECT_CRON) ctx.waitUntil(collectAll(env));
    else ctx.waitUntil(triggerReport(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/") return json({ service: "ElectoRadar — collecte", routes: ["/etat", "/collecter", "/reset"] });
    if (!env.ADMIN_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.ADMIN_TOKEN}`)
      return json({ erreur: "non autorisé" }, 401);
    if (url.pathname === "/etat") {
      const etat = {};
      for (const s of ["x", "rss"]) {
        etat[s] = { curseur: await getCursor(env, s), ...(JSON.parse((await env.CURSORS.get(`meta:${s}`)) || "{}")) };
      }
      return json(etat);
    }
    if (url.pathname === "/collecter" && request.method === "POST") {
      const source = url.searchParams.get("source");
      return json(source ? { [source]: await collect(env, source) } : await collectAll(env));
    }
    if (url.pathname === "/reset" && request.method === "POST") {
      for (const s of ["x", "rss"]) {
        await env.CURSORS.delete(`cursor:${s}`);
        await env.CURSORS.delete(`meta:${s}`);
      }
      let seen = 0, cursor;
      do {
        const page = await env.SEEN.list({ cursor });
        for (const k of page.keys) { await env.SEEN.delete(k.name); seen++; }
        cursor = page.list_complete ? null : page.cursor;
      } while (cursor);
      let azure = "non configuré";
      if (env.RESET_URL) azure = await (await fetch(env.RESET_URL, { method: "POST" })).json();
      return json({ reset: true, doublons_effaces: seen, azure });
    }
    if (url.pathname === "/rapport" && request.method === "POST") return json(await triggerReport(env));
    return json({ erreur: "route inconnue" }, 404);
  },
};
