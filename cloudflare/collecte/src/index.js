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

function clean(html) {
  return html.replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(parseInt(d, 10))).replace(/\s+/g, " ").trim();
}

function feeds(env) {
  return (env.RSS_FEEDS || "").split(",").map((s) => s.trim()).filter(Boolean);
}

function keywords(env) {
  return (env.KEYWORDS || "élection,électora,scrutin,urne,urnes,bureau de vote,bureaux de vote,législatives,انتخاب,الانتخابات,التصويت,الاقتراع,صناديق")
    .split(",").map((k) => k.trim().toLowerCase());
}

function matches(text, kw) {
  const t = " " + text.toLowerCase() + " ";
  return kw.some((k) => /^[a-zà-ÿ ]+$/.test(k) ? new RegExp(`(^|[^a-zà-ÿ])${k}`).test(t) : t.includes(k));
}

async function readFeed(feed) {
  const res = await fetch(feed, { headers: { "User-Agent": "Mozilla/5.0 (compatible; ElectoRadar/1.0)" } });
  if (!res.ok) throw new Error(`${res.status}`);
  const xml = await res.text();
  return xml.split(/<item[\s>]/i).slice(1).map((item) => {
    const title = clean(tag(item, "title"));
    const source = clean(tag(item, "source") || tag(item, "News:Source"));
    let link = clean(tag(item, "link"));
    const real = link.includes("bing.com/news/apiclick") && new URL(link).searchParams.get("url");
    if (real) link = real;
    return {
      title, source, link, pub: Date.parse(tag(item, "pubDate")),
      description: clean(tag(item, "description")).slice(0, 400),
    };
  });
}

async function collectRssLive(env) {
  const kw = keywords(env);
  const cur = await getCursor(env, "rss");
  const last = cur && !isNaN(Date.parse(cur)) ? Date.parse(cur) : Date.now() - 48 * 3600 * 1000;
  const max = parseInt(env.RSS_MAX || "12", 10);
  let found = [];
  const erreurs = {};
  for (const feed of feeds(env)) {
    try {
      for (const it of await readFeed(feed)) {
        if (!it.pub || it.pub <= last || !it.title) continue;
        if (!matches(it.title + " " + it.description, kw)) continue;
        found.push({ ...it, feed });
      }
    } catch (e) { erreurs[new URL(feed).hostname] = e.message; }
  }
  const seen = new Set();
  found = found.sort((a, b) => b.pub - a.pub).filter((it) => !seen.has(it.title) && seen.add(it.title)).slice(0, max);
  const posts = found.map((it) => ({
    id: "rss-" + [...it.link].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7), source: "rss",
    author: it.source || new URL(it.feed).hostname, text: it.title, url: it.link,
    created_at: new Date(it.pub).toISOString().replace(/\.\d{3}Z$/, "Z"),
    reach: 0, shares: 0, author_account_age_days: 3650, collected_at: nowIso(), mode: "live",
  }));
  const stored = await publish(env, posts);
  const newest = Math.max(last, ...found.map((it) => it.pub));
  if (found.length) await setCursor(env, "rss", new Date(newest).toISOString(), posts.length);
  return { nouvelles: posts.length, fichiers: stored, ...(Object.keys(erreurs).length ? { erreurs } : {}) };
}

async function sources(env) {
  const out = {};
  for (const feed of feeds(env)) {
    try {
      const items = await readFeed(feed);
      const kw = keywords(env);
      const hits = items.filter((it) => matches(it.title + " " + it.description, kw));
      out[feed] = { articles: items.length, sur_les_elections: hits.length, exemple: hits[0]?.title || null };
    } catch (e) { out[feed] = { erreur: e.message }; }
  }
  return out;
}

export async function collect(env, source) {
  if (source === "x") return env.X_MODE === "live" && env.X_BEARER_TOKEN ? collectXLive(env) : collectReplay(env, "x");
  return env.RSS_MODE === "live" ? collectRssLive(env) : collectReplay(env, "rss");
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
    if (url.pathname === "/") return json({ service: "ElectoRadar — collecte", routes: ["/etat", "/sources", "/collecter", "/reset"] });
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
    if (url.pathname === "/sources") return json(await sources(env));
    if (url.pathname === "/rapport" && request.method === "POST") return json(await triggerReport(env));
    return json({ erreur: "route inconnue" }, 404);
  },
};
