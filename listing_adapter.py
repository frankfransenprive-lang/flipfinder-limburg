import json
import urllib.request
import urllib.error
from statistics import median

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
            body = json.loads(
                r.read().decode("utf-8")
            )

    except urllib.error.HTTPError as e:
        detail = e.read().decode(
            "utf-8",
            errors="ignore"
        )
        raise RuntimeError(
            f"ReefAPI fout {e.code}: {detail}"
        ) from e

    except urllib.error.URLError as e:
        raise RuntimeError(
            f"ReefAPI niet bereikbaar: {e}"
        ) from e

    if not body.get("ok"):
        raise RuntimeError(
            f"ReefAPI fout: "
            f"{body.get('error') or 'onbekende fout'}"
        )

    return body.get("data") or {}


def _normalize_listing(item):
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

    return {
        "source_id": (
            url
            or str(
                item.get("global_id")
                or item.get("id")
                or ""
            )
        ),
        "city": address.get("city") or "",
        "street": street,
        "postal_code":
            address.get("postal_code") or "",
        "price":
            price.get("amount_eur") or 0,
        "size_m2":
            item.get("surface_m2") or 0,
        "plot_m2":
            item.get("plot_m2") or 0,
        "energy_label":
            item.get("energy_label") or "",
        "property_type":
            item.get("object_type") or "",
        "build_year": None,
        "source_url": url,
        "publish_date":
            item.get("publish_date") or "",
        "image_url":
            item.get("primary_photo_url") or "",
    }


def fetch_listings(
    api_key: str,
    area: str = "Limburg",
    page: int = 1
):
    if not api_key:
        raise ValueError(
            "REEFAPI_KEY ontbreekt"
        )

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

    return [
        _normalize_listing(item)
        for item in items
    ]


def fetch_comparables(
    api_key: str,
    city: str,
    target_size_m2: float,
    property_type: str = "",
    exclude_source_id: str = "",
    max_pages: int = 2
):
    """
    Zoekt vergelijkbare actuele woningen.

    We zoeken in dezelfde plaats en filteren
    vervolgens lokaal op:
    - ongeveer hetzelfde woonoppervlak
    - hetzelfde woningtype indien mogelijk

    Resultaat is gebaseerd op VRAAGPRIJZEN,
    niet op gerealiseerde verkoopprijzen.
    """

    if not api_key:
        raise ValueError(
            "REEFAPI_KEY ontbreekt"
        )

    if not city:
        return {
            "comparables": [],
            "count": 0,
            "median_price_per_m2": 0,
            "estimated_resale": 0,
            "basis": "geen plaats"
        }

    try:
        target_size_m2 = float(
            target_size_m2 or 0
        )
    except (TypeError, ValueError):
        target_size_m2 = 0

    all_items = []

    for page in range(
        1,
        max(1, max_pages) + 1
    ):
        data = _post(
            "search",
            api_key,
            {
                "area": city,
                "sort": "newest",
                "page": page
            }
        )

        results = data.get("results") or []

        if not results:
            break

        all_items.extend(
            _normalize_listing(item)
            for item in results
        )

    target_type = str(
        property_type or ""
    ).strip().lower()

    comparable_items = []

    for item in all_items:
        source_id = str(
            item.get("source_id") or ""
        )

        if (
            exclude_source_id
            and source_id == exclude_source_id
        ):
            continue

        price = float(
            item.get("price") or 0
        )

        size = float(
            item.get("size_m2") or 0
        )

        if price <= 0 or size <= 0:
            continue

        # Woonoppervlak maximaal ongeveer
        # 25% groter of kleiner.
        if target_size_m2 > 0:
            min_size = target_size_m2 * 0.75
            max_size = target_size_m2 * 1.25

            if not (
                min_size <= size <= max_size
            ):
                continue

        item_type = str(
            item.get("property_type") or ""
        ).strip().lower()

        # Alleen zelfde hoofdtype wanneer
        # ReefAPI dit gelijk aanlevert.
        if (
            target_type
            and item_type
            and item_type != target_type
        ):
            continue

        price_per_m2 = price / size

        comparable_items.append({
            **item,
            "price_per_m2":
                round(price_per_m2)
        })

    # Als exact woningtype te weinig
    # vergelijkingen geeft, opnieuw zonder
    # woningtype-filter.
    if (
        len(comparable_items) < 3
        and target_type
    ):
        comparable_items = []

        for item in all_items:
            source_id = str(
                item.get("source_id") or ""
            )

            if (
                exclude_source_id
                and source_id ==
                exclude_source_id
            ):
                continue

            price = float(
                item.get("price") or 0
            )

            size = float(
                item.get("size_m2") or 0
            )

            if price <= 0 or size <= 0:
                continue

            if target_size_m2 > 0:
                min_size = (
                    target_size_m2 * 0.75
                )
                max_size = (
                    target_size_m2 * 1.25
                )

                if not (
                    min_size
                    <= size
                    <= max_size
                ):
                    continue

            comparable_items.append({
                **item,
                "price_per_m2":
                    round(price / size)
            })

    if not comparable_items:
        return {
            "comparables": [],
            "count": 0,
            "median_price_per_m2": 0,
            "estimated_resale": 0,
            "basis":
                "geen geschikte vergelijkingen"
        }

    # Extremen beperken:
    # sorteren op €/m² en bij voldoende
    # woningen hoogste en laagste 10% weglaten.
    comparable_items.sort(
        key=lambda x:
        x["price_per_m2"]
    )

    if len(comparable_items) >= 10:
        trim = max(
            1,
            int(
                len(comparable_items) * 0.10
            )
        )

        comparable_items = (
            comparable_items[
                trim:-trim
            ]
        )

    prices_per_m2 = [
        x["price_per_m2"]
        for x in comparable_items
    ]

    median_price_per_m2 = median(
        prices_per_m2
    )

    estimated_resale = (
        median_price_per_m2 *
        target_size_m2
        if target_size_m2 > 0
        else 0
    )

    return {
        "comparables":
            comparable_items[:10],
        "count":
            len(comparable_items),
        "median_price_per_m2":
            round(median_price_per_m2),
        "estimated_resale":
            round(
                estimated_resale / 1000
            ) * 1000
            if estimated_resale
            else 0,
        "basis":
            "actuele vergelijkbare vraagprijzen"
    }
