/** Browser-side executors for client tools (web_search / maps_search).
 *
 * Locked rule: searches run HERE (user's phone/laptop IP), NEVER from our
 * server. The server only sends a `tool_call` SSE event with a client_spec;
 * this module executes it and returns results for POST /chat/resume.
 * Public CORS proxies are a last-resort fallback inside the chain only.
 */

export type ToolCallFrame = {
  type: "tool_call";
  conversation_id: string;
  call_id: string;
  name: string;
  arguments: { query: string };
  client_spec?: any;
};

export type ToolResult = {
  call_id: string;
  ok: boolean;
  results: any[];
  served_by: string;
  error: string;
  debug?: string;
};

type Stage = (msg: string) => void;
const noop: Stage = () => {};

const fail = (call: ToolCallFrame, error: string): ToolResult => ({
  call_id: call.call_id,
  ok: false,
  results: [],
  served_by: "",
  error: error.slice(0, 300),
});

async function fetchTimeout(url: string, init: RequestInit, ms: number, signal?: AbortSignal) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  const onAbort = () => ctl.abort();
  signal?.addEventListener("abort", onAbort);
  try {
    return await fetch(url, { ...init, signal: ctl.signal });
  } finally {
    clearTimeout(t);
    signal?.removeEventListener("abort", onAbort);
  }
}

// ---------------- Overpass (maps_search) ----------------
// Radius search around a geocoded center (area-based queries return
// empty for cities without boundary relations — radius always works).

const CITY_COORDS: Record<string, [number, number]> = {
  "تهران": [35.6892, 51.389], "مشهد": [36.2605, 59.6168],
  "اصفهان": [32.6546, 51.668], "کرج": [35.84, 50.9391],
  "شیراز": [29.5918, 52.5837], "تبریز": [38.0962, 46.2738],
  "قم": [34.6399, 50.8759], "اهواز": [31.3183, 48.6706],
  "کرمانشاه": [34.3142, 47.065], "ارومیه": [37.5494, 45.0689],
  "کرمان": [30.2852, 57.0648], "یزد": [31.8974, 54.3569],
  "رشت": [37.2808, 49.5832], "زاهدان": [29.4963, 60.8629],
  "همدان": [34.7983, 48.5148], "بندرعباس": [27.1832, 56.2666],
  "اراک": [34.0917, 49.6892], "قزوین": [36.2688, 50.0041],
  "زنجان": [36.6769, 48.485], "گرگان": [36.8417, 54.4348],
  "ساری": [36.5659, 53.0586],
};

const CATEGORY_RULES: Array<{ keys: string[]; filter: string }> = [
  { keys: ["رستوران", "restaurant"], filter: '["amenity"="restaurant"]' },
  { keys: ["فست‌فود", "فست فود", "fastfood", "fast food"], filter: '["amenity"="fast_food"]' },
  { keys: ["کافه", "cafe", "کافی", "قهوه", "coffee"], filter: '["amenity"="cafe"]' },
  { keys: ["هتل", "hotel", "مسافرخانه"], filter: '["tourism"="hotel"]' },
  { keys: ["بیمارستان", "hospital"], filter: '["amenity"="hospital"]' },
  { keys: ["درمانگاه", "clinic"], filter: '["amenity"="clinic"]' },
  { keys: ["داروخانه", "pharmacy"], filter: '["amenity"="pharmacy"]' },
  { keys: ["بانک", "bank", "عابربانک", "خودپرداز"], filter: '["amenity"="bank"]' },
  { keys: ["پمپ بنزین", "بنزین", "fuel", "gas station", "پمپ"], filter: '["amenity"="fuel"]' },
  { keys: ["پارک", "park", "بوستان"], filter: '["leisure"="park"]' },
  { keys: ["موزه", "museum"], filter: '["tourism"="museum"]' },
  { keys: ["سینما", "cinema", "تئاتر", "theatre", "theater"], filter: '["amenity"="cinema"]' },
  { keys: ["مدرسه", "school"], filter: '["amenity"="school"]' },
  { keys: ["دانشگاه", "university"], filter: '["amenity"="university"]' },
  { keys: ["مسجد", "mosque"], filter: '["amenity"="place_of_worship"]["religion"="muslim"]' },
  { keys: ["پارکینگ", "parking"], filter: '["amenity"="parking"]' },
  { keys: ["مترو", "metro", "subway"], filter: '["railway"="station"]' },
  { keys: ["فرودگاه", "airport"], filter: '["aeroway"="aerodrome"]' },
  { keys: ["نانوایی", "bakery"], filter: '["shop"="bakery"]' },
  { keys: ["سوپرمارکت", "supermarket", "فروشگاه", "مغازه", "shop", "store"], filter: '["shop"~"supermarket|convenience|mall|department_store"]' },
  { keys: ["آرایشگاه", "hairdresser", "salon"], filter: '["shop"="hairdresser"]' },
  { keys: ["باشگاه", "gym", "fitness"], filter: '["leisure"="fitness_centre"]' },
];

