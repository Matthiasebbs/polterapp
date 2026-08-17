
import sqlite3
from datetime import datetime
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from parsers import parse_pdf_bytes

try:
    from supabase import create_client
except Exception:
    create_client = None

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "polter.db"

st.set_page_config(page_title="Polter-Zentrale", page_icon="🪵", layout="wide")

FIELDS = [
    "quelle_datei","bereitstellung","lieferant","vertragsnummer","datum","holzliste",
    "hab","los","polter_nr","holzart","sortiment","laenge_m","stueck",
    "menge_rm_original","menge_rm_aktuell","kubatur_fm_original","kubatur_fm_aktuell",
    "einheit","lat","lon","waldort","lagerort","bemerkung","ansprechpartner",
    "zertifikat","status","interne_notiz","map_link","importiert_am","geaendert_am"
]

def secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""

def cloud():
    if create_client and secret("SUPABASE_URL") and secret("SUPABASE_KEY"):
        return create_client(secret("SUPABASE_URL"), secret("SUPABASE_KEY"))
    return None

SB = cloud()

def local_db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("""
    CREATE TABLE IF NOT EXISTS polter (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quelle_datei TEXT, bereitstellung TEXT, lieferant TEXT, vertragsnummer TEXT,
        datum TEXT, holzliste TEXT, hab TEXT, los TEXT, polter_nr TEXT,
        holzart TEXT, sortiment TEXT, laenge_m REAL, stueck REAL,
        menge_rm_original REAL, menge_rm_aktuell REAL,
        kubatur_fm_original REAL, kubatur_fm_aktuell REAL,
        einheit TEXT, lat REAL, lon REAL, waldort TEXT, lagerort TEXT,
        bemerkung TEXT, ansprechpartner TEXT, zertifikat TEXT,
        status TEXT DEFAULT 'Offen', interne_notiz TEXT DEFAULT '',
        map_link TEXT DEFAULT '', importiert_am TEXT, geaendert_am TEXT
    )
    """)
    con.commit()
    return con

CON = local_db() if SB is None else None

def df_all():
    if SB:
        data = SB.table("polter").select("*").order("id", desc=True).execute().data
        df = pd.DataFrame(data) if data else pd.DataFrame(columns=["id"]+FIELDS)
    else:
        df = pd.read_sql_query("SELECT * FROM polter ORDER BY id DESC", CON)
    for c in ["lieferant","holzart","status","map_link","interne_notiz"]:
        if c not in df:
            df[c] = ""
    return df

def duplicate(row):
    df = df_all()
    if df.empty:
        return False
    keycols = ["bereitstellung","holzliste","hab","los","polter_nr","quelle_datei"]
    mask = pd.Series(True, index=df.index)
    for c in keycols:
        mask &= df[c].fillna("").astype(str).eq(str(row.get(c) or ""))
    return bool(mask.any())

def save_rows(rows):
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for row in rows:
        if duplicate(row):
            continue
        r = dict(row)
        r["status"] = "Offen"
        r["interne_notiz"] = ""
        r["importiert_am"] = now
        r["geaendert_am"] = now
        if SB:
            SB.table("polter").insert(r).execute()
        else:
            vals = [r.get(c) for c in FIELDS]
            CON.execute(
                f"INSERT INTO polter ({','.join(FIELDS)}) VALUES ({','.join(['?']*len(FIELDS))})",
                vals
            )
            CON.commit()
        n += 1
    return n

def update_polter(pid, rm, fm, status, note, lat, lon):
    values = {
        "menge_rm_aktuell": rm, "kubatur_fm_aktuell": fm, "status": status,
        "interne_notiz": note, "lat": lat, "lon": lon,
        "geaendert_am": datetime.now().isoformat(timespec="seconds")
    }
    if SB:
        SB.table("polter").update(values).eq("id", int(pid)).execute()
    else:
        CON.execute("""
        UPDATE polter SET menge_rm_aktuell=?, kubatur_fm_aktuell=?, status=?,
        interne_notiz=?, lat=?, lon=?, geaendert_am=? WHERE id=?
        """, (rm,fm,status,note,lat,lon,values["geaendert_am"],int(pid)))
        CON.commit()

def delete_group(name):
    if SB:
        SB.table("polter").delete().eq("bereitstellung", str(name)).execute()
    else:
        CON.execute("DELETE FROM polter WHERE bereitstellung=?", (str(name),))
        CON.commit()

def delete_one(pid):
    if SB:
        SB.table("polter").delete().eq("id", int(pid)).execute()
    else:
        CON.execute("DELETE FROM polter WHERE id=?", (int(pid),))
        CON.commit()

st.title("🪵 Polter-Zentrale")
st.caption("Alle zugesandten Bereitstellungsformate · Lieferantenfilter · Mengenänderung · Löschen nach Abfuhr")

