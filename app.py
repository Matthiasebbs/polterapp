
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
    """
    Robuster Parser für FBG Isar-Lech / Bereitstellung BA-...

    Wichtig:
    - Jeder Polter bleibt separat.
    - Zusammengefasst wird NUR, wenn Liste + Los + Polternummer identisch sind.
    - GPS-Koordinaten können in diesen PDFs VOR der jeweiligen Polterzeile stehen.
    - Polternummern mit Punkt (z. B. 1.062) werden unterstützt.
    - RM- und FM-Zeilen werden unterstützt.
    - Faktor der App: 1,5 RM = 1 FM.
    """
    first = pages[0] if pages else ""
    if "Bereitstellung BA-" not in first or "GPS-N GPS-O" not in first:
        return []

    nr = re.search(r"Bereitstellung\s+(BA-\d+)", first, re.I)
    if not nr:
        return []

    date = re.search(r"ausgegeben am:\s*([^\n]+)", first, re.I)
    contract = re.search(r"Vertrag:\s*([^\n]+)", first, re.I)
    ap = re.search(r"Ihr Ansprechpartner:\s*([^\n]+)", first, re.I)
    revier = re.search(r"Revier:\s*([^\n]+)", first, re.I)

    lines = [re.sub(r"\s+", " ", x.strip()) for x in first.splitlines() if x.strip()]

    # Beispielzeilen:
    # 260751 861 1.062 Er-IL-K-4.0 18,700 Rm m.R. 9,350
    # 260403 521 308 Bu-L-B-3.4-41.0 1 0,427 Fm o.R. 0,427
    # 261504 861 407 Wei-BR-oa-3.0 0 1,360 Rm m.R. 0,816
    row_re = re.compile(
        r"^(?P<liste>\d+)\s+"
        r"(?P<los>\d+)\s+"
        r"(?P<polter>\d+(?:\.\d+)?)\s*"
        r"(?P<sort>[A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß0-9.]+)+)\s+"
        r"(?:(?P<stck>\d+)\s+)?"
        r"(?P<menge>\d+(?:,\d+)?)\s+"
        r"(?P<unit>Rm|Fm)\s*(?:m\.R\.|o\.R\.)?\s+"
        r"(?P<kubatur>\d+(?:,\d+)?)"
        r"(?:\s+(?P<lat>\d{1,2},\d{5,8}))?"
        r"(?:\s+(?P<lon>\d{1,3},\d{5,8}))?"
        r"$",
        re.I
    )

    # In den aktuellen FBG-PDFs stehen GPS-N und GPS-O häufig in EINER
    # eigenen Zeile direkt VOR dem dazugehörigen Polter.
    gps_pair_re = re.compile(
        r"^(?P<lat>\d{1,2},\d{5,8})\s+(?P<lon>\d{1,3},\d{5,8})$"
    )

    raw_rows = []
    pending_gps = None

    for idx, s in enumerate(lines):
        gps_pair = gps_pair_re.match(s)
        if gps_pair:
            pending_gps = (n(gps_pair.group("lat")), n(gps_pair.group("lon")))
            continue

        m = row_re.match(s)
        if not m:
            continue

        d = m.groupdict()

        # 1. Inline-GPS bevorzugen.
        lat = n(d.get("lat"))
        lon = n(d.get("lon"))

        # 2. Sonst die unmittelbar vorher gelesene GPS-Zeile verwenden.
        if (lat is None or lon is None) and pending_gps is not None:
            lat = pending_gps[0] if lat is None else lat
            lon = pending_gps[1] if lon is None else lon

        # 3. Fallback: Falls ein anderes FBG-Exportformat GPS erst NACH
        #    der Polterzeile schreibt, die nächsten wenigen Zeilen prüfen.
        if lat is None or lon is None:
            for nxt in lines[idx + 1: idx + 5]:
                gp = gps_pair_re.match(nxt)
                if gp:
                    lat = n(gp.group("lat")) if lat is None else lat
                    lon = n(gp.group("lon")) if lon is None else lon
                    break

        # GPS gehört nur zu dieser einen Polterzeile.
        pending_gps = None

        amount = n(d["menge"])
        unit = d["unit"].lower()

        # Pro Positionszeile NUR die angegebene Einheit als Basis nehmen.
        if unit == "rm":
            rm_val = amount
            fm_val = round(float(amount) / 1.5, 3) if amount is not None else None
        else:
            fm_val = amount
            rm_val = round(float(amount) * 1.5, 3) if amount is not None else None

        sortiment = d["sort"]
        parts = sortiment.split("-")

        # Sortimentlänge bestimmen. Bei z. B. Bu-L-B-3.4-41.0 ist 3.4 m
        # die Länge; 41.0 ist kein Längenwert.
        laenge = None
        for token in reversed(parts):
            try:
                val = float(token)
                if 1.0 <= val <= 12.0:
                    laenge = val
                    break
            except Exception:
                pass

        raw_rows.append({
            "liste": d["liste"],
            "los": d["los"],
            "polter": d["polter"],
            "sortiment": sortiment,
            "holzart": parts[0] if parts else "",
            "laenge": laenge,
            "stueck": n(d.get("stck")),
            "rm": rm_val,
            "fm": fm_val,
            "lat": lat,
            "lon": lon,
        })

    # NUR exakt gleiche Kombinationen zusammenfassen:
    # Liste + Los + Polter müssen ALLE identisch sein.
    grouped = {}
    for rr in raw_rows:
        key = (str(rr["liste"]), str(rr["los"]), str(rr["polter"]))

        if key not in grouped:
            grouped[key] = {
                **rr,
                "sortimente": [rr["sortiment"]],
            }
            continue

        g = grouped[key]
        g["rm"] = round(float(g.get("rm") or 0) + float(rr.get("rm") or 0), 3)
        g["fm"] = round(float(g.get("fm") or 0) + float(rr.get("fm") or 0), 3)
        g["stueck"] = float(g.get("stueck") or 0) + float(rr.get("stueck") or 0)

        if rr["sortiment"] not in g["sortimente"]:
            g["sortimente"].append(rr["sortiment"])

        # Für einen identischen Polter reicht ein GPS-Punkt; den ersten
        # vorhandenen beibehalten.
        if g.get("lat") is None and rr.get("lat") is not None:
            g["lat"] = rr["lat"]
        if g.get("lon") is None and rr.get("lon") is not None:
            g["lon"] = rr["lon"]
        if g.get("laenge") is None and rr.get("laenge") is not None:
            g["laenge"] = rr["laenge"]

    rows = []
    for (liste, los, pnr), g in grouped.items():
        note = ""
        nm = re.search(rf"(?mi)^P{re.escape(str(pnr))}:\s*(.+)$", first)
        if nm:
            note = nm.group(1).strip()

        r = empty(filename)
        r.update(
            bereitstellung=nr.group(1),
            lieferant="FBG Isar-Lech",
            vertragsnummer=contract.group(1).strip() if contract else "",
            datum=date.group(1).strip() if date else "",
            holzliste=liste,
            los=los,
            polter_nr=pnr,
            holzart=g.get("holzart", ""),
            sortiment=" / ".join(g.get("sortimente", [])),
            laenge_m=g.get("laenge"),
            stueck=g.get("stueck"),
            einheit="RM / FM",
            lat=g.get("lat"),
            lon=g.get("lon"),
            waldort=revier.group(1).strip() if revier else "",
            bemerkung=note,
            ansprechpartner=ap.group(1).strip() if ap else ""
        )

        r["menge_rm_original"] = g.get("rm")
        r["menge_rm_aktuell"] = g.get("rm")
        r["kubatur_fm_original"] = g.get("fm")
        r["kubatur_fm_aktuell"] = g.get("fm")
        rows.append(r)

    return rows

