from flask import Flask, jsonify, request, send_from_directory
import sqlite3
import os
from pathlib import Path

from listing_adapter import fetch_listings


BASE = Path(__file__).resolve().parent
DB = BASE / "flipfinder.db"
PUBLIC = BASE

app = Flask(
    __name__,
    static_folder=str(PUBLIC),
    static_url_path=""
)


# --------------------------------------------------
# STANDAARD REKENMODEL
# --------------------------------------------------

DEFAULTS = {
    "transfer_tax_pct": 8.0,
    "notary_and_advice": 3000,
    "interest_pct_year": 8.0,
    "holding_months": 6,
    "selling_cost_pct": 2.0,
    "renovation_contingency_pct": 10.0,
    "target_profit": 30000,
}


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


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

    c.commit()
    c.close()


# --------------------------------------------------
# VERBOUWINGSKOSTEN
# --------------------------------------------------

def estimate_renovation(item):
    size = float(item.get("size_m2") or 0)
    label = str(item.get("energy_label") or "").upper()
    property_type = str(
        item.get("property_type") or ""
    ).lower()

    if size <= 0:
        return 40000

    # Basis: normale cosmetische + technische renovatie
    cost_per_m2 = 350

    # Slechter energielabel = gemiddeld meer werk
    if label == "D":
        cost_per_m2 += 40

    elif label == "E":
        cost_per_m2 += 80

    elif label == "F":
        cost_per_m2 += 120

    elif label == "G":
        cost_per_m2 += 160

    # Appartement vaak iets goedkoper per m2
    if "apartment" in property_type or "appartement" in property_type:
        cost_per_m2 -= 50

    # Vrijstaand vaak meer gevel/dak/installaties
    if "detached" in property_type or "vrijstaand" in property_type:
        cost_per_m2 += 75

    renovation = size * cost_per_m2

    return round(renovation / 1000) * 1000


# --------------------------------------------------
# VOORLOPIGE VERKOOPWAARDE
# --------------------------------------------------

def estimate_resale(item, renovation):
    """
    Voorlopige verkoopwaarde.

    Deze vervangen we later door een model
    met vergelijkbare verkochte/aangeboden woningen
    en marktwaarde per m2.
    """

    purchase_price = float(
        item.get("price") or 0
    )

    if purchase_price <= 0:
        return 0

    # We gaan niet uit van €1 verbouwen = €1 waarde.
    # Goede renovatie moet marge creëren.
    added_value = renovation * 1.55

    estimated = purchase_price + added_value

    return round(estimated / 1000) * 1000


# --------------------------------------------------
# VOLLEDIGE FLIP-CALCULATIE
# --------------------------------------------------

def calculate_flip(item):
    price = float(item.get("price") or 0)

    renovation = float(
        item.get("renovation_estimate") or 0
    )

    resale = float(
        item.get("resale_estimate") or 0
    )

    transfer_tax = (
        price *
        DEFAULTS["transfer_tax_pct"] /
        100
    )

    notary = DEFAULTS["notary_and_advice"]

    contingency = (
        renovation *
        DEFAULTS["renovation_contingency_pct"] /
        100
    )

    # Financiering berekenen over aankoop + belasting +
    # verbouwing + onvoorzien.
    financed_amount = (
        price +
        transfer_tax +
        renovation +
        contingency
    )

    financing_cost = (
        financed_amount *
        DEFAULTS["interest_pct_year"] /
        100 *
        (DEFAULTS["holding_months"] / 12)
    )

    selling_cost = (
        resale *
        DEFAULTS["selling_cost_pct"] /
        100
    )

    total_investment = (
        price +
        transfer_tax +
        notary +
        renovation +
        contingency +
        financing_cost +
        selling_cost
    )

    profit = resale - total_investment

    if total_investment > 0:
        roi = (
            profit /
            total_investment *
            100
        )
    else:
        roi = 0

    # Max aankoopprijs waarbij nog minimaal
    # target_profit overblijft.
    fixed_non_purchase_costs = (
        notary +
        renovation +
        contingency +
        selling_cost
    )

    interest_factor = (
        DEFAULTS["interest_pct_year"] /
        100 *
        (DEFAULTS["holding_months"] / 12)
    )

    transfer_factor = (
        DEFAULTS["transfer_tax_pct"] /
        100
    )

    # Benadering waarbij aankoopprijs ook
    # overdrachtsbelasting en financiering beïnvloedt.
    denominator = (
        1 +
        transfer_factor +
        interest_factor *
        (1 + transfer_factor)
    )

    numerator = (
        resale -
        DEFAULTS["target_profit"] -
        fixed_non_purchase_costs -
        (
            renovation +
            contingency
        ) * interest_factor
    )

    max_purchase_price = (
        numerator / denominator
        if denominator > 0
        else 0
    )

    return {
        "transfer_tax": round(transfer_tax),
        "notary_and_advice": round(notary),
        "renovation": round(renovation),
        "contingency": round(contingency),
        "financing_cost": round(financing_cost),
        "selling_cost": round(selling_cost),
        "total_investment": round(total_investment),
        "estimated_resale": round(resale),
        "profit": round(profit),
        "roi": round(roi, 1),
        "max_purchase_price": max(
            0,
            round(max_purchase_price / 1000) * 1000
        ),
        "target_profit": DEFAULTS["target_profit"],
    }


