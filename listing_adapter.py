import json
import urllib.request
import urllib.error

BASE_URL = "https://api.reefapi.com/funda/v1"


def _post(path, api_key, payload):
    req = urllib.request.Request(
        f"{BASE_URL}/{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
    "x-api-key": api_key,
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "FlipFinder-Limburg/1.0",
}
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode("utf-8"))

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"ReefAPI fout {e.code}: {detail}") from e

    except urllib.error.URLError as e:
        raise RuntimeError(f"ReefAPI niet bereikbaar: {e}") from e

    if not body.get("ok"):
        raise RuntimeError(
            f"ReefAPI fout: {body.get('error') or 'onbekende fout'}"
        )

    return body.get("data") or {}


def fetch_listings(api_key: str, area: str = "Limburg", page: int = 1):
    if not api_key:
        raise ValueError("REEFAPI_KEY ontbreekt")

    data = _post(
        "search",
        api_key,
        {
            "area": area,
            "sort": "newest",
            "page": page
        }
    )

    items = data.get("results") or []
    out = []

    for item in items:
        address = item.get("address") or {}
        price = item.get("price") or {}

        street = " ".join(
            str(x)
            for x in [
                address.get("street"),
                address.get("house_number"),
                address.get("house_number_suffix")
            ]
            if x not in (None, "")
        ).strip()

        url = item.get("url") or ""

        out.append({
            "source_id": url or str(
                item.get("global_id")
                or item.get("id")
                or ""
            ),
            "city": address.get("city") or "",
            "street": street,
            "postal_code": address.get("postal_code") or "",
            "price": price.get("amount_eur") or 0,
            "size_m2": item.get("surface_m2") or 0,
            "plot_m2": item.get("plot_m2") or 0,
            "energy_label": item.get("energy_label") or "",
            "property_type": item.get("object_type") or "",
            "build_year": None,
            "source_url": url,
            "publish_date": item.get("publish_date") or "",
            "image_url": item.get("primary_photo_url") or "",
        })

    return out
