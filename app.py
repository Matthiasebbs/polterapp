
import io
import re
import time
import base64
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
    """
    Robuster Parser für Bereitstellungen der WBV Holzhandels GmbH / Wasserburg.

    Unterstützt insbesondere unterschiedliche Sortimentsschreibweisen wie:
      FI IS 3,00 m
      FI IS N 3,00 m
      FI IS N 4,00 m

    Jeder Eintrag aus Holzliste / Los / Polter bleibt ein eigener Polter.
    """
    text = "\n".join(pages)
    if "WBV Holzhandels GmbH" not in text:
        return []

    nr = re.search(r"Bereitstellung\s+Nr\.\s*([A-Z0-9\-]+)", text, re.I)
    if not nr:
        return []

    date = re.search(
        r"(?:Lief/Leist-Dat|Belegdatum)\s+(\d{2}\.\d{2}\.\d{4})",
        text
    )
    contract = re.search(r"Vertr\.-Nr\.\s*(.+)", text)

    # Beispiel:
    # 2605170/1/1 FI IS N 3,00 m 30,528 RM 21,370 FM
    #
    # Nach der Holzart dürfen ein oder mehrere Sortimentsbestandteile stehen.
    # Die Länge ist der eindeutige Abschluss des Sortimentsblocks.
    pat = re.compile(
        r"(?m)^"
        r"(?P<key>\d{6,9}/\d+/\d+)\s+"
        r"(?P<holzart>[A-Za-zÄÖÜäöüß]+)\s+"
        r"(?P<sortiment>.+?)\s+"
        r"(?P<laenge>[\d,]+)\s*m\s+"
        r"(?P<rm>[\d,]+)\s*RM\s+"
        r"(?P<fm>[\d,]+)\s*FM\s*$"
    )

    mm = list(pat.finditer(text))
    rows = []

    for i, m in enumerate(mm):
        seg = text[
            m.start():
            (mm[i + 1].start() if i + 1 < len(mm) else len(text))
        ]

        gps = re.search(
            r"(\d{1,2}\.\d{4,8})°?\s*N,\s*"
            r"(\d{1,3}\.\d{4,8})°?\s*E",
            seg
        )

        key_parts = m.group("key").split("/")
        holzliste_nr, los_nr, polter_nr = key_parts

        sortiment = re.sub(r"\s+", " ", m.group("sortiment")).strip()

        r = empty(filename)
        r.update(
            bereitstellung=nr.group(1),
            lieferant="WBV Holzhandels GmbH",
            vertragsnummer=contract.group(1).strip() if contract else "",
            datum=date.group(1) if date else "",

            # Für eindeutige Polterzuordnung separat speichern.
            holzliste=holzliste_nr,
            los=los_nr,
            polter_nr=polter_nr,

            holzart=m.group("holzart").strip(),
            sortiment=sortiment,
            laenge_m=n(m.group("laenge")),
            einheit="RM / FM",

            lat=float(gps.group(1)) if gps else None,
            lon=float(gps.group(2)) if gps else None,

            waldort=line_value(seg, "Waldort"),
            lagerort=line_value(seg, "Lagerort"),
            bemerkung=line_value(seg, "Bemerkung"),
            ansprechpartner=line_value(seg, "Ansprechpartner"),
            zertifikat=line_value(seg, "Zertifikat")
        )

        # Wie bisher wird die RM-Menge als gelesener Ausgangswert verwendet.
        # qty() übernimmt die vorhandene Mengenlogik der App.
        qty(
            r,
            n(m.group("rm")),
            n(m.group("fm"))
        )
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

    Unterstützt verschiedene FBG-Ausgaben, u. a.:
      - 18,700 Rm m.R. 9,350
      - 9,4 Rm m.R. 6,11 Fm
      - reine FM-Zeilen
      - GPS in eigener Zeile direkt VOR oder NACH der Polterzeile
      - Polternummern mit Punkt
      - mehrere Positionen desselben Polters

    Zusammengefasst wird NUR bei identischer Kombination:
    Liste + Los + Polternummer.
    """
    first = pages[0] if pages else ""
    if "Bereitstellung BA-" not in first:
        return []

    nr = re.search(r"Bereitstellung\s+(BA-\d+)", first, re.I)
    if not nr:
        return []

    # Neue und ältere FBG-Layouts unterstützen.
    date = (
        re.search(r"ausgegeben am:\s*([^\n]+)", first, re.I)
        or re.search(r"Datum:\s*(\d{2}\.\d{2}\.\d{4})", first, re.I)
    )

    # Vertrag kann im PDF durch die Textextraktion auf zwei Zeilen getrennt sein.
    contract = re.search(r"Vertrag:\s*([^\n]+)", first, re.I)
    if contract and not contract.group(1).strip():
        contract = None

    ap = (
        re.search(r"Ihr Ansprechpartner:\s*([^\n]+)", first, re.I)
        or re.search(r"Ansprechpartner:\s*([^\n]+)", first, re.I)
    )
    revier = re.search(r"Revier:\s*([^\n]+)", first, re.I)

    lines = [re.sub(r"\s+", " ", x.strip()) for x in first.splitlines() if x.strip()]

    # Beispiele:
    # 260751 861 1.062 Er-IL-K-4.0 18,700 Rm m.R. 9,350
    # 260403 521 308 Bu-L-B-3.4-41.0 1 0,427 Fm o.R. 0,427
    # 263251 166 808 Fi-XK-FK-2.0 0 9,4 Rm m.R. 6,11 Fm
    #
    # Der zweite Mengenwert kann am Ende optional nochmals "Fm" tragen.
    row_re = re.compile(
        r"^(?P<liste>\d+)\s+"
        r"(?P<los>\d+)\s+"
        r"(?P<polter>\d+(?:\.\d+)?)\s*"
        r"(?P<sort>[A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß0-9.]+)+)\s+"
        r"(?:(?P<stck>\d+)\s+)?"
        r"(?P<menge>\d+(?:,\d+)?)\s+"
        r"(?P<unit>Rm|Fm)\s*"
        r"(?:(?:m\.R\.|o\.R\.)\s*)?"
        # Ältere FBG-Ausgaben enden direkt nach "Rm m.R.".
        # Neuere Ausgaben enthalten zusätzlich z. B. "6,11 Fm".
        r"(?:(?P<kubatur>\d+(?:,\d+)?)(?:\s*Fm)?)?"
        r"(?:\s+(?P<lat>\d{1,2},\d{5,8}))?"
        r"(?:\s+(?P<lon>\d{1,3},\d{5,8}))?"
        r"$",
        re.I
    )

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

        lat = n(d.get("lat"))
        lon = n(d.get("lon"))

        # GPS-Zeile direkt vor der Polterzeile.
        if (lat is None or lon is None) and pending_gps is not None:
            lat = pending_gps[0] if lat is None else lat
            lon = pending_gps[1] if lon is None else lon

        # Fallback: GPS-Zeile unmittelbar nach der Polterzeile.
        if lat is None or lon is None:
            for nxt in lines[idx + 1: idx + 5]:
                gp = gps_pair_re.match(nxt)
                if gp:
                    lat = n(gp.group("lat")) if lat is None else lat
                    lon = n(gp.group("lon")) if lon is None else lon
                    break

        pending_gps = None

        amount = n(d["menge"])
        unit = d["unit"].lower()

        # App-Regel: nur EINE Mengenbasis aus dem PDF verwenden.
        # 1,5 RM = 1 FM.
        if unit == "rm":
            rm_val = amount
            fm_val = round(float(amount) / 1.5, 3) if amount is not None else None
        else:
            fm_val = amount
            rm_val = round(float(amount) * 1.5, 3) if amount is not None else None

        sortiment = d["sort"]
        parts = sortiment.split("-")

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

    grouped = {}
    for rr in raw_rows:
        # NUR vollständig identische Polter zusammenfassen.
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

    Interner Bereitstellungsname bleibt eindeutig:
      64.4, Kaindl_Ndh_26_01

    Für die kurze Anzeige in "Polter bearbeiten" werden zusätzlich gespeichert:
      hab = laufende Poltererfassungs-Nr. (z. B. 1)
      los = verkürzte Losnummer ohne "Kaindl_" (z. B. Ndh_26_01)
      bemerkung enthält die originale EFm-Menge dieses Polters.

    Beispiel-Anzeige:
      Törring 64,4, Ndh_26_01 - 1.1,23.8
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
    short_los = re.sub(r"^Kaindl_", "", losnr, flags=re.I)
    menge_los_efm_raw = los_efm.group(1).strip() if los_efm else ""
    menge_los_efm_dot = menge_los_efm_raw.replace(",", ".")

    bereitstellungsname = (
        f"{menge_los_efm_dot}, {losnr}"
        if menge_los_efm_dot
        else losnr
    )

    # Die Überschrift jeder Poltererfassung enthält:
    # Poltererfassung - <laufend>. <Polternummer>, <EFm>
    heads = list(re.finditer(
        r"Poltererfassung\s*-\s*(\d+)\.\s*(\d+),\s*([\d.,]+)",
        text,
        re.I
    ))

    rows = []
    for i, h in enumerate(heads):
        seg = text[h.start():(heads[i+1].start() if i+1 < len(heads) else len(text))]
        item_no = h.group(1)
        heading_polter = h.group(2)
        heading_efm = h.group(3).replace(",", ".")

        polter_m = re.search(r"Polternummer\s+(\d+)", seg)
        efm = re.search(r"Menge \[EFm\]\s+([\d.,]+)", seg)
        rm = re.search(r"Menge \[Rm\]\s+([\d.,]+)", seg)

        polter_nr = polter_m.group(1) if polter_m else heading_polter
        efm_original = n(efm.group(1)) if efm else n(heading_efm)
        rm_original = n(rm.group(1)) if rm else None

        r = empty(filename)
        r.update(
            bereitstellung=bereitstellungsname,
            lieferant="Unternehmensgruppe Toerring-Jettenbach",
            datum=datum.group(1).strip() if datum else "",
            holzliste=losnr,
            hab=item_no,
            los=short_los,
            polter_nr=polter_nr,
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
                f"Toerring_EFm_original={efm_original if efm_original is not None else heading_efm}; "
                "Keine numerischen GPS-Koordinaten im PDF."
            ),
            zertifikat=zert.group(1).strip() if zert else ""
        )

        # Mengenregel der App bleibt unverändert: RM bevorzugt, FM daraus mit Faktor 1,5.
        qty(r, rm_original, efm_original)
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

    Unterstützt auch automatisch erzeugte Kopie-Suffixe wie:
      "... Hunglinger (1).pdf" -> Hunglinger
      "... Hunglinger (2).pdf" -> Hunglinger
    """
    stem = Path(filename).stem.strip()
    stem = re.sub(r"\s+", " ", stem)

    # Windows/Browser-Kopie-Suffix am Dateiende entfernen.
    # z. B. "Hunglinger (1)" -> "Hunglinger"
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem).strip()

    # Name nach dem letzten Leerzeichen:
    # Kunde_26-0182 Astner -> Astner
    # Lieferschein_... Hunglinger -> Hunglinger
    m = re.search(r"\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß&.'-]*)$", stem)
    if m:
        carrier = m.group(1).strip()
        if carrier:
            return carrier

    # Name nach letztem Bindestrich:
    # ...-Soller / ...-Ammer / ...-G&H
    m = re.search(r"-([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß&.'-]*)$", stem)
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
    if not fraechter:
        fraechter = "Nicht angegeben"
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

