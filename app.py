from flask import Flask, jsonify, request, send_from_directory

import json
import os
import sqlite3
import threading

from datetime import datetime, timezone
from pathlib import Path

from listing_adapter import (
    fetch_listings,
    fetch_comparables,
)


# ==================================================
# BASIS
# ==================================================

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


analysis_lock = threading.Lock()
analysis_running = False


# ==================================================
# DATABASE
# ==================================================

def conn():

    c = sqlite3.connect(
        DB,
        timeout=30
    )

    c.row_factory = sqlite3.Row

    return c


def column_exists(
    c,
    table,
    column
):

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

    CREATE TABLE IF NOT EXISTS comparable_cache(
        cache_key TEXT PRIMARY KEY,
        result_json TEXT NOT NULL,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    extra_columns = {

        "postal_code":
            "TEXT",

        "publish_date":
            "TEXT",

        "image_url":
            "TEXT",

        "comparable_count":
            "INTEGER DEFAULT 0",

        "median_price_per_m2":
            "REAL DEFAULT 0",

        "valuation_basis":
            "TEXT",

        "analysis_status":
            "TEXT DEFAULT 'pending'",
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


# ==================================================
# VERBOUWINGSKOSTEN
# ==================================================

def estimate_renovation(item):

    try:
        size = float(
            item.get("size_m2") or 0
        )

    except (
        TypeError,
        ValueError
    ):
        size = 0


    label = str(
        item.get("energy_label") or ""
    ).upper().strip()


    property_type = str(
        item.get("property_type") or ""
    ).lower().strip()


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
        or
        "appartement" in property_type
    ):
        cost_per_m2 -= 50


    if (
        "detached" in property_type
        or
        "vrijstaand" in property_type
    ):
        cost_per_m2 += 75


    renovation = (
        size * cost_per_m2
    )


    return (
        round(
            renovation / 1000
        ) * 1000
    )


# ==================================================
# VOORLOPIGE VERKOOPWAARDE
# ==================================================

def fallback_resale(
    item,
    renovation
):

    try:
        price = float(
            item.get("price") or 0
        )

    except (
        TypeError,
        ValueError
    ):
        price = 0


    if price <= 0:
        return 0


    estimated = (
        price
        + renovation * 1.55
    )


    return (
        round(
            estimated / 1000
        ) * 1000
    )


# ==================================================
# FLIP BEREKENING
# ==================================================

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
            ]
            / 12
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
            ]
            / 12
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
        * (
            1
            + transfer_factor
        )
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
        )
        * interest_factor
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
            round(
                roi,
                1
            ),

        "max_purchase_price":
            max(
                0,
                round(
                    max_purchase_price
                    / 1000
                )
                * 1000
            ),

        "target_profit":
            DEFAULTS[
                "target_profit"
            ]
    }


# ==================================================
# WONINGEN OPSLAAN
# ==================================================

def save_basic_listings(listings):

    c = conn()

    imported = 0
    new_or_changed = 0


    for item in listings:

        source_id = str(
            item.get(
                "source_id"
            ) or ""
        ).strip()


        if not source_id:
            continue


        renovation = (
            estimate_renovation(item)
        )


        fallback = (
            fallback_resale(
                item,
                renovation
            )
        )


        existing = c.execute(
            """
            SELECT
                price,
                size_m2,
                property_type,
                city,
                analysis_status
            FROM properties
            WHERE source_id = ?
            """,
            (
                source_id,
            )
        ).fetchone()


        price = float(
            item.get("price") or 0
        )

        size_m2 = float(
            item.get("size_m2") or 0
        )

        property_type = str(
            item.get(
                "property_type"
            ) or ""
        )

        city = str(
            item.get("city") or ""
        )


        changed = False


        if existing is None:

            changed = True

        else:

            old_price = float(
                existing["price"] or 0
            )

            old_size = float(
                existing["size_m2"] or 0
            )

            old_type = str(
                existing[
                    "property_type"
                ] or ""
            )

            old_city = str(
                existing["city"] or ""
            )


            if (
                old_price != price
                or
                old_size != size_m2
                or
                old_type != property_type
                or
                old_city != city
            ):
                changed = True


        if existing is None:

            c.execute(
                """
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
                    valuation_basis,
                    analysis_status
                )
                VALUES(
                    ?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,0,0,
                    'voorlopige fallback schatting',
                    'pending'
                )
                """,
                (

                    source_id,

                    city,

                    item.get(
                        "street",
                        ""
                    ),

                    price,

                    size_m2,

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

                    fallback,

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
                )
            )


        else:

            c.execute(
                """
                UPDATE properties
                SET
                    city = ?,
                    street = ?,
                    price = ?,
                    size_m2 = ?,
                    plot_m2 = ?,
                    energy_label = ?,
                    property_type = ?,
                    build_year = ?,
                    renovation_estimate = ?,
                    source_url = ?,
                    postal_code = ?,
                    publish_date = ?,
                    image_url = ?
                WHERE source_id = ?
                """,
                (

                    city,

                    item.get(
                        "street",
                        ""
                    ),

                    price,

                    size_m2,

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

                    source_id,
                )
            )


            if changed:

                c.execute(
                    """
                    UPDATE properties
                    SET
                        resale_estimate = ?,
                        comparable_count = 0,
                        median_price_per_m2 = 0,
                        valuation_basis =
                            'voorlopige fallback schatting',
                        analysis_status =
                            'pending'
                    WHERE source_id = ?
                    """,
                    (
                        fallback,
                        source_id
                    )
                )


        if changed:
            new_or_changed += 1


        imported += 1


    c.commit()
    c.close()


    return {
        "imported": imported,
        "new_or_changed":
            new_or_changed
    }