def parse_toerring(pages, filename):
    """
    Parser für Unternehmensgruppe Toerring-Jettenbach / Fulcrum Holzverwaltung.

    Bereitstellungsname:
      <Menge Los [EFm]>, <Losnummer>
    Beispiel:
      64.4, Kaindl_Ndh_26_01

    Jeder Polter wird separat angelegt. GPS-Koordinaten sind in diesem
    PDF-Typ nicht als numerische Werte vorhanden und bleiben deshalb leer.
    """
    text = "\n".join(pages)
    if "Unternehmensgruppe Toerring-Jettenbach" not in text or "Poltererfassung" not in text:
        return []

    los = re.search(r"Losnummer\s+([^\n]+)", text)
    los_efm = re.search(r"Menge Los \[EFm\]\s+([\d.,]+)", text)
    datum = re.search(r"Datum Aufnahme\s+([^\n]+)", text)
    revier = re.search(r"Revier\s+([^\n]+)", text)
    distrikt = re.search(r"Distrikt\s+([^\n]+)", text)
    baum = re.search(r"Baumart\s+([^\n]+)", text)
    sort = re.search(r"Sortiment\s+([^\n]+)", text)
    length = re.search(r"Länge\s+([\d.,]+)\s*m", text)
    zert = re.search(r"PEFC Zertifikat Eigentümer ITJ-BY:\s*([^\n]+)", text)

    losnr = los.group(1).strip() if los else Path(filename).stem
    menge_los_efm = los_efm.group(1).strip().replace(",", ".") if los_efm else ""

    # Gewünschter Bereitstellungsname, z. B. "64.4, Kaindl_Ndh_26_01"
    bereitstellungsname = (
        f"{menge_los_efm}, {losnr}"
        if menge_los_efm
        else losnr
    )

    matches = list(re.finditer(r"Polternummer\s+(\d+)", text))
    rows = []

    for i, m in enumerate(matches):
        seg = text[m.start():(matches[i+1].start() if i+1 < len(matches) else len(text))]
        efm = re.search(r"Menge \[EFm\]\s+([\d.,]+)", seg)
        rm = re.search(r"Menge \[Rm\]\s+([\d.,]+)", seg)

        r = empty(filename)
        r.update(
            bereitstellung=bereitstellungsname,
            lieferant="Unternehmensgruppe Toerring-Jettenbach",
            datum=datum.group(1).strip() if datum else "",
            holzliste=losnr,
            los="",
            polter_nr=m.group(1),
            holzart=baum.group(1).strip() if baum else "",
            sortiment=sort.group(1).strip() if sort else "",
            laenge_m=n(length.group(1)) if length else None,
            einheit="RM / FM",
            lat=None,
            lon=None,
            waldort=" / ".join(
                x.group(1).strip() for x in (revier, distrikt) if x
            ),
            lagerort="Frei Waldstraße",
            bemerkung=(
                "Keine numerischen GPS-Koordinaten im PDF. "
                "Koordinaten können in 'Polter bearbeiten' manuell auf der Karte gesetzt werden."
            ),
            zertifikat=zert.group(1).strip() if zert else ""
        )

        # Wie in der restlichen App: RM ist Basis, falls vorhanden.
        # Der andere Wert wird mit 1,5 RM = 1 FM berechnet.
        qty(
            r,
            n(rm.group(1)) if rm else None,
            n(efm.group(1)) if efm else None
        )
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
    """
    Dauerhafte Cloud-Datenbank.
    Bevorzugt den modernen Supabase Secret Key, unterstützt aber weiterhin
    SUPABASE_KEY als Fallback.
    """
    url = secret("SUPABASE_URL")
    key = (
        secret("SUPABASE_SECRET_KEY")
        or secret("SUPABASE_KEY")
        or secret("SUPABASE_PUBLISHABLE_KEY")
    )
    if create_client and url and key:
        return create_client(url, key)
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
        r["lieferant"] = normalize_supplier_name(r.get("lieferant", ""))
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