st.markdown("""
<style>
/* ==========================================================
   FORST-DESIGN
   Ruhig, robust und professionell:
   Tannengrün + Moosgrün + warme Holz-/Sandtöne
   ========================================================== */

:root {
    --forest-900: #173426;
    --forest-800: #214735;
    --forest-700: #2f5a43;
    --moss-500: #708b61;
    --moss-100: #e8eee5;
    --wood-500: #9a7550;
    --sand-100: #f4f1e9;
    --paper: #fbfcf9;
    --ink: #1f2d25;
    --muted: #6b776f;
    --line: rgba(36, 73, 53, .14);
}

/* Hauptfläche */
.stApp {
    background:
        linear-gradient(rgba(251,252,249,.965), rgba(251,252,249,.965)),
        repeating-linear-gradient(
            90deg,
            rgba(47,90,67,.018) 0px,
            rgba(47,90,67,.018) 1px,
            transparent 1px,
            transparent 32px
        );
    color: var(--ink);
}

/* Überschriften */
h1, h2, h3 {
    color: var(--forest-900);
    letter-spacing: -0.025em;
}
h1 {
    font-weight: 760 !important;
}
h2, h3 {
    font-weight: 700 !important;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background:
        linear-gradient(180deg, #edf2eb 0%, #f5f6f0 48%, #eee9df 100%);
    border-right: 1px solid var(--line);
}
section[data-testid="stSidebar"] > div {
    padding-top: 1rem;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(255,255,255,.76);
    border: 1px solid var(--line);
    border-radius: 12px;
    box-shadow: 0 3px 12px rgba(23,52,38,.035);
}
section[data-testid="stSidebar"] .stMultiSelect [data-baseweb="select"] > div,
section[data-testid="stSidebar"] .stTextInput input {
    border-radius: 9px;
    border-color: rgba(47,90,67,.18);
}
section[data-testid="stSidebar"] .stButton > button {
    width: 100%;
}

/* Buttons: Forstgrün, nicht knallig */
.stButton > button[kind="primary"],
button[data-testid="stBaseButton-primary"] {
    background: var(--forest-700) !important;
    border-color: var(--forest-700) !important;
    color: white !important;
    border-radius: 9px !important;
}
.stButton > button {
    border-radius: 9px !important;
    font-weight: 650 !important;
}
.stButton > button:hover {
    border-color: var(--forest-700) !important;
}

/* Eingabefelder */
.stTextInput input,
.stNumberInput input,
.stTextArea textarea,
div[data-baseweb="select"] > div {
    border-radius: 9px !important;
}

/* Kennzahlen wie kleine Bestandskarten */
div[data-testid="stMetric"] {
    background: rgba(255,255,255,.78);
    border: 1px solid var(--line);
    border-left: 4px solid var(--moss-500);
    border-radius: 11px;
    padding: .72rem .85rem;
    box-shadow: 0 2px 8px rgba(23,52,38,.025);
}
div[data-testid="stMetricLabel"] {
    color: var(--muted);
}
div[data-testid="stMetricValue"] {
    color: var(--forest-900);
}

/* Tabellen / Dataframes */
div[data-testid="stDataFrame"] {
    border: 1px solid var(--line);
    border-radius: 11px;
    overflow: hidden;
}

/* Expander */
details {
    border-radius: 10px !important;
}

/* Tabs */
button[data-baseweb="tab"] {
    font-weight: 650;
}

/* Sidebar Branding */
.filter-kicker {
    display: inline-block;
    font-size: .72rem;
    font-weight: 800;
    color: #f7f5ee;
    background: var(--forest-800);
    text-transform: uppercase;
    letter-spacing: .11em;
    padding: .3rem .52rem;
    border-radius: 6px;
    margin-bottom: .55rem;
}
.filter-title {
    font-size: 1.42rem;
    font-weight: 780;
    color: var(--forest-900);
    line-height: 1.1;
    margin-bottom: .15rem;
}
.filter-subtitle {
    color: var(--muted);
    font-size: .84rem;
    line-height: 1.35;
    margin-bottom: .8rem;
}
.filter-summary {
    background: var(--moss-100);
    color: var(--forest-800);
    border: 1px solid rgba(47,90,67,.16);
    border-left: 4px solid var(--moss-500);
    border-radius: 9px;
    padding: .58rem .72rem;
    font-size: .84rem;
    font-weight: 600;
    margin: .4rem 0 .7rem 0;
}

/* Kleine Forst-Kopfleiste */
.forest-header {
    display: flex;
    align-items: center;
    gap: .8rem;
    background: linear-gradient(105deg, #173426 0%, #2f5a43 72%, #607a56 100%);
    color: white;
    padding: .9rem 1.1rem;
    border-radius: 13px;
    margin: .1rem 0 1rem 0;
    box-shadow: 0 4px 14px rgba(23,52,38,.10);
}
.forest-header-icon {
    font-size: 1.65rem;
    line-height: 1;
}
.forest-header-title {
    font-size: 1.03rem;
    font-weight: 750;
    letter-spacing: .01em;
}
.forest-header-sub {
    font-size: .78rem;
    opacity: .80;
    margin-top: .08rem;
}

/* Kompakte Dropdowns statt Chip-Wolken */
section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
    min-height: 42px !important;
    background: rgba(255,255,255,.92) !important;
    border: 1px solid rgba(47,90,67,.17) !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] div[data-baseweb="select"] > div:hover {
    border-color: rgba(47,90,67,.35) !important;
}
section[data-testid="stSidebar"] label {
    color: #31483a !important;
    font-weight: 600 !important;
}

/* Listen: dieselbe grüne Forstlinie wie im Rest der App */
div[data-testid="stDataFrame"] {
    border: 1px solid rgba(49,92,70,.15) !important;
    border-top: 3px solid #315C46 !important;
    border-radius: 10px !important;
    overflow: hidden;
    box-shadow: 0 2px 8px rgba(23,52,38,.025);
}

/* Polter bearbeiten: dezente grüne Eingabefelder */
div[data-testid="stNumberInput"] input,
div[data-testid="stTextInput"] input,
div[data-testid="stTextArea"] textarea {
    background: #F3F7F1 !important;
    border: 1px solid rgba(49,92,70,.18) !important;
    color: #20382A !important;
}
div[data-testid="stNumberInput"] input:focus,
div[data-testid="stTextInput"] input:focus,
div[data-testid="stTextArea"] textarea:focus {
    border-color: #6F8A72 !important;
    box-shadow: 0 0 0 1px rgba(49,92,70,.12) !important;
}
div[data-testid="stNumberInput"] button {
    background: #E8F0E5 !important;
    color: #315C46 !important;
    border-color: rgba(49,92,70,.12) !important;
}

/* Polter auswählen: identische dezente Forst-Optik wie die Eingabefelder */
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background: #F3F7F1 !important;
    border: 1px solid rgba(49,92,70,.18) !important;
    color: #20382A !important;
    border-radius: 9px !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {
    border-color: #6F8A72 !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within {
    border-color: #6F8A72 !important;
    box-shadow: 0 0 0 1px rgba(49,92,70,.12) !important;
}

/* 4. Bereitstellungen – optisch wie die übrigen Tabellenüberschriften */
div[data-testid="stExpander"]:has(.bereitstellungen-marker) {
    border: 1px solid rgba(49,92,70,.15) !important;
    border-top: 3px solid #315C46 !important;
    border-radius: 10px !important;
    background: #FAFCF8 !important;
    box-shadow: 0 2px 8px rgba(23,52,38,.025);
    overflow: hidden;
}
div[data-testid="stExpander"]:has(.bereitstellungen-marker) summary {
    min-height: 58px !important;
    padding: .75rem 1rem !important;
}
div[data-testid="stExpander"]:has(.bereitstellungen-marker) summary p {
    color: #173426 !important;
    font-size: 1.5rem !important;
    line-height: 1.25 !important;
    font-weight: 700 !important;
    letter-spacing: -0.025em !important;
}
div[data-testid="stExpander"]:has(.bereitstellungen-marker) summary svg {
    color: #315C46 !important;
}

/* Private Bauernpartie – Dialog im bestehenden Forststil */
div[role="dialog"] {
    border-radius: 14px !important;
}
div[role="dialog"] div[data-testid="stTextInput"] input,
div[role="dialog"] div[data-testid="stNumberInput"] input,
div[role="dialog"] div[data-testid="stTextArea"] textarea {
    background: #F3F7F1 !important;
    border-color: rgba(49,92,70,.20) !important;
}
</style>
""", unsafe_allow_html=True)

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
    """
    Neue Polter effizient speichern.
    Supabase wird beim Import nur einmal gelesen und neue Zeilen werden gesammelt
    eingefügt. Das macht besonders größere PDFs deutlich schneller.
    """
    if not rows:
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    existing = df_all()

    keycols = ["bereitstellung","holzliste","hab","los","polter_nr","quelle_datei"]
    existing_keys = set()

    if not existing.empty:
        for _, er in existing.iterrows():
            existing_keys.add(tuple(
                str(er.get(c) if pd.notna(er.get(c)) else "")
                for c in keycols
            ))

    new_rows = []
    for row in rows:
        r = dict(row)
        key = tuple(str(r.get(c) or "") for c in keycols)

        if key in existing_keys:
            continue

        r["lieferant"] = normalize_supplier_name(r.get("lieferant", ""))
        if not str(r.get("fraechter", "") or "").strip():
            r["fraechter"] = "Nicht angegeben"

        r["status"] = "Offen"
        r["interne_notiz"] = ""
        r["importiert_am"] = now
        r["geaendert_am"] = now

        # Nur bekannte DB-Felder senden.
        r = {c: r.get(c) for c in FIELDS}
        new_rows.append(r)
        existing_keys.add(key)

    if not new_rows:
        return 0

    if SB:
        # Eine einzige Netzwerkoperation statt einer pro Polter.
        SB.table("polter").insert(new_rows).execute()
    else:
        vals = [[r.get(c) for c in FIELDS] for r in new_rows]
        CON.executemany(
            f"INSERT INTO polter ({','.join(FIELDS)}) VALUES ({','.join(['?']*len(FIELDS))})",
            vals
        )
        CON.commit()

    return len(new_rows)

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


