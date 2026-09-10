"""
Authorised listing feed adapter skeleton.

Expected normalized property:
{
  "source_id": "...",
  "city": "...",
  "street": "...",
  "price": 250000,
  "size_m2": 120,
  "plot_m2": 200,
  "energy_label": "D",
  "property_type": "Tussenwoning",
  "build_year": 1975,
  "source_url": "https://..."
}
"""

def fetch_listings(api_key: str):
    # Add official feed/API call here.
    # Never scrape a source if its terms disallow it.
    return []
    import json
import urllib.request
import urllib.error


REEF_URL = "https://api.reefapi.com/funda/v1/search"


def fetch_listings(api_key: str, area: str = "Limburg"):
    if not api_key:
        raise ValueError("REEFAPI_KEY ontbreekt")

    payload = json.dumps({
        "area": area
    }).encode("utf-8")

    req = urllib.request.Request(
        REEF_URL,
        data=payload,
        method="POST",
        headers={
            "x-api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"ReefAPI fout {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"ReefAPI niet bereikbaar: {e}")

    # ReefAPI kan resultaten op verschillende manieren teruggeven.
    raw_items = (
        data.get("results")
        or data.get("items")
        or data.get("listings")
        or data.get("properties")
        or []
    )

    normalized = []

    for item in raw_items:
        address = item.get("address") or {}
        price = item.get("price") or item.get("asking_price") or 0

        if isinstance(price, dict):
            price = (
                price.get("value")
                or price.get("amount")
                or price.get("asking")
                or 0
            )

        city = (
            item.get("city")
            or address.get("city")
            or item.get("place")
            or ""
        )

        street = (
            item.get("street")
            or address.get("street")
            or address.get("full")
            or item.get("address")
            or ""
        )

        source_id = str(
            item.get("id")
            or item.get("object_id")
            or item.get("listing_id")
            or item.get("url")
            or f"{city}-{street}-{price}"
        )

        size_m2 = (
            item.get("living_area")
            or item.get("size_m2")
            or item.get("area")
            or 0
        )

        plot_m2 = (
            item.get("plot_area")
            or item.get("plot_m2")
            or item.get("lot_size")
            or 0
        )

        energy_label = (
            item.get("energy_label")
            or item.get("energyLabel")
            or ""
        )

        property_type = (
            item.get("property_type")
            or item.get("type")
            or item.get("house_type")
            or ""
        )

        build_year = (
            item.get("build_year")
            or item.get("year_built")
            or item.get("construction_year")
            or None
        )

        source_url = (
            item.get("url")
            or item.get("source_url")
            or item.get("link")
            or ""
        )

        normalized.append({
            "source_id": source_id,
            "city": city,
            "street": street,
            "price": price,
            "size_m2": size_m2,
            "plot_m2": plot_m2,
            "energy_label": energy_label,
            "property_type": property_type,
            "build_year": build_year,
            "source_url": source_url,
        })

    return normalized
