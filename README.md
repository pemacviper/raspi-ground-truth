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


## 1.1.0: Human-in-the-loop folder learning

The ScanSnap workflow must not create Drive folders automatically.

When a document cannot be assigned safely:
1. it stays in the ScanSnap Inbox;
2. the review message contains the proposed folder name/path and evidence;
3. the user either selects an existing folder or explicitly creates/approves a new folder;
4. for a new folder, register its real Google Drive ID with `POST /register-folder`;
5. store the confirmed document assignment with `POST /confirm`.

Only after steps 4 and 5 is the new folder part of Ground Truth and available to later runs.

Example register:
```bash
curl -X POST http://127.0.0.1:8000/register-folder   -H 'Content-Type: application/json'   -d '{"drive_folder_id":"REAL_DRIVE_ID","name":"Telekom","path":"Telefon und Handy / Telekom","parent_drive_folder_id":"1TePpHeJ7dPZqdLK7Fs6EjZg0yg7J2OtX","reason":"user_approved"}'
```


## 1.2.0: Persistente Review Queue

Unklare Dokumente werden mit `POST /review` gespeichert. Die Review-Seite ist im LAN unter
`http://192.168.1.115:8011/reviews/<ID>` erreichbar.

Der Nutzer kann:
- einen angebotenen bestehenden Ordner bestätigen,
- einen vorgeschlagenen neuen Ordner ausdrücklich genehmigen,
- die Entscheidung vertagen.

Die Entscheidung erzeugt noch keine Drive-Änderung. n8n liest `GET /reviews/approved`.
Bei `approved_new` legt n8n erst dann den Ordner in Drive an, registriert dessen echte ID mit
`POST /register-folder`, schreibt den bestätigten Fall mit `POST /confirm`, verschiebt die Datei
und markiert den Review mit `POST /reviews/<ID>/complete` als `resolved`.

Damit verändert weder Nova noch Ground Truth selbständig die Drive-Taxonomie.
