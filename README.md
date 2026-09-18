# ScanSnap Ground Truth Service 1.0.1

SQLite `/data/ground-truth.db` ist die Source of Truth.

## Fix 1.0.1

`/context` liefert jetzt zusätzlich den vollständigen Katalog der kanonischen Hauptordner.
Außerdem wird `ground_truth_seed.json` bei jedem Containerstart idempotent eingelesen.
Dadurch werden neue Katalogeinträge auch in einer bereits existierenden SQLite-DB ergänzt.
Bestehende Ground-Truth-Fälle werden nicht gelöscht.

## Deployment

Den bestehenden `raspi-ground-truth` Stack mit diesen vollständigen Dateien aktualisieren und neu bauen.

```bash
docker compose up -d --build
```

Prüfen:

```bash
docker exec ground-truth python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

Der Context muss `canonical_folders` enthalten:

```bash
docker exec ground-truth python -c "import json,urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/context',data=json.dumps({'text':'Test','file_name':'test.pdf'}).encode(),headers={'Content-Type':'application/json'}); d=json.loads(urllib.request.urlopen(r).read()); print(d['ground_truth_version'], len(d['canonical_folders'])); print([(x['name'],x['drive_folder_id']) for x in d['canonical_folders']])"
```

Erwartung: Version `1.0.1` und mindestens 27 kanonische Hauptordner plus bereits vorhandene spezielle Unterordner.

## Endpunkte

- `GET /health`
- `POST /context`
- `POST /confirm`
- `GET /export`

## Persistenz

Docker-Volume:

`raspi-ground-truth_ground_truth_data`

Containerpfad:

`/data/ground-truth.db`

Die DB wird beim Redeploy nicht ersetzt. Der Seed ergänzt nur noch fehlende Datensätze.
