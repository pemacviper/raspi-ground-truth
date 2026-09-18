# ScanSnap Ground Truth Service 2.0

> Die vollständige Betriebs- und Architektur-Dokumentation des Raspberry Pi befindet sich in [`RASPI-DOKUMENTATION.md`](RASPI-DOKUMENTATION.md).

Ground Truth 2.0 ist eine **hybride Wissensbasis**. SQLite bleibt die autoritative Source of Truth für Ordner, Regeln, Entitäten, Verträge und bestätigte Fälle. Zusätzlich können bestätigte Fälle semantische Embeddings erhalten.

## Architektur

```text
OCR / Metadaten
      |
      +--> deterministische Evidenz: Aliases, Verträge, Signale, Regeln
      |
      +--> Embedding --> semantisch ähnliche bestätigte Fälle
      |
      v
Ground-Truth-Kontext
      |
      v
Nova / n8n
      |
      +--> vorhandenes sicheres Ziel
      +--> Human Review
```

Ein Vektortreffer ist **Evidenz, keine automatische Entscheidung**. Neue Ordner entstehen weiterhin ausschließlich nach ausdrücklicher Nutzerfreigabe.

## Embeddings

Voreinstellung:

```text
Modell: amazon.titan-embed-text-v2:0
Region: eu-central-1
Dimensionen: 512
```

Der Ground-Truth-Service speichert Embeddings bestätigter Fälle in `case_embeddings`. Die eigentliche Embedding-Erzeugung bleibt bewusst außerhalb des Services und kann durch n8n über Bedrock erfolgen. Dadurch benötigt der Ground-Truth-Container keine AWS-Zugangsdaten.

Titan Text Embeddings V2 unterstützt 256, 512 und 1024 Dimensionen. Für dieses kleine private Archiv sind 512 Dimensionen ein sinnvoller Kompromiss.

## Neue API in 2.0

`GET /embeddings/missing` liefert bestätigte Fälle, für die noch kein Embedding gespeichert ist. Jeder Eintrag enthält den für das Embedding vorgesehenen normalisierten Text.

`POST /embeddings/upsert` speichert das von n8n/Bedrock erzeugte Embedding für einen bestätigten Fall.

Beispiel:

```json
{
  "drive_file_id": "DRIVE_FILE_ID",
  "model_id": "amazon.titan-embed-text-v2:0",
  "embedding": [0.01, -0.02, 0.03],
  "embedded_text": "..."
}
```

`POST /context` akzeptiert zusätzlich:

```json
{
  "text": "OCR-Text",
  "file_name": "scan.pdf",
  "embedding": [0.01, -0.02, 0.03],
  "semantic_top_k": 5
}
```

Die Antwort enthält zusätzlich `semantic_matches` mit Cosine-Similarity und dem bestätigten Ziel der ähnlichsten Ground-Truth-Fälle.

## Lernzyklus

```text
Dokument
  -> Klassifikation
  -> Human Review falls nötig
  -> /confirm
  -> bestätigter ground_truth_case
  -> Embedding erzeugen
  -> /embeddings/upsert
  -> künftig als semantischer Präzedenzfall verfügbar
```

Nur bestätigte oder korrigierte Fälle werden semantisches Gedächtnis. Eine ungeprüfte Nova-Entscheidung wird niemals automatisch Trainingsmaterial.

## Bestehende Funktionen

Die Review Queue, `/register-folder`, `/confirm`, der kanonische Ordnerkatalog und die bestehenden Regeln bleiben unverändert erhalten. Die vorhandene SQLite-Datei wird beim Upgrade weiterverwendet; `case_embeddings` wird idempotent ergänzt.

## Deployment

```bash
docker compose up -d --build
```

Danach:

```bash
docker exec ground-truth python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

Erwartet wird `version: 2.0.0`.

## Persistenz

```text
Docker-Volume: raspi-ground-truth_ground_truth_data
DB im Container: /data/ground-truth.db
Host: /var/lib/docker/volumes/raspi-ground-truth_ground_truth_data/_data/ground-truth.db
```

## Sicherheitsprinzip

**Strukturierte Ground Truth schlägt semantische Ähnlichkeit. Human Review schlägt beides, wenn die Evidenz nicht eindeutig ist.** Das LLM darf Vorschläge machen, aber weder die Archivtaxonomie selbst verändern noch seine eigenen ungeprüften Entscheidungen als Ground Truth zurückschreiben.

---

## Historische Review-Funktion

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


## ScanSnap Workflow V4.0

Der produktive Zielablauf verwendet Titan Text Embeddings V2 zusätzlich zur strukturierten Ground Truth:

1. OCR liefert den Dokumenttext.
2. n8n erzeugt mit `amazon.titan-embed-text-v2:0` ein normalisiertes 512-dimensionales Embedding.
3. `POST /context` erhält OCR-Text, Dateiname und Embedding.
4. Ground Truth liefert deterministische Treffer und `semantic_matches` aus ausschließlich bestätigten Fällen.
5. Nova bewertet beide Evidenzarten. Semantische Ähnlichkeit ist kein Freibrief für eine Zuordnung.
6. Unsichere Fälle gehen weiterhin in Human Review.
7. Alle sechs Stunden fragt n8n `GET /embeddings/missing` ab und erzeugt Embeddings für neu bestätigte Ground-Truth-Fälle.

Der Ground-Truth-Container benötigt dafür keine AWS-Credentials. Die Bedrock-Aufrufe erfolgen in n8n mit dem vorhandenen AWS-IAM-Credential.

### Sicherheitsregel

Nur `confirmed` oder `corrected` Ground-Truth-Fälle werden in das semantische Gedächtnis aufgenommen. Ein ungeprüfter LLM-Vorschlag wird niemals automatisch eingebettet und als Präzedenzfall verwendet.