# ==================================================
# PENDING WONINGEN
# ==================================================

def get_pending_properties():

    c = conn()

    rows = [

        dict(row)

        for row in c.execute(
            """
            SELECT *
            FROM properties
            WHERE
                analysis_status = 'pending'
                OR analysis_status IS NULL
            ORDER BY
                publish_date DESC,
                created_at DESC
            """
        ).fetchall()
    ]

    c.close()

    return rows


# ==================================================
# COMPARABLE CACHE
# ==================================================

def make_cache_key(
    city,
    property_type,
    size_m2
):

    try:
        size = float(
            size_m2 or 0
        )

    except (
        TypeError,
        ValueError
    ):
        size = 0


    size_band = (

        round(
            size / 20
        )
        * 20

        if size > 0

        else 0
    )


    return (
        f"{city.strip().lower()}"
        f"|{property_type.strip().lower()}"
        f"|{size_band}"
    )


def get_cached_comparison(
    cache_key
):

    c = conn()

    row = c.execute(
        """
        SELECT result_json
        FROM comparable_cache
        WHERE
            cache_key = ?
            AND datetime(fetched_at)
                >= datetime(
                    'now',
                    '-12 hours'
                )
        """,
        (
            cache_key,
        )
    ).fetchone()

    c.close()


    if not row:
        return None


    try:

        return json.loads(
            row["result_json"]
        )

    except Exception:

        return None


def save_cached_comparison(
    cache_key,
    comparison
):

    c = conn()

    c.execute(
        """
        INSERT INTO comparable_cache(
            cache_key,
            result_json,
            fetched_at
        )
        VALUES(
            ?,?,
            CURRENT_TIMESTAMP
        )

        ON CONFLICT(cache_key)
        DO UPDATE SET
            result_json =
                excluded.result_json,
            fetched_at =
                CURRENT_TIMESTAMP
        """,
        (
            cache_key,
            json.dumps(
                comparison
            )
        )
    )

    c.commit()
    c.close()


# ==================================================
# VERGELIJKINGSANALYSE
# ==================================================

def update_analysis_result(
    source_id,
    resale,
    count,
    median_m2,
    basis
):

    c = conn()

    c.execute(
        """
        UPDATE properties
        SET
            resale_estimate = ?,
            comparable_count = ?,
            median_price_per_m2 = ?,
            valuation_basis = ?,
            analysis_status = 'done'
        WHERE source_id = ?
        """,
        (
            resale,
            count,
            median_m2,
            basis,
            source_id
        )
    )

    c.commit()
    c.close()


def mark_analysis_done_with_fallback(
    item,
    reason="voorlopige fallback schatting"
):

    source_id = str(
        item.get(
            "source_id"
        ) or ""
    ).strip()


    renovation = (
        estimate_renovation(item)
    )


    resale = (
        fallback_resale(
            item,
            renovation
        )
    )


    update_analysis_result(
        source_id=source_id,
        resale=resale,
        count=0,
        median_m2=0,
        basis=reason
    )