def normalize_supplier_name(name):
    """
    Vereinheitlicht Lieferantennamen, die betrieblich als derselbe Lieferant
    behandelt werden sollen.
    """
    value = (name or "").strip()

    aliases = {
        "Stadtwerke München EW": "Stadtwerke München Wasser",
        "Stadtwerke München Wasser": "Stadtwerke München Wasser",
    }

    return aliases.get(value, value)


def berechne_abfuhrstatus(row):
    """
    Automatischer Abfuhrstatus:
    - Abgefahren: Restmenge = 0 RM
    - Teilweise abgefahren: Restmenge kleiner als Originalmenge, aber > 0 RM
    - Nicht abgefahren: Restmenge entspricht noch der Originalmenge
    """
    original = float(row.get("menge_rm_original") or 0)
    aktuell = float(row.get("menge_rm_aktuell") or 0)

    if aktuell <= 0.0005:
        return "Abgefahren"
    if original > 0 and aktuell < original - 0.0005:
        return "Teilweise abgefahren"
    return "Nicht abgefahren"

st.title("🪵 Polter-Zentrale")
st.caption("Alle zugesandten Bereitstellungsformate · Lieferanten-/Frächter-/Längenfilter · Mengenänderung · 1,5 RM = 1 FM · Löschen nach Abfuhr")

if SB:
    st.success("☁️ LIVE: Supabase verbunden – Polter, Mengen, Status und Notizen werden dauerhaft gespeichert.")
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

# Alte Statusbezeichnung aus früheren Versionen kompatibel übernehmen.
if not df.empty and "status" in df.columns:
    df["status"] = (
        df["status"]
        .fillna("")
        .replace({"Erledigt": "Abgefahren", "": "Offen"})
    )

# Lieferanten vereinheitlichen:
# "Stadtwerke München EW" und "Stadtwerke München Wasser"
# werden als derselbe Lieferant behandelt.
if not df.empty and "lieferant" in df.columns:
    df["lieferant"] = df["lieferant"].apply(normalize_supplier_name)

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
status_values = ["Offen","Eingeplant","In Abfuhr","Teilweise abgefahren","Abgefahren"]
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

# Oben getrennte Kennzahlen für aktive und bereits abgefahrene Polter.
counter_view = view.copy()
counter_view["abfuhrstatus"] = counter_view.apply(berechne_abfuhrstatus, axis=1)

offen_counter = counter_view[counter_view["abfuhrstatus"] != "Abgefahren"].copy()
abgefahren_counter = counter_view[counter_view["abfuhrstatus"] == "Abgefahren"].copy()

