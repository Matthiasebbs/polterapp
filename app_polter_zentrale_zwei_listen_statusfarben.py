
import io
import re
from pathlib import Path
import pdfplumber

def n(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in {"-", "–"}:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None

def pages_from_bytes(data):
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [p.extract_text(x_tolerance=2, y_tolerance=2) or "" for p in pdf.pages]

def empty(filename):
    return {
        "quelle_datei": filename, "bereitstellung": "", "lieferant": "", "fraechter": "",
        "vertragsnummer": "", "datum": "", "holzliste": "", "hab": "", "los": "",
        "polter_nr": "", "holzart": "", "sortiment": "", "laenge_m": None,
        "stueck": None, "menge_rm_original": None, "menge_rm_aktuell": None,
        "kubatur_fm_original": None, "kubatur_fm_aktuell": None, "einheit": "",
        "lat": None, "lon": None, "waldort": "", "lagerort": "", "bemerkung": "",
        "ansprechpartner": "", "zertifikat": "", "map_link": ""
    }

def qty(r, rm=None, fm=None):
    """
    Es wird aus jeder Bereitstellung nur EINE Mengenbasis übernommen:
    - Wenn Raummeter (RM) vorhanden sind, sind RM der Ausgangswert.
      FM werden mit RM / 1,5 berechnet.
    - Wenn keine RM vorhanden sind, aber FM/EFm/FmoR vorhanden sind,
      sind diese der Ausgangswert.
      RM werden mit FM * 1,5 berechnet.

    Dadurch werden niemals gleichzeitig RM und FM unverändert aus dem PDF übernommen.
    """
    UMR_FACTOR = 1.5

    if rm is not None:
        rm_val = round(float(rm), 3)
        fm_val = round(rm_val / UMR_FACTOR, 3)
    elif fm is not None:
        fm_val = round(float(fm), 3)
        rm_val = round(fm_val * UMR_FACTOR, 3)
    else:
        rm_val = None
        fm_val = None

    r["menge_rm_original"] = rm_val
    r["menge_rm_aktuell"] = rm_val
    r["kubatur_fm_original"] = fm_val
    r["kubatur_fm_aktuell"] = fm_val
    return r

def line_value(text, label):
    m = re.search(rf"(?mi)^{re.escape(label)}:\s*(.+)$", text)
    return m.group(1).strip() if m else ""

def parse_wbv_wasserburg(pages, filename):
    text = "\n".join(pages)
    if "WBV Holzhandels GmbH" not in text:
        return []
    nr = re.search(r"Bereitstellung\s+Nr\.\s*([A-Z0-9\-]+)", text, re.I)
    if not nr:
        return []
    date = re.search(r"(?:Lief/Leist-Dat|Belegdatum)\s+(\d{2}\.\d{2}\.\d{4})", text)
    contract = re.search(r"Vertr\.-Nr\.\s*(.+)", text)

    pat = re.compile(
        r"(?m)^(\d{6,9}/\d+/\d+)\s+([A-Za-zÄÖÜäöü]+)\s+([A-Za-z0-9]+)\s+"
        r"([\d,]+)\s*m\s+([\d,]+)\s*RM\s+([\d,]+)\s*FM"
    )
    mm = list(pat.finditer(text))
    rows = []
    for i, m in enumerate(mm):
        seg = text[m.start():(mm[i+1].start() if i+1 < len(mm) else len(text))]
        gps = re.search(r"(\d{1,2}\.\d{4,8})°?\s*N,\s*(\d{1,3}\.\d{4,8})°?\s*E", seg)
        p = m.group(1).split("/")
        r = empty(filename)
        r.update(
            bereitstellung=nr.group(1), lieferant="WBV Holzhandels GmbH",
            vertragsnummer=contract.group(1).strip() if contract else "",
            datum=date.group(1) if date else "",
            holzliste=m.group(1), los=p[1], polter_nr=p[2],
            holzart=m.group(2), sortiment=m.group(3), laenge_m=n(m.group(4)),
            einheit="RM / FM",
            lat=float(gps.group(1)) if gps else None,
            lon=float(gps.group(2)) if gps else None,
            waldort=line_value(seg, "Waldort"),
            lagerort=line_value(seg, "Lagerort"),
            bemerkung=line_value(seg, "Bemerkung"),
            ansprechpartner=line_value(seg, "Ansprechpartner"),
            zertifikat=line_value(seg, "Zertifikat")
        )
        qty(r, n(m.group(5)), n(m.group(6)))
        rows.append(r)
    return rows

def parse_muenchen(pages, filename):
    first = pages[0] if pages else ""
    if "Bereitstellungsanzeige Nr." not in first or "Polterinformation:" not in first:
        return []
    nr = re.search(r"Bereitstellungsanzeige\s+Nr\.\s*([A-Z0-9\-]+)", first)
    if not nr:
        return []

    date = re.search(r"Bereitstellungsdatum:\s*(\d{2}\.\d{2}\.\d{4})", first)
    contract = re.search(r"Vertragsnummer:\s*([A-Za-z0-9_\-]+)", first)

    # Lieferant ist der Text direkt vor "Kaindl Boards GmbH" in der ersten Datenzeile.
    supplier = "München / Forst"
    for ln in first.splitlines():
        if "Kaindl Boards GmbH" in ln and "Bereitstellungsmeldung" not in ln:
            left = ln.split("Kaindl Boards GmbH", 1)[0].strip()
            if left:
                supplier = left
                break

    ap = ""
    m_ap = re.search(r"(?m)^(Hr\.\s+.+?\(HAB-Nr\.:.*?\))$", first)
    if m_ap:
        ap = m_ap.group(1).strip()

    rows = []
    in_table = False
    for ln in first.splitlines():
        s = ln.strip()
        if s.startswith("HAB Los PNr."):
            in_table = True
            continue
        if in_table and s.startswith("Summe"):
            break
        if not in_table or not re.match(r"^\d+\s+\d+\s+\d+\s+", s):
            continue

        t = s.split()
        # HAB Los PNr HA HS GKL Länge [Stck] Menge Einh Kubatur Lat Lon Lagerort...
        try:
            hab, los, pnr, ha, hs, gkl = t[:6]
            length = n(t[6])
            ui = next(i for i, x in enumerate(t) if x.lower() in {"rm","fmor","fm","efm"})
            amount = n(t[ui-1])
            between = t[7:ui-1]
            stck = n(between[0]) if between else None
            kub = n(t[ui+1])
            lat = n(t[ui+2])
            lon = n(t[ui+3])
            tail = " ".join(t[ui+4:]).strip()
        except Exception:
            continue

        lager = tail
        info = ""
        for marker in ("Sackgasse ohne Wende", "Sackgasse mit Wende"):
            pos = tail.lower().find(marker.lower())
            if pos >= 0:
                lager = tail[:pos].strip()
                info = tail[pos:].strip()
                break

        r = empty(filename)
        r.update(
            bereitstellung=nr.group(1), lieferant=supplier,
            vertragsnummer=contract.group(1) if contract else "",
            datum=date.group(1) if date else "",
            hab=hab, los=los, polter_nr=pnr, holzart=ha,
            sortiment=f"{hs} {gkl}", laenge_m=length, stueck=stck,
            einheit=t[ui], lat=lat, lon=lon, lagerort=lager,
            bemerkung=info, ansprechpartner=ap
        )
        qty(r, amount, kub)
        rows.append(r)
    return rows

def parse_wbv_altoetting(pages, filename):
    first = pages[0] if pages else ""
    if "WBV Altötting-Burghausen e.V." not in first:
        return []
    nr = re.search(r"Nr\.\s*(BM[A-Z0-9\-]+)", first)
    if not nr:
        return []
    date = re.search(r"Bereitstellungsdatum:\s*(\d{2}\.\d{2}\.\d{4})", first)
    contract = re.search(r"Kaufvertragsnummer:\s*([^\n]+)", first)

    notes = []
    for ln in first.splitlines():
        if ln.startswith("Die vier Posten") or ln.startswith("Bei 66127"):
            notes.append(ln.strip())

    rows = []
    table = False
    for ln in first.splitlines():
        s = ln.strip()
        if s.startswith("Liste Los PNr."):
            table = True
            continue
        if table and s.startswith("Summe"):
            break
        if not table or not re.match(r"^\d{5,6}\s+\d+\s+\d+\s+", s):
            continue
        t = s.split()
        try:
            liste, los, pnr, ha, hs, gkl = t[:6]
            length = n(t[6])
            ui = next(i for i,x in enumerate(t) if x.lower() in {"rm","fmor","fm","efm"})
            amount = n(t[ui-1])
            between = t[7:ui-1]
            stck = n(between[0]) if between else None
            lat = n(t[ui+1]); lon = n(t[ui+2])
            lager = " ".join(t[ui+3:]).strip()
        except Exception:
            continue
        r = empty(filename)
        r.update(
            bereitstellung=nr.group(1), lieferant="WBV Altötting-Burghausen e.V.",
            vertragsnummer=contract.group(1).strip() if contract else "",
            datum=date.group(1) if date else "", holzliste=liste, los=los,
            polter_nr=pnr, holzart=ha, sortiment=f"{hs} {gkl}",
            laenge_m=length, stueck=stck, einheit=t[ui],
            lat=lat, lon=lon, lagerort=lager, bemerkung=" ".join(notes)
        )
        if t[ui].lower() == "rm":
            qty(r, amount, None)
        else:
            qty(r, None, amount)
        rows.append(r)
    return rows

def parse_fbg_isar_lech(pages, filename):
    first = pages[0] if pages else ""
    if "Bereitstellung BA-" not in first or "FBG" not in first and "Isar-Lech" not in first:
        # Text extraction may not contain logo name; BA structure is distinctive enough.
        if "Bereitstellung BA-" not in first or "GPS-N GPS-O" not in first:
            return []
    nr = re.search(r"Bereitstellung\s+(BA-\d+)", first)
    if not nr:
        return []
    date = re.search(r"ausgegeben am:\s*([^\n]+)", first)
    contract = re.search(r"Vertrag:\s*([^\n]+)", first)
    ap = re.search(r"Ihr Ansprechpartner:\s*([^\n]+)", first)
    revier = re.search(r"Revier:\s*([^\n]+)", first)

    lines = [x.strip() for x in first.splitlines()]
    rows = []
    pending_gps = None
    for i, s in enumerate(lines):
        gps = re.match(r"^(\d{1,2},\d{5,8})\s+(\d{1,3},\d{5,8})$", s)
        if gps:
            pending_gps = (n(gps.group(1)), n(gps.group(2)))
            continue
        m = re.match(
            r"^(\d+)\s+(\d+)\s+(\d+)\s+([A-Za-z]+-[A-Za-z]+-[A-Za-z]+-[\d.]+)\s+"
            r"(\d+)\s+([\d,]+)\s+Rm(?:\s+m\.R\.)?$", s
        )
        if not m:
            continue
        liste, los, pnr, sort, stck, amount = m.groups()
        parts = sort.split("-")
        note = ""
        nm = re.search(rf"(?m)^P{re.escape(pnr)}:\s*(.+)$", first)
        if nm:
            note = nm.group(1).strip()
        r = empty(filename)
        r.update(
            bereitstellung=nr.group(1), lieferant="FBG Isar-Lech",
            vertragsnummer=contract.group(1).strip() if contract else "",
            datum=date.group(1).strip() if date else "",
            holzliste=liste, los=los, polter_nr=pnr,
            holzart=parts[0], sortiment=sort,
            laenge_m=n(parts[-1]), stueck=n(stck), einheit="Rm",
            lat=pending_gps[0] if pending_gps else None,
            lon=pending_gps[1] if pending_gps else None,
            waldort=revier.group(1).strip() if revier else "",
            bemerkung=note,
            ansprechpartner=ap.group(1).strip() if ap else ""
        )
        qty(r, n(amount), None)
        rows.append(r)
        pending_gps = None
    return rows

def parse_toerring(pages, filename):
    text = "\n".join(pages)
    if "Unternehmensgruppe Toerring-Jettenbach" not in text or "Poltererfassung" not in text:
        return []
    los = re.search(r"Losnummer\s+([^\n]+)", text)
    datum = re.search(r"Datum Aufnahme\s+([^\n]+)", text)
    revier = re.search(r"Revier\s+([^\n]+)", text)
    distrikt = re.search(r"Distrikt\s+([^\n]+)", text)
    baum = re.search(r"Baumart\s+([^\n]+)", text)
    sort = re.search(r"Sortiment\s+([^\n]+)", text)
    length = re.search(r"Länge\s+([\d.,]+)\s*m", text)
    zert = re.search(r"PEFC Zertifikat Eigentümer ITJ-BY:\s*([^\n]+)", text)
    losnr = los.group(1).strip() if los else Path(filename).stem

    matches = list(re.finditer(r"Polternummer\s+(\d+)", text))
    rows = []
    for i,m in enumerate(matches):
        seg = text[m.start():(matches[i+1].start() if i+1<len(matches) else len(text))]
        efm = re.search(r"Menge \[EFm\]\s+([\d.,]+)", seg)
        rm = re.search(r"Menge \[Rm\]\s+([\d.,]+)", seg)
        r = empty(filename)
        r.update(
            bereitstellung=losnr, lieferant="Unternehmensgruppe Toerring-Jettenbach",
            datum=datum.group(1).strip() if datum else "", holzliste=losnr,
            polter_nr=m.group(1), holzart=baum.group(1).strip() if baum else "",
            sortiment=sort.group(1).strip() if sort else "",
            laenge_m=n(length.group(1)) if length else None,
            einheit="Rm / EFm",
            waldort=" / ".join(
                x.group(1).strip() for x in (revier,distrikt) if x
            ),
            lagerort="Frei Waldstraße",
            bemerkung="Im PDF sind die Positionen nur als Kartenmarker/Bild vorhanden; keine numerischen GPS-Koordinaten sind im Text hinterlegt.",
            zertifikat=zert.group(1).strip() if zert else ""
        )
        qty(r, n(rm.group(1)) if rm else None, n(efm.group(1)) if efm else None)
        rows.append(r)
    return rows

def parse_bayernatlas(pages, filename):
    text = "\n".join(pages)
    if "WBV Pfarrkirchen" not in text or "v.bayern.de/" not in text:
        return []
    owner = re.search(r"Waldbesitzer:\s*\n?(.+?)\s*\(([\d,]+)\s*rm;\s*([\d,]+)m\s*lang\)", text, re.I|re.S)
    link = re.search(r"(https://v\.bayern\.de/[A-Za-z0-9]+)", text)
    date = re.search(r"Erstellt am\s+([0-9.]+\s+[0-9:]+)", text)
    r = empty(filename)
    name = owner.group(1).strip() if owner else "Lagerort"
    r.update(
        bereitstellung=Path(filename).stem,
        lieferant="WBV Pfarrkirchen",
        datum=date.group(1).strip() if date else "",
        holzliste=name, polter_nr="1",
        sortiment="3 m", laenge_m=n(owner.group(3)) if owner else None,
        einheit="Rm", lagerort=name,
        bemerkung="Lagerort ist in der PDF-Karte markiert; keine numerischen GPS-Koordinaten sind im PDF-Text enthalten.",
        map_link=link.group(1) if link else ""
    )
    qty(r, n(owner.group(2)) if owner else None, None)
    return [r]


def extract_fraechter_from_filename(filename):
    """
    Frächter aus dem Dateinamen ableiten.

    Regel:
    - Eine Bereitstellungsnummer am Ende gehört NICHT zum Frächter.
    - Bei z.B. "Kunde_26-0182 Astner.pdf" und "Kunde_26-0183 Astner.pdf"
      ist der Frächter immer "Astner".
    - Auch Varianten wie "...-0182 Astner" werden zu "Astner".
    - "&" bleibt erhalten, z.B. "G&H".
    """
    stem = Path(filename).stem.strip()

    # Leerzeichen normalisieren.
    stem = re.sub(r"\s+", " ", stem)

    # Wenn am Ende nach einem Leerzeichen ein Name/Frächter steht,
    # ausschließlich diesen letzten Namensblock verwenden.
    # Beispiele:
    # Kunde_26-0182 Astner -> Astner
    # Kunde_26-0183 Astner -> Astner
    # Kunde_26-0092 G&H    -> G&H
    m = re.search(r"\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß&.' ]*)$", stem)
    if m:
        carrier = m.group(1).strip()
        if carrier:
            return carrier

    # Variante mit Bindestrich direkt vor dem Frächter:
    # ...-Soller / ...-Ammer / ...-G&H
    m = re.search(r"-([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß&.' ]*)$", stem)
    if m:
        carrier = m.group(1).strip()
        if carrier:
            return carrier

    return ""

PARSERS = [
    ("WBV Wasserburg", parse_wbv_wasserburg),
    ("München / Stadtwerke", parse_muenchen),
    ("WBV Altötting-Burghausen", parse_wbv_altoetting),
    ("FBG Isar-Lech", parse_fbg_isar_lech),
    ("Toerring-Jettenbach", parse_toerring),
    ("WBV Pfarrkirchen / BayernAtlas", parse_bayernatlas),
]

def parse_pdf_bytes(data, filename):
    pages = pages_from_bytes(data)
    attempts = []
    best = []
    best_name = ""
    for name, parser in PARSERS:
        try:
            rows = parser(pages, filename)
            attempts.append((name, len(rows), ""))
            if len(rows) > len(best):
                best = rows
                best_name = name
        except Exception as e:
            attempts.append((name, 0, str(e)))
    fraechter = extract_fraechter_from_filename(filename)
    for row in best:
        row["fraechter"] = fraechter
    return best, best_name, attempts


# ================= APP =================

import sqlite3
import hashlib
from datetime import datetime
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium


try:
    from supabase import create_client
except Exception:
    create_client = None

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "polter.db"

st.set_page_config(page_title="Polter-Zentrale", page_icon="🪵", layout="wide")

FIELDS = [
    "quelle_datei","bereitstellung","lieferant","fraechter","vertragsnummer","datum","holzliste",
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
        quelle_datei TEXT, bereitstellung TEXT, lieferant TEXT, fraechter TEXT, vertragsnummer TEXT,
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
    con.execute("""
    CREATE TABLE IF NOT EXISTS warenbelege (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        beleg_key TEXT UNIQUE,
        belegnummer TEXT,
        fraechter TEXT,
        belegdatum TEXT,
        belegtext TEXT,
        verbucht_am TEXT
    )
    """)
    cols = {row[1] for row in con.execute("PRAGMA table_info(polter)").fetchall()}
    if "fraechter" not in cols:
        con.execute("ALTER TABLE polter ADD COLUMN fraechter TEXT DEFAULT ''")
    con.commit()
    return con

CON = local_db() if SB is None else None

def df_all():
    if SB:
        data = SB.table("polter").select("*").order("id", desc=True).execute().data
        df = pd.DataFrame(data) if data else pd.DataFrame(columns=["id"]+FIELDS)
    else:
        df = pd.read_sql_query("SELECT * FROM polter ORDER BY id DESC", CON)
    for c in ["lieferant","fraechter","holzart","status","map_link","interne_notiz"]:
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

def normalize_name(value):
    """Für robustes Matching von Lieferantennamen."""
    s = (value or "").lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def detect_supplier_from_text(text):
    """
    Sucht einen Lieferanten des Warenbelegs gegen die bereits in der App
    vorhandenen Lieferantennamen. Dadurch werden unterschiedliche Schreibweisen
    wie 'WBV ALTÖTTING-BURGHAUSEN e.V.' und 'WBV Altötting-Burghausen e.V.'
    zusammengeführt.
    """
    df = df_all()
    suppliers = sorted([x for x in df["lieferant"].fillna("").unique().tolist() if x])
    norm_text = normalize_name(text)

    # Erst exakte normalisierte Namensprüfung.
    for supplier in sorted(suppliers, key=len, reverse=True):
        if normalize_name(supplier) and normalize_name(supplier) in norm_text:
            return supplier

    # Einige typische Aliasformen.
    aliases = {
        "wbvaltoettingburghausen": "WBV Altötting-Burghausen e.V.",
        "stadtwerkemuenchenwasser": "Stadtwerke München Wasser",
        "landeshauptstadtmuenchengemeindewald": "Landeshauptstadt München - Gemeindewald",
        "wbvholzhandelsgmbh": "WBV Holzhandels GmbH",
        "fbgisarlech": "FBG Isar-Lech",
        "unternehmensgruppetoerringjettenbach": "Unternehmensgruppe Toerring-Jettenbach",
        "wbvpfarrkirchen": "WBV Pfarrkirchen",
    }
    for alias, canonical in aliases.items():
        if alias in norm_text and canonical in suppliers:
            return canonical

    return ""


def extract_warenbeleg_pdf(pdf_bytes):
    """Liest Warenbeleg, Lieferant, Frächter und Belegtext aus."""
    pages = pages_from_bytes(pdf_bytes)
    text = "\n".join(pages)

    belegnummer = ""
    m = re.search(r"\bBeleg\s*:\s*(\d+)", text, re.I)
    if m:
        belegnummer = m.group(1)

    belegdatum = ""
    m = re.search(r"\bBelegdatum\s*:\s*([0-9.]+)", text, re.I)
    if m:
        belegdatum = m.group(1)

    fraechter = ""
    m = re.search(r"(?mi)^Frächter\s+\d+\s+(.+)$", text)
    if m:
        fraechter = m.group(1).strip()

    lieferant = detect_supplier_from_text(text)

    belegtext = ""
    m = re.search(r"Belegtext\s*:\s*(.*)$", text, re.I | re.S)
    if m:
        belegtext = re.sub(r"\s+", " ", m.group(1)).strip()

    return {
        "volltext": text,
        "belegnummer": belegnummer,
        "belegdatum": belegdatum,
        "fraechter": fraechter,
        "lieferant": lieferant,
        "belegtext": belegtext,
    }


def parse_absolute_abzuege(belegtext):
    """
    Absolute Angaben, z.B.:
      Partie 76022 los 1 P1 ca 22 Rm
      66123 los 2 P2 ca 11 Rm
    """
    if not belegtext or not belegtext.strip():
        return []

    clean = re.sub(r"\s+", " ", belegtext).strip()
    pattern = re.compile(
        r"(?:Partie\s+)?"
        r"(?P<partie>\d{4,8})\s+"
        r"los\s*(?P<los>\d+)\s+"
        r"P\s*\.?\s*(?P<polter>\d+)\s+"
        r"(?:ca\.?\s*)?"
        r"(?P<menge>\d+(?:[,.]\d+)?)\s*Rm\b",
        re.I
    )

    result = []
    for m in pattern.finditer(clean):
        result.append({
            "modus": "RM",
            "partie": m.group("partie"),
            "los": m.group("los"),
            "polter": m.group("polter"),
            "menge_rm": float(m.group("menge").replace(",", ".")),
            "prozent": None,
            "original_text": m.group(0),
        })
    return result


def parse_percentage_abzuege(belegtext, gesamt_rm=46.0):
    """
    Prozentangaben, z.B.:
      25% Polter 1 und 75% Polter 2
      25 % P1 / 75 % P2
      Polter 1 25%, Polter 2 75%

    Die Prozentwerte beziehen sich auf gesamt_rm.
    Standard gemäß Vorgabe: 46 RM = 100 %.
    """
    if not belegtext or not belegtext.strip():
        return []

    clean = re.sub(r"\s+", " ", belegtext).strip()
    found = []

    # Prozent zuerst: "25% Polter 1"
    p1 = re.compile(
        r"(?P<pct>\d+(?:[,.]\d+)?)\s*%\s*"
        r"(?:von\s*)?(?:Polter|P)\s*\.?\s*(?P<polter>\d+)",
        re.I
    )
    # Polter zuerst: "Polter 1 25%"
    p2 = re.compile(
        r"(?:Polter|P)\s*\.?\s*(?P<polter>\d+)\s*"
        r"(?P<pct>\d+(?:[,.]\d+)?)\s*%",
        re.I
    )

    occupied = []
    for pat in (p1, p2):
        for m in pat.finditer(clean):
            span = m.span()
            if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
                continue
            occupied.append(span)
            pct = float(m.group("pct").replace(",", "."))
            found.append({
                "modus": "%",
                "partie": "",
                "los": "",
                "polter": m.group("polter"),
                "menge_rm": round(float(gesamt_rm) * pct / 100.0, 3),
                "prozent": pct,
                "original_text": m.group(0),
            })

    # In Textreihenfolge sortieren ist für die Vorschau angenehmer.
    return found


def parse_bereitstellungs_abzug(belegtext, gesamt_rm=46.0):
    """
    Erkennt Fälle, in denen nur die Bereitstellungsnummer genannt wird,
    z. B.:
      BA 338
      BA-338
      Bereitstellung BA 338
      26-0183

    In diesem Fall bedeutet die Angabe:
      1 LKW = gesamt_rm RM
    Diese Menge wird später innerhalb der betreffenden Bereitstellung
    beginnend beim ersten Polter abgezogen.
    """
    if not belegtext or not belegtext.strip():
        return []

    clean = re.sub(r"\s+", " ", belegtext).strip()

    patterns = [
        re.compile(r"(?:Bereitstellung\s*)?(BA)\s*[- ]?\s*(\d{2,6})\b", re.I),
        re.compile(r"(?:Bereitstellung\s*)?(\d{2})\s*[- ]\s*(\d{3,6})\b", re.I),
    ]

    matches = []
    occupied = []
    for pat in patterns:
        for m in pat.finditer(clean):
            span = m.span()
            if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
                continue
            occupied.append(span)

            if m.group(1).upper() == "BA":
                bereit = f"BA-{m.group(2)}"
            else:
                bereit = f"{m.group(1)}-{m.group(2)}"

            matches.append({
                "modus": "BEREITSTELLUNG",
                "bereitstellung": bereit,
                "partie": "",
                "los": "",
                "polter": "",
                "menge_rm": round(float(gesamt_rm), 3),
                "prozent": None,
                "original_text": m.group(0),
            })
    return matches


def parse_abzuege_from_belegtext(belegtext, gesamt_rm=46.0):
    """
    Priorität:
    1. Absolute RM-Angaben je Polter
    2. Prozentangaben je Polter
    3. Nur Bereitstellungsnummer -> 1 LKW = gesamt_rm RM

    Eine reine Bereitstellungsangabe wird nur verwendet, wenn im Belegtext
    keine konkreteren Polterangaben erkannt wurden.
    """
    absolute = parse_absolute_abzuege(belegtext)
    percentage = parse_percentage_abzuege(belegtext, gesamt_rm)

    absolute_polter = {str(x["polter"]) for x in absolute}
    percentage = [x for x in percentage if str(x["polter"]) not in absolute_polter]

    specific = absolute + percentage
    if specific:
        return specific

    return parse_bereitstellungs_abzug(belegtext, gesamt_rm)

def receipt_key(belegnummer, belegtext, lieferant=""):
    basis = (belegnummer or "") + "|" + (lieferant or "") + "|" + (belegtext or "")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def warenbeleg_bereits_verbucht(key):
    if SB:
        try:
            data = SB.table("warenbelege").select("id").eq("beleg_key", key).limit(1).execute().data
            return bool(data)
        except Exception:
            return False
    row = CON.execute("SELECT 1 FROM warenbelege WHERE beleg_key=? LIMIT 1", (key,)).fetchone()
    return row is not None


def warenbeleg_loggen(key, belegnummer, fraechter, belegdatum, belegtext):
    now = datetime.now().isoformat(timespec="seconds")
    if SB:
        SB.table("warenbelege").insert({
            "beleg_key": key,
            "belegnummer": belegnummer,
            "fraechter": fraechter,
            "belegdatum": belegdatum,
            "belegtext": belegtext,
            "verbucht_am": now,
        }).execute()
    else:
        CON.execute("""
            INSERT OR IGNORE INTO warenbelege
            (beleg_key, belegnummer, fraechter, belegdatum, belegtext, verbucht_am)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (key, belegnummer, fraechter, belegdatum, belegtext, now))
        CON.commit()


def validate_percentage_split(abzuege):
    pct = [x for x in abzuege if x.get("modus") == "%"]
    if not pct:
        return True, ""
    total = round(sum(float(x.get("prozent") or 0) for x in pct), 6)
    if abs(total - 100.0) > 0.01:
        return False, f"Die Prozentaufteilung ergibt {total:g} % statt 100 %. Es wird nichts verbucht."
    return True, ""


def verbuche_abzug_auf_bereitstellung(bereitstellung, lieferant, abzug_rm):
    """
    Zieht abzug_rm RM von der Gesamtmenge einer Bereitstellung ab.
    Reihenfolge: erster Polter der Bereitstellung zuerst, dann der nächste usw.

    "Erster Polter" bedeutet hier die Reihenfolge, in der die Polter in der
    Datenbank/importierten Bereitstellung vorhanden sind (id aufsteigend).
    Sobald ein Polter auf 0 RM fällt, wird er auf 'Erledigt' gesetzt.
    """
    UMR_FACTOR = 1.5
    df = df_all()

    group = df[
        df["lieferant"].fillna("").astype(str).eq(str(lieferant)) &
        df["bereitstellung"].fillna("").astype(str).eq(str(bereitstellung))
    ].copy()

    if group.empty:
        return [{
            "modus": "BEREITSTELLUNG",
            "bereitstellung": bereitstellung,
            "partie": "",
            "los": "",
            "polter": "",
            "menge_rm": abzug_rm,
            "prozent": None,
            "original_text": bereitstellung,
            "status": "BEREITSTELLUNG NICHT GEFUNDEN",
            "vorher_rm": None,
            "nachher_rm": None,
            "nachher_fm": None,
        }]

    group = group.sort_values("id", ascending=True)

    rest_abzug = float(abzug_rm)
    results = []

    for _, row in group.iterrows():
        if rest_abzug <= 0.0005:
            break

        vorher = float(row["menge_rm_aktuell"] or 0)
        if vorher <= 0.0005:
            continue

        auf_diesem_polter = min(vorher, rest_abzug)
        nachher = round(vorher - auf_diesem_polter, 3)
        fm_neu = round(nachher / UMR_FACTOR, 3)
        neuer_status = "Erledigt" if nachher <= 0.0005 else row["status"]

        update_polter(
            int(row["id"]),
            nachher,
            fm_neu,
            neuer_status,
            row["interne_notiz"] or "",
            None if pd.isna(row["lat"]) else float(row["lat"]),
            None if pd.isna(row["lon"]) else float(row["lon"]),
        )

        results.append({
            "modus": "BEREITSTELLUNG",
            "bereitstellung": bereitstellung,
            "partie": row["holzliste"] or "",
            "los": row["los"] or "",
            "polter": row["polter_nr"] or "",
            "menge_rm": round(auf_diesem_polter, 3),
            "prozent": None,
            "original_text": bereitstellung,
            "status": "VERBUCHT",
            "vorher_rm": vorher,
            "nachher_rm": nachher,
            "nachher_fm": fm_neu,
        })

        rest_abzug = round(rest_abzug - auf_diesem_polter, 3)

    if rest_abzug > 0.0005:
        results.append({
            "modus": "BEREITSTELLUNG",
            "bereitstellung": bereitstellung,
            "partie": "",
            "los": "",
            "polter": "",
            "menge_rm": rest_abzug,
            "prozent": None,
            "original_text": bereitstellung,
            "status": "REST NICHT ABZIEHBAR",
            "vorher_rm": None,
            "nachher_rm": None,
            "nachher_fm": None,
        })

    return results


def verbuche_abzuege(abzuege, lieferant):
    """
    Matching-Regeln:
    1. Lieferant MUSS übereinstimmen.
    2. Absolute Angaben: Lieferant + Partie/Liste + Los + Polter.
    3. Prozentangaben: Lieferant + Polternummer; bei Mehrdeutigkeit kein Abzug.
    4. Nur Bereitstellungsnummer: Lieferant + Bereitstellungsnummer.
       Dann wird die LKW-Menge (Standard 46 RM) über die Polter dieser
       Bereitstellung verteilt, beginnend beim ersten Polter.
    """
    UMR_FACTOR = 1.5
    df = df_all()
    results = []

    if not lieferant:
        return [{
            "modus": "",
            "bereitstellung": "",
            "partie": "",
            "los": "",
            "polter": "",
            "menge_rm": None,
            "prozent": None,
            "original_text": "",
            "status": "KEIN LIEFERANT",
            "vorher_rm": None,
            "nachher_rm": None,
            "nachher_fm": None,
        }]

    supplier_df = df[
        df["lieferant"].fillna("").astype(str).eq(str(lieferant))
    ]

    for a in abzuege:
        if a.get("modus") == "BEREITSTELLUNG":
            results.extend(
                verbuche_abzug_auf_bereitstellung(
                    a.get("bereitstellung", ""),
                    lieferant,
                    float(a.get("menge_rm") or 0),
                )
            )
            continue

        if a.get("partie") and a.get("los"):
            match = supplier_df[
                supplier_df["holzliste"].fillna("").astype(str).eq(str(a["partie"])) &
                supplier_df["los"].fillna("").astype(str).eq(str(a["los"])) &
                supplier_df["polter_nr"].fillna("").astype(str).eq(str(a["polter"]))
            ]
        else:
            match = supplier_df[
                supplier_df["polter_nr"].fillna("").astype(str).eq(str(a["polter"]))
            ]

        if len(match) == 0:
            results.append({
                **a,
                "bereitstellung": a.get("bereitstellung", ""),
                "status": "NICHT GEFUNDEN",
                "vorher_rm": None,
                "nachher_rm": None,
                "nachher_fm": None,
            })
            continue

        if len(match) > 1:
            results.append({
                **a,
                "bereitstellung": a.get("bereitstellung", ""),
                "status": "MEHRDEUTIG",
                "vorher_rm": None,
                "nachher_rm": None,
                "nachher_fm": None,
            })
            continue

        row = match.iloc[0]
        vorher = float(row["menge_rm_aktuell"] or 0)
        abzug = float(a["menge_rm"])
        nachher = max(0.0, round(vorher - abzug, 3))
        fm_neu = round(nachher / UMR_FACTOR, 3)
        neuer_status = "Erledigt" if nachher <= 0.0005 else row["status"]

        update_polter(
            int(row["id"]),
            nachher,
            fm_neu,
            neuer_status,
            row["interne_notiz"] or "",
            None if pd.isna(row["lat"]) else float(row["lat"]),
            None if pd.isna(row["lon"]) else float(row["lon"]),
        )

        results.append({
            **a,
            "bereitstellung": row["bereitstellung"] or "",
            "status": "VERBUCHT",
            "vorher_rm": vorher,
            "nachher_rm": nachher,
            "nachher_fm": fm_neu,
        })

    return results



def berechne_abfuhrstatus(row):
    """
    Abfuhrstatus für Listen:
    - Erledigt: aktuelle RM <= 0
    - Teilweise abgefahren: aktuelle RM < Original-RM und > 0
    - Nicht abgefahren: sonst
    """
    original = float(row.get("menge_rm_original") or 0)
    aktuell = float(row.get("menge_rm_aktuell") or 0)

    if aktuell <= 0.0005:
        return "Erledigt"
    if original > 0 and aktuell < original - 0.0005:
        return "Teilweise abgefahren"
    return "Nicht abgefahren"

st.title("🪵 Polter-Zentrale")
st.caption("Alle zugesandten Bereitstellungsformate · Lieferanten-/Frächter-/Längenfilter · Mengenänderung · 1,5 RM = 1 FM · Löschen nach Abfuhr")

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

# Alte, bereits importierte Frächterwerte aus früheren Versionen in der Anzeige bereinigen.
# Beispiel: "0182 Astner" und "0183 Astner" werden beide als "Astner" behandelt.
if not df.empty and "fraechter" in df.columns:
    df["fraechter"] = (
        df["fraechter"]
        .fillna("")
        .astype(str)
        .str.replace(r"^\d{3,6}\s+", "", regex=True)
        .str.strip()
    )

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

fraechter_values = sorted([x for x in df["fraechter"].fillna("").unique().tolist() if x])
fraechter_choice = st.sidebar.multiselect(
    "Frächter",
    fraechter_values,
    default=fraechter_values,
    help="Der Frächter wird automatisch aus dem Ende des PDF-Dateinamens übernommen."
)
status_values = ["Offen","Eingeplant","In Abfuhr","Erledigt"]
status_choice = st.sidebar.multiselect("Status", status_values, default=status_values)

# Feste Lieferantenfarben für die Karte.
# Die Zuordnung bleibt stabil, solange die Lieferantennamen gleich bleiben.
SUPPLIER_COLORS = [
    "#1f77b4",  # blau
    "#ff7f0e",  # orange
    "#2ca02c",  # grün
    "#d62728",  # rot
    "#9467bd",  # violett
    "#8c564b",  # braun
    "#e377c2",  # pink
    "#17becf",  # türkis
    "#bcbd22",  # oliv
    "#7f7f7f",  # grau
]
supplier_color_map = {
    name: SUPPLIER_COLORS[i % len(SUPPLIER_COLORS)]
    for i, name in enumerate(sorted(supplier_values))
}

wood_values = sorted([x for x in df["holzart"].fillna("").unique().tolist() if x])
wood_choice = st.sidebar.multiselect("Holzart (optional)", wood_values, default=[])

# Längenfilter: z. B. nur 3-m-Holz anzeigen.
length_values = sorted([
    float(x) for x in df["laenge_m"].dropna().unique().tolist()
])
length_choice = st.sidebar.multiselect(
    "Länge des Sortiments",
    length_values,
    default=[],
    format_func=lambda x: f"{x:g} m",
    help="Hier kannst du z. B. nur Polter mit 3 m langem Holz anzeigen lassen."
)

search = st.sidebar.text_input("Suche", placeholder="Bereitstellung, Polter, Lagerort …")

view = df.copy()
if supplier_choice:
    view = view[view["lieferant"].isin(supplier_choice)]
else:
    view = view.iloc[0:0]

if fraechter_choice:
    view = view[view["fraechter"].isin(fraechter_choice)]
else:
    view = view.iloc[0:0]

if status_choice:
    view = view[view["status"].isin(status_choice)]
else:
    view = view.iloc[0:0]
if wood_choice:
    view = view[view["holzart"].isin(wood_choice)]

if length_choice:
    view = view[view["laenge_m"].isin(length_choice)]

if search.strip():
    q = search.lower()
    mask = pd.Series(False, index=view.index)
    for c in ["bereitstellung","lieferant","fraechter","holzliste","hab","los","polter_nr","lagerort","waldort","bemerkung"]:
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

    # Erledigte Polter werden bewusst NICHT mehr auf der Karte angezeigt.
    map_view = view[view["status"] != "Erledigt"].copy()
    pts = map_view.dropna(subset=["lat","lon"])
    missing = len(map_view) - len(pts)

    # Kleine Farblegende für die aktuell ausgewählten Lieferanten.
    visible_suppliers = sorted([x for x in map_view["lieferant"].dropna().unique() if x])
    if visible_suppliers:
        legend_html = " &nbsp; ".join(
            f'<span style="display:inline-flex;align-items:center;margin-right:12px;">'
            f'<span style="display:inline-block;width:12px;height:12px;border-radius:50%;'
            f'background:{supplier_color_map.get(name, "#666666")};margin-right:5px;"></span>'
            f'{name}</span>'
            for name in visible_suppliers
        )
        st.markdown(legend_html, unsafe_allow_html=True)

    erledigt_count = int((view["status"] == "Erledigt").sum())
    if erledigt_count:
        st.caption(f"{erledigt_count} erledigte Polter sind ausgeblendet.")
    if missing:
        st.caption(f"{missing} offene/eingeplante Polter haben keine numerischen GPS-Koordinaten und erscheinen deshalb nur in der Liste.")

    if pts.empty:
        st.info("Für die aktuelle Auswahl sind keine aktiven Polter mit numerischen GPS-Koordinaten vorhanden.")
    else:
        mp = folium.Map(
            location=[pts["lat"].mean(), pts["lon"].mean()],
            zoom_start=9,
            tiles="OpenStreetMap"
        )

        for _, r in pts.iterrows():
            label = r["holzliste"] or f"{r['hab']}/{r['los']}/{r['polter_nr']}"
            supplier_color = supplier_color_map.get(r["lieferant"], "#666666")
            pop = f"""
            <b>{r['lieferant']}</b><br>
            Frächter: {r['fraechter'] or '-'}<br>
            Bereitstellung: {r['bereitstellung']}<br>
            Polter: {label}<br>
            Lagerort: {r['lagerort'] or '-'}<br>
            Holzart: {r['holzart'] or '-'} {r['sortiment'] or ''}<br>
            RM aktuell: {r['menge_rm_aktuell'] if pd.notna(r['menge_rm_aktuell']) else '-'}<br>
            FM/EFm aktuell: {r['kubatur_fm_aktuell'] if pd.notna(r['kubatur_fm_aktuell']) else '-'}<br>
            Status: {r['status']}
            """

            # CircleMarker erlaubt für jeden Lieferanten eine frei definierte Farbe.
            folium.CircleMarker(
                location=[float(r["lat"]), float(r["lon"])],
                radius=8,
                color=supplier_color,
                fill=True,
                fill_color=supplier_color,
                fill_opacity=0.95,
                weight=2,
                tooltip=f"{r['lieferant']} · {r['bereitstellung']} · Polter {r['polter_nr']}",
                popup=folium.Popup(pop, max_width=380),
            ).add_to(mp)

        st_folium(mp, use_container_width=True, height=610, returned_objects=[])

with right:
    st.subheader("3. Polter bearbeiten")
    if not view.empty:
        opts = {}
        for _,r in view.iterrows():
            key = r["holzliste"] or f"{r['hab']}/{r['los']}/{r['polter_nr']}"
            opts[f"{r['lieferant']} · {r['fraechter'] or '-'} · {r['bereitstellung']} · {key}"] = int(r["id"])
        selected = st.selectbox("Polter auswählen", list(opts.keys()))
        pid = opts[selected]
        row = df[df["id"]==pid].iloc[0]
        with st.form("edit"):
            st.caption("Umrechnung: 1,5 RM = 1 FM. Ändere einfach RM oder FM – das jeweils andere Feld wird beim Speichern automatisch neu berechnet.")

            old_rm = float(row["menge_rm_aktuell"] or 0)
            old_fm = float(row["kubatur_fm_aktuell"] or 0)

            rm = st.number_input(
                "Menge aktuell (RM)",
                min_value=0.0,
                value=old_rm,
                step=0.1,
                format="%.3f"
            )
            fm = st.number_input(
                "Festmeter aktuell (FM / EFm)",
                min_value=0.0,
                value=old_fm,
                step=0.1,
                format="%.3f"
            )

            status = st.selectbox(
                "Status",
                status_values,
                index=status_values.index(row["status"]) if row["status"] in status_values else 0
            )
            note = st.text_area("Interne Notiz", value=row["interne_notiz"] or "")
            st.caption("Nur bei PDFs ohne numerische GPS-Koordinaten nötig:")
            lat = st.number_input(
                "Breitengrad",
                value=float(row["lat"]) if pd.notna(row["lat"]) else 0.0,
                format="%.7f"
            )
            lon = st.number_input(
                "Längengrad",
                value=float(row["lon"]) if pd.notna(row["lon"]) else 0.0,
                format="%.7f"
            )

            if st.form_submit_button("Speichern", type="primary"):
                # Automatische Mengen-Umrechnung:
                # 1,5 Raummeter = 1 Festmeter
                UMR_FACTOR = 1.5
                EPS = 0.0005

                rm_changed = abs(float(rm) - old_rm) > EPS
                fm_changed = abs(float(fm) - old_fm) > EPS

                if rm_changed and not fm_changed:
                    # RM wurde geändert -> FM automatisch berechnen
                    fm = round(float(rm) / UMR_FACTOR, 3)
                elif fm_changed and not rm_changed:
                    # FM wurde geändert -> RM automatisch berechnen
                    rm = round(float(fm) * UMR_FACTOR, 3)
                elif rm_changed and fm_changed:
                    # Falls ausnahmsweise beide Felder geändert wurden,
                    # hat RM Vorrang, damit die Werte eindeutig bleiben.
                    fm = round(float(rm) / UMR_FACTOR, 3)

                update_polter(
                    pid, rm, fm, status, note,
                    None if lat == 0 else lat,
                    None if lon == 0 else lon
                )
                st.success(f"Gespeichert: {rm:.3f} RM = {fm:.3f} FM")
                st.rerun()

        a,b,c = st.columns(3)
        a.metric("Original RM", f"{float(row['menge_rm_original'] or 0):,.3f}")
        b.metric("Aktuell RM", f"{float(row['menge_rm_aktuell'] or 0):,.3f}")
        c.metric("Aktuell FM", f"{float(row['kubatur_fm_aktuell'] or 0):,.3f}")

        if pd.notna(row["lat"]) and pd.notna(row["lon"]):
            st.link_button("📍 Google Maps", f"https://www.google.com/maps?q={row['lat']},{row['lon']}")
        if row.get("map_link"):
            st.link_button("🗺️ Original-Kartenlink", row["map_link"])

        with st.expander("Einzelnen Polter löschen"):
            if st.checkbox("Löschen bestätigen", key=f"conf_{pid}"):
                if st.button("Polter endgültig löschen", key=f"del_{pid}"):
                    delete_one(pid)
                    st.rerun()


st.subheader("4. Warenbeleg / Abfuhr verbuchen")
st.caption(
    "Erkannt werden absolute RM-Angaben, Prozentaufteilungen und auch nur eine "
    "Bereitstellungsnummer. Bei Prozentangaben oder nur einer Bereitstellungsnummer "
    "gelten standardmäßig 46 RM als 1 LKW bzw. 100 %. Der Lieferant wird immer mitgeprüft."
)

with st.container(border=True):
    waren_pdf = st.file_uploader(
        "Warenbeleg als PDF hochladen",
        type=["pdf"],
        key="warenbeleg_upload"
    )

    st.markdown("**Alternativ / zum Testen:** Belegtext direkt einfügen")
    manueller_belegtext = st.text_area(
        "Belegtext",
        placeholder=(
            "z. B. Partie 76022 los 1 P1 ca 22 Rm, "
            "25% Polter 1 und 75% Polter 2 oder nur BA 338"
        ),
        key="warenbeleg_text_manual",
        height=90
    )

    wb = {
        "belegnummer": "",
        "belegdatum": "",
        "fraechter": "",
        "lieferant": "",
        "belegtext": "",
    }

    if waren_pdf:
        try:
            wb = extract_warenbeleg_pdf(waren_pdf.getvalue())
            st.write(
                f"**Beleg:** {wb['belegnummer'] or '-'}  ·  "
                f"**Belegdatum:** {wb['belegdatum'] or '-'}  ·  "
                f"**Frächter:** {wb['fraechter'] or '-'}"
            )
            if wb["belegtext"]:
                st.text_area(
                    "Aus dem PDF erkannter Belegtext",
                    value=wb["belegtext"],
                    height=100,
                    disabled=True
                )
            else:
                st.info("Im PDF wurde kein Belegtext gefunden. Es wird nichts verändert.")
        except Exception as e:
            st.error(f"Warenbeleg konnte nicht gelesen werden: {e}")

    supplier_options = sorted([x for x in df["lieferant"].fillna("").unique().tolist() if x])
    detected_supplier = wb.get("lieferant", "")
    supplier_index = supplier_options.index(detected_supplier) if detected_supplier in supplier_options else 0

    lieferant_beleg = st.selectbox(
        "Lieferant des Warenbelegs",
        supplier_options,
        index=supplier_index if supplier_options else None,
        help=(
            "Der Lieferant wird aus dem Warenbeleg erkannt. Bitte kontrollieren. "
            "Beim Verbuchen werden ausschließlich Polter dieses Lieferanten berücksichtigt."
        )
    ) if supplier_options else ""

    if detected_supplier:
        st.success(f"Lieferant automatisch erkannt: {detected_supplier}")
    elif waren_pdf:
        st.warning("Lieferant konnte nicht sicher erkannt werden. Bitte manuell auswählen.")

    gesamt_rm_pct = st.number_input(
        "Gesamtmenge für Prozentaufteilung (RM)",
        min_value=0.0,
        value=46.0,
        step=0.5,
        format="%.3f",
        help=(
            "Bei Prozentaufteilungen und bei einer reinen Bereitstellungsnummer "
            "gilt dieser Wert als 1 LKW bzw. 100 %. Standard: 46 RM."
        )
    )

    effective_text = manueller_belegtext.strip() or wb.get("belegtext", "").strip()

    if effective_text:
        abzuege = parse_abzuege_from_belegtext(effective_text, gesamt_rm_pct)

        if not abzuege:
            st.warning(
                "Im Belegtext wurde keine vollständige RM-Angabe und keine "
                "Prozentaufteilung gefunden. Es wird nichts verändert."
            )
        else:
            pct_ok, pct_msg = validate_percentage_split(abzuege)

            preview = pd.DataFrame(abzuege).rename(columns={
                "modus": "Art",
                "bereitstellung": "Bereitstellung",
                "partie": "Partie/Liste",
                "los": "Los",
                "polter": "Polter",
                "menge_rm": "Abzug RM",
                "prozent": "Anteil %",
                "original_text": "Erkannter Text",
            })
            st.markdown("**Erkannte Abzüge – bitte vor dem Verbuchen prüfen:**")
            st.dataframe(preview, use_container_width=True, hide_index=True)

            pct_rows = [x for x in abzuege if x.get("modus") == "%"]
            if pct_rows:
                st.info(
                    f"Prozentaufteilung basiert auf {gesamt_rm_pct:.3f} RM = 100 %. "
                    f"Berechnete Summe: {sum(x['menge_rm'] for x in pct_rows):.3f} RM."
                )

            bereit_rows = [x for x in abzuege if x.get("modus") == "BEREITSTELLUNG"]
            if bereit_rows:
                st.info(
                    f"Nur Bereitstellungsnummer erkannt: Es werden {gesamt_rm_pct:.3f} RM "
                    "von dieser Bereitstellung abgezogen, beginnend beim ersten Polter."
                )

            if not pct_ok:
                st.error(pct_msg)

            key = receipt_key(
                wb.get("belegnummer", ""),
                effective_text,
                lieferant_beleg
            )
            already = warenbeleg_bereits_verbucht(key)

            if already:
                st.error(
                    "Dieser Warenbeleg bzw. exakt dieser Belegtext wurde für diesen "
                    "Lieferanten bereits verbucht. Ein zweiter Abzug wird verhindert."
                )
            else:
                confirm = st.checkbox(
                    "Ich habe Lieferant, Polter und Mengen geprüft.",
                    key="warenbeleg_confirm"
                )
                if st.button(
                    "🚚 Mengen jetzt von den Poltern abziehen",
                    type="primary",
                    disabled=(not confirm or not pct_ok or not lieferant_beleg),
                    key="warenbeleg_apply"
                ):
                    result = verbuche_abzuege(abzuege, lieferant_beleg)
                    result_df = pd.DataFrame(result).rename(columns={
                        "modus": "Art",
                        "bereitstellung": "Bereitstellung",
                        "partie": "Partie/Liste",
                        "los": "Los",
                        "polter": "Polter",
                        "menge_rm": "Abzug RM",
                        "prozent": "Anteil %",
                        "status": "Ergebnis",
                        "vorher_rm": "Vorher RM",
                        "nachher_rm": "Nachher RM",
                        "nachher_fm": "Nachher FM",
                    })

                    if any(x["status"] == "VERBUCHT" for x in result):
                        warenbeleg_loggen(
                            key,
                            wb.get("belegnummer", ""),
                            wb.get("fraechter", ""),
                            wb.get("belegdatum", ""),
                            effective_text,
                        )

                    st.dataframe(result_df, use_container_width=True, hide_index=True)

                    nicht_ok = [x for x in result if x["status"] != "VERBUCHT"]
                    if nicht_ok:
                        st.warning(
                            "Mindestens eine Angabe konnte innerhalb des ausgewählten "
                            "Lieferanten nicht eindeutig zugeordnet werden und wurde NICHT abgezogen."
                        )
                    else:
                        st.success("Alle erkannten Mengen wurden erfolgreich abgezogen.")

                    st.rerun()

st.subheader("5. Bereitstellungen")
summary = (df.groupby(["lieferant","fraechter","bereitstellung"], dropna=False)
           .agg(Polter=("id","count"),
                RM_aktuell=("menge_rm_aktuell","sum"),
                FM_EFm_aktuell=("kubatur_fm_aktuell","sum"))
           .reset_index())
st.dataframe(summary, use_container_width=True, hide_index=True)

with st.expander("Komplette abgefahrene Bereitstellung löschen"):
    keys = []
    for _,r in summary.iterrows():
        keys.append(f"{r['lieferant']} · {r['fraechter'] or '-'} · {r['bereitstellung']}")
    choice = st.selectbox("Bereitstellung", keys)
    idx = keys.index(choice)
    chosen_row = summary.iloc[idx]
    name = str(chosen_row["bereitstellung"])
    st.warning(f"{int(chosen_row['Polter'])} Polter dieser Bereitstellung werden dauerhaft gelöscht.")
    confirm = st.checkbox(f"Ja, {name} ist vollständig abgefahren.")
    if st.button("🗑️ Bereitstellung löschen", disabled=not confirm):
        delete_group(name)
        st.rerun()

st.subheader("6. Polterübersicht")

# Abfuhrstatus dynamisch aus Original- und Restmenge ableiten.
view = view.copy()
view["abfuhrstatus"] = view.apply(berechne_abfuhrstatus, axis=1)

cols = [
    "lieferant","fraechter","bereitstellung","holzliste","hab","los","polter_nr","holzart","sortiment",
    "laenge_m","menge_rm_original","menge_rm_aktuell","kubatur_fm_original","kubatur_fm_aktuell",
    "abfuhrstatus","status","waldort","lagerort","bemerkung","ansprechpartner","lat","lon","quelle_datei"
]

def make_show(frame):
    show = frame[cols].copy()
    show.columns = [
        "Lieferant","Frächter","Bereitstellung","Holzliste","HAB","Los","Polter","Holzart","Sortiment",
        "Länge m","RM Original","RM aktuell","FM Original","FM aktuell",
        "Abfuhrstatus","Status","Waldort","Lagerort","Bemerkung","Ansprechpartner","Breite","Länge","Quelldatei"
    ]
    return show

def style_active_rows(row):
    status = str(row.get("Abfuhrstatus", ""))
    if status == "Teilweise abgefahren":
        return ["background-color: #fce5cd; color: #7f6000"] * len(row)
    return [""] * len(row)

def style_completed_rows(row):
    return ["background-color: #d9ead3; color: #1f4e1f"] * len(row)

# Liste 1: alles, was noch nicht vollständig abgefahren ist
aktive = view[view["abfuhrstatus"] != "Erledigt"].copy()
show_active = make_show(aktive)

st.markdown("### Noch nicht vollständig abgefahren")
st.caption(
    "Weiße Zeilen = noch nicht angefahren. Orange Zeilen = bereits teilweise abgefahren."
)
if show_active.empty:
    st.success("Aktuell sind keine offenen oder teilweise abgefahrenen Polter vorhanden.")
else:
    styled_active = show_active.style.apply(style_active_rows, axis=1)
    st.dataframe(
        styled_active,
        use_container_width=True,
        hide_index=True,
        height=420
    )

# Liste 2: nur komplett abgefahrene Polter
erledigte = view[view["abfuhrstatus"] == "Erledigt"].copy()
show_done = make_show(erledigte)

st.markdown("### Vollständig abgefahren")
st.caption("Diese Polter haben 0 RM Restmenge und werden grün dargestellt.")
if show_done.empty:
    st.info("Noch keine vollständig abgefahrenen Polter in der aktuellen Filterauswahl.")
else:
    styled_done = show_done.style.apply(style_completed_rows, axis=1)
    st.dataframe(
        styled_done,
        use_container_width=True,
        hide_index=True,
        height=320
    )

# Export enthält beide Gruppen mit Abfuhrstatus.
export_all = make_show(view)
st.download_button(
    "⬇️ Gesamte aktuelle Auswahl als CSV exportieren",
    export_all.to_csv(index=False).encode("utf-8-sig"),
    "polter_export.csv",
    "text/csv"
)
