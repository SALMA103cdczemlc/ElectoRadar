const ARABIC = /[؀-ۿ]/g;
const LATIN = /[A-Za-zÀ-ÿ]/g;
const DARIJA_AR = new Set(["ديال", "هاد", "هادشي", "كيدور", "شوفو", "ما", "تمشيوش", "بزاف", "دابا", "واش", "فكل", "بلاصة",
  "كي", "غادي", "ماشي", "عامرة", "ديالكم", "البارح", "كيعطيو"]);
const DARIJA_LATIN = new Set(["dyal", "dial", "had", "chi", "chofo", "lbiro", "f", "ma", "tmchiwch", "bzaf", "daba", "wach",
  "machi", "hadi", "lw9t", "9bel", "msdod", "ghadi", "m9adin", "lwraq", "lwra9", "sandou9", "sanadi9"]);
const FRENCH = new Set(["le", "la", "les", "des", "est", "une", "un", "du", "de", "et", "dans", "pour", "avec", "à", "au", "sur", "qui"]);

export function detectLanguage(text) {
  const nAr = (text.match(ARABIC) || []).length;
  const nLat = (text.match(LATIN) || []).length;
  const words = text.toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  if (nAr && nLat && Math.min(nAr, nLat) / Math.max(nAr, nLat) > 0.3) return "mixte";
  if (nAr > nLat) return words.some((w) => DARIJA_AR.has(w)) ? "darija" : "ar";
  const darija = words.filter((w) => DARIJA_LATIN.has(w)).length
    + words.filter((w) => /[a-z][379]|[379][a-z]/.test(w)).length;
  const fr = words.filter((w) => FRENCH.has(w)).length;
  if (darija >= 2 && darija >= fr) return "darija-arabizi";
  return fr ? "fr" : "autre";
}

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function fingerprint(text) {
  const t = text.normalize("NFKC").toLowerCase()
    .replace(/https?:\/\/\S+|#[\p{L}\p{N}_]+|@[\p{L}\p{N}_]+/gu, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  return hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(t)));
}

export async function pseudonymize(author, secret) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(author));
  return "auteur_" + hex(sig).slice(0, 12);
}

export async function normalize(raw, secret) {
  return {
    post_id: raw.id,
    source: raw.source,
    text: raw.text.trim(),
    lang: detectLanguage(raw.text),
    author_pseudo: await pseudonymize(raw.author || "inconnu", secret),
    created_at: raw.created_at,
    reach: parseInt(raw.reach || 0, 10),
    shares: parseInt(raw.shares || 0, 10),
    author_account_age_days: raw.author_account_age_days ?? null,
    url: raw.url,
  };
}

export async function handle(raw, env) {
  const h = await fingerprint(raw.text);
  if (await env.SEEN.get(h)) return { post_id: raw.id, statut: "doublon ignoré" };
  const post = await normalize(raw, env.PSEUDO_SECRET || "change-moi");
  const res = await fetch(env.ANALYZE_URL, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(post),
  });
  if (!res.ok) throw new Error(`Azure analyze ${res.status}: ${await res.text()}`);
  await env.SEEN.put(h, raw.id, { expirationTtl: 60 * 60 * 24 * 30 });
  return { post_id: raw.id, statut: "envoyé à Azure", lang: post.lang, auteur: post.author_pseudo,
           analyse: await res.json().catch(() => null) };
}

export default {
  async queue(batch, env) {
    for (const msg of batch.messages) {
      try {
        console.log(JSON.stringify(await handle(msg.body, env)));
        msg.ack();
      } catch (e) {
        console.log(JSON.stringify({ post_id: msg.body?.id, erreur: String(e), nouvel_essai: true }));
        msg.retry({ delaySeconds: 30 });
      }
    }
  },
};