# Erste Zeile: noch nicht abgefahren
st.markdown("### Noch nicht abgefahren")
m1, m2, m3 = st.columns(3)
m1.metric(
    "Polter",
    len(offen_counter),
    help="Anzahl der offenen und teilweise abgefahrenen Polter."
)
m2.metric(
    "RM aktuell",
    f"{offen_counter['menge_rm_aktuell'].fillna(0).sum():,.1f}"
)
m3.metric(
    "FM / EFm aktuell",
    f"{offen_counter['kubatur_fm_aktuell'].fillna(0).sum():,.1f}"
)

# Zweite Zeile: abgefahren
st.markdown("### Abgefahren")
m4, m5, m6 = st.columns(3)
m4.metric(
    "Polter",
    len(abgefahren_counter),
    help="Anzahl der vollständig abgefahrenen Polter."
)
m5.metric(
    "RM abgefahren",
    f"{(abgefahren_counter['menge_rm_original'].fillna(0) - abgefahren_counter['menge_rm_aktuell'].fillna(0)).sum():,.1f}"
)
m6.metric(
    "FM / EFm abgefahren",
    f"{(abgefahren_counter['kubatur_fm_original'].fillna(0) - abgefahren_counter['kubatur_fm_aktuell'].fillna(0)).sum():,.1f}"
)

left,right = st.columns([1.45,1])

