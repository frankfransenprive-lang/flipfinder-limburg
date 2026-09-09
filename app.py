from flask import Flask, jsonify, request, send_from_directory
import sqlite3, os, json
from pathlib import Path

BASE=Path(__file__).resolve().parent
DB=BASE/"flipfinder.db"
PUBLIC=BASE/"public"
app=Flask(__name__, static_folder=str(PUBLIC), static_url_path="")

DEMO=[
("ff-001","Brunssum","Voorbeeldstraat 12",189000,112,210,"E","Tussenwoning",1968,38000,295000,"https://example.com/1"),
("ff-002","Heerlen","Mijnweg 44",219000,126,178,"D","Hoekwoning",1974,45000,329000,"https://example.com/2"),
("ff-003","Kerkrade","Marktlaan 7",169000,104,160,"F","Tussenwoning",1958,52000,275000,"https://example.com/3"),
("ff-004","Sittard","Parkweg 18",279000,131,240,"C","2-onder-1-kap",1982,35000,385000,"https://example.com/4"),
("ff-005","Maastricht","Heuvellaan 3",349000,118,95,"D","Stadswoning",1936,60000,485000,"https://example.com/5"),
("ff-006","Roermond","Singel 29",244000,121,205,"C","Hoekwoning",1977,42000,345000,"https://example.com/6")
]

def conn():
    c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c

def init_db():
    c=conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS properties(
      source_id TEXT PRIMARY KEY, city TEXT, street TEXT, price REAL, size_m2 REAL, plot_m2 REAL,
      energy_label TEXT, property_type TEXT, build_year INTEGER, renovation_estimate REAL,
      resale_estimate REAL, source_url TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS alerts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,min_profit REAL,min_roi REAL,max_price REAL,city TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    c.commit();c.close()

def import_demo():
    c=conn()
    for row in DEMO:
        c.execute("""INSERT INTO properties(source_id,city,street,price,size_m2,plot_m2,energy_label,property_type,build_year,renovation_estimate,resale_estimate,source_url)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
        city=excluded.city,street=excluded.street,price=excluded.price,size_m2=excluded.size_m2,plot_m2=excluded.plot_m2,
        energy_label=excluded.energy_label,property_type=excluded.property_type,build_year=excluded.build_year,
        renovation_estimate=excluded.renovation_estimate,resale_estimate=excluded.resale_estimate,source_url=excluded.source_url""",row)
    c.commit();c.close();return len(DEMO)

@app.get("/")
def home():return send_from_directory(PUBLIC,"index.html")

@app.get("/api/properties")
def properties():
    c=conn();rows=[dict(r) for r in c.execute("SELECT * FROM properties ORDER BY created_at DESC")];c.close();return jsonify(rows)

@app.post("/api/import")
def do_import():
    # Replace this function with an authorised listing-feed adapter.
    n=import_demo();return jsonify({"imported":n})

@app.post("/api/alerts")
def add_alert():
    d=request.get_json(force=True)
    c=conn();c.execute("INSERT INTO alerts(min_profit,min_roi,max_price,city) VALUES(?,?,?,?)",(d.get("min_profit"),d.get("min_roi"),d.get("max_price"),d.get("city","")));c.commit();c.close()
    return jsonify({"ok":True})

@app.get("/api/status")
def status():
    return jsonify({
      "database": DB.exists(),
      "listing_feed":"demo-adapter actief; productie-feed nog niet gekoppeld",
      "kadaster":"adapter voorbereid; API-key/serverkoppeling nog nodig",
      "push":"meldingsregels actief; APNs/Web Push nog niet gekoppeld"
    })

if __name__=="__main__":
    init_db()
    if not DB.exists() or sqlite3.connect(DB).execute("SELECT COUNT(*) FROM properties").fetchone()[0]==0:
        import_demo()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",8000)),debug=True)
