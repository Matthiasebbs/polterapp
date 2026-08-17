# Polter-Zentrale Web V2

## Neu in dieser Version

- komplette Bereitstellung kann nach Abfuhr gelöscht werden
- einzelner Polter kann ebenfalls gelöscht werden
- zusätzliche PDF-Formate:
  - WBV Altötting-Burghausen
  - FBG Isar-Lech / BA-xxx
  - Unternehmensgruppe Toerring-Jettenbach / Fulcrum-Holzverwaltung
  - BayernAtlas-Lagerort / WBV Pfarrkirchen
  - verbesserter Parser für München/Stadtwerke München
- GPS kann pro Polter manuell ergänzt oder korrigiert werden
- Kartenlink kann gespeichert werden

## Wichtiger Sonderfall

Die Toerring/Fulcrum-Datei und die BayernAtlas-Lagerort-Datei enthalten im auslesbaren PDF
keine numerischen GPS-Koordinaten. Die Polterdaten und Mengen werden trotzdem importiert.
Beim BayernAtlas-Dokument wird der vorhandene Kartenlink übernommen. Numerische GPS-Daten
können anschließend direkt in der App ergänzt werden.

## Aktualisierung einer bereits veröffentlichten Streamlit-App

1. `app.py`, `requirements.txt`, `.streamlit/config.toml` im GitHub-Repository ersetzen.
2. Wenn Supabase bereits eingerichtet ist, `supabase_schema.sql` im Supabase SQL Editor ausführen.
   Dadurch wird die neue Spalte `map_link` ergänzt.
3. Änderungen in GitHub committen.
4. Streamlit startet die App automatisch neu.

## Bei einer komplett neuen Installation

1. Dateien in GitHub hochladen.
2. Streamlit Community Cloud mit `app.py` deployen.
3. Optional Supabase anlegen und `supabase_schema.sql` ausführen.
4. Streamlit Secrets:
   SUPABASE_URL = "..."
   SUPABASE_KEY = "..."