if SB:
    st.success("☁️ Supabase verbunden – Daten bleiben dauerhaft gespeichert.")
else:
    st.warning("🧪 Lokaler Testmodus. Für die veröffentlichte Web-App Supabase verbinden.")

with st.container(border=True):
    st.subheader("1. PDFs importieren")
    uploads = st.file_uploader("Bereitstellungen hier hineinziehen", type=["pdf"], accept_multiple_files=True)
    if uploads and st.button("PDFs einlesen", type="primary"):
        total = 0
        results = []
        for f in uploads:
            rows, fmt, attempts = parse_pdf_bytes(f.getvalue(), f.name)
            if rows:
                added = save_rows(rows)
                total += added
                results.append((f.name, fmt, len(rows), added, "OK"))
            else:
                results.append((f.name, "nicht erkannt", 0, 0, "FEHLER"))
        if total:
            st.success(f"{total} neue Polter gespeichert.")
        for filename, fmt, found, added, state in results:
            if state == "OK":
                st.info(f"✅ {filename}: {fmt} erkannt – {found} Polter gelesen, {added} neu gespeichert.")
            else:
                st.error(f"❌ {filename}: Format noch nicht erkannt.")
        st.rerun()

df = df_all()
if df.empty:
    st.info("Noch keine Polter gespeichert.")
    st.stop()

# ---------- Lieferantenfilter ----------
st.sidebar.header("Ansicht filtern")
supplier_values = sorted([x for x in df["lieferant"].fillna("").unique().tolist() if x])
supplier_choice = st.sidebar.multiselect(
    "Lieferant",
    supplier_values,
    default=supplier_values,
    help="Hier kannst du einen oder mehrere Lieferanten auswählen."
)
status_values = ["Offen","Eingeplant","In Abfuhr","Erledigt"]
status_choice = st.sidebar.multiselect("Status", status_values, default=status_values)

wood_values = sorted([x for x in df["holzart"].fillna("").unique().tolist() if x])
wood_choice = st.sidebar.multiselect("Holzart (optional)", wood_values, default=[])
search = st.sidebar.text_input("Suche", placeholder="Bereitstellung, Polter, Lagerort …")

view = df.copy()
if supplier_choice:
    view = view[view["lieferant"].isin(supplier_choice)]
else:
    view = view.iloc[0:0]
if status_choice:
    view = view[view["status"].isin(status_choice)]
else:
    view = view.iloc[0:0]
if wood_choice:
    view = view[view["holzart"].isin(wood_choice)]
if search.strip():
    q = search.lower()
    mask = pd.Series(False, index=view.index)
    for c in ["bereitstellung","lieferant","holzliste","hab","los","polter_nr","lagerort","waldort","bemerkung"]:
        mask |= view[c].fillna("").astype(str).str.lower().str.contains(q, regex=False)
    view = view[mask]

m1,m2,m3,m4 = st.columns(4)
m1.metric("Polter", len(view))
m2.metric("RM aktuell", f"{view['menge_rm_aktuell'].fillna(0).sum():,.1f}")
m3.metric("FM / EFm aktuell", f"{view['kubatur_fm_aktuell'].fillna(0).sum():,.1f}")
m4.metric("Lieferanten", view["lieferant"].nunique())

left,right = st.columns([1.45,1])

with left:
    st.subheader("2. Karte")
    pts = view.dropna(subset=["lat","lon"])
    missing = len(view) - len(pts)
    if missing:
        st.caption(f"{missing} Polter haben im PDF keine numerischen GPS-Koordinaten und erscheinen deshalb nur in der Liste.")
    if pts.empty:
        st.info("Für die aktuelle Auswahl sind keine numerischen GPS-Koordinaten vorhanden.")
    else:
        mp = folium.Map(location=[pts["lat"].mean(),pts["lon"].mean()], zoom_start=9, tiles="OpenStreetMap")
        for _,r in pts.iterrows():
            label = r["holzliste"] or f"{r['hab']}/{r['los']}/{r['polter_nr']}"
            pop = f"""
            <b>{r['lieferant']}</b><br>
            Bereitstellung: {r['bereitstellung']}<br>
            Polter: {label}<br>
            Lagerort: {r['lagerort'] or '-'}<br>
            Holzart: {r['holzart'] or '-'} {r['sortiment'] or ''}<br>
            RM aktuell: {r['menge_rm_aktuell'] if pd.notna(r['menge_rm_aktuell']) else '-'}<br>
            FM/EFm aktuell: {r['kubatur_fm_aktuell'] if pd.notna(r['kubatur_fm_aktuell']) else '-'}<br>
            Status: {r['status']}
            """
            folium.Marker([float(r["lat"]),float(r["lon"])],
                          tooltip=f"{r['lieferant']} · {r['bereitstellung']} · Polter {r['polter_nr']}",
                          popup=folium.Popup(pop,max_width=380)).add_to(mp)
        st_folium(mp, use_container_width=True, height=610, returned_objects=[])