def run_comparable_analysis(
    listings,
    api_key
):

    global analysis_running


    try:

        memory_cache = {}


        for item in listings:

            source_id = str(
                item.get(
                    "source_id"
                ) or ""
            ).strip()


            if not source_id:
                continue


            city = str(
                item.get(
                    "city"
                ) or ""
            ).strip()


            property_type = str(
                item.get(
                    "property_type"
                ) or ""
            ).strip()


            try:

                size_m2 = float(
                    item.get(
                        "size_m2"
                    ) or 0
                )

            except (
                TypeError,
                ValueError
            ):

                size_m2 = 0


            if (
                not city
                or
                size_m2 <= 0
            ):

                mark_analysis_done_with_fallback(
                    item,
                    "onvoldoende woninggegevens"
                )

                continue


            cache_key = (
                make_cache_key(
                    city,
                    property_type,
                    size_m2
                )
            )


            comparison = (
                memory_cache.get(
                    cache_key
                )
            )


            if comparison is None:

                comparison = (
                    get_cached_comparison(
                        cache_key
                    )
                )


            if comparison is None:

                try:

                    # Maximaal 2 pagina's:
                    # maximaal circa 30 actuele
                    # vraagprijswoningen per
                    # unieke vergelijkingsgroep.
                    comparison = (
                        fetch_comparables(
                            api_key=
                                api_key,
                            city=
                                city,
                            target_size_m2=
                                size_m2,
                            property_type=
                                property_type,
                            exclude_source_id=
                                source_id,
                            max_pages=2
                        )
                    )


                    save_cached_comparison(
                        cache_key,
                        comparison
                    )


                except Exception as e:

                    print(
                        "Comparable fout:",
                        city,
                        repr(e),
                        flush=True
                    )


                    mark_analysis_done_with_fallback(
                        item,
                        "vergelijking tijdelijk niet beschikbaar"
                    )

                    continue


            memory_cache[
                cache_key
            ] = comparison


            count = int(
                comparison.get(
                    "count"
                ) or 0
            )


            try:

                median_m2 = float(
                    comparison.get(
                        "median_price_per_m2"
                    ) or 0
                )

            except (
                TypeError,
                ValueError
            ):

                median_m2 = 0


            try:

                comp_resale = float(
                    comparison.get(
                        "estimated_resale"
                    ) or 0
                )

            except (
                TypeError,
                ValueError
            ):

                comp_resale = 0


            renovation = (
                estimate_renovation(item)
            )


            if (
                count >= 3
                and
                median_m2 > 0
                and
                comp_resale > 0
            ):

                resale = (
                    comp_resale
                )

                basis = (
                    "vergelijkbare actuele "
                    "vraagprijzen"
                )


            else:

                resale = (
                    fallback_resale(
                        item,
                        renovation
                    )
                )

                basis = (
                    "te weinig vergelijkbare "
                    "actuele vraagprijzen"
                )


            update_analysis_result(
                source_id=
                    source_id,
                resale=
                    resale,
                count=
                    count,
                median_m2=
                    median_m2,
                basis=
                    basis
            )


    except Exception as e:

        print(
            "Analyse algemene fout:",
            repr(e),
            flush=True
        )


    finally:

        with analysis_lock:
            analysis_running = False


# ==================================================
# ANALYSE STARTEN
# ==================================================

def start_analysis(
    listings,
    api_key
):

    global analysis_running


    if not listings:
        return False


    with analysis_lock:

        if analysis_running:
            return False

        analysis_running = True


    thread = threading.Thread(
        target=
            run_comparable_analysis,
        args=(
            listings,
            api_key
        ),
        daemon=True
    )

    thread.start()

    return True


# ==================================================
# DEMO DATA VERWIJDEREN
# ==================================================

def remove_demo_properties():

    c = conn()

    c.execute(
        """
        DELETE FROM properties
        WHERE
            source_id LIKE 'ff-%'
        OR
            source_url LIKE
            'https://example.com/%'
        """
    )

    c.commit()
    c.close()


# ==================================================
# WEBSITE
# ==================================================

@app.get("/")
def home():

    return send_from_directory(
        BASE,
        "index.html"
    )


# ==================================================
# WONINGEN
# ==================================================

@app.get("/api/properties")
def properties():

    city = request.args.get(
        "city",
        ""
    ).strip()


    max_price = request.args.get(
        "max_price",
        ""
    ).strip()


    sql = """
        SELECT *
        FROM properties
        WHERE 1=1
    """

    params = []


    if city:

        sql += """
            AND LOWER(city)
            LIKE LOWER(?)
        """

        params.append(
            f"%{city}%"
        )


    if max_price:

        try:

            sql += """
                AND price <= ?
            """

            params.append(
                float(
                    max_price
                )
            )

        except ValueError:
            pass


    sql += """
        ORDER BY
            publish_date DESC,
            created_at DESC
    """


    c = conn()

    rows = [

        dict(r)

        for r in c.execute(
            sql,
            params
        ).fetchall()
    ]

    c.close()


    result = []


    for row in rows:

        row["analysis"] = (
            calculate_flip(row)
        )

        result.append(row)


    return jsonify(result)


