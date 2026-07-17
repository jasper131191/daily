"""
Tagebuch – Cloud-fähiger Webserver
Lokal:  python tagebuch_app.py    → http://localhost:5757
Cloud:  gunicorn tagebuch_app:app --bind 0.0.0.0:$PORT --workers 1

Daten werden in SQLite gespeichert (tagebuch.db).
Für Railway: Umgebungsvariable DB_PATH=/data/tagebuch.db setzen.
"""
import os
import re
import json
import uuid
import base64
import sqlite3
from datetime import date, datetime
from flask import Flask, request, jsonify, render_template_string, Response, session, redirect

try:
    import anthropic as _anthropic
    _anthropic_client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
except ImportError:
    _anthropic_client = None

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tagebuch-dev-key-bitte-aendern")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# ── Datenbank ──────────────────────────────────────────────────────────────
# Lokal: tagebuch.db neben dieser Datei
# Railway: Setze DB_PATH=/data/tagebuch.db als Umgebungsvariable (Volume-Pfad)
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tagebuch.db")
)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS eintraege (
            datum    TEXT PRIMARY KEY,
            stimmung INTEGER NOT NULL,
            energie  INTEGER NOT NULL,
            koerper  INTEGER NOT NULL,
            notiz    TEXT DEFAULT '',
            ort      TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS food_log (
            id             TEXT PRIMARY KEY,
            datum          TEXT NOT NULL,
            uhrzeit        TEXT NOT NULL,
            beschreibung   TEXT DEFAULT '',
            kcal           INTEGER,
            kohlenhydrate  INTEGER,
            fett           INTEGER,
            protein        INTEGER,
            foto           TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS gewicht (
            datum TEXT PRIMARY KEY,
            wert  REAL NOT NULL
        );
    """)
    conn.commit()
    conn.close()

init_db()

# ── Login ─────────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📓 Tagebuch – Login</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f5f7; min-height: 100vh;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }
  .card {
    background: #fff; border: 1px solid #e4e4e7;
    border-radius: 14px; box-shadow: 0 1px 16px rgba(0,0,0,0.07);
    padding: 40px 36px; width: 100%; max-width: 360px; text-align: center;
  }
  h1 { font-size: 1.4rem; margin-bottom: 6px; font-weight: 700; }
  p  { color: #8e8e93; font-size: 0.9rem; margin-bottom: 28px; }
  input {
    width: 100%; border: 1.5px solid #e4e4e7; border-radius: 10px;
    padding: 14px 16px; font-size: 1rem; font-family: inherit;
    background: #fafafa; outline: none; margin-bottom: 14px;
    transition: border-color 0.15s;
  }
  input:focus { border-color: #3a6b4a; background: #fff; }
  button {
    width: 100%; background: #1a1a1a; color: #fff; border: none;
    border-radius: 10px; padding: 14px; font-size: 1rem; font-weight: 600;
    cursor: pointer; transition: background 0.15s;
  }
  button:hover { background: #333; }
  .err { color: #c0392b; font-size: 0.85rem; margin-top: 10px; }
</style>
</head>
<body>
<div class="card">
  <h1>Mein Tagebuch</h1>
  <p>Bitte Passwort eingeben</p>
  <form method="post">
    <input type="password" name="password" placeholder="Passwort" autofocus>
    <button type="submit">Einloggen</button>
  </form>
  {% if error %}<div class="err">Falsches Passwort</div>{% endif %}
</div>
</body>
</html>"""

@app.before_request
def check_login():
    """Alle Routen außer /login erfordern Login."""
    if not APP_PASSWORD:
        return  # Kein Passwort gesetzt → kein Schutz (lokale Entwicklung)
    if request.path.startswith("/login") or request.path.startswith("/logout"):
        return
    if not session.get("logged_in"):
        return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    error = False
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect("/")
        error = True
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ── HTML / CSS / JS (Frontend) ─────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<title>Daily</title>
<style>
  :root {
    --bg: #f5f5f7;
    --card: #ffffff;
    --border: #e4e4e7;
    --text: #1a1a1a;
    --muted: #8e8e93;
    --accent: #3a6b4a;
    --accent-light: #eef4f0;
    --gold: #d4920a;
    --gold-hover: #e8a820;
    --gold-dim: #d8d8dc;
    --radius: 12px;
    --shadow: 0 1px 8px rgba(0,0,0,0.05);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { overflow-x: hidden; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    min-height: 100dvh;
    overflow-x: hidden;
    padding: 20px 16px env(safe-area-inset-bottom, 16px);
    padding-left: max(16px, env(safe-area-inset-left));
    padding-right: max(16px, env(safe-area-inset-right));
    padding-top: max(20px, env(safe-area-inset-top));
  }
  .wrap { max-width: 560px; margin: 0 auto; width: 100%; }
  /* Text overflow prevention */
  p, span, div, label, textarea { overflow-wrap: break-word; word-break: break-word; }
  img { max-width: 100%; height: auto; }

  /* Header */
  .header { text-align: left; margin-bottom: 28px; padding: 0 2px; }
  .header h1 { font-size: 1.35rem; font-weight: 700; letter-spacing: -0.025em; color: var(--text); }
  .header p { color: var(--muted); font-size: 0.85rem; margin-top: 3px; }

  /* Card */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 20px;
    margin-bottom: 16px;
    min-width: 0;
  }

  /* Star rows */
  .category { margin-bottom: 20px; }
  .category:last-of-type { margin-bottom: 0; }
  .cat-label {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .cat-label .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
  }
  .stars {
    display: flex;
    gap: 4px;
  }
  .star {
    font-size: clamp(1.4rem, 8vw, 1.75rem);
    cursor: pointer;
    color: var(--gold-dim);
    transition: color 0.12s;
    user-select: none;
    line-height: 1;
  }
  .star:hover, .star.active { color: var(--gold); }

  /* Note */
  .note-wrap { margin-top: 24px; }
  .note-wrap label {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    display: block;
    margin-bottom: 8px;
  }
  textarea {
    width: 100%;
    border: 1.5px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    font-size: 0.95rem;
    font-family: inherit;
    color: var(--text);
    background: #fafafa;
    resize: none;
    transition: border-color 0.15s;
    outline: none;
  }
  textarea:focus { border-color: var(--accent); background: #fff; }
  textarea::placeholder { color: #bbb; }

  /* Submit */
  .btn-row { margin-top: 20px; display: flex; align-items: center; gap: 12px; }
  button {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 10px;
    padding: 12px 28px;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s, transform 0.1s;
  }
  button:hover { background: #3a6449; }
  button:active { transform: scale(0.98); }
  button:disabled { background: #aaa; cursor: default; }
  .date-input {
    border: 1.5px solid var(--border);
    border-radius: 10px;
    padding: 11px 12px;
    font-size: 0.9rem;
    font-family: inherit;
    color: var(--text);
    background: #fafafa;
    outline: none;
    transition: border-color 0.15s;
  }
  .date-input:focus { border-color: var(--accent); }

  /* Toast */
  #toast {
    position: fixed; top: 20px; left: 50%; transform: translateX(-50%) translateY(-80px);
    background: #1a1a1a; color: #fff;
    padding: 10px 20px; border-radius: 8px;
    font-weight: 500; font-size: 0.88rem;
    box-shadow: 0 4px 16px rgba(0,0,0,0.18);
    transition: transform 0.25s ease;
    z-index: 999;
    white-space: nowrap;
  }
  #toast.show { transform: translateX(-50%) translateY(0); }

  /* History */
  .section-title {
    font-size: 0.78rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
    margin-bottom: 14px;
  }
  .entry-list { display: flex; flex-direction: column; gap: 10px; }
  .entry {
    display: grid;
    grid-template-columns: 80px 1fr auto auto;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    font-size: 0.88rem;
  }
  .entry-date { color: var(--muted); font-size: 0.82rem; font-variant-numeric: tabular-nums; }
  .entry-stars { display: flex; flex-direction: column; gap: 2px; }
  .entry-star-row { display: flex; align-items: center; gap: 4px; font-size: 0.75rem; }
  .entry-star-row .lbl { color: var(--muted); width: 58px; }
  .mini-stars { color: var(--gold); letter-spacing: -1px; font-size: 0.85rem; }
  .mini-stars .dim { color: var(--gold-dim); }
  .entry-avg {
    font-size: 1.2rem;
    font-weight: 700;
    color: var(--text);
    text-align: right;
  }
  .entry-location {
    grid-column: 1 / -1;
    font-size: 0.78rem;
    color: var(--accent);
    margin-top: 4px;
    padding-top: 6px;
    border-top: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .entry-note {
    grid-column: 1 / -1;
    margin-top: 0;
    color: #666;
    font-size: 0.82rem;
    padding-top: 4px;
    border-top: 1px solid var(--border);
    margin-top: 4px;
  }

  /* Trend bar */
  .trend-row { display: flex; align-items: flex-end; gap: 4px; height: 40px; margin-top: 16px; }
  .trend-bar {
    flex: 1;
    background: var(--accent-light);
    border-radius: 4px 4px 0 0;
    position: relative;
    transition: background 0.2s;
    cursor: default;
  }
  .trend-bar:hover { background: #d0e8d8; }
  .trend-bar .fill {
    position: absolute; bottom: 0; left: 0; right: 0;
    background: var(--accent);
    border-radius: 4px 4px 0 0;
    transition: height 0.35s ease;
  }
  .trend-label { text-align: center; font-size: 0.65rem; color: var(--muted); margin-top: 4px; }
  .trend-wrap { margin-top: 14px; }
  .trend-wrap .section-title { margin-bottom: 6px; }

  .empty { text-align: center; color: var(--muted); padding: 20px 0; font-size: 0.9rem; }

  /* Edit / Delete buttons */
  .entry { position: relative; }
  .entry-actions {
    display: flex; gap: 4px;
    align-items: center;
  }
  .btn-icon {
    background: none; border: none; padding: 4px 6px;
    font-size: 1rem; cursor: pointer; border-radius: 6px;
    opacity: 0.4; transition: opacity 0.15s, background 0.15s;
    line-height: 1;
  }
  .btn-icon:hover { opacity: 1; background: var(--accent-light); }
  .btn-del:hover  { background: #fde8e8; }
  .range-btn {
    background: var(--bg); border: 1.5px solid var(--border); border-radius: 8px;
    padding: 5px 13px; font-size: 0.82rem; font-weight: 600; cursor: pointer;
    font-family: inherit; color: var(--muted); transition: all .15s;
  }
  .range-btn.active { background: var(--accent-light); color: var(--accent); border-color: var(--accent-light); }
  .stat-avg-box { text-align:center; flex:1; }
  .stat-avg-val { font-size:1.6rem; font-weight:700; line-height:1.1; }
  .stat-avg-lbl { font-size:0.72rem; color:var(--muted); margin-top:2px; }

  /* Tabs */
  .tabs {
    display: flex;
    gap: 6px;
    margin-bottom: 24px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 4px;
    box-shadow: var(--shadow);
  }
  .tab-btn {
    flex: 1;
    background: none;
    color: var(--muted);
    border: none;
    border-radius: 7px;
    padding: 9px 6px;
    font-size: 0.82rem;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
    letter-spacing: 0.01em;
  }
  .tab-btn:hover { background: var(--bg); color: var(--text); }
  .tab-btn.active { background: var(--text); color: #fff; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* Food entries */
  .food-entry {
    padding: 10px 12px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    margin-bottom: 8px;
  }
  .food-entry-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px;
    margin-bottom: 4px;
  }
  .food-entry-bottom {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 6px;
  }
  .food-tag {
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 9px;
    border-radius: 6px;
    background: var(--bg);
    color: var(--muted);
    border: 1px solid var(--border);
  }
  .food-meta { font-size: 0.78rem; color: var(--muted); }
  .food-desc { font-size: 0.88rem; color: var(--text); margin-top: 4px; line-height: 1.4; }
  .food-photo {
    margin-top: 10px;
    border-radius: 10px;
    overflow: hidden;
    max-height: 220px;
    display: flex;
    justify-content: center;
    background: var(--bg);
  }
  .food-photo img {
    max-width: 100%;
    max-height: 220px;
    object-fit: cover;
    border-radius: 10px;
    cursor: pointer;
  }
  .photo-preview {
    margin-top: 12px;
    border-radius: 10px;
    overflow: hidden;
    max-height: 200px;
    display: none;
    justify-content: center;
    background: var(--bg);
  }
  .photo-preview img { max-width: 100%; max-height: 200px; object-fit: cover; border-radius: 10px; }
  .kcal-badge {
    display: inline-block;
    background: var(--bg);
    color: var(--text);
    font-size: 0.76rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    border: 1px solid var(--border);
  }
  .macro-row {
    display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap;
  }
  .macro-badge {
    font-size: 0.71rem; font-weight: 600; padding: 2px 8px;
    border-radius: 6px; border: 1px solid var(--border);
    background: var(--bg); color: var(--muted);
  }
  .macro-kh  { background: var(--bg); color: var(--muted); }
  .macro-fat { background: var(--bg); color: var(--muted); }
  .macro-pro { background: var(--bg); color: var(--muted); }
  .day-group { margin-bottom: 16px; }
  .day-header {
    padding: 10px 14px;
    background: var(--bg);
    border-radius: 10px;
    margin-bottom: 6px;
    border: 1px solid var(--border);
  }
  .day-header-top {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 6px;
  }
  .day-title { font-weight: 700; font-size: 0.88rem; }
  .day-kcal { font-size: 0.85rem; font-weight: 700; color: var(--text); }
  .progress-wrap {
    height: 6px; background: var(--border); border-radius: 3px; overflow: hidden;
  }
  .progress-bar {
    height: 100%; border-radius: 3px;
    transition: width 0.4s ease;
  }
  .day-macros { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
  .ziel-wrap {
    padding: 14px 16px; background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px; margin-bottom: 16px;
  }
  .ziel-wrap-title {
    font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.1em; color: var(--muted); margin-bottom: 10px;
  }
  .ziel-grid {
    display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 10px;
  }
  .ziel-item label {
    font-size: 0.72rem; font-weight: 600; color: var(--muted);
    display: block; margin-bottom: 4px;
  }
  .ziel-input {
    width: 100%; border: 1.5px solid var(--border); border-radius: 8px;
    padding: 8px 10px; font-size: 0.95rem; font-family: inherit;
    background: var(--bg); outline: none; color: var(--text); min-width: 0;
    transition: border-color 0.15s;
  }
  .ziel-input:focus { border-color: var(--accent); background: #fff; }
  /* Macro cells in day-header */
  .macro-cells { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; margin-top: 8px; }
  .macro-cell { background: var(--bg); border-radius: 8px; padding: 7px 10px 6px; }
  .macro-cell-label { font-size: 0.64rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 2px; }
  .macro-cell-value { font-size: 0.78rem; font-weight: 700; color: var(--text); margin-bottom: 5px; }
  .macro-cell-goal  { font-size: 0.64rem; color: var(--muted); font-weight: 400; }
  .macro-cell-bar   { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .macro-cell-fill  { height: 100%; border-radius: 2px; transition: width 0.35s ease; }
  .macro-inputs { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-top: 10px; min-width: 0; }
  .analyzing {
    display: none;
    align-items: center;
    gap: 8px;
    font-size: 0.85rem;
    color: var(--muted);
    margin-top: 10px;
    padding: 10px 14px;
    background: var(--accent-light);
    border-radius: 10px;
  }
  .analyzing.show { display: flex; }
  .upload-btn {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--bg);
    color: var(--text);
    border: 1.5px dashed var(--border);
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 0.9rem;
    cursor: pointer;
    width: 100%;
    transition: border-color 0.15s, background 0.15s;
    margin-top: 12px;
  }
  .upload-btn:hover { border-color: var(--accent); background: var(--accent-light); color: var(--accent); }

  /* Übersicht-Tab */
  .day-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    margin-bottom: 14px;
    box-shadow: var(--shadow);
  }
  .day-card-date {
    padding: 10px 16px;
    background: var(--bg);
    color: var(--text);
    font-weight: 700;
    font-size: 0.82rem;
    border-bottom: 1px solid var(--border);
    letter-spacing: 0.02em;
  }
  .day-tagebuch {
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
  }
  .day-tagebuch-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .day-avg-big {
    font-size: 1.5rem;
    font-weight: 700;
  }
  .day-notiz-text {
    font-size: 0.82rem;
    color: #666;
    margin-top: 6px;
    font-style: italic;
    line-height: 1.35;
  }
  .day-essen {
    padding: 12px 16px;
  }
  .day-essen-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }
  .essen-label { font-size: 0.78rem; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }
  .essen-item-row {
    display: flex;
    align-items: center;
    padding: 5px 0;
    font-size: 0.82rem;
    border-top: 1px solid var(--border);
    gap: 8px;
  }
  .essen-item-time { color: var(--muted); font-size: 0.75rem; min-width: 36px; }
  .essen-item-kcal { color: var(--muted); font-weight: 600; font-size: 0.78rem; white-space: nowrap; }
  .no-entry { font-size: 0.8rem; color: var(--muted); padding: 6px 0; font-style: italic; }
</style>
</head>
<body>
<div id="toast">✓ Eintrag gespeichert!</div>
<div class="wrap">

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('eingabe')">Eingabe</button>
    <button class="tab-btn" onclick="switchTab('uebersicht')">Verlauf</button>
    <button class="tab-btn" onclick="switchTab('statistik')">Statistik</button>
  </div>

  <!-- ══ TAB: EINGABE ══ -->
  <div id="tab-eingabe" class="tab-content active">

  <!-- 1. Was hast du gegessen? -->
  <div class="card">
    <div class="section-title" style="margin-bottom:14px">Was hast du gegessen?</div>

    <label class="upload-btn" for="food-photo-input">
      📷 Foto aufnehmen oder auswählen
      <input type="file" id="food-photo-input" accept="image/*"
             style="display:none" onchange="previewPhoto(this)">
    </label>

    <div class="analyzing" id="analyzing-indicator">
      <span>🔍</span> Erkenne Mahlzeit und schätze Kalorien…
    </div>

    <div class="photo-preview" id="photo-preview">
      <img id="photo-preview-img" src="" alt="Vorschau">
    </div>

    <div class="note-wrap" style="margin-top:14px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <label style="margin-bottom:0">Beschreibung</label>
        <button onclick="analyzeText()" id="estimate-btn"
          style="background:var(--accent-light);color:var(--accent);border:none;border-radius:8px;
                 padding:4px 12px;font-size:0.78rem;font-weight:600;cursor:pointer;font-family:inherit">
          🔥 Kalorien schätzen
        </button>
      </div>
      <textarea id="food-desc" rows="2" placeholder="Wird automatisch erkannt, oder selbst eingeben…"></textarea>
    </div>

    <div style="margin-top:10px">
      <label style="font-size:0.78rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);display:block;margin-bottom:6px">Kalorien (kcal)</label>
      <input type="number" id="food-kcal" min="0" placeholder="wird geschätzt…"
        style="width:100%;border:1.5px solid var(--border);border-radius:10px;padding:11px 14px;
               font-size:0.95rem;font-family:inherit;background:#fafafa;outline:none;color:var(--text)">
    </div>
    <div class="macro-inputs">
      <div>
        <label style="font-size:0.72rem;font-weight:700;color:var(--muted);display:block;margin-bottom:4px">KH (g)</label>
        <input type="number" id="food-kh" min="0" placeholder="—"
          style="width:100%;border:1.5px solid var(--border);border-radius:8px;padding:8px 10px;
                 font-size:0.9rem;font-family:inherit;background:#fafafa;outline:none;color:var(--text)">
      </div>
      <div>
        <label style="font-size:0.72rem;font-weight:700;color:var(--muted);display:block;margin-bottom:4px">Fett (g)</label>
        <input type="number" id="food-fett" min="0" placeholder="—"
          style="width:100%;border:1.5px solid var(--border);border-radius:8px;padding:8px 10px;
                 font-size:0.9rem;font-family:inherit;background:#fafafa;outline:none;color:var(--text)">
      </div>
      <div>
        <label style="font-size:0.72rem;font-weight:700;color:var(--muted);display:block;margin-bottom:4px">Protein (g)</label>
        <input type="number" id="food-pro" min="0" placeholder="—"
          style="width:100%;border:1.5px solid var(--border);border-radius:8px;padding:8px 10px;
                 font-size:0.9rem;font-family:inherit;background:#fafafa;outline:none;color:var(--text)">
      </div>
    </div>

    <div class="btn-row" style="margin-top:16px">
      <button id="food-submit-btn" onclick="submitFood()" disabled>Speichern</button>
      <button id="food-cancel-btn" onclick="cancelFoodEdit()"
        style="display:none;background:var(--bg);color:var(--muted);border:1.5px solid var(--border);
               border-radius:10px;padding:12px 20px;font-size:0.95rem;font-weight:600;cursor:pointer;font-family:inherit">
        Abbrechen
      </button>
      <input type="date" id="food-date" class="date-input">
    </div>
  </div>

  <!-- 2. Tagebuch -->
  <div class="card">
    <div class="section-title" style="margin-bottom:16px">Tagebuch</div>
    <div class="category">
      <div class="cat-label"><span class="dot" style="background:#3498db"></span>Stimmung</div>
      <div class="stars" id="stars-0">
        <span class="star" data-cat="0" data-val="1">★</span>
        <span class="star" data-cat="0" data-val="2">★</span>
        <span class="star" data-cat="0" data-val="3">★</span>
        <span class="star" data-cat="0" data-val="4">★</span>
        <span class="star" data-cat="0" data-val="5">★</span>
      </div>
    </div>
    <div class="category">
      <div class="cat-label"><span class="dot" style="background:#2ecc71"></span>Energie &amp; Schlaf</div>
      <div class="stars" id="stars-1">
        <span class="star" data-cat="1" data-val="1">★</span>
        <span class="star" data-cat="1" data-val="2">★</span>
        <span class="star" data-cat="1" data-val="3">★</span>
        <span class="star" data-cat="1" data-val="4">★</span>
        <span class="star" data-cat="1" data-val="5">★</span>
      </div>
    </div>
    <div class="category">
      <div class="cat-label"><span class="dot" style="background:#e67e22"></span>Körperliches Wohlbefinden</div>
      <div class="stars" id="stars-2">
        <span class="star" data-cat="2" data-val="1">★</span>
        <span class="star" data-cat="2" data-val="2">★</span>
        <span class="star" data-cat="2" data-val="3">★</span>
        <span class="star" data-cat="2" data-val="4">★</span>
        <span class="star" data-cat="2" data-val="5">★</span>
      </div>
    </div>

    <div class="note-wrap">
      <label>Notiz (optional)</label>
      <textarea id="note" rows="2" placeholder="Was hast du heute gemacht?"></textarea>
    </div>

    <div class="note-wrap" style="margin-top:12px">
      <label style="display:flex;align-items:center;gap:8px">
        📍 Ort
        <span id="loc-status" style="font-weight:400;color:var(--muted);font-size:0.75rem;text-transform:none;letter-spacing:0"></span>
      </label>
      <div style="display:flex;gap:8px;align-items:center">
        <input id="ort" type="text" placeholder="Wird automatisch erkannt…"
          style="flex:1;border:1.5px solid var(--border);border-radius:10px;padding:11px 14px;
                 font-size:0.95rem;font-family:inherit;background:#fafafa;outline:none;
                 transition:border-color 0.15s;color:var(--text)">
        <button onclick="detectLocation()" title="Neu ermitteln"
          style="background:var(--accent-light);color:var(--accent);border:none;
                 border-radius:10px;padding:11px 14px;cursor:pointer;font-size:1rem">
          🔄
        </button>
      </div>
    </div>

    <div class="btn-row">
      <button id="submit-btn" onclick="submitEntry()" disabled>Eintragen</button>
      <button id="cancel-btn" onclick="cancelEdit()" style="display:none;background:#888">Abbrechen</button>
      <input type="date" id="entry-date" class="date-input">
    </div>
  </div>

  <!-- 3. Gewicht heute -->
  <div class="card" style="margin-bottom:12px">
    <div class="section-title" style="margin-bottom:12px">Gewicht heute</div>
    <div style="display:flex;align-items:center;gap:10px">
      <input type="number" id="gewicht-input" step="0.1" min="20" max="300" placeholder="kg"
        style="width:100px;border:1.5px solid var(--border);border-radius:8px;padding:9px 12px;
               font-size:1rem;font-family:inherit;background:#fafafa;outline:none;color:var(--text)">
      <button onclick="saveGewicht()"
        style="background:var(--accent);color:#fff;border:none;border-radius:8px;
               padding:9px 18px;font-size:0.88rem;font-weight:600;cursor:pointer;font-family:inherit">
        Speichern
      </button>
      <span id="gewicht-status" style="font-size:0.8rem;color:var(--muted)"></span>
    </div>
  </div>

  <!-- 4. Tagesziele -->
  <div class="ziel-wrap">
    <div class="ziel-wrap-title">Tagesziele</div>
    <div class="ziel-grid">
      <div class="ziel-item">
        <label>Kalorien (kcal)</label>
        <input type="number" id="kcal-ziel" class="ziel-input" placeholder="2000" min="0"
          onchange="saveSetting('kcal_ziel', this.value)">
      </div>
      <div class="ziel-item">
        <label>Kohlenhydrate (g)</label>
        <input type="number" id="kh-ziel" class="ziel-input" placeholder="250" min="0"
          onchange="saveSetting('kh_ziel', this.value)">
      </div>
      <div class="ziel-item">
        <label>Fett (g)</label>
        <input type="number" id="fett-ziel" class="ziel-input" placeholder="70" min="0"
          onchange="saveSetting('fett_ziel', this.value)">
      </div>
      <div class="ziel-item">
        <label>Protein (g)</label>
        <input type="number" id="pro-ziel" class="ziel-input" placeholder="120" min="0"
          onchange="saveSetting('pro_ziel', this.value)">
      </div>
    </div>
  </div>

  <div id="food-list" style="display:none"></div>

  </div><!-- end tab-eingabe -->

  <!-- ══ TAB: ÜBERSICHT ══ -->
  <div id="tab-uebersicht" class="tab-content">
    <div id="overview-list">
      <div class="empty">Lädt…</div>
    </div>
  </div>

  <!-- ══ TAB: STATISTIK ══ -->
  <div id="tab-statistik" class="tab-content">

    <div class="card" style="margin-bottom:12px">
      <div class="section-title" style="margin-bottom:12px">Stimmung, Energie & Körper</div>
      <div style="display:flex;gap:8px;margin-bottom:14px">
        <button class="range-btn active" onclick="setStatsRange(30,this)">30 Tage</button>
        <button class="range-btn" onclick="setStatsRange(90,this)">90 Tage</button>
        <button class="range-btn" onclick="setStatsRange(0,this)">Alles</button>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:0.8rem;font-weight:700;color:#e8a020;margin-bottom:4px;letter-spacing:0.04em">😊 STIMMUNG</div>
        <svg id="chart-stimmung" viewBox="0 0 360 130" style="width:100%;height:auto;display:block"></svg>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:0.8rem;font-weight:700;color:#2980b9;margin-bottom:4px;letter-spacing:0.04em">⚡ ENERGIE</div>
        <svg id="chart-energie" viewBox="0 0 360 130" style="width:100%;height:auto;display:block"></svg>
      </div>
      <div style="margin-bottom:4px">
        <div style="font-size:0.8rem;font-weight:700;color:#27ae60;margin-bottom:4px;letter-spacing:0.04em">💪 KÖRPER</div>
        <svg id="chart-koerper" viewBox="0 0 360 130" style="width:100%;height:auto;display:block"></svg>
      </div>
      <div id="stats-averages" style="display:flex;gap:8px;margin-top:16px;padding-top:14px;border-top:1px solid var(--border)"></div>
    </div>

    <div class="card" id="gewicht-chart-card" style="display:none">
      <div class="section-title" style="margin-bottom:8px">⚖️ Gewichtsverlauf</div>
      <svg id="weight-chart" viewBox="0 0 360 160" style="width:100%;height:auto;display:block"></svg>
    </div>

    <div class="card" id="stats-empty" style="display:none">
      <div class="empty">Noch keine Einträge im gewählten Zeitraum</div>
    </div>

  </div>

</div>

<script>
const ratings = [0, 0, 0];

// Default: yesterday (entries are usually made the day after)
const today = new Date().toISOString().slice(0, 10);
const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
document.getElementById('entry-date').value = yesterday;

// Star interaction
document.querySelectorAll('.star').forEach(star => {
  star.addEventListener('click', () => {
    const cat = +star.dataset.cat;
    const val = +star.dataset.val;
    ratings[cat] = val;
    updateStars(cat, val);
    checkReady();
  });
  star.addEventListener('mouseenter', () => {
    const cat = +star.dataset.cat;
    const val = +star.dataset.val;
    highlightStars(cat, val);
  });
  star.addEventListener('mouseleave', () => {
    const cat = +star.dataset.cat;
    updateStars(cat, ratings[cat]);
  });
});

function highlightStars(cat, upTo) {
  document.querySelectorAll(`.star[data-cat="${cat}"]`).forEach(s => {
    s.style.color = +s.dataset.val <= upTo ? 'var(--gold-hover)' : 'var(--gold-dim)';
  });
}
function updateStars(cat, val) {
  document.querySelectorAll(`.star[data-cat="${cat}"]`).forEach(s => {
    const active = +s.dataset.val <= val;
    s.classList.toggle('active', active);
    s.style.color = '';
  });
}
function checkReady() {
  document.getElementById('submit-btn').disabled = ratings.some(r => r === 0);
}

let editingDatum = null;  // null = neuer Eintrag, sonst Datum des zu editierenden

async function submitEntry() {
  const btn = document.getElementById('submit-btn');
  btn.disabled = true;
  btn.textContent = '…';
  const datum = document.getElementById('entry-date').value;
  const payload = {
    stimmung: ratings[0], energie: ratings[1], koerper: ratings[2],
    notiz: document.getElementById('note').value.trim(),
    ort: document.getElementById('ort').value.trim(),
    datum
  };
  try {
    let res;
    if (editingDatum) {
      // Datum in DD.MM.YYYY für die API
      const [y,m,d] = editingDatum.split('-');
      res = await fetch(`/entry/${d}.${m}.${y}`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
    } else {
      res = await fetch('/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
    }
    const data = await res.json();
    if (data.ok) {
      showToast(editingDatum ? '✓ Eintrag aktualisiert!' : '✓ Eintrag gespeichert!');
      cancelEdit();
      loadEntries();
    } else {
      showToast('⚠ Fehler: ' + data.error, true);
    }
  } catch(e) {
    console.error('submitEntry Fehler:', e);
    showToast('⚠ Verbindungsfehler', true);
  }
  btn.textContent = editingDatum ? 'Speichern' : 'Eintragen';
  checkReady();
}

function startEdit(e) {
  editingDatum = e.datum.split('.').reverse().join('-'); // DD.MM.YYYY → YYYY-MM-DD
  ratings[0] = e.stimmung; ratings[1] = e.energie; ratings[2] = e.koerper;
  [0,1,2].forEach(c => updateStars(c, ratings[c]));
  document.getElementById('note').value = e.notiz || '';
  document.getElementById('ort').value  = e.ort  || '';
  document.getElementById('loc-status').textContent = '';
  document.getElementById('entry-date').value = editingDatum;
  document.getElementById('entry-date').disabled = true;
  document.getElementById('submit-btn').textContent = 'Speichern';
  document.getElementById('submit-btn').disabled = false;
  document.getElementById('cancel-btn').style.display = '';
  // header-subtitle entfernt
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function cancelEdit() {
  editingDatum = null;
  ratings[0] = ratings[1] = ratings[2] = 0;
  [0,1,2].forEach(c => updateStars(c, 0));
  document.getElementById('note').value = '';
  document.getElementById('ort').value = '';
  document.getElementById('loc-status').textContent = '';
  document.getElementById('entry-date').value = yesterday;
  document.getElementById('entry-date').disabled = false;
  document.getElementById('submit-btn').textContent = 'Eintragen';
  document.getElementById('cancel-btn').style.display = 'none';
  // header-subtitle entfernt
  detectLocation();
  checkReady();
}

async function deleteEntry(datum) {
  if (!confirm(`Eintrag vom ${datum} wirklich löschen?`)) return;
  const [d,m,y] = datum.split('.');
  const res = await fetch(`/entry/${datum}`, {method: 'DELETE'});
  const data = await res.json();
  if (data.ok) { showToast('🗑 Eintrag gelöscht'); loadEntries(); }
  else showToast('⚠ Fehler: ' + data.error, true);
}

function showToast(msg, err=false) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = err ? '#c0392b' : '#1a1a1a';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}

function miniStars(n) {
  return '<span class="mini-stars">'
    + '★'.repeat(n)
    + '<span class="dim">' + '★'.repeat(5-n) + '</span></span>';
}

async function loadEntries() {
  const res = await fetch('/entries');
  const data = await res.json();
  const list = document.getElementById('entry-list');
  if (!list) return; // entry-list nicht mehr auf dieser Seite
  if (!data.entries || data.entries.length === 0) {
    list.innerHTML = '<div class="empty">Noch keine Einträge</div>';
    return;
  }
  list.innerHTML = data.entries.slice().reverse().map(e => `
    <div class="entry">
      <div class="entry-date">${e.datum}</div>
      <div class="entry-stars">
        <div class="entry-star-row"><span class="lbl">Stimmung</span>${miniStars(e.stimmung)}</div>
        <div class="entry-star-row"><span class="lbl">Energie</span>${miniStars(e.energie)}</div>
        <div class="entry-star-row"><span class="lbl">Körper</span>${miniStars(e.koerper)}</div>
      </div>
      <div class="entry-avg">${e.avg}</div>
      <div class="entry-actions">
        <button class="btn-icon" onclick='startEdit(${JSON.stringify(e)})' title="Bearbeiten">✏️</button>
        <button class="btn-icon btn-del" onclick="deleteEntry('${e.datum}')" title="Löschen">🗑</button>
      </div>
      ${e.ort ? `<div class="entry-location">📍 ${e.ort}</div>` : ''}
      ${e.notiz ? `<div class="entry-note">${e.notiz}</div>` : ''}
    </div>
  `).join('');

  // Trend bars (last 14)
  const recent = data.entries.slice(-14);
  const trendWrap = document.getElementById('trend-wrap');
  if (recent.length >= 2 && trendWrap) {
    trendWrap.style.display = '';
    const bars = document.getElementById('trend-bars');
    const labels = document.getElementById('trend-labels');
    bars.innerHTML = recent.map(e => {
      const pct = (parseFloat(e.avg) / 5 * 100).toFixed(0);
      const color = parseFloat(e.avg) >= 4 ? '#4a7c59' : parseFloat(e.avg) >= 3 ? '#e8a020' : '#c0392b';
      return `<div class="trend-bar" title="${e.datum}: Ø ${e.avg}">
        <div class="fill" style="height:${pct}%;background:${color}"></div>
      </div>`;
    }).join('');
    labels.innerHTML = recent.map(e => {
      const d = new Date(e.datum.split('.').reverse().join('-'));
      return `<div style="flex:1;text-align:center;font-size:0.62rem;color:var(--muted)">${d.getDate()}.</div>`;
    }).join('');
  }
}

// ── Tab switching ────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector(`.tab-btn[onclick="switchTab('${name}')"]`).classList.add('active');
  if (name === 'eingabe') { loadFood(); loadZiel(); }
  if (name === 'uebersicht') loadOverview();
  if (name === 'statistik') loadStats();
}

// ── Food tracking ─────────────────────────────────────────────
let selectedPhotoData = null;
let editingFoodId = null;
let editingFoodHasPhoto = false;
let foodEntriesMap = {};

document.getElementById('food-date').value = today;

// Ziele laden (mit sinnvollen Startwerten für 130 kg / 2100 kcal)
async function loadZiel() {
  try {
    const res = await fetch('/settings');
    const data = await res.json();
    const defaults = { kcal_ziel: 2100, kh_ziel: 200, fett_ziel: 70, pro_ziel: 160 };
    // Fehlende Werte mit Defaults belegen und direkt speichern
    const toSave = {};
    for (const [k, v] of Object.entries(defaults)) {
      if (data[k] == null) toSave[k] = v;
    }
    if (Object.keys(toSave).length) {
      await fetch('/settings', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(toSave)
      });
      Object.assign(data, toSave);
    }
    document.getElementById('kcal-ziel').value = data.kcal_ziel;
    document.getElementById('kh-ziel').value   = data.kh_ziel;
    document.getElementById('fett-ziel').value = data.fett_ziel;
    document.getElementById('pro-ziel').value  = data.pro_ziel;
  } catch(e) {}
}
async function saveSetting(key, val) {
  await fetch('/settings', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ [key]: parseInt(val) || null })
  });
  loadFood();
}

function fillMacros(data) {
  if (data.kcal)          document.getElementById('food-kcal').value  = data.kcal;
  if (data.kohlenhydrate) document.getElementById('food-kh').value    = data.kohlenhydrate;
  if (data.fett)          document.getElementById('food-fett').value  = data.fett;
  if (data.protein)       document.getElementById('food-pro').value   = data.protein;
}

function checkFoodReady() {
  const hasDesc = document.getElementById('food-desc').value.trim().length > 0;
  document.getElementById('food-submit-btn').disabled = !(hasDesc || selectedPhotoData);
}
document.getElementById('food-desc').addEventListener('input', checkFoodReady);

function previewPhoto(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async e => {
    selectedPhotoData = e.target.result;
    document.getElementById('photo-preview-img').src = selectedPhotoData;
    document.getElementById('photo-preview').style.display = 'flex';
    checkFoodReady();
    await analyzePhoto(selectedPhotoData);
  };
  reader.readAsDataURL(file);
}

async function analyzePhoto(photoData) {
  const indicator = document.getElementById('analyzing-indicator');
  indicator.classList.add('show');
  try {
    const res = await fetch('/food-analyze', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ foto: photoData })
    });
    const data = await res.json();
    if (data.ok) { fillMacros(data); if (data.beschreibung) document.getElementById('food-desc').value = data.beschreibung; checkFoodReady(); }
  } catch(e) {}
  indicator.classList.remove('show');
}

async function analyzeText() {
  const desc = document.getElementById('food-desc').value.trim();
  if (!desc) return;
  const btn = document.getElementById('estimate-btn');
  btn.textContent = '⏳ …'; btn.disabled = true;
  try {
    const res = await fetch('/food-analyze', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ beschreibung: desc })
    });
    const data = await res.json();
    if (data.ok) fillMacros(data);
  } catch(e) {}
  btn.textContent = '🔥 Schätzen'; btn.disabled = false;
}