with left:
    st.subheader("2. Karte")

    map_view = view[~view["status"].isin(["Abgefahren", "Erledigt"])].copy()
    pts = map_view.dropna(subset=["lat", "lon"]).copy()
    missing = len(map_view) - len(pts)

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

    abgefahren_count = int(view["status"].isin(["Abgefahren", "Erledigt"]).sum())
    if abgefahren_count:
        st.caption(f"{abgefahren_count} abgefahrene Polter sind ausgeblendet.")
    if missing:
        st.caption(
            f"{missing} aktive Polter haben noch keine numerischen GPS-Koordinaten. "
            "Diese können unter „Polter bearbeiten“ manuell gesetzt werden."
        )

    # Manueller Koordinatenmodus wird rechts bei "Polter bearbeiten" aktiviert.
    manual_pid = st.session_state.get("_coord_edit_polter_id")
    manual_key = f"coord_manual_enabled_{manual_pid}" if manual_pid is not None else None
    manual_enabled = bool(manual_key and st.session_state.get(manual_key, False))

    if manual_enabled:
        st.warning(
            "📍 Koordinatenmodus aktiv: Klicke auf der Karte auf den Standort des ausgewählten Polters. "
            "Der Punkt wird zunächst nur vorgemerkt und erst mit „Speichern“ übernommen."
        )

    # Karte IMMER anzeigen – auch wenn die aktuelle Auswahl noch keine GPS-Punkte hat.
    if not pts.empty:
        center = [float(pts["lat"].mean()), float(pts["lon"].mean())]
        zoom = 9
    else:
        # Falls im aktuellen Filter keine Koordinaten vorhanden sind, vorhandene
        # Polter aus der gesamten Datenbank als Orientierung verwenden.
        all_pts = df.dropna(subset=["lat", "lon"])
        if not all_pts.empty:
            center = [float(all_pts["lat"].mean()), float(all_pts["lon"].mean())]
            zoom = 8
        else:
            # Süddeutschland/Österreich als neutraler Startpunkt.
            center = [48.2, 11.5]
            zoom = 7

    mp = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles="OpenStreetMap"
    )

    for _, r in pts.iterrows():
        liste = str(r["holzliste"] or "-")
        los = str(r["los"] or "-")
        polter = str(r["polter_nr"] or "-")
        supplier_color = supplier_color_map.get(r["lieferant"], "#666666")

        pop = f"""
        <b>{r['lieferant']}</b><br>
        Frächter: {r['fraechter'] or '-'}<br>
        Bereitstellung: {r['bereitstellung']}<br>
        Liste: {liste}<br>
        Los: {los}<br>
        Polter: {polter}<br>
        Lagerort: {r['lagerort'] or '-'}<br>
        Holzart: {r['holzart'] or '-'} {r['sortiment'] or ''}<br>
        RM aktuell: {r['menge_rm_aktuell'] if pd.notna(r['menge_rm_aktuell']) else '-'}<br>
        FM aktuell: {r['kubatur_fm_aktuell'] if pd.notna(r['kubatur_fm_aktuell']) else '-'}<br>
        Status: {r['status']}
        """

        folium.CircleMarker(
            location=[float(r["lat"]), float(r["lon"])],
            radius=8,
            color=supplier_color,
            fill=True,
            fill_color=supplier_color,
            fill_opacity=0.95,
            weight=2,
            tooltip=(
                f"{r['lieferant']} · {r['bereitstellung']} · "
                f"Liste {liste} · Los {los} · Polter {polter}"
            ),
            popup=folium.Popup(pop, max_width=380),
        ).add_to(mp)

    # Einen vorgemerkten manuellen Punkt zusätzlich sichtbar machen.
    pending_pid = st.session_state.get("_manual_coord_pending_pid")
    pending_lat = st.session_state.get("_manual_coord_pending_lat")
    pending_lon = st.session_state.get("_manual_coord_pending_lon")
    if (
        manual_enabled
        and pending_pid == manual_pid
        and pending_lat is not None
        and pending_lon is not None
    ):
        folium.Marker(
            [float(pending_lat), float(pending_lon)],
            tooltip="Neuer Standort – noch nicht gespeichert",
            icon=folium.Icon(icon="map-marker")
        ).add_to(mp)

    map_state = st_folium(
        mp,
        use_container_width=True,
        height=610,
        returned_objects=[
            "last_clicked",
            "last_object_clicked",
            "last_object_clicked_tooltip"
        ]
    )

    if isinstance(map_state, dict):
        # --------------------------------------------------------
        # A) Manueller Koordinatenmodus:
        # beliebiger Kartenklick = neuer Standort für aktiven Polter
        # --------------------------------------------------------
        if manual_enabled and manual_pid is not None:
            click = map_state.get("last_clicked")
            if isinstance(click, dict):
                click_lat = click.get("lat")
                click_lng = click.get("lng")

                if click_lat is not None and click_lng is not None:
                    signature = (
                        f"{manual_pid}|{float(click_lat):.7f}|{float(click_lng):.7f}"
                    )
                    if signature != st.session_state.get("_last_manual_coord_click"):
                        st.session_state["_last_manual_coord_click"] = signature
                        st.session_state["_manual_coord_pending_pid"] = int(manual_pid)
                        st.session_state["_manual_coord_pending_lat"] = float(click_lat)
                        st.session_state["_manual_coord_pending_lon"] = float(click_lng)
                        st.rerun()

        # --------------------------------------------------------
        # B) Normalmodus:
        # vorhandenen Marker anklicken -> Polter bearbeiten auswählen
        # --------------------------------------------------------
        else:
            clicked_obj = map_state.get("last_object_clicked")
            clicked_tooltip = map_state.get("last_object_clicked_tooltip")

            clicked_id = None
            click_signature = None

            if isinstance(clicked_obj, dict):
                click_lat = clicked_obj.get("lat")
                click_lng = clicked_obj.get("lng")

                if click_lat is not None and click_lng is not None:
                    click_signature = f"{float(click_lat):.7f}|{float(click_lng):.7f}"

                    coord_match = pts[
                        (pts["lat"].astype(float) - float(click_lat)).abs() < 0.000001
                    ]
                    coord_match = coord_match[
                        (coord_match["lon"].astype(float) - float(click_lng)).abs() < 0.000001
                    ]

                    if len(coord_match) == 1:
                        clicked_id = int(coord_match.iloc[0]["id"])

            if clicked_id is None and clicked_tooltip:
                clicked = str(clicked_tooltip)
                click_signature = clicked

                tooltip_match = view[
                    view.apply(
                        lambda rr: (
                            str(rr["lieferant"]) in clicked
                            and str(rr["bereitstellung"]) in clicked
                            and f"Polter {rr['polter_nr']}" in clicked
                        ),
                        axis=1
                    )
                ]

                if len(tooltip_match) == 1:
                    clicked_id = int(tooltip_match.iloc[0]["id"])

            if clicked_id is not None:
                old_signature = st.session_state.get("_last_map_click_signature")
                if click_signature != old_signature:
                    st.session_state["_last_map_click_signature"] = click_signature
                    st.session_state["_map_selected_polter_id"] = clicked_id
                    st.rerun()