const CITY_FA = Object.keys(CITY_COORDS);

const IRAN_CENTER: [number, number] = [32.0, 54.0]; // last resort, wide radius

function normFa(s: string) {
  return (s || "").toLowerCase().replace(/[\u064B-\u0652]/g, "").replace(/ي/g, "ی").replace(/ك/g, "ک").trim();
}

function escapeQl(s: string) {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').slice(0, 120);
}

export function buildOverpassQL(query: string, lat: number, lon: number, radiusM = 30000, maxResults = 100) {
  const nq = normFa(query);
  const cat = CATEGORY_RULES.find((r) => r.keys.some((k) => nq.includes(normFa(k))));
  let filter: string;
  if (cat) {
    filter = cat.filter;
  } else {
    const words = (query.match(/[\w\u0600-\u06FF]{3,}/g) ?? []).slice(0, 3);
    filter = words.length ? `["name"~"${words.map(escapeQl).join("|")}",i]` : '["name"~".",i]';
  }
  return `[out:json][timeout:25];(nwr${filter}(around:${radiusM},${lat.toFixed(4)},${lon.toFixed(4)}););out center tags ${maxResults};`;
}

async function geocodeCity(query: string, signal?: AbortSignal): Promise<{ lat: number; lon: number; via: string }> {
  const nq = normFa(query);
  const hit = Object.entries(CITY_COORDS).find(([c]) => nq.includes(c));
  if (hit) return { lat: hit[1][0], lon: hit[1][1], via: `table:${hit[0]}` };
  try {
    const r = await fetchTimeout(
      "https://nominatim.openstreetmap.org/search?" +
        new URLSearchParams({ q: query.trim(), format: "json", limit: "1", "accept-language": "fa" }),
      {},
      15000,
      signal
    );
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const items = await r.json();
    if (items?.length) return { lat: Number(items[0].lat), lon: Number(items[0].lon), via: "nominatim" };
  } catch {
    /* fall through */
  }
  return { lat: IRAN_CENTER[0], lon: IRAN_CENTER[1], via: "iran-wide" };
}

function mapOverpassElement(el: any) {
  const tags = el.tags ?? {};
  const name = String(tags.name ?? tags["name:fa"] ?? "").trim();
  if (!name) return null;
  const lat = el.lat ?? el.center?.lat ?? null;
  const lon = el.lon ?? el.center?.lon ?? null;
  const addrParts = ["addr:housenumber", "addr:road", "addr:suburb", "addr:city"]
    .map((k) => tags[k])
    .filter(Boolean);
  return {
    name: name.slice(0, 300),
    address: addrParts.join("، ").slice(0, 1000),
    phone: String(tags.phone ?? tags["contact:phone"] ?? "").slice(0, 100),
    hours: String(tags.opening_hours ?? "").slice(0, 300),
    website: String(tags.website ?? tags["contact:website"] ?? "").slice(0, 512),
    lat: typeof lat === "number" ? lat : null,
    lng: typeof lon === "number" ? lon : null,
    raw: { osm_type: el.type, osm_id: el.id, tags },
  };
}

async function executeOverpass(spec: any, query: string, signal?: AbortSignal, onStage: Stage = noop) {
  const attempts: any[] = spec?.attempts ?? [
    { endpoint: "overpass-de", url: "https://overpass-api.de/api/interpreter" },
    { endpoint: "overpass-kumi", url: "https://overpass.kumi.systems/api/interpreter" },
  ];
  const maxResults = spec?.max_results ?? 100;
  const timeout = (spec?.timeout_s ?? 45) * 1000;
  let lastError = "no endpoint";
  const geo = await geocodeCity(query, signal);
  onStage(`مختصات (${geo.via}): ${geo.lat.toFixed(3)}, ${geo.lon.toFixed(3)}`);
  // pass 1: 30km — pass 2 (when empty): 60km widen
  for (const radius of [30000, 60000]) {
    const ql = buildOverpassQL(query, geo.lat, geo.lon, radius, maxResults);
    onStage(`Overpass QL (شعاع ${radius / 1000}km): ${ql.slice(0, 110)}…`);
    for (const a of attempts) {
      const endpoint = a.endpoint ?? a.url;
      onStage(`تلاش ${endpoint}…`);
      try {
        const r = await fetchTimeout(a.url, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: "data=" + encodeURIComponent(ql),
        }, timeout, signal);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const places = ((data?.elements ?? []) as any[])
          .map(mapOverpassElement)
          .filter(Boolean)
          .slice(0, maxResults);
        onStage(`${endpoint}: ${places.length} مکان`);
        if (places.length) return { results: places, served_by: endpoint };
        lastError = "empty result";
      } catch (e: any) {
        lastError = e?.name === "AbortError" ? "timeout" : String(e?.message ?? e);
        onStage(`${endpoint}: خطا (${lastError})`);
      }
    }
    if (radius === 60000) break;
  }
  return { results: [] as any[], served_by: "", error: lastError };
}

