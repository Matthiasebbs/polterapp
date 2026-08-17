# Polter-Zentrale – browserbasierte Version

Diese Version ist für einen Betrieb im Browser gedacht. Auf dem Firmenlaptop muss dann nichts installiert werden.

## Funktionen

- PDF-Upload per Drag & Drop
- automatische Erkennung der aktuell eingebauten WBV- und München-Formate
- Polter mit GPS-Koordinaten auf OpenStreetMap
- Originalmenge aus PDF bleibt gespeichert
- RM und FM je Polter manuell änderbar
- Status: Offen / Eingeplant / In Abfuhr / Erledigt
- interne Notizen
- Suche und Filter
- Google-Maps-Navigation
- CSV-Export
- Supabase-Unterstützung für dauerhafte Cloud-Speicherung

## Variante A – schnell testen über Streamlit Community Cloud

1. Kostenloses GitHub-Konto und Streamlit-Konto verwenden.
2. Den Inhalt dieses Ordners in ein neues GitHub-Repository hochladen.
3. Auf Streamlit Community Cloud eine neue App erstellen.
4. Als Main file `app.py` wählen.
5. Deploy drücken.

Ohne Supabase läuft die App im Testmodus. Daten können bei einem Neustart der Cloud-App verloren gehen.

## Variante B – dauerhafte Speicherung mit Supabase

1. Kostenloses Supabase-Projekt anlegen.
2. Im SQL Editor den Inhalt von `supabase_schema.sql` ausführen.
3. In Streamlit Cloud unter Settings > Secrets eintragen:

SUPABASE_URL = "https://DEIN-PROJEKT.supabase.co"
SUPABASE_KEY = "DEIN-ANON-KEY"

4. App neu starten.

Dann bleiben Mengenänderungen, Status und Notizen dauerhaft gespeichert.

## Datenschutz-Hinweis

Die PDFs werden von der App nur verarbeitet; in dieser Version werden die PDF-Dateien selbst nicht dauerhaft gespeichert. In der Datenbank landen nur die ausgelesenen Polterdaten.

## Für produktiven Firmeneinsatz

Vor einem echten Produktivbetrieb sollte die Supabase-Sicherheitsregel eingeschränkt und ein Login ergänzt werden. Die mitgelieferte Policy ist bewusst einfach gehalten, damit die Testversion schnell funktioniert.
