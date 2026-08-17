
import io
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import folium
import pandas as pd
import pdfplumber
import streamlit as st
from streamlit_folium import st_folium

# Optional: Supabase for persistent browser hosting
try:
    from supabase import create_client
except Exception:
    create_client = None

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "polter.db"

st.set_page_config(
    page_title="Polter-Zentrale",
    page_icon="🪵",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------
# Storage backend
# --------------------------
def get_supabase():
    url = st.secrets.get("SUPABASE_URL", "") if hasattr(st, "secrets") else ""
    key = st.secrets.get("SUPABASE_KEY", "") if hasattr(st, "secrets") else ""
    if url and key and create_client:
        return create_client(url, key)
    return None

SUPABASE = get_supabase()

def local_con():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS polter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quelle_datei TEXT,
            bereitstellung TEXT,
            lieferant TEXT,
            vertragsnummer TEXT,
            datum TEXT,
            holzliste TEXT,
            hab TEXT,
            los TEXT,
            polter_nr TEXT,
            holzart TEXT,
            sortiment TEXT,
            laenge_m REAL,
            stueck REAL,
            menge_rm_original REAL,
            menge_rm_aktuell REAL,
            kubatur_fm_original REAL,
            kubatur_fm_aktuell REAL,
            einheit TEXT,
            lat REAL,
            lon REAL,
            waldort TEXT,
            lagerort TEXT,
            bemerkung TEXT,
            ansprechpartner TEXT,
            zertifikat TEXT,
            status TEXT DEFAULT 'Offen',
            interne_notiz TEXT DEFAULT '',
            importiert_am TEXT,
            geaendert_am TEXT,
            UNIQUE(quelle_datei, bereitstellung, holzliste, hab, los, polter_nr, lat, lon)
        )
    """)
    con.commit()
    return con

CON = local_con() if SUPABASE is None else None

COLS = [
    "quelle_datei","bereitstellung","lieferant","vertragsnummer","datum","holzliste",
    "hab","los","polter_nr","holzart","sortiment","laenge_m","stueck",
    "menge_rm_original","menge_rm_aktuell","kubatur_fm_original","kubatur_fm_aktuell",
    "einheit","lat","lon","waldort","lagerort","bemerkung","ansprechpartner","zertifikat",
    "status","interne_notiz","importiert_am","geaendert_am"
]

def load_df():
    if SUPABASE:
        data = SUPABASE.table("polter").select("*").order("id", desc=True).execute().data
        return pd.DataFrame(data) if data else pd.DataFrame(columns=["id"] + COLS)
    return pd.read_sql_query("SELECT * FROM polter ORDER BY id DESC", CON)

def duplicate_exists(r):
    df = load_df()
    if df.empty:
        return False
    checks = (
        (df["quelle_datei"].fillna("") == (r.get("quelle_datei") or "")) &
        (df["bereitstellung"].fillna("") == (r.get("bereitstellung") or "")) &
        (df["holzliste"].fillna("") == (r.get("holzliste") or "")) &
        (df["hab"].fillna("") == (r.get("hab") or "")) &
        (df["los"].fillna("") == (r.get("los") or "")) &
        (df["polter_nr"].fillna("") == (r.get("polter_nr") or ""))
    )
    if r.get("lat") is not None:
        checks &= (df["lat"].fillna(-999) == r.get("lat"))
    if r.get("lon") is not None:
        checks &= (df["lon"].fillna(-999) == r.get("lon"))
    return bool(checks.any())

def insert_rows(rows):
    inserted = 0
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        if duplicate_exists(r):
            continue
        r = dict(r)
        r["status"] = r.get("status") or "Offen"
        r["interne_notiz"] = r.get("interne_notiz") or ""
        r["importiert_am"] = now
        r["geaendert_am"] = now
        if SUPABASE:
            SUPABASE.table("polter").insert(r).execute()
        else:
            fields = [c for c in COLS]
            vals = [r.get(c) for c in fields]
            CON.execute(
                f"INSERT INTO polter ({','.join(fields)}) VALUES ({','.join(['?']*len(fields))})",
                vals
            )
            CON.commit()
        inserted += 1
    return inserted

def update_row(pid, rm, fm, status, note):
    now = datetime.now().isoformat(timespec="seconds")
    values = dict(
        menge_rm_aktuell=rm,
        kubatur_fm_aktuell=fm,
        status=status,
        interne_notiz=note,
        geaendert_am=now
    )
    if SUPABASE:
        SUPABASE.table("polter").update(values).eq("id", int(pid)).execute()
    else:
        CON.execute(
            """UPDATE polter
               SET menge_rm_aktuell=?, kubatur_fm_aktuell=?, status=?,
                   interne_notiz=?, geaendert_am=?
               WHERE id=?""",
            (rm, fm, status, note, now, int(pid))
        )
        CON.commit()

def delete_all():
    if SUPABASE:
        SUPABASE.table("polter").delete().neq("id", 0).execute()
    else:
        CON.execute("DELETE FROM polter")
        CON.commit()

# --------------------------
# PDF parsing
# --------------------------
def fde(v):
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)

def extract_text(pdf_bytes):
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text(x_tolerance=2, y_tolerance=2) or "")
    return "\n".join(pages)

def parse_wbv(text, filename):
    rows = []
    bereit = re.search(r"Bereitstellung\s+Nr\.\s*([A-Z0-9\-]+)", text, re.I)
    if not bereit:
        return rows
    bereitstellung = bereit.group(1)
    datum_m = re.search(r"(?:Lief/Leist-Dat|Belegdatum)\s+(\d{2}\.\d{2}\.\d{4})", text)
    datum = datum_m.group(1) if datum_m else ""
    vertr = re.search(r"Vertr\.-Nr\.\s*(.+)", text)
    vertragsnummer = vertr.group(1).strip() if vertr else ""

    pattern = re.compile(
        r"(?m)^(\d{6,9}/\d+/\d+)\s+([A-Za-zÄÖÜäöü]+)\s+([A-Za-z0-9]+)\s+"
        r"([\d,]+)\s*m\s+([\d,]+)\s*RM\s+([\d,]+)\s*FM"
    )
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        seg = text[m.start():end]
        coord = re.search(r"(\d{1,2}\.\d{4,8})°?\s*N,\s*(\d{1,3}\.\d{4,8})°?\s*E", seg, re.I)

        def line(label):
            mm = re.search(rf"{label}:\s*(.+)", seg, re.I)
            return mm.group(1).strip() if mm else ""

        parts = m.group(1).split("/")
        rm = fde(m.group(5))
        fm = fde(m.group(6))
        rows.append({
            "quelle_datei": filename,
            "bereitstellung": bereitstellung,
            "lieferant": "WBV Holzhandels GmbH",
            "vertragsnummer": vertragsnummer,
            "datum": datum,
            "holzliste": m.group(1),
            "hab": "",
            "los": parts[1],
            "polter_nr": parts[2],
            "holzart": m.group(2),
            "sortiment": m.group(3),
            "laenge_m": fde(m.group(4)),
            "stueck": None,
            "menge_rm_original": rm,
            "menge_rm_aktuell": rm,
            "kubatur_fm_original": fm,
            "kubatur_fm_aktuell": fm,
            "einheit": "RM / FM",
            "lat": float(coord.group(1)) if coord else None,
            "lon": float(coord.group(2)) if coord else None,
            "waldort": line("Waldort"),
            "lagerort": line("Lagerort"),
            "bemerkung": line("Bemerkung"),
            "ansprechpartner": line("Ansprechpartner"),
            "zertifikat": line("Zertifikat"),
        })
    return rows

def parse_muenchen(text, filename):
    rows = []
    bereit = re.search(r"Bereitstellungsanzeige\s+Nr\.\s*([A-Z0-9\-]+)", text, re.I)
    if not bereit:
        return rows
    bereitstellung = bereit.group(1)
    datum_m = re.search(r"Bereitstellungsdatum:\s*(\d{2}\.\d{2}\.\d{4})", text, re.I)
    datum = datum_m.group(1) if datum_m else ""
    vertr = re.search(r"Vertragsnummer:\s*([A-Z0-9_\-]+)", text, re.I)
    vertragsnummer = vertr.group(1) if vertr else ""

    row_re = re.compile(
        r"(?m)^(\d+)\s+(\d+)\s+(\d+)\s+([A-Za-zÄÖÜäöü]+)\s+([A-Za-z0-9]+)\s+([A-Za-z0-9]+)\s+"
        r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([A-Za-z]+)\s+([\d,]+)\s+"
        r"(\d{1,2},\d{4,8})\s+(\d{1,3},\d{4,8})\s+(.+)$"
    )
    seen = set()
    for m in row_re.finditer(text):
        key = (m.group(1),m.group(2),m.group(3),m.group(12),m.group(13))
        if key in seen:
            continue
        seen.add(key)

        tail = m.group(14).strip()
        lagerort, info = tail, ""
        marker = "Sackgasse ohne Wende"
        if marker.lower() in tail.lower():
            pos = tail.lower().find(marker.lower())
            lagerort = tail[:pos].strip()
            info = tail[pos:].strip()

        rm = fde(m.group(9))
        fm = fde(m.group(11))
        rows.append({
            "quelle_datei": filename,
            "bereitstellung": bereitstellung,
            "lieferant": "Landeshauptstadt München - Gemeindewald",
            "vertragsnummer": vertragsnummer,
            "datum": datum,
            "holzliste": "",
            "hab": m.group(1),
            "los": m.group(2),
            "polter_nr": m.group(3),
            "holzart": m.group(4),
            "sortiment": f"{m.group(5)} {m.group(6)}",
            "laenge_m": fde(m.group(7)),
            "stueck": fde(m.group(8)),
            "menge_rm_original": rm,
            "menge_rm_aktuell": rm,
            "kubatur_fm_original": fm,
            "kubatur_fm_aktuell": fm,
            "einheit": m.group(10),
            "lat": float(m.group(12).replace(",", ".")),
            "lon": float(m.group(13).replace(",", ".")),
            "waldort": "",
            "lagerort": lagerort,
            "bemerkung": info,
            "ansprechpartner": "",
            "zertifikat": "",
        })
    return rows

def parse_pdf(pdf_bytes, filename):
    text = extract_text(pdf_bytes)
    candidates = []
    for parser in (parse_wbv, parse_muenchen):
        try:
            r = parser(text, filename)
            if r:
                candidates.append(r)
        except Exception:
            pass
    return max(candidates, key=len) if candidates else []

# --------------------------
# UI
# --------------------------
st.title("🪵 Polter-Zentrale")
st.caption("Browserbasierte Version – PDFs einlesen, Polter auf Karte verwalten und Mengen manuell anpassen.")

if SUPABASE:
    st.success("☁️ Cloud-Datenbank verbunden – Änderungen werden dauerhaft gespeichert.")
else:
    st.warning("🧪 Testmodus ohne Cloud-Datenbank – für dauerhafte Browser-Nutzung bitte Supabase verbinden.")

with st.container(border=True):
    st.subheader("PDFs importieren")
    uploads = st.file_uploader(
        "Bereitstellungs-PDFs hier per Drag & Drop hineinziehen",
        type=["pdf"],
        accept_multiple_files=True
    )
    if uploads and st.button("PDFs einlesen", type="primary"):
        total, failed = 0, []
        for f in uploads:
            rows = parse_pdf(f.getvalue(), f.name)
            if rows:
                total += insert_rows(rows)
            else:
                failed.append(f.name)
        if total:
            st.success(f"{total} neue Polter importiert.")
        else:
            st.info("Keine neuen Polter importiert – eventuell waren sie bereits vorhanden.")
        if failed:
            st.warning("Nicht erkannt: " + ", ".join(failed))
        st.rerun()

df = load_df()
if df.empty:
    st.info("Noch keine Polter vorhanden. Lade oben deine erste PDF hoch.")
    st.stop()

status_opts = ["Offen", "Eingeplant", "In Abfuhr", "Erledigt"]

# Sidebar
st.sidebar.header("Filter")
lieferanten = sorted(x for x in df["lieferant"].dropna().unique() if x)
holzarten = sorted(x for x in df["holzart"].dropna().unique() if x)

sel_lief = st.sidebar.multiselect("Lieferant", lieferanten, default=lieferanten)
sel_holz = st.sidebar.multiselect("Holzart", holzarten, default=holzarten)
sel_status = st.sidebar.multiselect("Status", status_opts, default=status_opts)
search = st.sidebar.text_input("Suche", placeholder="Polter, Lagerort, Bereitstellung …")

view = df[
    df["lieferant"].isin(sel_lief) &
    df["holzart"].isin(sel_holz) &
    df["status"].isin(sel_status)
].copy()

if search.strip():
    q = search.lower()
    mask = pd.Series(False, index=view.index)
    for c in ["bereitstellung","holzliste","hab","los","polter_nr","lagerort","waldort","bemerkung","ansprechpartner"]:
        mask |= view[c].fillna("").astype(str).str.lower().str.contains(q, regex=False)
    view = view[mask]

m1,m2,m3,m4 = st.columns(4)
m1.metric("Polter", len(view))
m2.metric("Aktuelle RM", f"{view['menge_rm_aktuell'].fillna(0).sum():,.1f}")
m3.metric("Aktuelle FM", f"{view['kubatur_fm_aktuell'].fillna(0).sum():,.1f}")
m4.metric("Offen", int((view["status"] == "Offen").sum()))

left, right = st.columns([1.45, 1])

with left:
    st.subheader("Karte")
    pts = view.dropna(subset=["lat","lon"])
    if pts.empty:
        st.info("Keine GPS-Koordinaten für diese Auswahl.")
    else:
        center = [pts["lat"].mean(), pts["lon"].mean()]
        m = folium.Map(location=center, zoom_start=10, tiles="OpenStreetMap")
        for _, r in pts.iterrows():
            key = r["holzliste"] if r["holzliste"] else f"{r['hab']}/{r['los']}/{r['polter_nr']}"
            popup = f"""
            <b>{key}</b><br>
            Bereitstellung: {r['bereitstellung']}<br>
            Lagerort: {r['lagerort'] or '-'}<br>
            Holzart: {r['holzart']} {r['sortiment']}<br>
            Menge aktuell: {r['menge_rm_aktuell'] if pd.notna(r['menge_rm_aktuell']) else '-'} RM<br>
            Original: {r['menge_rm_original'] if pd.notna(r['menge_rm_original']) else '-'} RM<br>
            Status: {r['status']}<br>
            Bemerkung: {r['bemerkung'] or '-'}
            """
            folium.Marker(
                [r["lat"], r["lon"]],
                tooltip=f"{key} · {r['lagerort'] or ''} · {r['menge_rm_aktuell'] or 0} RM",
                popup=folium.Popup(popup, max_width=380)
            ).add_to(m)
        st_folium(m, use_container_width=True, height=620, returned_objects=[])

with right:
    st.subheader("Polter bearbeiten")
    if not view.empty:
        options = {}
        for _, r in view.iterrows():
            key = r["holzliste"] if r["holzliste"] else f"{r['hab']}/{r['los']}/{r['polter_nr']}"
            label = f"{r['bereitstellung']} · {key} · {r['lagerort'] or 'ohne Lagerort'}"
            options[label] = int(r["id"])

        chosen = st.selectbox("Polter auswählen", list(options.keys()))
        pid = options[chosen]
        row = df[df["id"] == pid].iloc[0]

        with st.form("edit_polter"):
            st.caption("Originalwerte aus dem PDF bleiben erhalten.")
            rm = st.number_input(
                "Menge aktuell (RM)",
                min_value=0.0,
                value=float(row["menge_rm_aktuell"] or 0.0),
                step=0.1,
                format="%.3f"
            )
            fm = st.number_input(
                "FM / Kubatur aktuell",
                min_value=0.0,
                value=float(row["kubatur_fm_aktuell"] or 0.0),
                step=0.1,
                format="%.3f"
            )
            status = st.selectbox(
                "Status",
                status_opts,
                index=status_opts.index(row["status"]) if row["status"] in status_opts else 0
            )
            note = st.text_area("Interne Notiz", value=row.get("interne_notiz", "") or "", height=90)

            if st.form_submit_button("Änderungen speichern", type="primary"):
                update_row(pid, rm, fm, status, note)
                st.success("Änderungen gespeichert.")
                st.rerun()

        a,b = st.columns(2)
        a.metric("Original RM", f"{float(row['menge_rm_original'] or 0):,.3f}")
        b.metric("Aktuell RM", f"{float(row['menge_rm_aktuell'] or 0):,.3f}")

        if pd.notna(row["lat"]) and pd.notna(row["lon"]):
            st.link_button("📍 In Google Maps öffnen", f"https://www.google.com/maps?q={row['lat']},{row['lon']}")

        with st.expander("Alle Details"):
            st.write({
                "Bereitstellung": row["bereitstellung"],
                "Holzliste / Polter": row["holzliste"] or f"{row['hab']}/{row['los']}/{row['polter_nr']}",
                "Holzart": row["holzart"],
                "Sortiment": row["sortiment"],
                "Länge": row["laenge_m"],
                "Waldort": row["waldort"],
                "Lagerort": row["lagerort"],
                "Bemerkung": row["bemerkung"],
                "Ansprechpartner": row["ansprechpartner"],
                "Koordinaten": f"{row['lat']}, {row['lon']}",
                "Quelldatei": row["quelle_datei"],
            })

st.subheader("Polterliste")
table = view[[
    "id","bereitstellung","holzliste","hab","los","polter_nr","holzart","sortiment",
    "laenge_m","menge_rm_original","menge_rm_aktuell",
    "kubatur_fm_original","kubatur_fm_aktuell","status",
    "waldort","lagerort","bemerkung","ansprechpartner","lat","lon","quelle_datei"
]].copy()

table.columns = [
    "ID","Bereitstellung","Holzliste","HAB","Los","Polter","Holzart","Sortiment",
    "Länge m","RM Original","RM aktuell","FM Original","FM aktuell","Status",
    "Waldort","Lagerort","Bemerkung","Ansprechpartner","Breite","Länge","Quelldatei"
]
st.dataframe(table, use_container_width=True, hide_index=True, height=420)

st.download_button(
    "⬇️ CSV exportieren",
    table.to_csv(index=False).encode("utf-8-sig"),
    "polter_export.csv",
    "text/csv"
)

with st.expander("Verwaltung"):
    if st.button("Alle Polter löschen"):
        delete_all()
        st.rerun()
