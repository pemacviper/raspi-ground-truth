# ScanSnap Ground Truth Service 1.2.1

SQLite `/data/ground-truth.db` ist die persistente Source of Truth für die ScanSnap-Dokumentablage. Der JSON-Seed ergänzt die Datenbank beim Containerstart idempotent und ersetzt keine bestehenden bestätigten Daten.

## Aktueller Stand

Version 1.2.1 kombiniert den kanonischen Ordnerkatalog, bestätigte Ground-Truth-Fälle und Regeln mit einer persistenten Human-in-the-loop Review Queue.

Grundprinzipien:

- Absender, Leistungserbringer, betroffene Person und Ablageziel sind getrennte Konzepte.
- Bestehende spezifische kanonische Ordner haben Vorrang vor generischen Personen- oder Sammelordnern.
- Nova darf Ordner vorschlagen, aber keine neue Archivtaxonomie erzeugen.
- Neue Google-Drive-Ordner entstehen ausschließlich nach ausdrücklicher Nutzerfreigabe.
- Unsichere Dokumente bleiben zur manuellen Prüfung in der ScanSnap Inbox.
- Bestätigte Entscheidungen werden über `/confirm` zu Ground Truth.

## Version 1.2.1

Zusätzlich bestätigt sind:

- DRV-Renteninformationen und Rentenauskünfte → `# Versicherungen / Rentenversicherung Bund`
- Schreiben von Bundesnotarkammer/Zentralem Vorsorgeregister zu Vertrauensperson, Vorsorgevollmacht oder Betreuung → `Medizin / Patientenverfügung, Vollmacht, Betreuung`
- ZVR-Schreiben sind nicht allein wegen der betroffenen Person als medizinischer Befund abzulegen.
- Arbeitgeber wie AWS sind bei Sozialversicherungsmeldungen Absender/Arbeitgeberkontext und nicht automatisch Ablageziel.
- DEÜV- und §28a-SGB-IV-Dokumente werden bei eindeutiger Evidenz im kanonischen Ordner `Sozialversicherung` abgelegt.

Die am 18.09.2026 manuell korrigierten Fälle sind im Seed als bestätigte bzw. korrigierte Ground-Truth-Fälle enthalten.

## Human-in-the-loop Review

Kann der Workflow kein bestehendes Ziel sicher bestimmen, legt er keinen Ordner an. Stattdessen schreibt er mit `POST /review` einen Fall in `review_queue`.

Die Review-Seite ist im LAN erreichbar unter:

```text
http://192.168.1.115:8011/reviews/<ID>
```

Dort kann der Nutzer einen bestehenden Ordner bestätigen, einen vorgeschlagenen neuen Ordner ausdrücklich genehmigen oder die Entscheidung vertagen.

Bei einem bestätigten bestehenden Ordner ruft n8n `/confirm` auf und verschiebt anschließend das Dokument.

Bei einem ausdrücklich genehmigten neuen Ordner ist die Reihenfolge:

```text
Nutzer bestätigt neuen Ordner
        ↓
n8n erstellt Ordner in Google Drive
        ↓
Google liefert echte Drive-ID
        ↓
POST /register-folder
        ↓
POST /confirm
        ↓
Dokument verschieben
        ↓
POST /reviews/<ID>/complete
```

Damit gelangt ein neuer Ordner erst nach einer menschlichen Entscheidung in die Ground Truth.

## API

- `GET /health` – Status, Version und Zähler
- `POST /context` – Ground-Truth-Kontext für die Dokumentklassifikation
- `POST /confirm` – bestätigte Dokumentzuordnung speichern
- `POST /register-folder` – einen ausdrücklich freigegebenen, bereits real existierenden Drive-Ordner kanonisch registrieren
- `POST /review` – unsicheren Fall in die Review Queue schreiben
- `GET /reviews` – Reviews nach Status lesen
- `GET /reviews/approved` – vom Nutzer freigegebene, noch zu verarbeitende Reviews lesen
- `GET /reviews/<ID>` – lokale Review-Oberfläche
- `POST /reviews/<ID>/resolve` – Nutzerentscheidung erfassen
- `POST /reviews/<ID>/complete` – verarbeiteten Review abschließen
- `GET /export` – Ground Truth exportieren

## Deployment

Der Portainer-Stack kann direkt aus diesem Repository gebaut werden. Die bestehende SQLite-Datenbank im Docker-Volume bleibt erhalten.

```bash
docker compose up -d --build
```

Status prüfen:

```bash
docker exec ground-truth python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

Der Health-Endpunkt sollte `version: 1.2.1` melden.

## Persistenz

Docker-Volume:

```text
raspi-ground-truth_ground_truth_data
```

Containerpfad:

```text
/data/ground-truth.db
```

Hostpfad:

```text
/var/lib/docker/volumes/raspi-ground-truth_ground_truth_data/_data/ground-truth.db
```

Der Seed wird bei jedem Start idempotent verarbeitet. Bereits vorhandene SQLite-Daten werden nicht durch die JSON-Datei ersetzt.

## Dateien

- `main.py` – FastAPI-Service, SQLite-Schema, Review UI und API
- `ground_truth_seed.json` – versionierter Bootstrap für kanonische Ordner, Entitäten, Regeln und bestätigte Fälle
- `docker-compose.yml` – Container, persistentes Volume, LAN-Port 8011 und Proxy-Netz
- `Dockerfile` – Service-Image
- `requirements.txt` – Python-Abhängigkeiten
- `README.md` – Betriebs- und Architekturübersicht

## Sicherheitsprinzip

Ein LLM-Vorschlag ist keine Ground Truth. Erst eine bestätigte Zuordnung oder eine explizite Nutzerentscheidung darf dauerhaft gelernt werden. Dokumente werden nicht automatisch gelöscht.