with right:
    st.subheader("3. Polter bearbeiten")

    # Vollständig abgefahrene Polter dürfen hier nicht mehr bearbeitet werden.
    # Für die Bearbeitung werden auch Polter OHNE GPS berücksichtigt.
    # Außerdem darf ein frisch importierter Polter nicht nur wegen eines
    # Statusfilters aus der Bearbeitung verschwinden.
    edit_view = df.copy()

    # Dieselben fachlichen Filter wie links anwenden – Statusfilter bewusst NICHT,
    # damit neu importierte/offene Polter sicher in "Polter bearbeiten" erscheinen.
    if supplier_choice:
        edit_view = edit_view[edit_view["lieferant"].isin(supplier_choice)]
    else:
        edit_view = edit_view.iloc[0:0]

    if fraechter_choice:
        edit_view = edit_view[edit_view["fraechter"].isin(fraechter_choice)]
    else:
        edit_view = edit_view.iloc[0:0]

    if wood_choice:
        edit_view = edit_view[edit_view["holzart"].isin(wood_choice)]

    if length_choice:
        edit_view = edit_view[edit_view["laenge_m"].isin(length_choice)]

    if search.strip():
        q = search.lower()
        mask = pd.Series(False, index=edit_view.index)
        for c in [
            "bereitstellung","lieferant","fraechter","holzliste","hab","los",
            "polter_nr","lagerort","waldort","bemerkung"
        ]:
            mask |= edit_view[c].fillna("").astype(str).str.lower().str.contains(q, regex=False)
        edit_view = edit_view[mask]

    edit_view["abfuhrstatus"] = edit_view.apply(berechne_abfuhrstatus, axis=1)
    edit_view = edit_view[edit_view["abfuhrstatus"] != "Abgefahren"].copy()

    if not edit_view.empty:
        ohne_gps_edit = int(edit_view[["lat","lon"]].isna().any(axis=1).sum())
        if ohne_gps_edit:
            st.caption(
                f"{ohne_gps_edit} Polter in der Bearbeitung haben noch keine GPS-Koordinaten "
                "und können über „Koordinaten manuell bearbeiten“ positioniert werden."
            )

        # Dropdown-Key jetzt IMMER eindeutig:
        # Liste + Los + Polter müssen sichtbar sein.
        opts = {}
        for _, r in edit_view.iterrows():
            liste = str(r["holzliste"] or "-")
            los = str(r["los"] or "-")
            polter = str(r["polter_nr"] or "-")
            label = (
                f"{r['lieferant']} · {r['fraechter'] or '-'} · "
                f"{r['bereitstellung']} · Liste {liste} · Los {los} · Polter {polter}"
            )
            opts[label] = int(r["id"])

        option_labels = list(opts.keys())

        # Falls ein Kartenpunkt angeklickt wurde, die zugehörige Dropdown-Beschriftung
        # direkt in den Selectbox-State schreiben.
        map_selected_id = st.session_state.get("_map_selected_polter_id")

        if map_selected_id is not None:
            target_label = next(
                (label for label in option_labels if opts[label] == int(map_selected_id)),
                None
            )
            if target_label is not None:
                st.session_state["edit_polter_selector"] = target_label

        # Falls der gespeicherte Selectbox-Wert durch Filter nicht mehr vorhanden ist,
        # sauber auf den ersten verfügbaren Polter zurückfallen.
        if (
            "edit_polter_selector" in st.session_state
            and st.session_state["edit_polter_selector"] not in option_labels
        ):
            st.session_state["edit_polter_selector"] = option_labels[0]

        selected = st.selectbox(
            "Polter auswählen",
            option_labels,
            key="edit_polter_selector"
        )
        pid = opts[selected]

        # Aktuellen Bearbeitungs-Polter für den manuellen Kartenmodus merken.
        st.session_state["_coord_edit_polter_id"] = int(pid)

        if map_selected_id is not None and pid == int(map_selected_id):
            st.session_state.pop("_map_selected_polter_id", None)

        row_match = df[df["id"] == pid]
        if row_match.empty:
            st.warning("Dieser Polter ist momentan nicht mehr verfügbar. Bitte die Seite neu laden.")
        else:
            row = row_match.iloc[0]

            def safe_num(value, default=0.0):
                try:
                    if pd.isna(value):
                        return float(default)
                    return float(value)
                except Exception:
                    return float(default)

            UMR_FACTOR = 1.5
            EPS = 0.0005

            old_rm = safe_num(row["menge_rm_aktuell"])
            old_fm = safe_num(row["kubatur_fm_aktuell"])
            original_rm = safe_num(row["menge_rm_original"])

            selected_pid_key = "_edit_selected_pid"
            previous_pid = st.session_state.get(selected_pid_key)

            # Bei Polter-Wechsel sämtliche alten Edit-Widgetwerte entfernen.
            # So hängen keine 0-Werte vom zuvor geöffneten Polter fest.
            if previous_pid != pid:
                for key in list(st.session_state.keys()):
                    if (
                        str(key).startswith("edit_rm_")
                        or str(key).startswith("edit_fm_")
                        or str(key).startswith("edit_status_")
                        or str(key).startswith("edit_note_")
                        or str(key).startswith("edit_lat_")
                        or str(key).startswith("edit_lon_")
                    ):
                        st.session_state.pop(key, None)
                st.session_state[selected_pid_key] = pid

            rm_key = f"edit_rm_{pid}"
            fm_key = f"edit_fm_{pid}"
            status_key = f"edit_status_{pid}"
            note_key = f"edit_note_{pid}"
            lat_key = f"edit_lat_{pid}"
            lon_key = f"edit_lon_{pid}"

            # Echte DB-Werte laden, nicht 0 als Default.
            if rm_key not in st.session_state:
                st.session_state[rm_key] = old_rm
            if fm_key not in st.session_state:
                st.session_state[fm_key] = old_fm
            if status_key not in st.session_state:
                st.session_state[status_key] = str(row["status"] or "Offen")
            if note_key not in st.session_state:
                st.session_state[note_key] = row["interne_notiz"] or ""
            if lat_key not in st.session_state:
                st.session_state[lat_key] = safe_num(row["lat"])
            if lon_key not in st.session_state:
                st.session_state[lon_key] = safe_num(row["lon"])

            def automatic_status(rm_value):
                rm_value = safe_num(rm_value)
                if rm_value <= EPS:
                    return "Abgefahren"
                if original_rm > 0 and rm_value < original_rm - EPS:
                    return "Teilweise abgefahren"

                current_saved = str(row["status"] or "Offen")
                if current_saved in ["Teilweise abgefahren", "Abgefahren", "Erledigt"]:
                    return "Offen"
                return current_saved

            def rm_changed():
                rm_value = safe_num(st.session_state.get(rm_key))
                st.session_state[fm_key] = round(rm_value / UMR_FACTOR, 3)
                st.session_state[status_key] = automatic_status(rm_value)

            def fm_changed():
                fm_value = safe_num(st.session_state.get(fm_key))
                rm_value = round(fm_value * UMR_FACTOR, 3)
                st.session_state[rm_key] = rm_value
                st.session_state[status_key] = automatic_status(rm_value)

            st.caption(
                "Umrechnung: 1,5 RM = 1 FM. Wenn du RM oder FM änderst, wird der andere "
                "Wert sofort vorgerechnet. Dauerhaft übernommen wird erst mit „Speichern“."
            )

            st.number_input(
                "Menge aktuell (RM)",
                min_value=0.0,
                step=0.1,
                format="%.3f",
                key=rm_key,
                on_change=rm_changed
            )

            st.number_input(
                "Festmeter aktuell (FM / EFm)",
                min_value=0.0,
                step=0.1,
                format="%.3f",
                key=fm_key,
                on_change=fm_changed
            )

            st.text_input(
                "Status (automatisch)",
                key=status_key,
                disabled=True
            )

            st.text_area("Interne Notiz", key=note_key)

            manual_coord_key = f"coord_manual_enabled_{pid}"
            manual_coord_enabled = st.checkbox(
                "Koordinaten manuell bearbeiten",
                key=manual_coord_key,
                help=(
                    "Aktivieren und anschließend links auf der Karte auf den Standort klicken. "
                    "Die Koordinaten werden erst mit „Speichern“ dauerhaft übernommen."
                )
            )

            # Falls links auf der Karte ein Punkt für genau diesen Polter gewählt wurde,
            # die Werte als noch nicht gespeicherte Vorschau in die Eingabefelder übernehmen.
            if (
                manual_coord_enabled
                and st.session_state.get("_manual_coord_pending_pid") == int(pid)
                and st.session_state.get("_manual_coord_pending_lat") is not None
                and st.session_state.get("_manual_coord_pending_lon") is not None
            ):
                pending_lat = float(st.session_state["_manual_coord_pending_lat"])
                pending_lon = float(st.session_state["_manual_coord_pending_lon"])

                # Vor den Widgets setzen, damit die neuen Werte sofort sichtbar sind.
                st.session_state[lat_key] = pending_lat
                st.session_state[lon_key] = pending_lon

                st.success(
                    f"📍 Neuer Kartenpunkt vorgemerkt: "
                    f"{pending_lat:.6f}, {pending_lon:.6f}. "
                    "Zum Übernehmen bitte auf „Speichern“ klicken."
                )

            st.caption("GPS-Koordinaten:")
            st.number_input("Breitengrad", format="%.7f", key=lat_key)
            st.number_input("Längengrad", format="%.7f", key=lon_key)

            rm_preview = safe_num(st.session_state.get(rm_key))
            fm_preview = safe_num(st.session_state.get(fm_key))
            status_preview = str(st.session_state.get(status_key, "Offen"))

            if status_preview == "Abgefahren":
                st.success(
                    f"Vorschau: {rm_preview:.3f} RM / {fm_preview:.3f} FM · "
                    "Status wird beim Speichern auf „Abgefahren“ gesetzt."
                )
            elif status_preview == "Teilweise abgefahren":
                st.warning(
                    f"Vorschau: {rm_preview:.3f} RM / {fm_preview:.3f} FM · "
                    "Status wird beim Speichern auf „Teilweise abgefahren“ gesetzt."
                )

            if st.button("Speichern", type="primary", key=f"save_polter_{pid}"):
                rm_save = safe_num(st.session_state.get(rm_key))
                fm_save = safe_num(st.session_state.get(fm_key))
                status_save = automatic_status(rm_save)
                note_save = st.session_state.get(note_key, "")
                lat_save = safe_num(st.session_state.get(lat_key))
                lon_save = safe_num(st.session_state.get(lon_key))

                update_polter(
                    pid,
                    rm_save,
                    fm_save,
                    status_save,
                    note_save,
                    None if lat_save == 0 else lat_save,
                    None if lon_save == 0 else lon_save
                )

                for k in [rm_key, fm_key, status_key, note_key, lat_key, lon_key]:
                    st.session_state.pop(k, None)
                st.session_state.pop(selected_pid_key, None)

                # Manuellen Koordinatenmodus nach erfolgreichem Speichern zurücksetzen.
                st.session_state.pop(f"coord_manual_enabled_{pid}", None)
                st.session_state.pop("_manual_coord_pending_pid", None)
                st.session_state.pop("_manual_coord_pending_lat", None)
                st.session_state.pop("_manual_coord_pending_lon", None)
                st.session_state.pop("_last_manual_coord_click", None)

                st.success(
                    f"Gespeichert: {rm_save:.3f} RM = {fm_save:.3f} FM · "
                    f"Status: {status_save}"
                )
                st.rerun()

            a, b, c = st.columns(3)
            a.metric("Original RM", f"{safe_num(row['menge_rm_original']):,.3f}")
            b.metric("Aktuell RM", f"{safe_num(row['menge_rm_aktuell']):,.3f}")
            c.metric("Aktuell FM", f"{safe_num(row['kubatur_fm_aktuell']):,.3f}")

            if pd.notna(row["lat"]) and pd.notna(row["lon"]):
                st.link_button(
                    "📍 Google Maps",
                    f"https://www.google.com/maps?q={row['lat']},{row['lon']}"
                )
            if row.get("map_link"):
                st.link_button("🗺️ Original-Kartenlink", row["map_link"])

            with st.expander("Einzelnen Polter löschen"):
                if st.checkbox("Löschen bestätigen", key=f"conf_{pid}"):
                    if st.button("Polter endgültig löschen", key=f"del_{pid}"):
                        delete_one(pid)
                        st.rerun()
    else:
        st.info("Keine offenen oder teilweise abgefahrenen Polter zum Bearbeiten vorhanden.")


