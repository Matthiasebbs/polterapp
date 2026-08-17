
import io
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import folium
import pandas as pd
import pdfplumber
import streamlit as st
from streamlit_folium import st_folium

try:
    from supabase import create_client
except Exception:
    create_client = None

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "polter.db"

st.set_page_config(page_title="Polter-Zentrale", page_icon="🪵", layout="wide")

# ==========================================================
# Datenbank / Cloud
# ==========================================================
def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""

def get_supabase():
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_KEY")
    if url and key and create_client:
        return create_client(url, key)
    return None

SUPABASE = get_supabase()

DB_FIELDS = [
    "quelle_datei","bereitstellung","lieferant","vertragsnummer","datum","holzliste",
    "hab","los","polter_nr","holzart","sortiment","laenge_m","stueck",
    "menge_rm_original","menge_rm_aktuell","kubatur_fm_original","kubatur_fm_aktuell",
    "einheit","lat","lon","waldort","lagerort","bemerkung","ansprechpartner","zertifikat",
    "status","interne_notiz","map_link","importiert_am","geaendert_am"
]

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
            map_link TEXT DEFAULT '',
            importiert_am TEXT,
            geaendert_am TEXT
        )
    """)
    # Für ältere lokale Datenbanken:
    cols = {r[1] for r in con.execute("PRAGMA table_info(polter)").fetchall()}
    if "map_link" not in cols:
        con.execute("ALTER TABLE polter ADD COLUMN map_link TEXT DEFAULT ''")
    con.commit()
    return con

CON = local_con() if SUPABASE is None else None

def load_df():
    if SUPABASE:
        data = SUPABASE.table("polter").select("*").order("id", desc=True).execute().data
        df = pd.DataFrame(data) if data else pd.DataFrame(columns=["id"] + DB_FIELDS)
    else:
        df = pd.read_sql_query("SELECT * FROM polter ORDER BY id DESC", CON)
    for c in ["map_link","interne_notiz","status"]:
        if c not in df.columns:
            df[c] = ""
    return df

def duplicate_exists(r):
    df = load_df()
    if df.empty:
        return False
    cols = ["quelle_datei","bereitstellung","holzliste","hab","los","polter_nr"]
    mask = pd.Series(True, index=df.index)
    for c in cols:
        mask &= df[c].fillna("").astype(str).eq(str(r.get(c) or ""))
    # Bei vorhandenen Koordinaten zusätzlich vergleichen.
    if r.get("lat") is not None and "lat" in df:
        mask &= df["lat"].fillna(-999).astype(float).eq(float(r["lat"]))
    if r.get("lon") is not None and "lon" in df:
        mask &= df["lon"].fillna(-999).astype(float).eq(float(r["lon"]))
    return bool(mask.any())

def insert_rows(rows):
    now = datetime.now().isoformat(timespec="seconds")
    inserted = 0
    for r in rows:
        if duplicate_exists(r):
            continue
        r = dict(r)
        r.setdefault("status", "Offen")
        r.setdefault("interne_notiz", "")
        r.setdefault("map_link", "")
        r["importiert_am"] = now
        r["geaendert_am"] = now
        if SUPABASE:
            SUPABASE.table("polter").insert(r).execute()
        else:
            vals = [r.get(c) for c in DB_FIELDS]
            CON.execute(
                f"INSERT INTO polter ({','.join(DB_FIELDS)}) VALUES ({','.join(['?']*len(DB_FIELDS))})",
                vals
            )
            CON.commit()
        inserted += 1
    return inserted

def update_row(pid, rm, fm, status, note, lat, lon, map_link):
    vals = {
        "menge_rm_aktuell": rm,
        "kubatur_fm_aktuell": fm,
        "status": status,
        "interne_notiz": note,
        "lat": lat,
        "lon": lon,
        "map_link": map_link,
        "geaendert_am": datetime.now().isoformat(timespec="seconds")
    }
    if SUPABASE:
        SUPABASE.table("polter").update(vals).eq("id", int(pid)).execute()
    else:
        CON.execute("""
            UPDATE polter
            SET menge_rm_aktuell=?, kubatur_fm_aktuell=?, status=?, interne_notiz=?,
                lat=?, lon=?, map_link=?, geaendert_am=?
            WHERE id=?
        """, (rm, fm, status, note, lat, lon, map_link, vals["geaendert_am"], int(pid)))
        CON.commit()

def delete_bereitstellung(name):
    if SUPABASE:
        SUPABASE.table("polter").delete().eq("bereitstellung", str(name)).execute()
    else:
        CON.execute("DELETE FROM polter WHERE bereitstellung=?", (str(name),))
        CON.commit()

def delete_polter(pid):
    if SUPABASE:
        SUPABASE.table("polter").delete().eq("id", int(pid)).execute()
    else:
        CON.execute("DELETE FROM polter WHERE id=?", (int(pid),))
        CON.commit()

# ==========================================================
# PDF Hilfsfunktionen
# ==========================================================
def fnum(v):
    if v is None or str(v).strip() in ("", "-", "–"):
        return None
    s = str(v).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)

def extract_pages(pdf_bytes):
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text(x_tolerance=2, y_tolerance=2) or "")
    return pages

def base_row(filename):
    return dict(
        quelle_datei=filename, bereitstellung="", lieferant="", vertragsnummer="", datum="",
        holzliste="", hab="", los="", polter_nr="", holzart="", sortiment="", laenge_m=None,
        stueck=None, menge_rm_original=None, menge_rm_aktuell=None,
        kubatur_fm_original=None, kubatur_fm_aktuell=None, einheit="",
        lat=None, lon=None, waldort="", lagerort="", bemerkung="",
        ansprechpartner="", zertifikat="", map_link=""
    )

def set_qty(row, rm=None, fm=None):
    row["menge_rm_original"] = rm
    row["menge_rm_aktuell"] = rm
    row["kubatur_fm_original"] = fm
    row["kubatur_fm_aktuell"] = fm
    return row

# ==========================================================
# Parser 1: WBV Wasserburg / bisherige 1-seitige Formulare
# ==========================================================
def parse_wbv_wasserburg(pages, filename):
    text = "\n".join(pages)
    if "WBV Holzhandels GmbH" not in text:
        return []
    bereit = re.search(r"Bereitstellung\s+Nr\.\s*([A-Z0-9\-]+)", text, re.I)
    if not bereit:
        return []
    datum_m = re.search(r"(?:Lief/Leist-Dat|Belegdatum)\s+(\d{2}\.\d{2}\.\d{4})", text)
    vertr = re.search(r"Vertr\.-Nr\.\s*(.+)", text)
    pattern = re.compile(
        r"(?m)^(\d{6,9}/\d+/\d+)\s+([A-Za-zÄÖÜäöü]+)\s+([A-Za-z0-9]+)\s+"
        r"([\d,]+)\s*m\s+([\d,]+)\s*RM\s+([\d,]+)\s*FM"
    )
    matches = list(pattern.finditer(text))
    rows = []
    for i,m in enumerate(matches):
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        seg = text[m.start():end]
        coord = re.search(r"(\d{1,2}\.\d{4,8})°?\s*N,\s*(\d{1,3}\.\d{4,8})°?\s*E", seg, re.I)
        def line(label):
            x = re.search(rf"{label}:\s*(.+)", seg, re.I)
            return x.group(1).strip() if x else ""
        parts = m.group(1).split("/")
        r = base_row(filename)
        r.update(
            bereitstellung=bereit.group(1), lieferant="WBV Holzhandels GmbH",
            vertragsnummer=vertr.group(1).strip() if vertr else "",
            datum=datum_m.group(1) if datum_m else "",
            holzliste=m.group(1), los=parts[1], polter_nr=parts[2],
            holzart=m.group(2), sortiment=m.group(3), laenge_m=fnum(m.group(4)),
            einheit="RM / FM",
            lat=float(coord.group(1)) if coord else None,
            lon=float(coord.group(2)) if coord else None,
            waldort=line("Waldort"), lagerort=line("Lagerort"),
            bemerkung=line("Bemerkung"), ansprechpartner=line("Ansprechpartner"),
            zertifikat=line("Zertifikat")
        )
        set_qty(r, fnum(m.group(5)), fnum(m.group(6)))
        rows.append(r)
    return rows

# ==========================================================
# Parser 2: München / Stadtwerke München
# Flexible Behandlung von leerer Stück-Spalte
# ==========================================================
def parse_muenchen(pages, filename):
    first = pages[0] if pages else ""
    if "Bereitstellungsanzeige Nr." not in first or "Polterinformation" not in first:
        return []
    nr = re.search(r"Bereitstellungsanzeige\s+Nr\.\s*([A-Z0-9\-]+)", first, re.I)
    if not nr:
        return []
    datum = re.search(r"Bereitstellungsdatum:\s*(\d{2}\.\d{2}\.\d{4})", first, re.I)
    vertr = re.search(r"Vertragsnummer:\s*([A-Z0-9_\-]+)", first, re.I)
    lieferant_m = re.search(r"Lieferant:.*?\n(.+?)\s+Kaindl Boards GmbH", first, re.S)
    lieferant = lieferant_m.group(1).splitlines()[0].strip() if lieferant_m else "Landeshauptstadt München / Forst"
    ansp_m = re.search(r"(Hr\.\s+[A-Za-zÄÖÜäöüß]+\s+\d[\d\-; ()]+\(HAB-Nr\.:.*?\))", first)
    ansp = ansp_m.group(1).strip() if ansp_m else ""

    rows = []
    for line in first.splitlines():
        line = line.strip()
        if not re.match(r"^\d+\s+\d+\s+\d+\s+[A-Za-zÄÖÜäöü]+", line):
            continue
        toks = line.split()
        try:
            unit_i = next(i for i,t in enumerate(toks) if t.lower() in ("rm","fmor","fm","efm"))
        except StopIteration:
            continue
        if unit_i < 8 or len(toks) < unit_i + 4:
            continue
        try:
            hab, los, pnr, ha, hs, gkl = toks[:6]
            length = fnum(toks[6])
            qty = fnum(toks[unit_i-1])
            possible_stck = toks[7:unit_i-1]
            stck = fnum(possible_stck[0]) if possible_stck else None
            unit = toks[unit_i]
            kub = fnum(toks[unit_i+1])
            lat = fnum(toks[unit_i+2])
            lon = fnum(toks[unit_i+3])
            tail = " ".join(toks[unit_i+4:])
        except Exception:
            continue

        info = ""
        lager = tail
        for marker in ["Sackgasse ohne Wende", "Sackgasse mit Wende"]:
            pos = tail.lower().find(marker.lower())
            if pos >= 0:
                lager, info = tail[:pos].strip(), tail[pos:].strip()
                break

        r = base_row(filename)
        r.update(
            bereitstellung=nr.group(1), lieferant=lieferant,
            vertragsnummer=vertr.group(1) if vertr else "",
            datum=datum.group(1) if datum else "", hab=hab, los=los, polter_nr=pnr,
            holzart=ha, sortiment=f"{hs} {gkl}", laenge_m=length, stueck=stck,
            einheit=unit, lat=lat, lon=lon, lagerort=lager, bemerkung=info,
            ansprechpartner=ansp
        )
        # Menge in diesen Formularen = RM, Kubatur = FM/FmoR
        set_qty(r, qty, kub)
        rows.append(r)
    return rows

# ==========================================================
# Parser 3: WBV Altötting-Burghausen
# ==========================================================
def parse_wbv_altoetting(pages, filename):
    first = pages[0] if pages else ""
    if "WBV Altötting-Burghausen" not in first:
        return []
    nr = re.search(r"Nr\.\s*(BM[A-Z0-9\-]+)", first, re.I)
    if not nr:
        return []
    datum = re.search(r"Bereitstellungsdatum:\s*(\d{2}\.\d{2}\.\d{4})", first)
    vertrag = re.search(r"Kaufvertragsnummer:\s*(.+)", first)
    global_note = []
    for pat in [
        r"(Die vier Posten im Süden.*?abfahren\.)",
        r"(Bei 66127.*?mitnehmen\.)"
    ]:
        m = re.search(pat, first, re.I)
        if m: global_note.append(m.group(1).strip())

    rows=[]
    for line in first.splitlines():
        if not re.match(r"^\d{5,6}\s+\d+\s+\d+\s+[A-Za-zÄÖÜäöü]+", line.strip()):
            continue
        t=line.split()
        # Liste Los PNr HA HS GKL Länge [Stck] Menge Einh Lat Lon [Lagerort]
        if len(t) < 11:
            continue
        try:
            liste,los,pnr,ha,hs,gkl=t[:6]
            length=fnum(t[6])
            # unit finden
            ui=next(i for i,x in enumerate(t) if x.lower() in ("rm","fmor","fm","efm"))
            qty=fnum(t[ui-1])
            stck=fnum(t[7]) if ui-1 > 7 else None
            lat=fnum(t[ui+1]); lon=fnum(t[ui+2])
            lager=" ".join(t[ui+3:])
        except Exception:
            continue
        r=base_row(filename)
        r.update(
            bereitstellung=nr.group(1), lieferant="WBV Altötting-Burghausen e.V.",
            vertragsnummer=vertrag.group(1).strip() if vertrag else "",
            datum=datum.group(1) if datum else "", holzliste=liste, los=los, polter_nr=pnr,
            holzart=ha, sortiment=f"{hs} {gkl}", laenge_m=length, stueck=stck,
            einheit=t[ui], lat=lat, lon=lon, lagerort=lager,
            bemerkung=" ".join(global_note)
        )
        # FmoR-Zeile ist FM, Rm-Zeilen RM
        if t[ui].lower()=="rm":
            set_qty(r, qty, None)
        else:
            set_qty(r, None, qty)
        rows.append(r)
    return rows

# ==========================================================
# Parser 4: FBG Isar-Lech / BA-xxx
# ==========================================================
def parse_fbg_isar_lech(pages, filename):
    first = pages[0] if pages else ""
    if "Bereitstellung BA-" not in first:
        return []
    nr = re.search(r"Bereitstellung\s+(BA-\d+)", first)
    liste_head = re.search(r"Liste:\s*(\d+)", first)
    date_m = re.search(r"ausgegeben am:\s*([^\n]+)", first)
    vertrag = re.search(r"Vertrag:\s*(.+)", first)
    ansp = re.search(r"Ihr Ansprechpartner:\s*(.+)", first)
    revier = re.search(r"Revier:\s*(.+)", first)

    # Koordinaten stehen beim PDF-Text jeweils unmittelbar VOR der Polterzeile.
    coord_iter = list(re.finditer(r"(?m)^(\d{1,2},\d{5,8})\s+(\d{1,3},\d{5,8})\s*$", first))
    line_iter = list(re.finditer(
        r"(?m)^(\d+)\s+(\d+)\s+(\d+)\s+([A-Za-z]+-[A-Za-z]+-[A-Za-z]+-[\d.]+)\s+"
        r"(\d+)\s+([\d,]+)\s+Rm(?:\s+m\.R\.)?\s*$", first
    ))
    rows=[]
    for lm in line_iter:
        prev_coords=[c for c in coord_iter if c.start() < lm.start()]
        coord=prev_coords[-1] if prev_coords else None
        liste,los,pnr,sortiment,stck,qty=lm.groups()
        parts=sortiment.split("-")
        ha=parts[0] if parts else ""
        length=None
        try:
            length=float(parts[-1])
        except:
            pass
        note_m=re.search(rf"(?m)^P{re.escape(pnr)}:\s*(.+)$", first)
        r=base_row(filename)
        r.update(
            bereitstellung=nr.group(1), lieferant="FBG Isar-Lech",
            vertragsnummer=vertrag.group(1).strip() if vertrag else "",
            datum=date_m.group(1).strip() if date_m else "",
            holzliste=liste, los=los, polter_nr=pnr, holzart=ha,
            sortiment=sortiment, laenge_m=length, stueck=fnum(stck),
            einheit="Rm", lat=fnum(coord.group(1)) if coord else None,
            lon=fnum(coord.group(2)) if coord else None,
            waldort=revier.group(1).strip() if revier else "",
            bemerkung=note_m.group(1).strip() if note_m else "",
            ansprechpartner=ansp.group(1).strip() if ansp else ""
        )
        set_qty(r, fnum(qty), None)
        rows.append(r)
    return rows

# ==========================================================
# Parser 5: Toerring-Jettenbach / Fulcrum-Holzverwaltung
# PDF enthält Poltermengen, aber KEINE numerischen GPS-Koordinaten.
# Daher werden die Polter trotzdem angelegt und können danach manuell
# auf der Karte positioniert werden.
# ==========================================================
def parse_toerring(pages, filename):
    text="\n".join(pages)
    if "Unternehmensgruppe Toerring-Jettenbach" not in text or "Poltererfassung" not in text:
        return []
    los=re.search(r"Losnummer\s+([^\n]+)", text)
    datum=re.search(r"Datum Aufnahme\s+([^\n]+)", text)
    betrieb=re.search(r"Betrieb\s+([^\n]+)", text)
    revier=re.search(r"Revier\s+([^\n]+)", text)
    distrikt=re.search(r"Distrikt\s+([^\n]+)", text)
    baumart=re.search(r"Baumart\s+([^\n]+)", text)
    sortiment=re.search(r"Sortiment\s+([^\n]+)", text)
    length=re.search(r"Länge\s+([\d.,]+)\s*m", text)
    zert=re.search(r"PEFC Zertifikat Eigentümer ITJ-BY:\s*([^\n]+)", text)
    losnr=los.group(1).strip() if los else filename.rsplit(".",1)[0]

    # Blöcke nach Polternummer
    matches=list(re.finditer(r"Polternummer\s+(\d+)", text))
    rows=[]
    for i,m in enumerate(matches):
        end=matches[i+1].start() if i+1<len(matches) else len(text)
        seg=text[m.start():end]
        efm=re.search(r"Menge \[EFm\]\s+([\d.,]+)", seg)
        rm=re.search(r"Menge \[Rm\]\s+([\d.,]+)", seg)
        r=base_row(filename)
        r.update(
            bereitstellung=losnr, lieferant="Unternehmensgruppe Toerring-Jettenbach",
            datum=datum.group(1).strip() if datum else "",
            holzliste=losnr, polter_nr=m.group(1),
            holzart=baumart.group(1).strip() if baumart else "",
            sortiment=sortiment.group(1).strip() if sortiment else "",
            laenge_m=fnum(length.group(1)) if length else None,
            einheit="Rm / EFm",
            waldort=" / ".join(x.group(1).strip() for x in [revier,distrikt] if x),
            lagerort="Frei Waldstraße",
            bemerkung="PDF enthält eine Kartenabbildung, aber keine numerischen GPS-Koordinaten. Position kann in der App manuell ergänzt werden.",
            ansprechpartner="",
            zertifikat=zert.group(1).strip() if zert else ""
        )
        set_qty(r, fnum(rm.group(1)) if rm else None, fnum(efm.group(1)) if efm else None)
        rows.append(r)
    return rows

# ==========================================================
# Parser 6: BayernAtlas-Lagerort / WBV Pfarrkirchen
# Karte + Kurzlink, aber keine numerischen Koordinaten im PDF.
# ==========================================================
def parse_bayernatlas_lagerort(pages, filename):
    text="\n".join(pages)
    if "WBV Pfarrkirchen" not in text or "v.bayern.de/" not in text:
        return []
    name=re.search(r"Waldbesitzer:\s*\n?(.+?)\s*\(([\d,]+)\s*rm;\s*([\d,]+)m\s*lang\)", text, re.I|re.S)
    link=re.search(r"(https://v\.bayern\.de/[A-Za-z0-9]+)", text)
    datum=re.search(r"Erstellt am\s+([0-9.]+\s+[0-9:]+)", text)
    r=base_row(filename)
    r.update(
        bereitstellung=filename.rsplit(".",1)[0],
        lieferant="WBV Pfarrkirchen",
        datum=datum.group(1).strip() if datum else "",
        holzliste=name.group(1).strip() if name else "",
        polter_nr="1",
        sortiment="3 m" if name else "",
        laenge_m=fnum(name.group(3)) if name else None,
        einheit="Rm",
        lagerort=name.group(1).strip() if name else "BayernAtlas-Lagerort",
        bemerkung="Lagerort ist im PDF als BayernAtlas-Karte markiert. Keine numerischen GPS-Koordinaten im Dokument.",
        map_link=link.group(1) if link else ""
    )
    set_qty(r, fnum(name.group(2)) if name else None, None)
    return [r]

PARSERS = [
    parse_wbv_wasserburg,
    parse_muenchen,
    parse_wbv_altoetting,
    parse_fbg_isar_lech,
    parse_toerring,
    parse_bayernatlas_lagerort,
]

def parse_pdf(pdf_bytes, filename):
    pages=extract_pages(pdf_bytes)
    candidates=[]
    errors=[]
    for parser in PARSERS:
        try:
            rows=parser(pages, filename)
            if rows:
                candidates.append(rows)
        except Exception as e:
            errors.append(f"{parser.__name__}: {e}")
    return (max(candidates,key=len) if candidates else []), errors

# ==========================================================
# Oberfläche
# ==========================================================
st.title("🪵 Polter-Zentrale")
st.caption("PDFs einlesen · Polter auf Karte · Mengen ändern · abgefahrene Bereitstellungen löschen")

if SUPABASE:
    st.success("☁️ Cloud-Datenbank verbunden – Daten werden dauerhaft gespeichert.")
else:
    st.warning("🧪 Testmodus ohne Supabase – Daten liegen nur in der lokalen App-Datei.")

with st.container(border=True):
    st.subheader("1. Bereitstellungen importieren")
    uploads=st.file_uploader(
        "PDFs hier per Drag & Drop hineinziehen",
        type=["pdf"], accept_multiple_files=True
    )
    if uploads and st.button("PDFs einlesen", type="primary"):
        total=0
        failed=[]
        for f in uploads:
            rows, errors=parse_pdf(f.getvalue(), f.name)
            if rows:
                total += insert_rows(rows)
            else:
                failed.append(f.name)
        if total:
            st.success(f"{total} neue Polter wurden übernommen.")
        else:
            st.info("Keine neuen Polter übernommen – eventuell bereits vorhanden.")
        if failed:
            st.warning("Noch nicht automatisch erkannt: " + ", ".join(failed))
        st.rerun()

df=load_df()
if df.empty:
    st.info("Noch keine Bereitstellungen vorhanden.")
    st.stop()

status_opts=["Offen","Eingeplant","In Abfuhr","Erledigt"]

# Filter
st.sidebar.header("Filter")
lieferanten=sorted(x for x in df["lieferant"].dropna().unique() if x)
holzarten=sorted(x for x in df["holzart"].dropna().unique() if x)
sel_lief=st.sidebar.multiselect("Lieferant",lieferanten,default=lieferanten)
sel_holz=st.sidebar.multiselect("Holzart",holzarten,default=holzarten)
sel_status=st.sidebar.multiselect("Status",status_opts,default=status_opts)
search=st.sidebar.text_input("Suche",placeholder="Bereitstellung, Polter, Lagerort …")

view=df[
    df["lieferant"].isin(sel_lief) &
    df["holzart"].isin(sel_holz) &
    df["status"].isin(sel_status)
].copy()

if search.strip():
    q=search.lower()
    mask=pd.Series(False,index=view.index)
    for c in ["bereitstellung","holzliste","hab","los","polter_nr","lagerort","waldort","bemerkung","ansprechpartner"]:
        mask |= view[c].fillna("").astype(str).str.lower().str.contains(q,regex=False)
    view=view[mask]

c1,c2,c3,c4=st.columns(4)
c1.metric("Polter",len(view))
c2.metric("Aktuelle RM",f"{view['menge_rm_aktuell'].fillna(0).sum():,.1f}")
c3.metric("FM / EFm",f"{view['kubatur_fm_aktuell'].fillna(0).sum():,.1f}")
c4.metric("Bereitstellungen",view["bereitstellung"].nunique())

left,right=st.columns([1.45,1])

with left:
    st.subheader("2. Karte")
    pts=view.dropna(subset=["lat","lon"])
    if pts.empty:
        st.info("Für die aktuelle Auswahl sind noch keine numerischen GPS-Koordinaten vorhanden.")
    else:
        m=folium.Map(location=[pts["lat"].mean(),pts["lon"].mean()],zoom_start=9,tiles="OpenStreetMap")
        for _,r in pts.iterrows():
            key=r["holzliste"] if r["holzliste"] else f"{r['hab']}/{r['los']}/{r['polter_nr']}"
            popup=f"""
            <b>{key}</b><br>
            Bereitstellung: {r['bereitstellung']}<br>
            Lagerort: {r['lagerort'] or '-'}<br>
            Holzart: {r['holzart']} {r['sortiment']}<br>
            RM aktuell: {r['menge_rm_aktuell'] if pd.notna(r['menge_rm_aktuell']) else '-'}<br>
            FM/EFm aktuell: {r['kubatur_fm_aktuell'] if pd.notna(r['kubatur_fm_aktuell']) else '-'}<br>
            Status: {r['status']}<br>
            """
            folium.Marker(
                [float(r["lat"]),float(r["lon"])],
                tooltip=f"{r['bereitstellung']} · Polter {r['polter_nr']} · {r['menge_rm_aktuell'] or 0} RM",
                popup=folium.Popup(popup,max_width=380)
            ).add_to(m)
        st_folium(m,use_container_width=True,height=620,returned_objects=[])

with right:
    st.subheader("3. Polter bearbeiten")
    if not view.empty:
        options={}
        for _,r in view.iterrows():
            key=r["holzliste"] if r["holzliste"] else f"{r['hab']}/{r['los']}/{r['polter_nr']}"
            options[f"{r['bereitstellung']} · {key} · {r['lagerort'] or 'ohne Lagerort'}"]=int(r["id"])
        chosen=st.selectbox("Polter auswählen",list(options.keys()))
        pid=options[chosen]
        row=df[df["id"]==pid].iloc[0]

        with st.form("edit_polter"):
            rm=st.number_input(
                "Menge aktuell (RM)", min_value=0.0,
                value=float(row["menge_rm_aktuell"] or 0), step=0.1, format="%.3f"
            )
            fm=st.number_input(
                "FM / EFm / Kubatur aktuell", min_value=0.0,
                value=float(row["kubatur_fm_aktuell"] or 0), step=0.1, format="%.3f"
            )
            status=st.selectbox(
                "Status",status_opts,
                index=status_opts.index(row["status"]) if row["status"] in status_opts else 0
            )
            note=st.text_area("Interne Notiz",value=row["interne_notiz"] or "",height=80)

            st.markdown("**GPS bei Bedarf manuell ergänzen/korrigieren**")
            lat=st.number_input(
                "Breitengrad", value=float(row["lat"]) if pd.notna(row["lat"]) else 0.0,
                format="%.7f"
            )
            lon=st.number_input(
                "Längengrad", value=float(row["lon"]) if pd.notna(row["lon"]) else 0.0,
                format="%.7f"
            )
            map_link=st.text_input("Karten-Link",value=row["map_link"] or "")

            if st.form_submit_button("Änderungen speichern",type="primary"):
                lat_save=None if lat==0 else lat
                lon_save=None if lon==0 else lon
                update_row(pid,rm,fm,status,note,lat_save,lon_save,map_link)
                st.success("Gespeichert.")
                st.rerun()

        d1,d2=st.columns(2)
        d1.metric("Original RM",f"{float(row['menge_rm_original'] or 0):,.3f}")
        d2.metric("Aktuell RM",f"{float(row['menge_rm_aktuell'] or 0):,.3f}")

        if pd.notna(row["lat"]) and pd.notna(row["lon"]):
            st.link_button("📍 Google Maps",f"https://www.google.com/maps?q={row['lat']},{row['lon']}")
        if row["map_link"]:
            st.link_button("🗺️ Original-Kartenlink öffnen",row["map_link"])

        with st.expander("Polterdetails"):
            st.write({
                "Bereitstellung":row["bereitstellung"],
                "Holzliste":row["holzliste"],
                "HAB":row["hab"],"Los":row["los"],"Polter":row["polter_nr"],
                "Holzart":row["holzart"],"Sortiment":row["sortiment"],
                "Länge":row["laenge_m"],"Waldort":row["waldort"],
                "Lagerort":row["lagerort"],"Bemerkung":row["bemerkung"],
                "Ansprechpartner":row["ansprechpartner"],
                "Quelldatei":row["quelle_datei"]
            })

        with st.expander("⚠️ Diesen einzelnen Polter löschen"):
            st.warning("Der Polter wird dauerhaft aus der Datenbank gelöscht.")
            if st.checkbox("Löschen bestätigen",key=f"delpolter_confirm_{pid}"):
                if st.button("Polter endgültig löschen",key=f"delpolter_{pid}"):
                    delete_polter(pid)
                    st.success("Polter gelöscht.")
                    st.rerun()

st.subheader("4. Bereitstellungen verwalten / löschen")
st.caption("Wenn eine komplette Bereitstellung abgefahren ist, kannst du hier alle zugehörigen Polter auf einmal löschen.")

summary=(df.groupby("bereitstellung",dropna=False)
         .agg(
             Polter=("id","count"),
             RM_aktuell=("menge_rm_aktuell","sum"),
             FM_EFm_aktuell=("kubatur_fm_aktuell","sum"),
             Lieferant=("lieferant","first"),
             Datum=("datum","first")
         ).reset_index())
st.dataframe(summary,use_container_width=True,hide_index=True)

bereits=[x for x in summary["bereitstellung"].astype(str).tolist() if x]
if bereits:
    del_b=st.selectbox("Bereitstellung zum Löschen auswählen",bereits)
    del_count=int(summary.loc[summary["bereitstellung"].astype(str)==del_b,"Polter"].iloc[0])
    st.warning(f"Beim Löschen von **{del_b}** werden **{del_count} Polter** dauerhaft entfernt.")
    confirm=st.checkbox(f"Ja, Bereitstellung {del_b} ist abgefahren und darf gelöscht werden.")
    if st.button("🗑️ Gesamte Bereitstellung löschen",disabled=not confirm,type="secondary"):
        delete_bereitstellung(del_b)
        st.success(f"Bereitstellung {del_b} wurde gelöscht.")
        st.rerun()

st.subheader("5. Polterliste")
show=view[[
    "id","bereitstellung","lieferant","holzliste","hab","los","polter_nr","holzart","sortiment",
    "laenge_m","menge_rm_original","menge_rm_aktuell","kubatur_fm_original","kubatur_fm_aktuell",
    "status","waldort","lagerort","bemerkung","ansprechpartner","lat","lon","map_link","quelle_datei"
]].copy()
show.columns=[
    "ID","Bereitstellung","Lieferant","Holzliste","HAB","Los","Polter","Holzart","Sortiment",
    "Länge m","RM Original","RM aktuell","FM/EFm Original","FM/EFm aktuell",
    "Status","Waldort","Lagerort","Bemerkung","Ansprechpartner","Breite","Länge","Kartenlink","Quelldatei"
]
st.dataframe(show,use_container_width=True,hide_index=True,height=430)
st.download_button(
    "⬇️ CSV exportieren",
    show.to_csv(index=False).encode("utf-8-sig"),
    "polter_export.csv","text/csv"
)
