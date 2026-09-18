# ScanSnap Ground Truth Service V1.0

Die SQLite-Datenbank `/data/ground-truth.db` ist die Source of Truth.

`ground_truth_seed.json` dient ausschließlich zum initialen Bootstrap einer leeren Datenbank.
Spätere Änderungen und bestätigte Korrekturen werden in SQLite gespeichert.

## Deployment

In Portainer als eigener Git-Stack deployen oder die Dateien in ein neues Repository legen.

```bash
docker compose up -d --build
docker exec ground-truth python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

Von n8n im gemeinsamen `proxy`-Netz:

```text
http://ground-truth:8000/health
http://ground-truth:8000/context
http://ground-truth:8000/confirm
http://ground-truth:8000/export
```

## Sicherheitsmodell

Die Ground Truth liefert Evidenz und bestätigte Referenzfälle. Sie darf fehlende Evidenz im Dokument nicht ersetzen.

V3.1:
- Confidence < 0.75: keine automatische Ablage.
- Neue Ordner erst ab Confidence >= 0.90.
- Bestehende kanonische Ordner bevorzugen.
- Unsichere Fälle bleiben in der ScanSnap Inbox.
- Keine automatische Löschung.

## Backup

Die persistente Docker-Volume-Datei `ground-truth.db` wird durch das bestehende Raspberry-Pi-Backup mitgesichert, sofern Docker-Volumes Teil des Backups sind.

Für einen menschenlesbaren Export:

```bash
curl http://localhost:8000/export
```

Der Export ist nicht die Source of Truth.