# ============================================================
# PRIVATE BAUERNPARTIEN / MANUELLE BEREITSTELLUNG
# ============================================================

PRIVATE_SOURCE = "Manuell erstellt · Private Bauernpartie"


def private_supplier_code(supplier):
    """
    Erste 4 Buchstaben des Lieferanten, in Großbuchstaben.
    Leerzeichen, Zahlen und Sonderzeichen werden für den Code ignoriert.
    Beispiel: "Huber Franz" -> HUBE
    """
    clean = "".join(ch for ch in str(supplier).strip() if ch.isalpha())
    return clean[:4].upper()


def next_private_polter_number(supplier):
    """
    Je Lieferant eigener Nummernkreis:
      HUBE1, HUBE2, HUBE3 ...
      MAIE1, MAIE2 ...
    """
    code = private_supplier_code(supplier)
    if len(code) < 4:
        return ""

    existing = df_all()
    highest = 0

    if not existing.empty:
        mask = (
            existing["lieferant"].fillna("").astype(str).str.strip().str.casefold()
            == str(supplier).strip().casefold()
        )
        if "quelle_datei" in existing.columns:
            mask &= (
                existing["quelle_datei"]
                .fillna("")
                .astype(str)
                .eq(PRIVATE_SOURCE)
            )

        for value in existing.loc[mask, "polter_nr"].fillna("").astype(str):
            m = re.fullmatch(rf"{re.escape(code)}(\d+)", value.strip(), re.I)
            if m:
                highest = max(highest, int(m.group(1)))

    return f"{code}{highest + 1}"


