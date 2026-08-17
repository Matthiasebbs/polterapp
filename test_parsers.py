
from pathlib import Path
from parsers import parse_pdf_bytes

expected = {
"26-BE2-51-1-Soller.pdf": 2,
"26-BE2-65-1 Hunglinger.pdf": 2,
"Kunde_26-0183 Astner.pdf": 3,
"Kunde_BM26-0546 Mösl.pdf": 5,
"BA-358 Kaindl 05.03.26-Ammer.pdf": 5,
"Lieferschein_Kaindl_Ndh_26_01.pdf": 2,
"Kunde_26-0092-G&H.pdf": 5,
"Kunde_26-0094-G&H.pdf": 7,
"Lagerort Pinzl Mösl.pdf": 1,
}
ok = True
for name, count in expected.items():
    p = Path("TEST_PDFS") / name
    rows, fmt, attempts = parse_pdf_bytes(p.read_bytes(), name)
    status = "OK" if len(rows)==count else "FAIL"
    print(status, name, "=>", fmt, len(rows), "expected", count,
          "| supplier:", rows[0]["lieferant"] if rows else "-")
    if len(rows) != count:
        print(attempts)
        ok = False
raise SystemExit(0 if ok else 1)
