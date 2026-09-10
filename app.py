from flask import Flask, jsonify, request, send_from_directory
import sqlite3
import os
from pathlib import Path

from listing_adapter import fetch_listings, fetch_comparables


BASE = Path(__file__).resolve().parent
DB = BASE / "flipfinder.db"

app = Flask(
    __name__,
    static_folder=str(BASE),
    static_url_path=""
)


DEFAULTS = {
    "transfer_tax_pct": 8.0,
    "notary_and_advice": 3000,
    "interest_pct_year": 8.0,
    "holding_months": 6,
    "selling_cost_pct": 2.0,
    "renovation_contingency_pct": 10.0,
    "target_profit": 30000,
}


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def column_exists(c, table, column):
    rows = c.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return any(
        row["name"] == column
        for row in rows
    )


def init_db():
    c = conn()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS properties(
        source_id TEXT PRIMARY KEY,
        city TEXT,
        street TEXT,
        price REAL,
        size_m2 REAL,
        plot_m2 REAL,
        energy_label TEXT,
        property_type TEXT,
        build_year INTEGER,
        renovation_estimate REAL,
        resale_estimate REAL,
        source_url TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        min_profit REAL,
        min_roi REAL,
        max_price REAL,
        city TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    extra_columns = {
        "postal_code": "TEXT",
        "publish_date": "TEXT",
        "image_url": "TEXT",
        "comparable_count": "INTEGER DEFAULT 0",
        "median_price_per_m2": "REAL DEFAULT 0",
        "valuation_basis": "TEXT",
    }

    for name, sql_type in extra_columns.items():
        if not column_exists(
            c,
            "properties",
            name
        ):
            c.execute(
                f"""
                ALTER TABLE properties
                ADD COLUMN {name} {sql_type}
                """
            )

    c.commit()
    c.close()


def estimate_renovation(item):
    size = float(
        item.get("size_m2") or 0
    )

    label = str(
        item.get("energy_label") or ""
    ).upper()

    property_type = str(
        item.get("property_type") or ""
    ).lower()

    if size <= 0:
        return 40000

    cost_per_m2 = 350

    if label == "D":
        cost_per_m2 += 40
    elif label == "E":
        cost_per_m2 += 80
    elif label == "F":
        cost_per_m2 += 120
    elif label == "G":
        cost_per_m2 += 160

    if (
        "apartment" in property_type
        or "appartement" in property_type
    ):
        cost_per_m2 -= 50

    if (
        "detached" in property_type
        or "vrijstaand" in property_type
    ):
        cost_per_m2 += 75

    renovation = (
        size * cost_per_m2
    )

    return (
        round(renovation / 1000)
        * 1000
    )


def fallback_resale(
    item,
    renovation
):
    price = float(
        item.get("price") or 0
    )

    if price <= 0:
        return 0

    estimated = (
        price
        + renovation * 1.55
    )

    return (
        round(estimated / 1000)
        * 1000
    )


def calculate_flip(item):
    price = float(
        item.get("price") or 0
    )

    renovation = float(
        item.get(
            "renovation_estimate"
        ) or 0
    )

    resale = float(
        item.get(
            "resale_estimate"
        ) or 0
    )

    transfer_tax = (
        price
        * DEFAULTS[
            "transfer_tax_pct"
        ]
        / 100
    )

    notary = DEFAULTS[
        "notary_and_advice"
    ]

    contingency = (
        renovation
        * DEFAULTS[
            "renovation_contingency_pct"
        ]
        / 100
    )

    financed_amount = (
        price
        + transfer_tax
        + renovation
        + contingency
    )

    financing_cost = (
        financed_amount
        * DEFAULTS[
            "interest_pct_year"
        ]
        / 100
        * (
            DEFAULTS[
                "holding_months"
            ] / 12
        )
    )

    selling_cost = (
        resale
        * DEFAULTS[
            "selling_cost_pct"
        ]
        / 100
    )

    total_investment = (
        price
        + transfer_tax
        + notary
        + renovation
        + contingency
        + financing_cost
        + selling_cost
    )

    profit = (
        resale
        - total_investment
    )

    roi = (
        profit
        / total_investment
        * 100
        if total_investment > 0
        else 0
    )

    interest_factor = (
        DEFAULTS[
            "interest_pct_year"
        ]
        / 100
        * (
            DEFAULTS[
                "holding_months"
            ] / 12
        )
    )

    transfer_factor = (
        DEFAULTS[
            "transfer_tax_pct"
        ]
        / 100
    )

    fixed_non_purchase_costs = (
        notary
        + renovation
        + contingency
        + selling_cost
    )

    denominator = (
        1
        + transfer_factor
        + interest_factor
        * (1 + transfer_factor)
    )

    numerator = (
        resale
        - DEFAULTS[
            "target_profit"
        ]
        - fixed_non_purchase_costs
        - (
            renovation
            + contingency
        ) * interest_factor
    )

    max_purchase_price = (
        numerator / denominator
        if denominator > 0
        else 0
    )

    return {
        "transfer_tax":
            round(transfer_tax),

        "notary_and_advice":
            round(notary),

        "renovation":
            round(renovation),

        "contingency":
            round(contingency),

        "financing_cost":
            round(financing_cost),

        "selling_cost":
            round(selling_cost),

        "total_investment":
            round(total_investment),

        "estimated_resale":
            round(resale),

        "profit":
            round(profit),

        "roi":
            round(roi, 1),

        "max_purchase_price":
            max(
                0,
                round(
                    max_purchase_price
                    / 1000
                ) * 1000
            ),

        "target_profit":
            DEFAULTS[
                "target_profit"
            ],
    }


def save_live_listings(
    listings,
    api_key
):
    c = conn()

    imported = 0
    comparable_searches = 0

    # Cache per plaats + type + m2-band.
    # Zo zoeken we niet onnodig
    # meerdere keren dezelfde markt.
    comp_cache = {}

    for item in listings:

        source_id = str(
            item.get(
                "source_id"
            ) or ""
        ).strip()

        if not source_id:
            continue

        renovation = (
            estimate_renovation(
                item
            )
        )

        city = str(
            item.get("city") or ""
        ).strip()

        property_type = str(
            item.get(
                "property_type"
            ) or ""
        ).strip()

        size_m2 = float(
            item.get(
                "size_m2"
            ) or 0
        )

        # M2 in blokken van 20 m2
        # voor hergebruik vergelijkingszoektocht.
        size_band = (
            round(size_m2 / 20)
            * 20
            if size_m2 > 0
            else 0
        )

        cache_key = (
            city.lower(),
            property_type.lower(),
            size_band
        )

        comparison = (
            comp_cache.get(
                cache_key
            )
        )

        if comparison is None:

            try:
                comparison = (
                    fetch_comparables(
                        api_key=api_key,
                        city=city,
                        target_size_m2=
                            size_m2,
                        property_type=
                            property_type,
                        exclude_source_id=
                            source_id,
                        max_pages=1
                    )
                )

                comparable_searches += 1

            except Exception as e:

                print(
                    "Comparable fout:",
                    city,
                    repr(e)
                )

                comparison = {
                    "count": 0,
                    "median_price_per_m2": 0,
                    "estimated_resale": 0,
                    "basis":
                        "fallback schatting"
                }

            comp_cache[
                cache_key
            ] = comparison

        comparable_resale = float(
            comparison.get(
                "estimated_resale"
            ) or 0
        )

        if (
            comparison.get("count", 0)
            >= 3
            and comparable_resale > 0
        ):
            resale = (
                comparable_resale
            )

            valuation_basis = (
                "vergelijkbare "
                "actuele vraagprijzen"
            )

        else:
            resale = (
                fallback_resale(
                    item,
                    renovation
                )
            )

            valuation_basis = (
                "voorlopige "
                "fallback schatting"
            )

        values = (
            source_id,
            city,
            item.get(
                "street",
                ""
            ),
            item.get(
                "price",
                0
            ),
            item.get(
                "size_m2",
                0
            ),
            item.get(
                "plot_m2",
                0
            ),
            item.get(
                "energy_label",
                ""
            ),
            property_type,
            item.get(
                "build_year"
            ),
            renovation,
            resale,
            item.get(
                "source_url",
                ""
            ),
            item.get(
                "postal_code",
                ""
            ),
            item.get(
                "publish_date",
                ""
            ),
            item.get(
                "image_url",
                ""
            ),
            comparison.get(
                "count",
                0
            ),
            comparison.get(
                "median_price_per_m2",
                0
            ),
            valuation_basis
        )

        c.execute("""
        INSERT INTO properties(
            source_id,
            city,
            street,
            price,
            size_m2,
            plot_m2,
            energy_label,
            property_type,
            build_year,
            renovation_estimate,
            resale_estimate,
            source_url,
            postal_code,
            publish_date,
            image_url,
            comparable_count,
            median_price_per_m2,
            valuation_basis
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?
        )

        ON CONFLICT(source_id)
        DO UPDATE SET

            city=
                excluded.city,

            street=
                excluded.street,

            price=
                excluded.price,

            size_m2=
                excluded.size_m2,

            plot_m2=
                excluded.plot_m2,

            energy_label=
                excluded.energy_label,

            property_type=
                excluded.property_type,

            build_year=
                excluded.build_year,

            renovation_estimate=
                excluded.renovation_estimate,

            resale_estimate=
                excluded.resale_estimate,

            source_url=
                excluded.source_url,

            postal_code=
                excluded.postal_code,

            publish_date=
                excluded.publish_date,

            image_url=
                excluded.image_url,

            comparable_count=
                excluded.comparable_count,

            median_price_per_m2=
                excluded.median_price_per_m2,

            valuation_basis=
                excluded.valuation_basis
        """, values)

        imported += 1

    c.commit()
    c.close()

    return (
        imported,
        comparable_searches
    )


def remove_demo_properties():
    c = conn()

    c.execute("""
        DELETE FROM properties
        WHERE
        source_id LIKE 'ff-%'
        OR
        source_url LIKE
        'https://example.com/%'
    """)

    c.commit()
    c.close()


@app.get("/")
def home():
    return send_from_directory(
        BASE,
        "index.html"
    )


@app.get("/api/properties")
def properties():

    c = conn()

    rows = [
        dict(r)
        for r in c.execute("""
            SELECT *
            FROM properties
            ORDER BY
            created_at DESC
        """)
    ]

    c.close()

    result = []

    for row in rows:
        row[
            "analysis"
        ] = calculate_flip(
            row
        )

        result.append(row)

    return jsonify(result)


@app.post("/api/import")
def do_import():

    api_key = os.getenv(
        "REEFAPI_KEY"
    )

    if not api_key:

        return jsonify({
            "ok": False,
            "error":
                "REEFAPI_KEY ontbreekt"
        }), 500

    try:

        listings = (
            fetch_listings(
                api_key=
                    api_key,
                area=
                    "Limburg"
            )
        )

        if not listings:

            return jsonify({
                "ok": False,
                "imported": 0,
                "message":
                    "Geen woningen ontvangen."
            }), 502

        (
            imported,
            comparable_searches
        ) = save_live_listings(
            listings,
            api_key
        )

        if imported > 0:
            remove_demo_properties()

        return jsonify({
            "ok": True,
            "imported":
                imported,
            "comparable_searches":
                comparable_searches,
            "source":
                "ReefAPI",
            "area":
                "Limburg"
        })

    except Exception as e:

        print(
            "ReefAPI import error:",
            repr(e)
        )

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.post("/api/alerts")
def add_alert():

    d = request.get_json(
        force=True
    )

    c = conn()

    c.execute("""
        INSERT INTO alerts(
            min_profit,
            min_roi,
            max_price,
            city
        )
        VALUES(?,?,?,?)
    """, (
        d.get(
            "min_profit"
        ),
        d.get(
            "min_roi"
        ),
        d.get(
            "max_price"
        ),
        d.get(
            "city",
            ""
        )
    ))

    c.commit()
    c.close()

    return jsonify({
        "ok": True
    })


@app.get("/api/status")
def status():

    api_key_present = bool(
        os.getenv(
            "REEFAPI_KEY"
        )
    )

    c = conn()

    try:
        count = c.execute("""
            SELECT COUNT(*)
            FROM properties
        """).fetchone()[0]

    except Exception:
        count = 0

    c.close()

    return jsonify({
        "database":
            DB.exists(),

        "properties":
            count,

        "listing_feed":
            "ReefAPI",

        "reefapi_key":
            (
                "configured"
                if api_key_present
                else "missing"
            ),

        "area":
            "Limburg",

        "kadaster":
            "uitgeschakeld",

        "push":
            "nog niet gekoppeld",

        "valuation":
            "vergelijkbare vraagprijzen",

        "calculation_model": {

            "transfer_tax_pct":
                DEFAULTS[
                    "transfer_tax_pct"
                ],

            "interest_pct_year":
                DEFAULTS[
                    "interest_pct_year"
                ],

            "holding_months":
                DEFAULTS[
                    "holding_months"
                ],

            "selling_cost_pct":
                DEFAULTS[
                    "selling_cost_pct"
                ],

            "target_profit":
                DEFAULTS[
                    "target_profit"
                ]
        }
    })


init_db()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                8000
            )
        ),
        debug=True
    )