st.subheader("4. Bereitstellungen")
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

st.subheader("5. Polterübersicht")

# Abfuhrstatus immer direkt aus Original- und aktueller Restmenge ableiten.
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

def style_partial_rows(row):
    return ["background-color: #fce5cd; color: #7f6000"] * len(row)

def style_completed_rows(row):
    return ["background-color: #d9ead3; color: #1f4e1f"] * len(row)

# ----------------------------------------------------------
# Tabelle 1: Noch nicht abgefahren
# ----------------------------------------------------------
nicht_abgefahren = view[view["abfuhrstatus"] == "Nicht abgefahren"].copy()
show_open = make_show(nicht_abgefahren)

st.markdown("### Noch nicht abgefahren")
st.caption("Hier stehen nur Polter, von denen noch keine Menge abgefahren wurde.")
if show_open.empty:
    st.info("Keine vollständig unangetasteten Polter in der aktuellen Filterauswahl.")
else:
    st.dataframe(
        show_open,
        use_container_width=True,
        hide_index=True,
        height=360
    )

# ----------------------------------------------------------
# Tabelle 2: Teilweise abgefahren
# ----------------------------------------------------------
teilweise = view[view["abfuhrstatus"] == "Teilweise abgefahren"].copy()
show_partial = make_show(teilweise)