// ---------------- DuckDuckGo (web_search) ----------------

function resolveDdgHref(href: string) {
  try {
    if (href.startsWith("//")) href = "https:" + href;
    const u = new URL(href, "https://duckduckgo.com");
    const uddg = u.searchParams.get("uddg");
    if (uddg) return decodeURIComponent(uddg);
    return href;
  } catch {
    return href;
  }
}

function parseDdgHtml(html: string, maxResults: number) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const out: Array<{ title: string; uri: string; snippet: string }> = [];
  doc.querySelectorAll("a.result__a").forEach((a) => {
    if (out.length >= maxResults) return;
    const uri = resolveDdgHref((a as HTMLAnchorElement).href || "");
    if (!/^https?:\/\//i.test(uri)) return;
    const row = (a as HTMLElement).closest("tr") ?? (a as HTMLElement).parentElement;
    const snip = row?.querySelector(".result__snippet")?.textContent?.trim() ?? "";
    out.push({
      title: (a.textContent?.trim() || uri).slice(0, 300),
      uri: uri.slice(0, 1000),
      snippet: snip.slice(0, 2000),
    });
  });
  return out;
}

async function executeDDG(spec: any, query: string, signal?: AbortSignal, onStage: Stage = noop) {
  const attempts: any[] = spec?.attempts ?? [
    { endpoint: "direct", url: "https://html.duckduckgo.com/html/" },
  ];
  const timeout = 25000;
  let lastError = "no endpoint";
  for (const a of attempts) {
    const endpoint = a.endpoint ?? a.url;
    onStage(`تلاش ${endpoint}…`);
    try {
      const body = "q=" + encodeURIComponent(query);
      const url: string = a.endpoint === "proxy" && a.url.includes("allorigins")
        ? a.url + encodeURIComponent("https://html.duckduckgo.com/html/?" + body)
        : a.url;
      const r = await fetchTimeout(url, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: a.endpoint === "proxy" && a.url.includes("allorigins") ? undefined : body,
      }, timeout, signal);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const results = parseDdgHtml(await r.text(), 8);
      onStage(`${endpoint}: ${results.length} نتیجه`);
      if (results.length) return { results, served_by: endpoint };
      lastError = "empty result";
    } catch (e: any) {
      lastError = e?.name === "AbortError" ? "timeout" : String(e?.message ?? e);
      onStage(`${endpoint}: خطا (${lastError})`);
    }
  }
  return { results: [] as any[], served_by: "", error: lastError };
}

// ---------------- dispatcher ----------------

export async function executeToolCall(
  call: ToolCallFrame, signal?: AbortSignal, onStage: Stage = noop
): Promise<ToolResult> {
  const query = (call.arguments?.query ?? "").trim();
  if (!query) return fail(call, "empty query");
  const withDebug = (r: ToolResult, dbg: string): ToolResult => ({ ...r, debug: dbg.slice(0, 300) });
  try {
    if (call.name === "maps_search") {
      const stages: string[] = [];
      const r = await executeOverpass(call.client_spec, query, signal, (m) => { stages.push(m); onStage(m); });
      const out: ToolResult = r.results.length
        ? { call_id: call.call_id, ok: true, results: r.results, served_by: r.served_by, error: "" }
        : { call_id: call.call_id, ok: false, results: [], served_by: r.served_by, error: (r as any).error ?? "empty" };
      return withDebug(out, stages.join(" | "));
    }
    if (call.name === "web_search") {
      const stages: string[] = [];
      const r = await executeDDG(call.client_spec, query, signal, (m) => { stages.push(m); onStage(m); });
      const out: ToolResult = r.results.length
        ? { call_id: call.call_id, ok: true, results: r.results, served_by: r.served_by, error: "" }
        : { call_id: call.call_id, ok: false, results: [], served_by: r.served_by, error: (r as any).error ?? "empty" };
      return withDebug(out, stages.join(" | "));
    }
    return fail(call, `unknown tool ${call.name}`);
  } catch (e: any) {
    return fail(call, e?.name === "AbortError" ? "aborted" : String(e?.message ?? e));
  }
}
