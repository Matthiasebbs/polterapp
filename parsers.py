
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
        "quelle_datei": filename, "bereitstellung": "", "lieferant": "",
        "vertragsnummer": "", "datum": "", "holzliste": "", "hab": "", "los": "",
        "polter_nr": "", "holzart": "", "sortiment": "", "laenge_m": None,
        "stueck": None, "menge_rm_original": None, "menge_rm_aktuell": None,
        "kubatur_fm_original": None, "kubatur_fm_aktuell": None, "einheit": "",
        "lat": None, "lon": None, "waldort": "", "lagerort": "", "bemerkung": "",
        "ansprechpartner": "", "zertifikat": "", "map_link": ""
    }

def qty(r, rm=None, fm=None):
    r["menge_rm_original"] = rm
    r["menge_rm_aktuell"] = rm
    r["kubatur_fm_original"] = fm
    r["kubatur_fm_aktuell"] = fm
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
    return best, best_name, attempts