# --------------------------------------------------
# LIVE WONINGEN OPSLAAN
# --------------------------------------------------

def save_live_listings(listings):
    c = conn()

    imported = 0

    for item in listings:

        source_id = str(
            item.get("source_id") or ""
        ).strip()

        if not source_id:
            continue

        renovation = estimate_renovation(item)
        resale = estimate_resale(
            item,
            renovation
        )

        values = (
            source_id,
            item.get("city", ""),
            item.get("street", ""),
            item.get("price", 0),
            item.get("size_m2", 0),
            item.get("plot_m2", 0),
            item.get("energy_label", ""),
            item.get("property_type", ""),
            item.get("build_year"),
            renovation,
            resale,
            item.get("source_url", "")
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
                source_url
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)

            ON CONFLICT(source_id) DO UPDATE SET
                city=excluded.city,
                street=excluded.street,
                price=excluded.price,
                size_m2=excluded.size_m2,
                plot_m2=excluded.plot_m2,
                energy_label=excluded.energy_label,
                property_type=excluded.property_type,
                build_year=excluded.build_year,
                renovation_estimate=excluded.renovation_estimate,
                resale_estimate=excluded.resale_estimate,
                source_url=excluded.source_url
        """, values)

        imported += 1

    c.commit()
    c.close()

    return imported


def remove_demo_properties():
    c = conn()

    c.execute("""
        DELETE FROM properties
        WHERE source_id LIKE 'ff-%'
        OR source_url LIKE 'https://example.com/%'
    """)

    c.commit()
    c.close()


# --------------------------------------------------
# WEBSITE
# --------------------------------------------------

@app.get("/")
def home():
    return send_from_directory(
        BASE,
        "index.html"
    )


# --------------------------------------------------
# WONINGEN
# --------------------------------------------------

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
            sql += " AND price <= ?"

            params.append(
                float(max_price)
            )

        except ValueError:
            pass

    sql += """
        ORDER BY created_at DESC
    """

    c = conn()

    rows = [
        dict(r)
        for r in c.execute(
            sql,
            params
        )
    ]

    c.close()

    result = []

    for row in rows:
        row["analysis"] = calculate_flip(row)
        result.append(row)

    return jsonify(result)


# --------------------------------------------------
# LIVE IMPORT
# --------------------------------------------------

@app.post("/api/import")
def do_import():

    api_key = os.getenv(
        "REEFAPI_KEY"
    )

    if not api_key:
        return jsonify({
            "ok": False,
            "error": (
                "REEFAPI_KEY ontbreekt "
                "in Railway"
            )
        }), 500

    try:

        listings = fetch_listings(
            api_key=api_key,
            area="Limburg"
        )

        if not listings:
            return jsonify({
                "ok": False,
                "imported": 0,
                "message": (
                    "ReefAPI reageerde, "
                    "maar leverde geen woningen."
                )
            }), 502

        imported = save_live_listings(
            listings
        )

        if imported > 0:
            remove_demo_properties()

        return jsonify({
            "ok": True,
            "imported": imported,
            "source": "ReefAPI",
            "area": "Limburg"
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


# --------------------------------------------------
# MELDINGEN
# --------------------------------------------------

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
        d.get("min_profit"),
        d.get("min_roi"),
        d.get("max_price"),
        d.get("city", "")
    ))

    c.commit()
    c.close()

    return jsonify({
        "ok": True
    })


# --------------------------------------------------
# STATUS
# --------------------------------------------------

@app.get("/api/status")
def status():

    api_key_present = bool(
        os.getenv("REEFAPI_KEY")
    )

    c = conn()

    try:
        count = c.execute(
            """
            SELECT COUNT(*)
            FROM properties
            """
        ).fetchone()[0]

    except Exception:
        count = 0

    c.close()

    return jsonify({
        "database": DB.exists(),
        "properties": count,
        "listing_feed": "ReefAPI",
        "reefapi_key": (
            "configured"
            if api_key_present
            else "missing"
        ),
        "area": "Limburg",
        "kadaster": "uitgeschakeld",
        "push": "nog niet gekoppeld",
        "calculation_model": {
            "transfer_tax_pct":
                DEFAULTS["transfer_tax_pct"],
            "interest_pct_year":
                DEFAULTS["interest_pct_year"],
            "holding_months":
                DEFAULTS["holding_months"],
            "selling_cost_pct":
                DEFAULTS["selling_cost_pct"],
            "target_profit":
                DEFAULTS["target_profit"]
        }
    })


# Gunicorn voert __main__ niet uit.
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