async function submitFood() {
  const btn = document.getElementById('food-submit-btn');
  const wasEditing = editingFoodId;
  btn.disabled = true; btn.textContent = '…';
  const n = id => { const v = document.getElementById(id).value; return v ? parseInt(v) : null; };
  const origEntry = wasEditing ? foodEntriesMap[wasEditing] : null;
  const payload = {
    datum:        document.getElementById('food-date').value,
    uhrzeit:      origEntry ? origEntry.uhrzeit : new Date().toTimeString().slice(0,5),
    beschreibung: document.getElementById('food-desc').value.trim(),
    kcal:         n('food-kcal'), kohlenhydrate: n('food-kh'),
    fett:         n('food-fett'), protein:       n('food-pro'),
  };
  if (selectedPhotoData) {
    payload.foto = selectedPhotoData;     // neues Foto
  } else if (!wasEditing) {
    payload.foto = null;                  // neuer Eintrag ohne Foto
  }
  // beim Bearbeiten ohne neues Foto: kein foto-Schlüssel → Server behält bestehendes Foto
  try {
    let res;
    if (wasEditing) {
      res = await fetch(`/food-entry/${wasEditing}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
    } else {
      res = await fetch('/food', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
    }
    const data = await res.json();
    if (data.ok) {
      showToast(wasEditing ? '✓ Mahlzeit aktualisiert!' : '✓ Mahlzeit gespeichert!');
      cancelFoodEdit();
      loadFood();
    } else showToast('⚠ ' + data.error, true);
  } catch(e) { console.error('submitFood Fehler:', e); showToast('⚠ Verbindungsfehler', true); }
  btn.textContent = editingFoodId ? 'Aktualisieren' : 'Speichern';
  checkFoodReady();
}

function cancelFoodEdit() {
  editingFoodId = null;
  editingFoodHasPhoto = false;
  ['food-desc','food-kcal','food-kh','food-fett','food-pro'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('food-photo-input').value = '';
  document.getElementById('photo-preview').style.display = 'none';
  document.getElementById('photo-preview-img').src = '';
  document.getElementById('food-date').value = today;
  document.getElementById('food-submit-btn').textContent = 'Speichern';
  document.getElementById('food-cancel-btn').style.display = 'none';
  selectedPhotoData = null;
  checkFoodReady();
}

function startFoodEdit(id) {
  const e = foodEntriesMap[id];
  if (!e) return;
  editingFoodId = id;
  editingFoodHasPhoto = !!e.foto;
  document.getElementById('food-desc').value = e.beschreibung || '';
  document.getElementById('food-kcal').value = e.kcal  || '';
  document.getElementById('food-kh').value   = e.kohlenhydrate || '';
  document.getElementById('food-fett').value = e.fett  || '';
  document.getElementById('food-pro').value  = e.protein || '';
  // Datum DD.MM.YYYY → YYYY-MM-DD
  const [d,m,y] = e.datum.split('.');
  document.getElementById('food-date').value = `${y}-${m}-${d}`;
  // Bestehendes Foto anzeigen
  if (e.foto) {
    document.getElementById('photo-preview-img').src = `/food-photo/${e.foto}`;
    document.getElementById('photo-preview').style.display = 'flex';
  }
  selectedPhotoData = null;
  document.getElementById('food-submit-btn').textContent = 'Aktualisieren';
  document.getElementById('food-submit-btn').disabled = false;
  document.getElementById('food-cancel-btn').style.display = '';
  window.scrollTo({top: 0, behavior: 'smooth'});
  checkFoodReady();
}

async function deleteFoodEntry(id) {
  if (!confirm('Eintrag wirklich löschen?')) return;
  const res = await fetch('/food-entry/' + id, {method: 'DELETE'});
  const data = await res.json();
  if (data.ok) { showToast('🗑 Gelöscht'); loadFood(); }
  else showToast('⚠ ' + data.error, true);
}

async function loadFood() {
  const [fRes, sRes] = await Promise.all([fetch('/food-entries'), fetch('/settings')]);
  const fData = await fRes.json();
  const sData = await sRes.json().catch(() => ({}));
  const ziel     = sData.kcal_ziel || null;
  const khZiel   = sData.kh_ziel   || null;
  const fettZiel = sData.fett_ziel || null;
  const proZiel  = sData.pro_ziel  || null;
  const list = document.getElementById('food-list');
  if (!fData.entries || fData.entries.length === 0) {
    list.innerHTML = '<div class="empty">Noch keine Einträge</div>'; return;
  }
  // Einträge in Map für späteres Bearbeiten speichern
  foodEntriesMap = {};
  fData.entries.forEach(e => { foodEntriesMap[e.id] = e; });
  // Gruppe nach Tag (neueste zuerst)
  const grouped = {};
  [...fData.entries].reverse().forEach(e => {
    if (!grouped[e.datum]) grouped[e.datum] = [];
    grouped[e.datum].push(e);
  });
  // Helper: Kachel-Fortschrittsanzeige für ein Makro
  function macroCell(label, val, goal) {
    if (!val && !goal) return '';
    const pct = goal ? Math.min(100, Math.round(val / goal * 100)) : 0;
    const color = pct >= 100 ? '#c0392b' : pct >= 80 ? 'var(--accent)' : pct >= 50 ? '#d4920a' : '#c0392b';
    const valHtml = goal
      ? `<div class="macro-cell-value">${val}g <span class="macro-cell-goal">/ ${goal}g</span></div>`
      : `<div class="macro-cell-value">${val}g</div>`;
    const barHtml = goal
      ? `<div class="macro-cell-bar"><div class="macro-cell-fill" style="width:${pct}%;background:${color}"></div></div>`
      : '';
    return `<div class="macro-cell">
      <div class="macro-cell-label">${label}</div>
      ${valHtml}
      ${barHtml}
    </div>`;
  }
  list.innerHTML = Object.entries(grouped).map(([datum, entries]) => {
    const totalKcal = entries.reduce((s,e) => s + (e.kcal||0), 0);
    const totalKh   = entries.reduce((s,e) => s + (e.kohlenhydrate||0), 0);
    const totalFett = entries.reduce((s,e) => s + (e.fett||0), 0);
    const totalPro  = entries.reduce((s,e) => s + (e.protein||0), 0);
    const pct = ziel && totalKcal ? Math.min(100, Math.round(totalKcal/ziel*100)) : 0;
    const barColor = pct >= 100 ? '#c0392b' : pct >= 80 ? '#e8a020' : 'var(--accent)';
    const zielHtml = ziel ? `
      <div class="progress-wrap" style="margin-top:6px">
        <div class="progress-bar" style="width:${pct}%;background:${barColor}"></div>
      </div>
      <div style="font-size:0.72rem;color:var(--muted);margin-top:3px">${totalKcal} / ${ziel} kcal (${pct}%)</div>
    ` : (totalKcal ? `<div style="font-size:0.72rem;color:var(--muted);margin-top:4px">${totalKcal} kcal</div>` : '');
    const hasMacros = totalKh || totalFett || totalPro;
    const macroHtml = hasMacros ? `
      <div class="macro-cells">
        ${macroCell('KH', totalKh, khZiel)}
        ${macroCell('Fett', totalFett, fettZiel)}
        ${macroCell('Protein', totalPro, proZiel)}
      </div>` : '';
    const entriesHtml = entries.map(e => {
      const mKh   = e.kohlenhydrate ? `<span class="macro-badge macro-kh">KH ${e.kohlenhydrate}g</span>` : '';
      const mFett = e.fett   ? `<span class="macro-badge macro-fat">Fett ${e.fett}g</span>` : '';
      const mPro  = e.protein? `<span class="macro-badge macro-pro">Protein ${e.protein}g</span>` : '';
      return `<div class="food-entry">
        <div class="food-entry-top">
          <div class="food-desc" style="flex:1">${e.beschreibung || '—'}</div>
          <span style="display:flex;align-items:center;gap:2px;flex-shrink:0">
            <button class="btn-icon" onclick="startFoodEdit('${e.id}')" title="Bearbeiten">✏️</button>
            <button class="btn-icon btn-del" onclick="deleteFoodEntry('${e.id}')" title="Löschen">🗑</button>
          </span>
        </div>
        ${(mKh||mFett||mPro) ? `<div class="macro-row">${mKh}${mFett}${mPro}</div>` : ''}
        <div class="food-entry-bottom">
          ${e.kcal ? `<span class="kcal-badge">🔥 ${e.kcal} kcal</span>` : '<span></span>'}
          <span class="food-meta">${e.uhrzeit}</span>
        </div>
        ${e.foto ? `<div class="food-photo"><img src="/food-photo/${e.foto}" loading="lazy" onclick="window.open(this.src)"></div>` : ''}
      </div>`;
    }).join('');
    return `<div class="day-group">
      <div class="day-header">
        <div class="day-header-top">
          <span class="day-title">${datum}</span>
          ${totalKcal ? `<span class="day-kcal">${totalKcal} kcal</span>` : ''}
        </div>
        ${zielHtml}${macroHtml}
      </div>
      ${entriesHtml}
    </div>`;
  }).join('');
}

async function loadOverview() {
  const [eRes, fRes, sRes] = await Promise.all([
    fetch('/entries'), fetch('/food-entries'), fetch('/settings')
  ]);
  const eData = await eRes.json();
  const fData = await fRes.json();
  const sData = await sRes.json().catch(() => ({}));
  const ziel  = sData.kcal_ziel || null;
  const list  = document.getElementById('overview-list');

  // Lookup maps
  const diaryMap = {};
  (eData.entries || []).forEach(e => diaryMap[e.datum] = e);
  const foodMap = {};
  (fData.entries || []).forEach(f => {
    if (!foodMap[f.datum]) foodMap[f.datum] = [];
    foodMap[f.datum].push(f);
  });

  // Alle Tage sammeln und neueste zuerst sortieren
  const allDates = new Set([...Object.keys(diaryMap), ...Object.keys(foodMap)]);
  const toNum = d => { const p = d.split('.'); return p[2]+p[1]+p[0]; };
  const sorted = [...allDates].sort((a, b) => toNum(b).localeCompare(toNum(a)));

  if (!sorted.length) {
    list.innerHTML = '<div class="empty">Noch keine Einträge</div>';
    return;
  }

  const dayNames = ['So','Mo','Di','Mi','Do','Fr','Sa'];

  list.innerHTML = sorted.map(datum => {
    const [d, m, y] = datum.split('.');
    const dow = dayNames[new Date(`${y}-${m}-${d}`).getDay()];

    // ── Tagebuch ──
    const diary = diaryMap[datum];
    let tbHtml = '';
    if (diary) {
      const avgColor = diary.avg >= 4 ? '#4a7c59' : diary.avg >= 3 ? '#e8a020' : '#c0392b';
      tbHtml = `<div class="day-tagebuch">
        <div class="day-tagebuch-row">
          <div>
            <div class="entry-star-row"><span class="lbl">Stimmung</span>${miniStars(diary.stimmung)}</div>
            <div class="entry-star-row"><span class="lbl">Energie</span>${miniStars(diary.energie)}</div>
            <div class="entry-star-row"><span class="lbl">Körper</span>${miniStars(diary.koerper)}</div>
          </div>
          <div class="day-avg-big" style="color:${avgColor}">Ø ${diary.avg}</div>
        </div>
        ${diary.ort   ? `<div style="font-size:0.78rem;color:var(--accent);margin-top:6px">📍 ${diary.ort}</div>` : ''}
        ${diary.notiz ? `<div class="day-notiz-text">"${diary.notiz}"</div>` : ''}
      </div>`;
    } else {
      tbHtml = `<div class="day-tagebuch"><div class="no-entry">Kein Tagebucheintrag</div></div>`;
    }

    // ── Essen ──
    const foods = foodMap[datum] || [];
    let esHtml = '';
    if (foods.length) {
      const totalKcal = foods.reduce((s, f) => s + (f.kcal || 0), 0);
      const pct      = ziel && totalKcal ? Math.min(100, Math.round(totalKcal / ziel * 100)) : 0;
      const barColor = pct >= 100 ? '#c0392b' : pct >= 80 ? '#e8a020' : '#4a7c59';
      const progressHtml = ziel ? `
        <div class="progress-wrap" style="margin:4px 0 6px">
          <div class="progress-bar" style="width:${pct}%;background:${barColor}"></div>
        </div>
        <div style="font-size:0.72rem;color:var(--muted);margin-bottom:4px">${totalKcal} / ${ziel} kcal (${pct}%)</div>` : '';
      const items = foods.map(f => `
        <div class="essen-item-row">
          <span class="essen-item-time">${f.uhrzeit}</span>
          <span style="flex:1;line-height:1.3">${f.beschreibung || '–'}</span>
          ${f.kcal ? `<span class="essen-item-kcal">🔥 ${f.kcal}</span>` : ''}
        </div>`).join('');
      esHtml = `<div class="day-essen">
        <div class="day-essen-header">
          <span class="essen-label">🍽️ Essen</span>
          ${totalKcal ? `<span style="font-weight:700;font-size:0.88rem;color:#e67e22">🔥 ${totalKcal} kcal</span>` : ''}
        </div>
        ${progressHtml}${items}
      </div>`;
    } else {
      esHtml = `<div class="day-essen"><div class="no-entry">Kein Essen eingetragen</div></div>`;
    }

    return `<div class="day-card">
      <div class="day-card-date">${dow}, ${datum}</div>
      ${tbHtml}${esHtml}
    </div>`;
  }).join('');
}

// ── Gewicht ──────────────────────────────────────────────────
async function loadGewicht() {
  try {
    const res = await fetch('/gewicht');
    const data = await res.json();
    const todayEntry = (data.entries||[]).find(g => {
      const [d,m,y] = g.datum.split('.');
      return `${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}` === today;
    });
    if (todayEntry) {
      document.getElementById('gewicht-input').value = todayEntry.wert;
      document.getElementById('gewicht-status').textContent = '✓ gespeichert';
    }
  } catch(e) {}
}

async function saveGewicht() {
  const val = parseFloat(document.getElementById('gewicht-input').value);
  if (!val || val < 20 || val > 500) return;
  const status = document.getElementById('gewicht-status');
  status.textContent = '…';
  try {
    const res = await fetch('/gewicht', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ datum: today, wert: val })
    });
    const data = await res.json();
    status.textContent = data.ok ? '✓ gespeichert' : '✗ Fehler';
    if (data.ok && document.getElementById('tab-statistik').classList.contains('active')) loadStats();
  } catch(e) { status.textContent = '✗ Fehler'; }
}

// ── Statistik ─────────────────────────────────────────────────
let statsRange = 30;
let statsCache = null;

function setStatsRange(days, btn) {
  statsRange = days;
  document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderStats();
}

async function loadStats() {
  try {
    const [eRes, gRes] = await Promise.all([fetch('/entries'), fetch('/gewicht')]);
    const eData = await eRes.json();
    const gData = await gRes.json();
    statsCache = { entries: eData.entries || [], gewicht: gData.entries || [] };
    renderStats();
  } catch(e) {}
}

function filterByRange(list, days) {
  if (!days) return list;
  const cutoff = Date.now() - days * 86400000;
  return list.filter(e => {
    const [d,m,y] = e.datum.split('.');
    return new Date(+y, m-1, +d).getTime() >= cutoff;
  });
}

function renderStats() {
  if (!statsCache) return;
  const entries = filterByRange(statsCache.entries, statsRange)
    .slice().sort((a,b) => {
      const ka = a.datum.split('.').reverse().join('');
      const kb = b.datum.split('.').reverse().join('');
      return ka.localeCompare(kb);
    });
  const gewicht = filterByRange(statsCache.gewicht, statsRange)
    .slice().sort((a,b) => {
      const ka = a.datum.split('.').reverse().join('');
      const kb = b.datum.split('.').reverse().join('');
      return ka.localeCompare(kb);
    });

  document.getElementById('stats-empty').style.display = (!entries.length && !gewicht.length) ? '' : 'none';
  drawSingleChart('stimmung', '#e8a020', entries);
  drawSingleChart('energie',  '#2980b9', entries);
  drawSingleChart('koerper',  '#27ae60', entries);

  if (entries.length) {
    const avg = k => (entries.reduce((s,e) => s+(e[k]||0), 0) / entries.length).toFixed(1);
    const avgBox = (val, lbl, color) =>
      `<div class="stat-avg-box">
        <div class="stat-avg-val" style="color:${color}">${val}</div>
        <div class="stat-avg-lbl">${lbl}</div>
      </div>`;
    document.getElementById('stats-averages').innerHTML =
      avgBox(avg('stimmung'), 'Ø Stimmung', '#e8a020') +
      avgBox(avg('energie'),  'Ø Energie',  '#2980b9') +
      avgBox(avg('koerper'),  'Ø Körper',   '#27ae60') +
      avgBox(entries.length,  'Einträge',   'var(--text)');
  } else {
    document.getElementById('stats-averages').innerHTML = '';
  }

  if (gewicht.length) {
    document.getElementById('gewicht-chart-card').style.display = '';
    drawWeightChart(gewicht);
  } else {
    document.getElementById('gewicht-chart-card').style.display = 'none';
  }
}

function drawSingleChart(key, color, entries) {
  const svg = document.getElementById('chart-' + key);
  if (!entries.length) {
    svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#aaa" font-size="12">Keine Daten im Zeitraum</text>';
    return;
  }
  const W=360, H=130, pL=22, pR=10, pT=10, pB=24;
  const w=W-pL-pR, h=H-pT-pB, n=entries.length;
  const xS = i => pL + (n>1 ? i/(n-1)*w : w/2);
  const yS = v => pT + h - (v-1)/4*h;
  let s = '';
  // Hintergrund-Rasterlinien
  for (let v=1;v<=5;v++) {
    const y=yS(v).toFixed(1);
    s+=`<line x1="${pL}" y1="${y}" x2="${W-pR}" y2="${y}" stroke="#e8e8e4" stroke-width="${v===3?1:0.5}"/>`;
    s+=`<text x="${pL-3}" y="${(+y+3).toFixed(1)}" text-anchor="end" font-size="8" fill="#bbb">${v}</text>`;
  }
  // Linie
  const path = entries.map((e,i) => `${i?'L':'M'}${xS(i).toFixed(1)},${yS(e[key]).toFixed(1)}`).join('');
  s+=`<path d="${path}" stroke="${color}" stroke-width="2" fill="none" stroke-linejoin="round"/>`;
  // Punkte
  const r = n>60?1.5:n>30?2:2.8;
  entries.forEach((e,i) => {
    s+=`<circle cx="${xS(i).toFixed(1)}" cy="${yS(e[key]).toFixed(1)}" r="${r}" fill="${color}"/>`;
  });
  // X-Achse Beschriftung
  const step=Math.max(1,Math.floor(n/4));
  const idxs=new Set([0,...Array.from({length:Math.floor(n/step)},(_,i)=>(i+1)*step).filter(i=>i<n),n-1]);
  idxs.forEach(i=>{
    const[d,m]=entries[i].datum.split('.');
    s+=`<text x="${xS(i).toFixed(1)}" y="${H-4}" text-anchor="middle" font-size="9" fill="#aaa">${d}.${m}</text>`;
  });
  svg.innerHTML = s;
}

function drawWeightChart(gewicht) {
  const svg = document.getElementById('weight-chart');
  if (!gewicht.length) { svg.innerHTML=''; return; }
  const W=360, H=160, pL=36, pR=10, pT=12, pB=24;
  const w=W-pL-pR, h=H-pT-pB, n=gewicht.length;
  const vals = gewicht.map(g=>g.wert);
  const yMin=Math.floor(Math.min(...vals)-0.5), yMax=Math.ceil(Math.max(...vals)+0.5);
  const xS = i => pL + (n>1?i/(n-1)*w:w/2);
  const yS = v => pT + h - (v-yMin)/(yMax-yMin)*h;
  let s='';
  const steps=4;
  for(let i=0;i<=steps;i++){
    const v=yMin+i*(yMax-yMin)/steps;
    const y=yS(v).toFixed(1);
    s+=`<line x1="${pL}" y1="${y}" x2="${W-pR}" y2="${y}" stroke="#e8e8e4" stroke-width="0.8"/>`;
    s+=`<text x="${pL-3}" y="${(+y+3).toFixed(1)}" text-anchor="end" font-size="9" fill="#aaa">${v.toFixed(1)}</text>`;
  }
  const path=gewicht.map((g,i)=>`${i?'L':'M'}${xS(i).toFixed(1)},${yS(g.wert).toFixed(1)}`).join('');
  s+=`<path d="${path}" stroke="#555" stroke-width="2" fill="none" stroke-linejoin="round"/>`;
  gewicht.forEach((g,i)=>{
    const x=xS(i).toFixed(1), y=yS(g.wert).toFixed(1);
    s+=`<circle cx="${x}" cy="${y}" r="3" fill="#555"/>`;
    if(n<=25) s+=`<text x="${x}" y="${(+y-6).toFixed(1)}" text-anchor="middle" font-size="8.5" fill="#555">${g.wert}</text>`;
  });
  const step=Math.max(1,Math.floor(n/4));
  const idxs=new Set([0,...Array.from({length:Math.floor(n/step)},(_,i)=>(i+1)*step).filter(i=>i<n),n-1]);
  idxs.forEach(i=>{
    const[d,m]=gewicht[i].datum.split('.');
    s+=`<text x="${xS(i).toFixed(1)}" y="${H-3}" text-anchor="middle" font-size="9" fill="#aaa">${d}.${m}</text>`;
  });
  svg.innerHTML=s;
}

loadEntries();
loadFood();
loadZiel();
loadGewicht();
detectLocation();

async function detectLocation() {
  const status = document.getElementById('loc-status');
  const input  = document.getElementById('ort');
  if (!navigator.geolocation) {
    status.textContent = '(nicht verfügbar)';
    return;
  }
  status.textContent = 'wird ermittelt…';
  navigator.geolocation.getCurrentPosition(async pos => {
    const { latitude: lat, longitude: lon } = pos.coords;
    try {
      const res  = await fetch(
        `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}`,
        { headers: { 'Accept-Language': 'de' } }
      );
      const data = await res.json();
      const a    = data.address || {};
      const ort  = a.suburb || a.neighbourhood || a.village || a.town || a.city || a.county || '';
      const city = (a.city || a.town || a.village || '');
      input.value = ort && city && ort !== city ? `${ort}, ${city}` : city || ort;
      status.textContent = '✓ erkannt';
    } catch {
      status.textContent = '(Fehler beim Abrufen)';
    }
  }, () => {
    status.textContent = '(Zugriff verweigert)';
  }, { timeout: 8000 });
}
</script>
</body>
</html>
"""

# ── Hilfsfunktionen ────────────────────────────────────────────────────────

def _date_sort(datum: str) -> str:
    """DD.MM.YYYY → YYYYMMDD (für SQLite ORDER BY)."""
    p = datum.split('.')
    return p[2] + p[1] + p[0] if len(p) == 3 else datum


def _read_entries():
    conn = get_db()
    rows = conn.execute(
        "SELECT datum, stimmung, energie, koerper, notiz, ort FROM eintraege "
        "ORDER BY substr(datum,7,4)||substr(datum,4,2)||substr(datum,1,2)"
    ).fetchall()
    conn.close()
    return [
        {
            "datum":    r["datum"],
            "stimmung": r["stimmung"],
            "energie":  r["energie"],
            "koerper":  r["koerper"],
            "avg":      round((r["stimmung"] + r["energie"] + r["koerper"]) / 3, 1),
            "notiz":    r["notiz"] or "",
            "ort":      r["ort"] or "",
        }
        for r in rows
    ]


# ── Routen: Tagebuch ───────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/entries")
def get_entries():
    try:
        return jsonify({"entries": _read_entries()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/add", methods=["POST"])
def add_entry():
    try:
        data  = request.get_json()
        st    = int(data["stimmung"])
        en    = int(data["energie"])
        ko    = int(data["koerper"])
        notiz = data.get("notiz", "")
        ort   = data.get("ort", "")
        datum_raw = data.get("datum", "")
        try:
            entry_date = datetime.strptime(datum_raw, "%Y-%m-%d").date() if datum_raw else date.today()
        except Exception:
            entry_date = date.today()
        datum = entry_date.strftime("%d.%m.%Y")

        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO eintraege (datum,stimmung,energie,koerper,notiz,ort) "
            "VALUES (?,?,?,?,?,?)",
            (datum, st, en, ko, notiz, ort)
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/entry/<datum>", methods=["PUT"])
def update_entry(datum):
    try:
        data = request.get_json()
        conn = get_db()
        res = conn.execute(
            "UPDATE eintraege SET stimmung=?,energie=?,koerper=?,notiz=?,ort=? WHERE datum=?",
            (int(data["stimmung"]), int(data["energie"]), int(data["koerper"]),
             data.get("notiz", ""), data.get("ort", ""), datum)
        )
        conn.commit()
        changed = res.rowcount
        conn.close()
        if not changed:
            return jsonify({"ok": False, "error": "Eintrag nicht gefunden"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/entry/<datum>", methods=["DELETE"])
def delete_entry(datum):
    try:
        conn = get_db()
        res = conn.execute("DELETE FROM eintraege WHERE datum=?", (datum,))
        conn.commit()
        changed = res.rowcount
        conn.close()
        if not changed:
            return jsonify({"ok": False, "error": "Eintrag nicht gefunden"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Routen: Einstellungen ──────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        data = request.get_json()
        conn = get_db()
        for k, v in data.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                (k, json.dumps(v))
            )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return jsonify({r["key"]: json.loads(r["value"]) for r in rows})


# ── Routen: Essen ──────────────────────────────────────────────────────────

@app.route("/food-analyze", methods=["POST"])
def analyze_food():
    try:
        if not _anthropic_client or not os.environ.get("ANTHROPIC_API_KEY"):
            return jsonify({"ok": False, "error": "Kein API Key konfiguriert"})
        data = request.get_json()
        foto_data         = data.get("foto", "")
        beschreibung_text = data.get("beschreibung", "")
        JSON_FORMAT = '{"beschreibung": "...", "kcal": 650, "kohlenhydrate": 80, "fett": 25, "protein": 35}'

        if foto_data and foto_data.startswith("data:image"):
            media_type = foto_data.split(";")[0].split(":")[1]
            img_b64    = foto_data.split(",")[1]
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": (
                    "Beschreibe kurz auf Deutsch was auf dem Foto zu essen ist (max. 1-2 Sätze). "
                    "Schätze Kalorien, Kohlenhydrate, Fett und Protein so genau wie möglich. "
                    f"Antworte NUR in diesem JSON-Format: {JSON_FORMAT}"
                )}
            ]
        elif beschreibung_text:
            content = [{"type": "text", "text": (
                f"Schätze für diese Mahlzeit: {beschreibung_text}\n"
                "Schätze Kalorien, Kohlenhydrate, Fett und Protein so genau wie möglich. "
                f"Antworte NUR in diesem JSON-Format: {JSON_FORMAT}"
            )}]
        else:
            return jsonify({"ok": False, "error": "Kein Bild und keine Beschreibung"})

        msg = _anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": content}]
        )
        raw = msg.content[0].text.strip()
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            result = json.loads(m.group())
            return jsonify({
                "ok":            True,
                "beschreibung":  result.get("beschreibung", ""),
                "kcal":          result.get("kcal"),
                "kohlenhydrate": result.get("kohlenhydrate"),
                "fett":          result.get("fett"),
                "protein":       result.get("protein"),
            })
        return jsonify({"ok": False, "error": "Konnte Antwort nicht parsen"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/food", methods=["POST"])
def add_food():
    try:
        data     = request.get_json()
        entry_id = str(uuid.uuid4())
        datum    = data.get("datum", date.today().strftime("%Y-%m-%d"))
        try:
            datum = datetime.strptime(datum, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            pass

        conn = get_db()
        conn.execute(
            "INSERT INTO food_log "
            "(id,datum,uhrzeit,beschreibung,kcal,kohlenhydrate,fett,protein,foto) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                entry_id,
                datum,
                data.get("uhrzeit", datetime.now().strftime("%H:%M")),
                data.get("beschreibung", ""),
                data.get("kcal"),
                data.get("kohlenhydrate"),
                data.get("fett"),
                data.get("protein"),
                data.get("foto"),   # base64 data URL oder None
            )
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "id": entry_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/food-entries")
def get_food():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT id, datum, uhrzeit, beschreibung, kcal, kohlenhydrate, fett, protein, "
            "  CASE WHEN foto IS NOT NULL THEN id ELSE NULL END AS foto "
            "FROM food_log "
            "ORDER BY substr(datum,7,4)||substr(datum,4,2)||substr(datum,1,2), uhrzeit"
        ).fetchall()
        conn.close()
        return jsonify({"entries": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/food-entry/<entry_id>", methods=["PUT"])
def update_food(entry_id):
    try:
        data = request.get_json()
        datum_raw = data.get("datum", "")
        try:
            entry_date = datetime.strptime(datum_raw, "%Y-%m-%d").date() if datum_raw else date.today()
        except Exception:
            entry_date = date.today()
        datum = entry_date.strftime("%d.%m.%Y")
        conn = get_db()
        if "foto" in data:
            conn.execute(
                "UPDATE food_log SET datum=?,uhrzeit=?,beschreibung=?,kcal=?,kohlenhydrate=?,fett=?,protein=?,foto=? WHERE id=?",
                (datum, data.get("uhrzeit",""), data.get("beschreibung",""),
                 data.get("kcal"), data.get("kohlenhydrate"), data.get("fett"), data.get("protein"),
                 data.get("foto"), entry_id)
            )
        else:
            conn.execute(
                "UPDATE food_log SET datum=?,uhrzeit=?,beschreibung=?,kcal=?,kohlenhydrate=?,fett=?,protein=? WHERE id=?",
                (datum, data.get("uhrzeit",""), data.get("beschreibung",""),
                 data.get("kcal"), data.get("kohlenhydrate"), data.get("fett"), data.get("protein"),
                 entry_id)
            )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/food-entry/<entry_id>", methods=["DELETE"])
def delete_food(entry_id):
    try:
        conn = get_db()
        res  = conn.execute("DELETE FROM food_log WHERE id=?", (entry_id,))
        conn.commit()
        conn.close()
        if not res.rowcount:
            return jsonify({"ok": False, "error": "Nicht gefunden"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/food-photo/<entry_id>")
def serve_food_photo(entry_id):
    """Foto aus der Datenbank lesen und als Bild zurückgeben."""
    try:
        conn = get_db()
        row  = conn.execute("SELECT foto FROM food_log WHERE id=?", (entry_id,)).fetchone()
        conn.close()
        if not row or not row["foto"]:
            return "", 404
        foto_url   = row["foto"]                         # z.B. "data:image/jpeg;base64,..."
        media_type = foto_url.split(";")[0].split(":")[1]
        img_bytes  = base64.b64decode(foto_url.split(",")[1])
        return Response(img_bytes, mimetype=media_type)
    except Exception:
        return "", 404


# ── Admin: Daten-Import (für einmalige Migration von Mac → Cloud) ──────────
# Sende POST /admin/import mit Header X-Admin-Key: DEIN_ADMIN_KEY
# Body: {"eintraege": [...], "food_log": [...], "settings": {...}}

@app.route("/gewicht")
def get_gewicht():
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT datum, wert FROM gewicht "
            "ORDER BY substr(datum,7,4)||substr(datum,4,2)||substr(datum,1,2)"
        ).fetchall()
        conn.close()
        return jsonify({"entries": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/gewicht", methods=["POST"])
def save_gewicht():
    try:
        data = request.get_json()
        datum_raw = data.get("datum", "")
        try:
            entry_date = datetime.strptime(datum_raw, "%Y-%m-%d").date() if datum_raw else date.today()
        except Exception:
            entry_date = date.today()
        datum = entry_date.strftime("%d.%m.%Y")
        wert = float(data.get("wert", 0))
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO gewicht (datum, wert) VALUES (?,?)", (datum, wert))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/import", methods=["POST"])
def admin_import():
    admin_key = os.environ.get("ADMIN_KEY", "")
    if not admin_key or request.headers.get("X-Admin-Key") != admin_key:
        return jsonify({"error": "Nicht autorisiert"}), 401

    data = request.get_json()
    conn = get_db()

    imported_e = 0
    for e in data.get("eintraege", []):
        conn.execute(
            "INSERT OR IGNORE INTO eintraege (datum,stimmung,energie,koerper,notiz,ort) VALUES (?,?,?,?,?,?)",
            (e["datum"], e["stimmung"], e["energie"], e["koerper"], e.get("notiz",""), e.get("ort",""))
        )
        imported_e += 1

    imported_f = 0
    for f in data.get("food_log", []):
        conn.execute(
            "INSERT OR IGNORE INTO food_log (id,datum,uhrzeit,beschreibung,kcal,kohlenhydrate,fett,protein,foto) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (f["id"], f["datum"], f["uhrzeit"], f.get("beschreibung",""),
             f.get("kcal"), f.get("kohlenhydrate"), f.get("fett"), f.get("protein"), f.get("foto"))
        )
        imported_f += 1

    for k, v in data.get("settings", {}).items():
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, json.dumps(v)))

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "eintraege": imported_e, "food_log": imported_f})


# ── Start ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5757))
    print(f"🌿  Tagebuch läuft auf http://localhost:{port}")
    print(f"     Datenbank: {DB_PATH}")
    app.run(host="0.0.0.0", port=port, debug=False)