def private_map_center():
    """Sinnvoller Kartenstart anhand bereits vorhandener Polter."""
    existing = df_all()
    if not existing.empty:
        pts = existing.dropna(subset=["lat", "lon"]).copy()
        if not pts.empty:
            return [float(pts["lat"].mean()), float(pts["lon"].mean())], 9
    return [48.2, 11.5], 7


def clear_private_dialog_state():
    keys = [
        "private_supplier", "private_carrier", "private_rm", "private_fm",
        "private_note", "private_lat", "private_lon",
        "_private_last_map_click"
    ]
    for key in keys:
        st.session_state.pop(key, None)


@st.dialog("Private Bereitstellung erstellen", width="large")
def private_bereitstellung_dialog():
    st.markdown(
        """
        <div style="
            background:#EEF4EA;
            border-left:4px solid #315C46;
            border-radius:9px;
            padding:.7rem .85rem;
            margin-bottom:.8rem;
            color:#294735;
        ">
            <b>Private Bauernpartie</b><br>
            Lieferant und Mengen eingeben, anschließend den Lagerplatz direkt auf der Karte markieren.
        </div>
        """,
        unsafe_allow_html=True
    )

    supplier = st.text_input(
        "Lieferant",
        key="private_supplier",
        placeholder="z. B. Huber Franz"
    )

    polter_nr = next_private_polter_number(supplier) if supplier.strip() else ""
    if supplier.strip() and len(private_supplier_code(supplier)) < 4:
        st.warning("Der Lieferantenname muss mindestens 4 Buchstaben enthalten.")

    st.text_input(
        "Polternummer",
        value=polter_nr,
        disabled=True,
        help="Wird automatisch aus den ersten 4 Buchstaben des Lieferanten und einer fortlaufenden Nummer erzeugt."
    )

    carrier = st.text_input(
        "Frächter",
        key="private_carrier",
        placeholder="Frächter eingeben"
    )

    # ---------------- Mengen ----------------
    if "private_rm" not in st.session_state:
        st.session_state["private_rm"] = 0.0
    if "private_fm" not in st.session_state:
        st.session_state["private_fm"] = 0.0

    def private_rm_changed():
        rm = float(st.session_state.get("private_rm", 0.0) or 0.0)
        st.session_state["private_fm"] = round(rm / 1.5, 3)

    def private_fm_changed():
        fm = float(st.session_state.get("private_fm", 0.0) or 0.0)
        st.session_state["private_rm"] = round(fm * 1.5, 3)

    c1, c2 = st.columns(2)
    with c1:
        st.number_input(
            "Menge RM",
            min_value=0.0,
            step=0.1,
            format="%.3f",
            key="private_rm",
            on_change=private_rm_changed
        )
    with c2:
        st.number_input(
            "Menge FM",
            min_value=0.0,
            step=0.1,
            format="%.3f",
            key="private_fm",
            on_change=private_fm_changed
        )

    st.caption("Umrechnung wie in der gesamten App: **1,5 RM = 1 FM**.")

    note = st.text_area(
        "Notiz",
        key="private_note",
        placeholder="Optionale Notiz zur Bauernpartie …"
    )

    # ---------------- Standort ----------------
    st.markdown("#### 📍 Standort")
    st.caption("Klicke einmal auf den Lagerplatz in der Karte.")

    center, zoom = private_map_center()

    selected_lat = st.session_state.get("private_lat")
    selected_lon = st.session_state.get("private_lon")

    # Wenn bereits ein Punkt gewählt wurde, Karte dort zentrieren.
    if selected_lat is not None and selected_lon is not None:
        center = [float(selected_lat), float(selected_lon)]
        zoom = 14

    private_map = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles="OpenStreetMap"
    )

    if selected_lat is not None and selected_lon is not None:
        folium.Marker(
            [float(selected_lat), float(selected_lon)],
            tooltip="Gewählter Standort",
            icon=folium.DivIcon(
                html=polter_pin_html("#315C46", size=42),
                icon_size=(42, 51),
                icon_anchor=(21, 51),
                class_name="polter-div-icon"
            )
        ).add_to(private_map)

    private_map_state = st_folium(
        private_map,
        use_container_width=True,
        height=390,
        key="private_bauernpartie_map",
        returned_objects=["last_clicked"]
    )

    if isinstance(private_map_state, dict):
        click = private_map_state.get("last_clicked")
        if isinstance(click, dict):
            lat = click.get("lat")
            lng = click.get("lng")
            if lat is not None and lng is not None:
                signature = f"{float(lat):.7f}|{float(lng):.7f}"
                if signature != st.session_state.get("_private_last_map_click"):
                    st.session_state["_private_last_map_click"] = signature
                    st.session_state["private_lat"] = float(lat)
                    st.session_state["private_lon"] = float(lng)
                    # Dialog neu zeichnen, damit der Marker sofort sichtbar wird.
                    st.rerun(scope="fragment")

    selected_lat = st.session_state.get("private_lat")
    selected_lon = st.session_state.get("private_lon")

    if selected_lat is not None and selected_lon is not None:
        st.success(
            f"Standort gewählt: {float(selected_lat):.6f}, {float(selected_lon):.6f}"
        )
    else:
        st.info("Noch kein Standort gewählt.")

    # ---------------- Speichern ----------------
    st.markdown("---")
    can_save = (
        len(private_supplier_code(supplier)) >= 4
        and bool(polter_nr)
        and float(st.session_state.get("private_rm", 0.0) or 0.0) > 0
        and selected_lat is not None
        and selected_lon is not None
    )

    if st.button(
        "🌲 Private Bereitstellung speichern",
        type="primary",
        use_container_width=True,
        disabled=not can_save
    ):
        # Nummer direkt vor dem Speichern noch einmal frisch bestimmen.
        final_polter_nr = next_private_polter_number(supplier)

        rm_value = float(st.session_state.get("private_rm", 0.0) or 0.0)
        fm_value = round(rm_value / 1.5, 3)

        row = empty(PRIVATE_SOURCE)
        row.update(
            bereitstellung=f"Privat · {supplier.strip()}",
            lieferant=supplier.strip(),
            fraechter=carrier.strip() or "Nicht angegeben",
            holzliste="Privat",
            hab="",
            los="",
            polter_nr=final_polter_nr,
            einheit="RM / FM",
            lat=float(selected_lat),
            lon=float(selected_lon),
            bemerkung=note.strip(),
            lagerort="Manuell gesetzter Standort"
        )

        # RM ist die gemeinsame Mengenbasis; FM wird mit Faktor 1,5 berechnet.
        qty(row, rm=rm_value, fm=None)

        added = save_rows([row])

        if added == 1:
            st.success(f"Polter {final_polter_nr} wurde gespeichert.")
            clear_private_dialog_state()
            st.rerun()
        else:
            st.error(
                "Der Polter konnte nicht gespeichert werden. "
                "Bitte die Eingaben prüfen und erneut versuchen."
            )


st.title("🪵 Polter-Zentrale")
st.markdown("""
<div class="forest-header">
    <div class="forest-header-icon">🌲</div>
    <div>
        <div class="forest-header-title">Digitale Polterverwaltung</div>
        <div class="forest-header-sub">Bereitstellungen · Bestände · Abfuhr · Standorte</div>
    </div>
</div>
""", unsafe_allow_html=True)

st.caption("Alle zugesandten Bereitstellungsformate · Lieferanten-/Frächter-/Längenfilter · Mengenänderung · 1,5 RM = 1 FM · Löschen nach Abfuhr")

if SB:
    st.success("☁️ LIVE: Supabase verbunden – Polter, Mengen, Status und Notizen werden dauerhaft gespeichert.")
