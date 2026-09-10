from flask import Flask, jsonify, request, send_from_directory
import sqlite3
import os
from pathlib import Path

from listing_adapter import fetch_listings


BASE = Path(__file__).resolve().parent
DB = BASE / "flipfinder.db"
PUBLIC = BASE

app = Flask(__name__, static_folder=str(PUBLIC), static_url_path="")


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


def estimate_renovation(item):
    """
    Eerste globale verbouwingsinschatting.
    Wordt later slimmer gemaakt op basis van woningtekst,
    bouwjaar, energielabel en kenmerken.
    """

    size = float(item.get("size_m2") or 0)
    year = item.get("build_year")
    label = str(item.get("energy_label") or "").upper()

    if size <= 0:
        return 40000

    cost_per_m2 = 300

    if year:
        try:
            year = int(year)

            if year < 1960:
                cost_per_m2 = 550
            elif year < 1980:
                cost_per_m2 = 450
            elif year < 2000:
                cost_per_m2 = 350
            else:
                cost_per_m2 = 275
        except:
            pass

    if label in ["E", "F", "G"]:
        cost_per_m2 += 75

    return round(size * cost_per_m2, -3)


def estimate_resale(item, renovation):
    """
    Tijdelijke verkoopwaarde-inschatting.
    Later vervangen we dit door vergelijkbare woningen.
    """

    price = float(item.get("price") or 0)

    if price <= 0:
        return 0

    # Voorlopige waardestijging na renovatie.
    improvement = renovation * 1.35

    return round(price + improvement, -3)


def save_live_listings(listings):
    c = conn()

    imported = 0

    for item in listings:

        source_id = str(item.get("source_id") or "").strip()

        if not source_id:
            continue

        renovation = estimate_renovation(item)
        resale = estimate_resale(item, renovation)

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


@app.get("/")
def home():
    return send_from_directory(BASE, "index.html")


@app.get("/api/properties")
def properties():

    city = request.args.get("city", "").strip()
    max_price = request.args.get("max_price", "").strip()

    sql = "SELECT * FROM properties WHERE 1=1"
    params = []

    if city:
        sql += " AND LOWER(city) LIKE LOWER(?)"
        params.append(f"%{city}%")

    if max_price:
        try:
            sql += " AND price <= ?"
            params.append(float(max_price))
        except ValueError:
            pass

    sql += " ORDER BY created_at DESC"

    c = conn()
    rows = [dict(r) for r in c.execute(sql, params)]
    c.close()

    return jsonify(rows)


@app.post("/api/import")
def do_import():

    api_key = os.getenv("REEFAPI_KEY")

    if not api_key:
        return jsonify({
            "ok": False,
            "error": "REEFAPI_KEY ontbreekt in Railway"
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
                "message": "ReefAPI reageerde, maar er zijn geen woningen verwerkt."
            }), 502

        imported = save_live_listings(listings)

        if imported > 0:
            remove_demo_properties()

        return jsonify({
            "ok": True,
            "imported": imported,
            "source": "ReefAPI",
            "area": "Limburg"
        })

    except Exception as e:

        print("ReefAPI import error:", repr(e))

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.post("/api/alerts")
def add_alert():

    d = request.get_json(force=True)

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

    return jsonify({"ok": True})


@app.get("/api/status")
def status():

    api_key_present = bool(os.getenv("REEFAPI_KEY"))

    c = conn()

    try:
        count = c.execute(
            "SELECT COUNT(*) FROM properties"
        ).fetchone()[0]
    except:
        count = 0

    c.close()

    return jsonify({
        "database": DB.exists(),
        "properties": count,
        "listing_feed": "ReefAPI",
        "reefapi_key": "configured" if api_key_present else "missing",
        "area": "Limburg",
        "kadaster": "uitgeschakeld",
        "push": "nog niet gekoppeld"
    })


# BELANGRIJK:
# Gunicorn voert __main__ niet uit.
# Daarom initialiseren we de database hier.
init_db()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        debug=True
    )
