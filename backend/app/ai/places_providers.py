"""Places providers behind one interface (swappable, never hard-wired).

Locked rules (same as web_providers):
- Tools depend ONLY on ``PlacesProvider`` / ``ProviderChain`` here.
- Default chain is client-side only: Overpass runs in the USER'S BROWSER
  (see web/lib/browserTools.ts). NO Overpass call may originate from this
  server — there is intentionally no server-side Overpass implementation.
- Google Places stays as the dormant server-side upgrade path (needs
  GOOGLE_MAPS_API_KEY). Chain order comes from config.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class PlaceResult:
    name: str = ""
    address: str = ""
    phone: str = ""
    hours: str = ""
    website: str = ""
    lat: float | None = None
    lng: float | None = None
    maps_uri: str = ""
    rating: float | None = None


class PlacesProvider:
    """One swappable places backend."""

    name: str = "base"
    side: str = "server"  # "server" (runs here) | "client" (runs in browser)

    def search(self, query: str, max_results: int = 6,
               timeout: float = 30.0) -> list[PlaceResult]:
        raise NotImplementedError

    def client_spec(self) -> dict:
        raise NotImplementedError(f"{self.name} has no browser executor")


def validate_place_results(items: object, max_items: int = 30) -> list[dict]:
    """Clean UNTRUSTED browser-supplied places into safe dicts.

    Used by the /chat/resume endpoint before DB insert. Caps count and
    field lengths, coerces floats, keeps http(s) URLs only. Never raises.
    """
    out: list[dict] = []
    if not isinstance(items, list):
        return out

    _KNOWN = {"name", "address", "phone", "hours", "opening_hours",
              "website", "lat", "lng", "lon", "maps_uri", "rating"}

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _u(v):
        u = str(v or "")[:1000].strip()
        return u if u.lower().startswith(("http://", "https://")) else ""

    for it in items[:max(1, max_items)]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "")[:300].strip()
        if not name:
            continue
        out.append({
            "name": name,
            "address": str(it.get("address") or "")[:1000].strip(),
            "phone": str(it.get("phone") or "")[:100].strip(),
            "hours": str(it.get("hours") or it.get("opening_hours") or "")[:300].strip(),
            "website": _u(it.get("website")),
            "lat": _f(it.get("lat")),
            "lng": _f(it.get("lng") or it.get("lon")),
            "maps_uri": _u(it.get("maps_uri")),
            "rating": _f(it.get("rating")),
            # Full provider payload (bounded) for the leads.raw column.
            "raw": {str(k)[:80]: str(v)[:500] for k, v in it.items()
                    if k not in _KNOWN},
        })
    return out


class OverpassClientSpec(PlacesProvider):
    """OpenStreetMap Overpass: browser-ONLY descriptor (no server impl).

    The browser posts Overpass QL (built from the user query, see
    browserTools.ts) to a public Overpass endpoint — both send
    ``Access-Control-Allow-Origin: *``. Endpoints are tried in order.
    """

    name = "overpass"
    side = "client"

    def client_spec(self) -> dict:
        return {
            "name": "overpass",
            "side": "client",
            "attempts": [
                {"endpoint": "overpass-de",
                 "url": "https://overpass-api.de/api/interpreter",
                 "method": "POST"},
                {"endpoint": "overpass-kumi",
                 "url": "https://overpass.kumi.systems/api/interpreter",
                 "method": "POST"},
            ],
            "timeout_s": 45,
            "max_results": 100,
        }


class GooglePlacesProvider(PlacesProvider):
    """Google Maps Platform Places (server side). Dormant unless
    GOOGLE_MAPS_API_KEY is set."""

    name = "google-places"
    side = "server"

    def search(self, query: str, max_results: int = 6,
               timeout: float = 30.0) -> list[PlaceResult]:
        from app.ai.maps import search_places

        try:
            places = search_places(query or "", max_results=max_results)
        except Exception as exc:
            log.warning("google places unavailable: %s", exc)
            return []
        out = []
        for p in places:
            out.append(PlaceResult(
                name=p.get("name") or "", address=p.get("address") or "",
                phone=p.get("phone") or "", hours="", website="",
                lat=p.get("lat"), lng=p.get("lng"),
                maps_uri=p.get("maps_uri") or "", rating=p.get("rating")))
        return out


PROVIDERS: dict[str, PlacesProvider] = {
    "overpass": OverpassClientSpec(),
    "overpass-server": None,  # bound below
    "google-places": GooglePlacesProvider(),
}


_OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# Major Iranian cities: geocode fallback when Nominatim is unreachable.
# (Nominatim itself is tried first, live, for any city spelling.)
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "تهران": (35.6892, 51.3890),
    "مشهد": (36.2605, 59.6168),
    "اصفهان": (32.6546, 51.6680),
    "کرج": (35.8400, 50.9391),
    "شیراز": (29.5918, 52.5837),
    "تبریز": (38.0962, 46.2738),
    "قم": (34.6399, 50.8759),
    "اهواز": (31.3183, 48.6706),
    "کرمانشاه": (34.3142, 47.0650),
    "ارومیه": (37.5494, 45.0689),
    "کرمان": (30.2852, 57.0648),
    "یزد": (31.8974, 54.3569),
    "رشت": (37.2808, 49.5832),
    "زاهدان": (29.4963, 60.8629),
    "همدان": (34.7983, 48.5148),
    "بندرعباس": (27.1832, 56.2666),
    "اراک": (34.0917, 49.6892),
    "قزوین": (36.2688, 50.0041),
    "زنجان": (36.6769, 48.4850),
    "گرگان": (36.8417, 54.4348),
    "ساری": (36.5659, 53.0586),
}

_CATEGORY_TAGS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("رستوران", "restaurant"), '["amenity"="restaurant"]'),
    (("فست‌فود", "فست فود", "fastfood", "fast food"), '["amenity"="fast_food"]'),
    (("کافه", "کافی", "قهوه", "coffee", "cafe"), '["amenity"="cafe"]'),
    (("هتل", "hotel", "مسافرخانه"), '["tourism"="hotel"]'),
    (("بیمارستان", "hospital"), '["amenity"="hospital"]'),
    (("داروخانه", "pharmacy"), '["amenity"="pharmacy"]'),
    (("آرایشگاه", "hairdresser", "salon", "آرایش"), '["shop"="hairdresser"]'),
    (("بانک", "bank", "عابربانک"), '["amenity"="bank"]'),
    (("پمپ بنزین", "بنزین", "fuel"), '["amenity"="fuel"]'),
    (("نانوایی", "bakery"), '["shop"="bakery"]'),
    (("سوپرمارکت", "supermarket", "فروشگاه", "مغازه"), '["shop"~"supermarket|convenience|mall|department_store"]'),
    (("پارک", "park", "بوستان"), '["leisure"="park"]'),
    (("موزه", "museum"), '["tourism"="museum"]'),
    (("سینما", "cinema", "تئاتر"), '["amenity"="cinema"]'),
    (("مدرسه", "school"), '["amenity"="school"]'),
    (("دانشگاه", "university"), '["amenity"="university"]'),
    (("مسجد", "mosque"), '["amenity"="place_of_worship"]["religion"="muslim"]'),
    (("باشگاه", "gym", "fitness"), '["leisure"="fitness_centre"]'),
)


def _overpass_headers() -> dict:
    # Proven live (3/3): overpass.de 406s without Origin/Referer (anti-bot
    # check); with them it returns 200. Browsers send these automatically.
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fa,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "http://localhost:3001",
        "Referer": "http://localhost:3001/",
    }


def geocode_city(query: str, timeout: float = 20.0) -> tuple[float, float] | None:
    """City center via Nominatim, else the built-in table. None if unknown."""
    import httpx

    nq = (query or "").lower()
    for city, ll in _CITY_COORDS.items():
        if city in nq:
            return ll
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": (query or "").strip(), "format": "json",
                    "limit": 1, "accept-language": "fa"},
            headers={"User-Agent": "AllMai/1.0"}, timeout=timeout)
        r.raise_for_status()
        items = r.json()
        if items:
            return float(items[0]["lat"]), float(items[0]["lon"])
    except Exception as exc:
        log.warning("nominatim geocode failed: %s", exc)
    return None


def _parse_overpass_elements(elements: list) -> list[PlaceResult]:
    out: list[PlaceResult] = []
    for el in elements or []:
        tags = (el or {}).get("tags") or {}
        name = str(tags.get("name") or tags.get("name:fa") or "").strip()[:300]
        if not name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        addr = "، ".join(str(tags[k]) for k in
                         ("addr:housenumber", "addr:road", "addr:suburb", "addr:city")
                         if tags.get(k))[:1000]
        out.append(PlaceResult(
            name=name, address=addr,
            phone=str(tags.get("phone") or tags.get("contact:phone") or "")[:100],
            hours=str(tags.get("opening_hours") or "")[:300],
            website=str(tags.get("website") or tags.get("contact:website") or "")[:512],
            lat=float(lat) if lat is not None else None,
            lng=float(lon) if lon is not None else None))
    return out


def search_overpass_radius(query: str, lat: float, lon: float,
                           radius_m: int = 30000, max_results: int = 100,
                           timeout: float = 60.0) -> tuple[list[PlaceResult], str]:
    """Radius search around a center. Returns (places, served_by)."""
    import httpx

    nq = (query or "").lower()
    filt = next((f for keys, f in _CATEGORY_TAGS if any(k in nq for k in keys)),
                None)
    if filt is None:
        import re as _re

        words = [w for w in _re.findall(r"[\w\u0600-\u06FF]{3,}", query or "")
                 if len(w) >= 3][:3]
        filt = f'["name"~"{"|".join(words) or query.strip()}",i]' if words \
            else '["name"~".",i]'
    ql = (f"[out:json][timeout:25];(nwr{filt}"
          f"(around:{radius_m},{lat:.4f},{lon:.4f}););out center tags {max_results};")
    last: Exception | None = None
    for url in _OVERPASS_URLS:
        try:
            r = httpx.post(url, content=("data=" + __import__(
                "urllib.parse", fromlist=["quote"]).quote(ql)).encode("utf-8"),
                           headers=_overpass_headers(), timeout=timeout)
            r.raise_for_status()
            els = r.json().get("elements", [])
            log.info("overpass %s: %d elements", url, len(els))
            return _parse_overpass_elements(els)[:max_results], url
        except Exception as exc:
            last = exc
            log.warning("overpass %s failed: %s", url, exc)
    raise RuntimeError(f"all overpass endpoints failed: {last}")


class OverpassServerProvider(PlacesProvider):
    """Overpass radius search executed HERE (chain remainder when the
    browser fails). Proven live: 92 Kerman restaurants, 29 with phones."""

    name = "overpass-server"
    side = "server"

    def search(self, query: str, max_results: int = 100,
               timeout: float = 90.0) -> list[PlaceResult]:
        center = geocode_city(query)
        if center is None:
            log.warning("overpass-server: unknown city in query")
            return []
        try:
            out, _ = search_overpass_radius(
                query, center[0], center[1], max_results=max_results,
                timeout=timeout)
            return out
        except Exception as exc:
            log.warning("overpass-server unavailable: %s", exc)
            return []


PROVIDERS["overpass-server"] = OverpassServerProvider()


@dataclass
class ProviderChain:
    """Ordered [primary, *fallbacks]: first non-empty result wins.

    Only ``side="server"`` entries run here; client-side links run in
    the browser (their failure is reported back via /chat/resume, which
    then continues down the server-side remainder of the chain).
    """

    providers: list[PlacesProvider] = field(default_factory=list)

    def run(self, query: str, max_results: int = 6,
            timeout_each: float = 30.0) -> tuple[list[PlaceResult], str]:
        for p in self.providers:
            if getattr(p, "side", "server") != "server":
                continue
            t0 = time.perf_counter()
            try:
                out = p.search(query, max_results=max_results,
                               timeout=timeout_each) or []
            except Exception as exc:
                log.warning("places provider %s failed: %s", p.name, exc)
                out = []
            if out:
                log.info("maps_search served by %s (%.1fs)", p.name,
                         time.perf_counter() - t0)
                return out, p.name
        return [], ""


def get_places_chain() -> ProviderChain:
    """Server-side remainder of the chain from config (PLACES_PROVIDER +
    comma PLACES_FALLBACKS). Default install: client-only Overpass, so
    this is empty and nothing runs here."""
    from app.core.config import get_settings

    s = get_settings()
    names = [s.PLACES_PROVIDER] + [
        n.strip() for n in (s.PLACES_FALLBACKS or "").split(",") if n.strip()]
    providers = [PROVIDERS[n] for n in dict.fromkeys(names) if n in PROVIDERS]
    return ProviderChain([p for p in providers if p.side == "server"])
