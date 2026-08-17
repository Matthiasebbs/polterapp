# Polter-Zentrale Web V3

Diese Version wurde gegen ALLE bisher vom Nutzer zugesandten PDF-Beispiele getestet.

Unterstützte Formate:
1. WBV Holzhandels GmbH / Wasserburg
2. Landeshauptstadt München / Gemeindewald
3. Stadtwerke München Wasser
4. WBV Altötting-Burghausen
5. FBG Isar-Lech / Bereitstellung BA-...
6. Unternehmensgruppe Toerring-Jettenbach / Holzverwaltung
7. WBV Pfarrkirchen / BayernAtlas-Lagerort

Wichtig:
- Toerring-Jettenbach und BayernAtlas/Pfarrkirchen enthalten in den gelieferten PDFs keine
  numerischen GPS-Koordinaten im Text. Diese Datensätze werden trotzdem vollständig
  hinsichtlich Bereitstellung, Lieferant und Menge eingelesen und können manuell mit GPS ergänzt werden.
- Lieferanten können in der linken Seitenleiste gefiltert werden.
- Eine komplette Bereitstellung kann nach vollständiger Abfuhr gelöscht werden.
- Einzelne Polter können ebenfalls gelöscht werden.

AKTUALISIERUNG IN GITHUB:
- app.py ersetzen
- parsers.py neu hinzufügen/ersetzen
- requirements.txt ersetzen
- .streamlit/config.toml ersetzen
- bei Supabase einmal supabase_schema.sql im SQL Editor ausführen
- GitHub committen; Streamlit lädt danach automatisch neu.