with right:
    st.subheader("3. Polter bearbeiten")
    if not view.empty:
        opts = {}
        for _,r in view.iterrows():
            key = r["holzliste"] or f"{r['hab']}/{r['los']}/{r['polter_nr']}"
            opts[f"{r['lieferant']} · {r['bereitstellung']} · {key}"] = int(r["id"])
        selected = st.selectbox("Polter auswählen", list(opts.keys()))
        pid = opts[selected]
        row = df[df["id"]==pid].iloc[0]
        with st.form("edit"):
            rm = st.number_input("Menge aktuell (RM)", min_value=0.0,
                                 value=float(row["menge_rm_aktuell"] or 0), step=0.1, format="%.3f")
            fm = st.number_input("FM / EFm aktuell", min_value=0.0,
                                 value=float(row["kubatur_fm_aktuell"] or 0), step=0.1, format="%.3f")
            status = st.selectbox("Status", status_values,
                                  index=status_values.index(row["status"]) if row["status"] in status_values else 0)
            note = st.text_area("Interne Notiz", value=row["interne_notiz"] or "")
            st.caption("Nur bei PDFs ohne numerische GPS-Koordinaten nötig:")
            lat = st.number_input("Breitengrad", value=float(row["lat"]) if pd.notna(row["lat"]) else 0.0, format="%.7f")
            lon = st.number_input("Längengrad", value=float(row["lon"]) if pd.notna(row["lon"]) else 0.0, format="%.7f")
            if st.form_submit_button("Speichern", type="primary"):
                update_polter(pid, rm, fm, status, note, None if lat==0 else lat, None if lon==0 else lon)
                st.success("Gespeichert.")
                st.rerun()

        a,b = st.columns(2)
        a.metric("Original RM", f"{float(row['menge_rm_original'] or 0):,.3f}")
        b.metric("Aktuell RM", f"{float(row['menge_rm_aktuell'] or 0):,.3f}")

        if pd.notna(row["lat"]) and pd.notna(row["lon"]):
            st.link_button("📍 Google Maps", f"https://www.google.com/maps?q={row['lat']},{row['lon']}")
        if row.get("map_link"):
            st.link_button("🗺️ Original-Kartenlink", row["map_link"])

        with st.expander("Einzelnen Polter löschen"):
            if st.checkbox("Löschen bestätigen", key=f"conf_{pid}"):
                if st.button("Polter endgültig löschen", key=f"del_{pid}"):
                    delete_one(pid)
                    st.rerun()

st.subheader("4. Bereitstellungen")
summary = (df.groupby(["lieferant","bereitstellung"], dropna=False)
           .agg(Polter=("id","count"),
                RM_aktuell=("menge_rm_aktuell","sum"),
                FM_EFm_aktuell=("kubatur_fm_aktuell","sum"))
           .reset_index())
st.dataframe(summary, use_container_width=True, hide_index=True)

with st.expander("Komplette abgefahrene Bereitstellung löschen"):
    keys = []
    for _,r in summary.iterrows():
        keys.append(f"{r['lieferant']} · {r['bereitstellung']}")
    choice = st.selectbox("Bereitstellung", keys)
    idx = keys.index(choice)
    chosen_row = summary.iloc[idx]
    name = str(chosen_row["bereitstellung"])
    st.warning(f"{int(chosen_row['Polter'])} Polter dieser Bereitstellung werden dauerhaft gelöscht.")
    confirm = st.checkbox(f"Ja, {name} ist vollständig abgefahren.")
    if st.button("🗑️ Bereitstellung löschen", disabled=not confirm):
        delete_group(name)
        st.rerun()

st.subheader("5. Alle Polter")
cols = [
    "lieferant","bereitstellung","holzliste","hab","los","polter_nr","holzart","sortiment",
    "laenge_m","menge_rm_original","menge_rm_aktuell","kubatur_fm_original","kubatur_fm_aktuell",
    "status","waldort","lagerort","bemerkung","ansprechpartner","lat","lon","quelle_datei"
]
show = view[cols].copy()
show.columns = [
    "Lieferant","Bereitstellung","Holzliste","HAB","Los","Polter","Holzart","Sortiment",
    "Länge m","RM Original","RM aktuell","FM/EFm Original","FM/EFm aktuell",
    "Status","Waldort","Lagerort","Bemerkung","Ansprechpartner","Breite","Länge","Quelldatei"
]
st.dataframe(show, use_container_width=True, hide_index=True, height=450)
st.download_button("⬇️ CSV exportieren", show.to_csv(index=False).encode("utf-8-sig"),
                   "polter_export.csv", "text/csv")
