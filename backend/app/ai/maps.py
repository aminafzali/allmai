"""Google Maps Platform Places API (New) Text Search via REST (no SDK).

Server-side key only (GOOGLE_MAPS_API_KEY). Used by the agent
`maps_search` tool; never exposed to the browser.
"""

import httpx

from app.core.config import get_settings

_BASE = "https://places.googleapis.com/v1/places:searchText"

_FIELDS = ",".join([
    "places.displayName",
    "places.formattedAddress",
    "places.rating",
    "places.userRatingCount",
    "places.location",
    "places.types",
    "places.googleMapsUri",
    "places.nationalPhoneNumber",
])


def search_places(query: str, max_results: int = 6,
                  language: str = "fa", timeout: float = 30.0) -> list[dict]:
    """Text-search places/businesses. Returns [{name, address, rating,
    ratings_total, lat, lng, types, maps_uri, phone}]. Raises RuntimeError
    when the key is missing or Google is unreachable."""
    api_key = get_settings().GOOGLE_MAPS_API_KEY
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is not configured")
    try:
        r = httpx.post(
            _BASE,
            headers={"Content-Type": "application/json",
                     "X-Goog-Api-Key": api_key,
                     "X-Goog-FieldMask": _FIELDS},
            json={"textQuery": (query or "").strip(),
                  "maxResultCount": max(1, min(10, max_results)),
                  "languageCode": language},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Maps request failed: {exc}") from exc
    out = []
    for p in data.get("places") or []:
        name = ((p.get("displayName") or {}).get("text") or "").strip()
        loc = p.get("location") or {}
        out.append({
            "name": name,
            "address": p.get("formattedAddress") or "",
            "rating": p.get("rating"),
            "ratings_total": p.get("userRatingCount"),
            "lat": loc.get("latitude"),
            "lng": loc.get("longitude"),
            "types": list(p.get("types") or []),
            "maps_uri": p.get("googleMapsUri") or "",
            "phone": p.get("nationalPhoneNumber") or "",
        })
    return out