else:
    st.warning("🧪 Lokaler Testmodus. Für die veröffentlichte Web-App Supabase verbinden.")

# ============================================================
# MANUELLES BACKUP
# ============================================================
with st.container(border=True):
    st.subheader("💾 Datensicherung")
    st.caption(
        "Erstellt auf Knopfdruck eine vollständige Sicherung aller Polterdaten. "
        "Die ZIP-Datei kannst du direkt auf deinem Firmenlaptop speichern."
    )

    if st.button("Backup jetzt erstellen", key="create_manual_backup"):
        backup_df = df_all().copy()

        if backup_df.empty:
            st.warning("Es sind derzeit keine Polterdaten vorhanden, die gesichert werden können.")
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

            csv_bytes = backup_df.to_csv(index=False).encode("utf-8-sig")
            json_bytes = backup_df.where(pd.notna(backup_df), None).to_json(
                orient="records",
                force_ascii=False,
                indent=2
            ).encode("utf-8")

            info_text = (
                "Polter-Zentrale Datensicherung\n"
                f"Erstellt am: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"Anzahl Polter-Datensätze: {len(backup_df)}\n"
                "Enthalten: CSV + JSON\n"
            ).encode("utf-8")

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(f"polter_backup_{timestamp}.csv", csv_bytes)
                zf.writestr(f"polter_backup_{timestamp}.json", json_bytes)
                zf.writestr("backup_info.txt", info_text)

            st.session_state["_manual_backup_bytes"] = zip_buffer.getvalue()
            st.session_state["_manual_backup_name"] = f"Polter_Zentrale_Backup_{timestamp}.zip"
            st.session_state["_manual_backup_count"] = len(backup_df)

    if st.session_state.get("_manual_backup_bytes"):
        st.success(
            f"✅ Backup fertig – {st.session_state.get('_manual_backup_count', 0)} "
            "Polter-Datensätze wurden gesichert."
        )
        st.download_button(
            "⬇️ Backup auf Firmenlaptop speichern",
            data=st.session_state["_manual_backup_bytes"],
            file_name=st.session_state["_manual_backup_name"],
            mime="application/zip",
            use_container_width=True,
            key="download_manual_backup"
        )
        st.caption(
            "Tipp: Speichere die Datei immer im gleichen Ordner, z. B. "
            "Dokumente → Polter-Zentrale → Backups."
        )