# ==================================================
# IMPORT
# ==================================================

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
                    "Limburg",
                page=1
            )
        )


        if not listings:

            return jsonify({
                "ok": False,
                "imported": 0,
                "message":
                    "Geen woningen ontvangen."
            }), 502


        save_result = (
            save_basic_listings(
                listings
            )
        )


        imported = (
            save_result[
                "imported"
            ]
        )


        new_or_changed = (
            save_result[
                "new_or_changed"
            ]
        )


        if imported > 0:
            remove_demo_properties()


        # Belangrijk:
        # analyseert niet alleen de zojuist
        # opgehaalde woningen maar OOK oude
        # woningen die ooit op 'pending'
        # zijn blijven hangen.
        pending_listings = (
            get_pending_properties()
        )


        started = (
            start_analysis(
                pending_listings,
                api_key
            )
        )


        return jsonify({

            "ok":
                True,

            "imported":
                imported,

            "new_or_changed":
                new_or_changed,

            "pending_analysis":
                len(
                    pending_listings
                ),

            "source":
                "ReefAPI",

            "area":
                "Limburg",

            "analysis":
                (
                    "gestart"
                    if started
                    else
                    "loopt al of niets te analyseren"
                ),

            "message":
                (
                    f"{imported} woningen opgehaald. "
                    f"{len(pending_listings)} woningen "
                    "staan klaar voor "
                    "vergelijkingsanalyse."
                )
        })


    except Exception as e:

        print(
            "Import fout:",
            repr(e),
            flush=True
        )


        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# ==================================================
# ANALYSE HANDMATIG OPNIEUW STARTEN
# ==================================================

@app.post("/api/analyse")
def analyse_pending():

    api_key = os.getenv(
        "REEFAPI_KEY"
    )


    if not api_key:

        return jsonify({
            "ok": False,
            "error":
                "REEFAPI_KEY ontbreekt"
        }), 500


    pending_listings = (
        get_pending_properties()
    )


    started = (
        start_analysis(
            pending_listings,
            api_key
        )
    )


    return jsonify({

        "ok":
            True,

        "pending":
            len(
                pending_listings
            ),

        "analysis":
            (
                "gestart"
                if started
                else
                "loopt al of niets te analyseren"
            )
    })


# ==================================================
# ALERTS
# ==================================================

@app.post("/api/alerts")
def add_alert():

    d = request.get_json(
        force=True
    )


    c = conn()


    c.execute(
        """
        INSERT INTO alerts(
            min_profit,
            min_roi,
            max_price,
            city
        )
        VALUES(
            ?,?,?,?
        )
        """,
        (

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
        )
    )


    c.commit()
    c.close()


    return jsonify({
        "ok": True
    })


# ==================================================
# STATUS
# ==================================================

@app.get("/api/status")
def status():

    api_key_present = bool(
        os.getenv(
            "REEFAPI_KEY"
        )
    )


    c = conn()


    try:

        count = c.execute(
            """
            SELECT COUNT(*)
            FROM properties
            """
        ).fetchone()[0]


        pending = c.execute(
            """
            SELECT COUNT(*)
            FROM properties
            WHERE
                analysis_status = 'pending'
                OR analysis_status IS NULL
            """
        ).fetchone()[0]


        compared = c.execute(
            """
            SELECT COUNT(*)
            FROM properties
            WHERE comparable_count >= 3
            """
        ).fetchone()[0]


        cached = c.execute(
            """
            SELECT COUNT(*)
            FROM comparable_cache
            WHERE datetime(fetched_at)
                >= datetime(
                    'now',
                    '-12 hours'
                )
            """
        ).fetchone()[0]


    except Exception:

        count = 0
        pending = 0
        compared = 0
        cached = 0


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
                else
                "missing"
            ),

        "area":
            "Limburg",

        "kadaster":
            "uitgeschakeld",

        "push":
            "nog niet gekoppeld",

        "valuation":
            "actuele vergelijkbare vraagprijzen",

        "analysis_running":
            analysis_running,

        "analysis_pending":
            pending,

        "properties_with_comparables":
            compared,

        "comparable_cache_groups":
            cached,

        "comparable_cache_hours":
            12,

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


# ==================================================
# START
# ==================================================

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