st.markdown("### Teilweise abgefahren")
st.caption(
    "Diese Polter haben bereits eine reduzierte Restmenge. "
    "Sie werden orange markiert und bekommen automatisch den Status „Teilweise abgefahren“."
)
if show_partial.empty:
    st.info("Keine teilweise abgefahrenen Polter in der aktuellen Filterauswahl.")
else:
    st.dataframe(
        show_partial.style.apply(style_partial_rows, axis=1),
        use_container_width=True,
        hide_index=True,
        height=360
    )

# ----------------------------------------------------------
# Tabelle 3: Abgefahren
# ----------------------------------------------------------
abgefahren = view[view["abfuhrstatus"] == "Abgefahren"].copy()
show_done = make_show(abgefahren)

st.markdown("### Abgefahren")
st.caption(
    "Diese Polter haben 0 RM Restmenge, bekommen automatisch den Status „Abgefahren“ "
    "und werden grün dargestellt. Auf der Karte erscheinen sie nicht mehr."
)
if show_done.empty:
    st.info("Keine vollständig abgefahrenen Polter in der aktuellen Filterauswahl.")
else:
    st.dataframe(
        show_done.style.apply(style_completed_rows, axis=1),
        use_container_width=True,
        hide_index=True,
        height=320
    )

# Export enthält alle drei Gruppen.
export_all = make_show(view)
st.download_button(
    "⬇️ Gesamte aktuelle Auswahl als CSV exportieren",
    export_all.to_csv(index=False).encode("utf-8-sig"),
    "polter_export.csv",
    "text/csv"
)