with st.container(border=True):
    st.subheader("1. PDFs importieren")
    uploads = st.file_uploader(
        "Bereitstellungen hier hineinziehen",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploads and st.button("PDFs einlesen", type="primary"):
        results = []

        for f in uploads:
            rows, fmt, attempts = parse_pdf_bytes(f.getvalue(), f.name)

            if not rows:
                results.append({
                    "filename": f.name,
                    "state": "error",
                    "message": "PDF konnte nicht eingelesen werden."
                })
                continue

            added = save_rows(rows)

            if added == 0:
                results.append({
                    "filename": f.name,
                    "state": "duplicate",
                    "message": "PDF schon hochgeladen."
                })
            else:
                results.append({
                    "filename": f.name,
                    "state": "success",
                    "message": "PDF erfolgreich eingelesen."
                })

        st.session_state["_pdf_import_feedback"] = results
        st.session_state["_pdf_import_feedback_ts"] = time.time()
        st.rerun()

    feedback = st.session_state.get("_pdf_import_feedback", [])
    feedback_ts = st.session_state.get("_pdf_import_feedback_ts")

    if feedback and feedback_ts is not None:
        age = time.time() - float(feedback_ts)

        if age <= 3.0:
            for item in feedback:
                filename = item["filename"]
                state = item["state"]
                message = item["message"]

                if state == "success":
                    st.toast(f"{message} — {filename}", icon="✅")
                    st.success(f"✅ **{message}** — {filename}")
                elif state == "duplicate":
                    st.toast(f"{message} — {filename}", icon="ℹ️")
                    st.warning(f"ℹ️ **{message}** — {filename}")
                else:
                    st.toast(f"{message} — {filename}", icon="❌")
                    st.error(f"❌ **{message}** — {filename}")

            time.sleep(max(0.0, 3.0 - age))
            st.session_state.pop("_pdf_import_feedback", None)
            st.session_state.pop("_pdf_import_feedback_ts", None)
            st.rerun()
        else:
            st.session_state.pop("_pdf_import_feedback", None)
            st.session_state.pop("_pdf_import_feedback_ts", None)

    st.markdown("---")
    st.subheader("Private Bauernpartien")
    st.caption("Eigene Bereitstellung ohne PDF direkt in der App anlegen.")
    if st.button(
        "＋ Private Bereitstellung erstellen",
        key="open_private_bereitstellung",
        use_container_width=False
    ):
        private_bereitstellung_dialog()

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
    # PDFs ohne angegebenen/erkennbaren Frächter dürfen nicht aus dem
    # Frächterfilter und damit aus "Polter bearbeiten" verschwinden.
    df.loc[df["fraechter"] == "", "fraechter"] = "Nicht angegeben"

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
# ------------------------------------------------------------
# Professionelle Filterleiste – kompakte Forst-Optik
# ------------------------------------------------------------
supplier_values = sorted([x for x in df["lieferant"].fillna("").unique().tolist() if x])
fraechter_values = sorted([x for x in df["fraechter"].fillna("").unique().tolist() if x])
wood_values = sorted([x for x in df["holzart"].fillna("").unique().tolist() if x])
length_values = sorted([float(x) for x in df["laenge_m"].dropna().unique().tolist()])
status_values = ["Offen","Eingeplant","In Abfuhr","Teilweise abgefahren","Abgefahren"]

FILTER_KEYS = [
    "filter_search",
    "filter_supplier_single",
    "filter_carrier_single",
    "filter_status_single",
    "filter_wood_single",
    "filter_length_single",
]

def reset_filters():
    # Alte Multiselect-Zustände aus früheren App-Versionen ebenfalls entfernen.
    for key in FILTER_KEYS + [
        "filter_supplier","filter_carrier","filter_status","filter_wood","filter_length"
    ]:
        st.session_state.pop(key, None)

with st.sidebar:
    st.markdown('<div class="filter-kicker">🌲 Forstverwaltung</div>', unsafe_allow_html=True)
    st.markdown('<div class="filter-title">Bestand filtern</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="filter-subtitle">Polterbestand nach Partner, Sortiment und Abfuhrstatus eingrenzen.</div>',
        unsafe_allow_html=True
    )

    search = st.text_input(
        "Schnellsuche",
        placeholder="Bereitstellung, Polter, Lagerort …",
        key="filter_search",
        help="Durchsucht Bereitstellung, Lieferant, Frächter, Polter, Lagerort und Bemerkungen."
    )

    with st.container(border=True):
        st.markdown("**🏢 Partner & Transport**")

        supplier_single = st.selectbox(
            "Lieferant",
            ["Alle Lieferanten"] + supplier_values,
            index=0,
            key="filter_supplier_single"
        )

        fraechter_single = st.selectbox(
            "Frächter",
            ["Alle Frächter"] + fraechter_values,
            index=0,
            key="filter_carrier_single"
        )

    with st.container(border=True):
        st.markdown("**🪵 Holz & Sortiment**")

        wood_single = st.selectbox(
            "Holzart",
            ["Alle Holzarten"] + wood_values,
            index=0,
            key="filter_wood_single"
        )

        length_options = ["Alle Längen"] + length_values
        length_single = st.selectbox(
            "Länge",
            length_options,
            index=0,
            key="filter_length_single",
            format_func=lambda x: x if isinstance(x, str) else f"{x:g} m"
        )

    with st.container(border=True):
        st.markdown("**🚚 Abfuhrstatus**")

        status_single = st.selectbox(
            "Status",
            ["Alle Status"] + status_values,
            index=0,
            key="filter_status_single"
        )

    active_filter_count = (
        int(bool(search.strip()))
        + int(supplier_single != "Alle Lieferanten")
        + int(fraechter_single != "Alle Frächter")
        + int(wood_single != "Alle Holzarten")
        + int(length_single != "Alle Längen")
        + int(status_single != "Alle Status")
    )

    if active_filter_count:
        st.markdown(
            f'<div class="filter-summary">✓ {active_filter_count} Filter aktiv</div>',
            unsafe_allow_html=True
        )
        st.button(
            "↺ Alle Filter zurücksetzen",
            on_click=reset_filters,
            use_container_width=True
        )
    else:
        st.caption("Alle Daten werden angezeigt.")

# Einheitliche Variablen für die bestehende Filterlogik.
supplier_choice = [] if supplier_single == "Alle Lieferanten" else [supplier_single]
fraechter_choice = [] if fraechter_single == "Alle Frächter" else [fraechter_single]
wood_choice = [] if wood_single == "Alle Holzarten" else [wood_single]
length_choice = [] if length_single == "Alle Längen" else [length_single]
status_choice = [] if status_single == "Alle Status" else [status_single]

# Feste Lieferantenfarben für die Karte.
# Die Zuordnung bleibt stabil, solange die Lieferantennamen gleich bleiben.
SUPPLIER_COLORS = [
    "#0F5A38",  # tiefes Tannengrün
    "#7B8F3A",  # Moos / Olive
    "#A56A2A",  # Holz / Ocker
    "#4F7F86",  # Schiefer-Türkis
    "#7A6A5A",  # Rindengrau
    "#3D6B4F",  # Waldgrün
    "#8C5B3D",  # Kastanienbraun
    "#6A8247",  # Farn
    "#5A6F78",  # kühles Schiefergrau
    "#725F7D",  # gedecktes Pflaume
]
supplier_color_map = {
    name: SUPPLIER_COLORS[i % len(SUPPLIER_COLORS)]
    for i, name in enumerate(sorted(supplier_values))
}


def polter_pin_html(color, size=42):
    """
    Forst-Pin mit Holzpyramide aus Stammenden:
    unten 3 Kreise, darüber 2, oben 1.
    """
    w = int(size)
    h = int(size * 1.22)
    return f"""
    <div class="polter-marker-wrap" style="
        width:{w}px;
        height:{h}px;
        position:relative;
        transform-origin:50% 100%;
        filter:drop-shadow(0 2px 3px rgba(18,35,24,.24));
        transition:transform .14s ease-out;
    ">
        <svg viewBox="0 0 64 78" width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">
            <path d="
                M32 3
                C15.5 3 5 14.2 5 29.5
                C5 44 17 57.2 32 75
                C47 57.2 59 44 59 29.5
                C59 14.2 48.5 3 32 3 Z"
                fill="{color}" stroke="#F5F1E7" stroke-width="3"/>

            <!-- Holzpyramide: 1 / 2 / 3 -->
            <g stroke="#F5F1E7" stroke-width="2.5" fill="none">
                <circle cx="32" cy="24" r="4.4"/>
                <circle cx="26" cy="33" r="4.4"/>
                <circle cx="38" cy="33" r="4.4"/>
                <circle cx="20" cy="42" r="4.4"/>
                <circle cx="32" cy="42" r="4.4"/>
                <circle cx="44" cy="42" r="4.4"/>
            </g>
        </svg>
    </div>
    """

def polter_list_icon_data_uri(color):
    """
    Kleine SVG-Version desselben Pins für die Tabellen.
    """
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 78">
      <path d="M32 3 C15.5 3 5 14.2 5 29.5 C5 44 17 57.2 32 75
               C47 57.2 59 44 59 29.5 C59 14.2 48.5 3 32 3 Z"
            fill="{color}" stroke="#F5F1E7" stroke-width="3"/>
      <g stroke="#F5F1E7" stroke-width="2.5" fill="none">
        <circle cx="32" cy="24" r="4.4"/>
        <circle cx="26" cy="33" r="4.4"/>
        <circle cx="38" cy="33" r="4.4"/>
        <circle cx="20" cy="42" r="4.4"/>
        <circle cx="32" cy="42" r="4.4"/>
        <circle cx="44" cy="42" r="4.4"/>
      </g>
    </svg>
    """
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


view = df.copy()

# Leere Auswahl = keine Einschränkung / alle anzeigen.
if supplier_choice:
    view = view[view["lieferant"].isin(supplier_choice)]

if fraechter_choice:
    view = view[view["fraechter"].isin(fraechter_choice)]

if status_choice:
    view = view[view["status"].isin(status_choice)]

if wood_choice:
    view = view[view["holzart"].isin(wood_choice)]

if length_choice:
    view = view[view["laenge_m"].isin(length_choice)]

if search.strip():
    q = search.lower()

    # Erst in ALLEN Daten suchen, damit bereits abgefahrene Polter erkannt werden,
    # auch wenn sie in der aktuellen Karten-/Statusansicht nicht mehr sichtbar sind.
    full_search_mask = pd.Series(False, index=df.index)
    for c in [
        "bereitstellung","lieferant","fraechter","holzliste","hab","los",
        "polter_nr","lagerort","waldort","bemerkung"
    ]:
        full_search_mask |= (
            df[c]
            .fillna("")
            .astype(str)
            .str.lower()
            .str.contains(q, regex=False)
        )

    full_search_hits = df[full_search_mask].copy()
    if not full_search_hits.empty:
        full_search_hits["abfuhrstatus"] = full_search_hits.apply(
            berechne_abfuhrstatus,
            axis=1
        )

        abgefahren_hits = full_search_hits[
            full_search_hits["abfuhrstatus"] == "Abgefahren"
        ].copy()

        # Nur anzeigen, wenn die Suche tatsächlich mindestens einen abgefahrenen
        # Polter trifft. So wird der Hinweis nicht bei jeder Eingabe eingeblendet.
        if not abgefahren_hits.empty:
            # Kompakte, eindeutige Bezeichnungen für bis zu 3 Treffer.
            names = []
            for _, rr in abgefahren_hits.head(3).iterrows():
                parts = [
                    str(rr.get("bereitstellung") or "").strip(),
                    str(rr.get("holzliste") or "").strip(),
                    str(rr.get("los") or "").strip(),
                    str(rr.get("polter_nr") or "").strip(),
                ]
                parts = [p for p in parts if p]
                names.append(" / ".join(parts))

            suffix = ""
            if len(abgefahren_hits) > 3:
                suffix = f" (+{len(abgefahren_hits) - 3} weitere)"

            st.toast(
                "Bereits abgefahren: " + " | ".join(names) + suffix,
                icon="✅"
            )
            st.warning(
                "Die Schnellsuche trifft auf mindestens einen Polter, der bereits "
                "vollständig abgefahren wurde."
            )

    # Danach wie bisher die sichtbare Ansicht filtern.
    mask = pd.Series(False, index=view.index)
    for c in [
        "bereitstellung","lieferant","fraechter","holzliste","hab","los",
        "polter_nr","lagerort","waldort","bemerkung"
    ]:
        mask |= (
            view[c]
            .fillna("")
            .astype(str)
            .str.lower()
            .str.contains(q, regex=False)
        )
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

        folium.Marker(
            location=[float(r["lat"]), float(r["lon"])],
            tooltip=(
                f"{r['lieferant']} · {r['bereitstellung']} · "
                f"Liste {liste} · Los {los} · Polter {polter}"
            ),
            popup=folium.Popup(pop, max_width=380),
            icon=folium.DivIcon(
                html=polter_pin_html(supplier_color, size=42),
                icon_size=(42, 51),
                icon_anchor=(21, 51),
                class_name="polter-div-icon"
            ),
        ).add_to(mp)

    # Markergröße abhängig vom Zoom:
    # beim Herauszoomen kleiner, aber weiterhin gut sichtbar.
    map_var = mp.get_name()
    zoom_script = f"""
    <script>
    (function() {{
        var mapObj = {map_var};

        function updatePolterMarkerScale() {{
            var z = mapObj.getZoom();

            // Ziel:
            // Zoom 13+ ≈ 100 %
            // Zoom 10  ≈ 84 %
            // Zoom 8   ≈ 72 %
            // Zoom 6   ≈ 60 %
            var scale = 0.60 + Math.max(0, Math.min(7, z - 6)) * 0.057;
            scale = Math.max(0.60, Math.min(1.00, scale));

            var nodes = document.querySelectorAll('.polter-marker-wrap');
            nodes.forEach(function(node) {{
                node.style.transform = 'scale(' + scale + ')';
            }});
        }}

        mapObj.on('zoomend', updatePolterMarkerScale);
        mapObj.whenReady(updatePolterMarkerScale);
        setTimeout(updatePolterMarkerScale, 150);
    }})();
    </script>
    """
    mp.get_root().html.add_child(folium.Element(zoom_script))

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
        key="polter_map",
        returned_objects=[
            "last_clicked",
            "last_object_clicked",
            "last_object_clicked_tooltip"
        ]
    )

    if isinstance(map_state, dict):
        # Den letzten freien Kartenklick immer merken. Das verhindert,
        # dass beim Einschalten des manuellen Modus ein alter Klick erneut
        # als neuer Standort übernommen wird.
        raw_click = map_state.get("last_clicked")
        raw_click_signature = None
        if isinstance(raw_click, dict):
            raw_lat = raw_click.get("lat")
            raw_lng = raw_click.get("lng")
            if raw_lat is not None and raw_lng is not None:
                raw_click_signature = f"{float(raw_lat):.7f}|{float(raw_lng):.7f}"

        # --------------------------------------------------------
        # A) Manueller Koordinatenmodus
        # --------------------------------------------------------
        if manual_enabled and manual_pid is not None:
            if isinstance(raw_click, dict):
                click_lat = raw_click.get("lat")
                click_lng = raw_click.get("lng")

                if click_lat is not None and click_lng is not None:
                    ignore_signature = st.session_state.get("_manual_ignore_click_signature")
                    accepted_signature = st.session_state.get("_manual_last_accepted_signature")

                    # Nur einen wirklich NEUEN Klick übernehmen.
                    if (
                        raw_click_signature
                        and raw_click_signature != ignore_signature
                        and raw_click_signature != accepted_signature
                    ):
                        st.session_state["_manual_last_accepted_signature"] = raw_click_signature
                        st.session_state["_manual_coord_pending_pid"] = int(manual_pid)
                        st.session_state["_manual_coord_pending_lat"] = float(click_lat)
                        st.session_state["_manual_coord_pending_lon"] = float(click_lng)
                        # Kein zusätzlicher Rerun: der Kartenklick selbst hat den
                        # aktuellen Durchlauf bereits ausgelöst.

        # --------------------------------------------------------
        # B) Normalmodus: Marker anklicken -> Polter auswählen
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

        if raw_click_signature:
            st.session_state["_last_seen_map_click_signature"] = raw_click_signature

with right:
    st.subheader("3. Polter bearbeiten")

    # Immer alle noch nicht vollständig abgefahrenen Polter anbieten.
    # Sidebar-Filter dürfen neu importierte Polter hier nicht "verstecken".
    edit_view = df.copy()
    edit_view["abfuhrstatus"] = edit_view.apply(berechne_abfuhrstatus, axis=1)
    edit_view = edit_view[edit_view["abfuhrstatus"] != "Abgefahren"].copy()
    edit_view = edit_view.sort_values("id", ascending=False)

    if edit_view.empty:
        st.info("Keine offenen oder teilweise abgefahrenen Polter zum Bearbeiten vorhanden.")
    else:
        ohne_gps_edit = int(edit_view[["lat","lon"]].isna().any(axis=1).sum())
        if ohne_gps_edit:
            st.caption(
                f"{ohne_gps_edit} Polter haben noch keine GPS-Koordinaten und können "
                "über „Koordinaten manuell bearbeiten“ positioniert werden."
            )

        opts = {}
        for _, r in edit_view.iterrows():
            liste = str(r["holzliste"] or "-")
            los = str(r["los"] or "-")
            polter = str(r["polter_nr"] or "-")

            if str(r["lieferant"]) == "Unternehmensgruppe Toerring-Jettenbach":
                # Kurzes Törring-Muster:
                # Törring 64,4, Ndh_26_01 - 1.1,23.8
                bereit_menge = str(r["bereitstellung"]).split(",", 1)[0].strip().replace(".", ",")
                short_los = los if los not in ["", "-"] else re.sub(r"^Kaindl_", "", liste, flags=re.I)
                item_no = str(r["hab"] or polter)

                bem = str(r.get("bemerkung", "") or "")
                m_efm = re.search(r"Toerring_EFm_original=([\\d.]+)", bem)
                if m_efm:
                    efm_original = m_efm.group(1)
                elif pd.notna(r["kubatur_fm_original"]):
                    efm_original = f"{float(r['kubatur_fm_original']):g}"
                else:
                    efm_original = "-"

                label = (
                    f"Törring {bereit_menge}, {short_los} - "
                    f"{item_no}.{polter},{efm_original}"
                )
            else:
                label = (
                    f"{r['lieferant']} · {r['fraechter'] or '-'} · "
                    f"{r['bereitstellung']} · Liste {liste} · Los {los} · Polter {polter}"
                )

            opts[label] = int(r["id"])

        option_labels = list(opts.keys())

        # Kartenklick hat Vorrang für die Vorwahl.
        map_selected_id = st.session_state.get("_map_selected_polter_id")
        if map_selected_id is not None:
            target_label = next(
                (label for label in option_labels if opts[label] == int(map_selected_id)),
                None
            )
            if target_label:
                st.session_state["edit_polter_selector"] = target_label

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

        st.session_state["_coord_edit_polter_id"] = int(pid)
        if map_selected_id is not None and pid == int(map_selected_id):
            st.session_state.pop("_map_selected_polter_id", None)

        row_match = df[df["id"] == pid]
        if row_match.empty:
            st.warning("Dieser Polter ist nicht mehr verfügbar. Bitte Seite neu laden.")
        else:
            selected_row = row_match.iloc[0]

            @st.fragment
            def render_polter_editor(pid, row):
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

                # Beim Wechsel wirklich alle alten Eingabewerte des Editors entfernen.
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

                    # Auch einen eventuell offenen Koordinatenmodus des alten Polters beenden.
                    if previous_pid is not None:
                        st.session_state.pop(f"coord_manual_enabled_{previous_pid}", None)

                    st.session_state[selected_pid_key] = pid

                rm_key = f"edit_rm_{pid}"
                fm_key = f"edit_fm_{pid}"
                status_key = f"edit_status_{pid}"
                note_key = f"edit_note_{pid}"
                lat_key = f"edit_lat_{pid}"
                lon_key = f"edit_lon_{pid}"

                # Immer DB-Werte als Ausgangspunkt – keine zufälligen 0-Defaults.
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
                    "1,5 RM = 1 FM. Änderungen werden sofort vorgerechnet, "
                    "dauerhaft aber erst mit „Speichern“ übernommen."
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

                st.text_input("Status (automatisch)", key=status_key, disabled=True)
                st.text_area("Interne Notiz", key=note_key)

                manual_coord_key = f"coord_manual_enabled_{pid}"
                old_manual_state = bool(st.session_state.get(manual_coord_key, False))

                manual_coord_enabled = st.checkbox(
                    "Koordinaten manuell bearbeiten",
                    key=manual_coord_key,
                    help=(
                        "Aktivieren, links auf der Karte einmal auf den gewünschten Standort klicken "
                        "und anschließend speichern."
                    )
                )

                # Aktivieren/Deaktivieren des Kartenmodus braucht genau EINEN App-Rerun,
                # danach bleiben RM/FM-Eingaben innerhalb des Fragments schnell.
                last_mode_key = f"_manual_mode_last_{pid}"
                last_mode = st.session_state.get(last_mode_key, old_manual_state)

                if manual_coord_enabled != last_mode:
                    st.session_state[last_mode_key] = manual_coord_enabled

                    if manual_coord_enabled:
                        st.session_state["_manual_ignore_click_signature"] = (
                            st.session_state.get("_last_seen_map_click_signature")
                        )
                        st.session_state.pop("_manual_last_accepted_signature", None)
                        st.session_state.pop("_manual_coord_pending_pid", None)
                        st.session_state.pop("_manual_coord_pending_lat", None)
                        st.session_state.pop("_manual_coord_pending_lon", None)
                    else:
                        st.session_state.pop("_manual_coord_pending_pid", None)
                        st.session_state.pop("_manual_coord_pending_lat", None)
                        st.session_state.pop("_manual_coord_pending_lon", None)
                        st.session_state.pop("_manual_last_accepted_signature", None)

                    st.rerun(scope="app")

                if manual_coord_enabled:
                    st.info(
                        "📍 Kartenmodus aktiv: links einmal auf den gewünschten Standort klicken. "
                        "Der Punkt wird nur vorgemerkt, bis du speicherst."
                    )

                # Kartenklick-Vorschau übernehmen.
                if (
                    manual_coord_enabled
                    and st.session_state.get("_manual_coord_pending_pid") == int(pid)
                    and st.session_state.get("_manual_coord_pending_lat") is not None
                    and st.session_state.get("_manual_coord_pending_lon") is not None
                ):
                    pending_lat = float(st.session_state["_manual_coord_pending_lat"])
                    pending_lon = float(st.session_state["_manual_coord_pending_lon"])

                    # Nur setzen, wenn sich die Werte wirklich geändert haben.
                    if abs(safe_num(st.session_state.get(lat_key)) - pending_lat) > 1e-9:
                        st.session_state[lat_key] = pending_lat
                    if abs(safe_num(st.session_state.get(lon_key)) - pending_lon) > 1e-9:
                        st.session_state[lon_key] = pending_lon

                    st.success(
                        f"📍 Vorgemerkt: {pending_lat:.6f}, {pending_lon:.6f} – "
                        "mit „Speichern“ übernehmen."
                    )

                st.caption("GPS-Koordinaten:")
                st.number_input("Breitengrad", format="%.7f", key=lat_key)
                st.number_input("Längengrad", format="%.7f", key=lon_key)

                rm_preview = safe_num(st.session_state.get(rm_key))
                fm_preview = safe_num(st.session_state.get(fm_key))
                status_preview = str(st.session_state.get(status_key, "Offen"))

                if status_preview == "Abgefahren":
                    st.success(
                        f"Vorschau: {rm_preview:.3f} RM / {fm_preview:.3f} FM · Abgefahren"
                    )
                elif status_preview == "Teilweise abgefahren":
                    st.warning(
                        f"Vorschau: {rm_preview:.3f} RM / {fm_preview:.3f} FM · Teilweise abgefahren"
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

                    # Sämtlichen temporären Zustand dieses Polters entfernen.
                    for k in [
                        rm_key, fm_key, status_key, note_key, lat_key, lon_key,
                        manual_coord_key, last_mode_key
                    ]:
                        st.session_state.pop(k, None)

                    st.session_state.pop(selected_pid_key, None)
                    st.session_state.pop("_manual_coord_pending_pid", None)
                    st.session_state.pop("_manual_coord_pending_lat", None)
                    st.session_state.pop("_manual_coord_pending_lon", None)
                    st.session_state.pop("_manual_last_accepted_signature", None)
                    st.session_state.pop("_manual_ignore_click_signature", None)

                    st.success(
                        f"Gespeichert: {rm_save:.3f} RM = {fm_save:.3f} FM · Status: {status_save}"
                    )
                    st.rerun(scope="app")

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
                            st.rerun(scope="app")

            render_polter_editor(pid, selected_row)


# ----------------------------------------------------------
# 4. Bereitstellungen – standardmäßig eingeklappt
# ----------------------------------------------------------
with st.expander("4. Bereitstellungen", expanded=False):
    st.markdown('<span class="bereitstellungen-marker"></span>', unsafe_allow_html=True)
    summary = (
        df.groupby(["lieferant", "fraechter", "bereitstellung"], dropna=False)
        .agg(
            Polter=("id", "count"),
            RM_aktuell=("menge_rm_aktuell", "sum"),
            FM_EFm_aktuell=("kubatur_fm_aktuell", "sum")
        )
        .reset_index()
    )

    # Saubere deutsche Spaltenbezeichnungen.
    summary_display = summary.rename(
        columns={
            "lieferant": "Lieferant",
            "fraechter": "Frächter",
            "bereitstellung": "Bereitstellung",
            "RM_aktuell": "RM aktuell",
            "FM_EFm_aktuell": "FM / EFm aktuell",
        }
    )

    st.caption("Übersicht aller importierten Bereitstellungen und der aktuell vorhandenen Mengen.")
    st.dataframe(
        summary_display,
        use_container_width=True,
        hide_index=True
    )

    with st.expander("Vollständig abgefahrene Bereitstellung löschen", expanded=False):
        if summary.empty:
            st.info("Keine Bereitstellungen vorhanden.")
        else:
            keys = []
            for _, r in summary.iterrows():
                keys.append(
                    f"{r['lieferant']} · {r['fraechter'] or '-'} · {r['bereitstellung']}"
                )

            choice = st.selectbox(
                "Bereitstellung auswählen",
                keys,
                key="delete_bereitstellung_select"
            )
            selected_idx = keys.index(choice)
            chosen_row = summary.iloc[selected_idx]
            name = str(chosen_row["bereitstellung"])

            st.warning(
                f"{int(chosen_row['Polter'])} Polter dieser Bereitstellung "
                "werden dauerhaft gelöscht."
            )
            confirm = st.checkbox(
                f"Ja, die Bereitstellung „{name}“ ist vollständig abgefahren.",
                key="delete_bereitstellung_confirm"
            )

            if st.button(
                "🗑️ Bereitstellung endgültig löschen",
                disabled=not confirm,
                key="delete_bereitstellung_button"
            ):
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
    # Exakt dieselbe Pin-Sprache wie auf der Karte.
    icons = [
        polter_list_icon_data_uri(supplier_color_map.get(str(supplier), "#3D6B4F"))
        for supplier in show["Lieferant"].tolist()
    ]
    show.insert(0, "Polterzeichen", icons)
    return show

def style_open_rows(row):
    return ["background-color: #FAFCF8; color: #20382A"] * len(row)

def style_partial_rows(row):
    # Teilweise abgefahren bleibt erkennbar warm, aber im Forst-Farbsystem.
    return ["background-color: #F4EFE4; color: #5F503E"] * len(row)

def style_completed_rows(row):
    return ["background-color: #E3ECE0; color: #294735"] * len(row)

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
        show_open.style.apply(style_open_rows, axis=1),
        column_config={"Polterzeichen": st.column_config.ImageColumn("", width="small")},
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
        column_config={"Polterzeichen": st.column_config.ImageColumn("", width="small")},
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
        column_config={"Polterzeichen": st.column_config.ImageColumn("", width="small")},
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
