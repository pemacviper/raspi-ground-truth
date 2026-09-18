# Raspberry Pi Home Server – Gesamtdokumentation

Stand: 18.09.2026

Diese Dokumentation beschreibt den aktuell bekannten und bestätigten Zustand des Raspberry-Pi-Servers, der lokalen Infrastruktur sowie der ScanSnap-/Dokumentenautomatisierung. Sie trennt bewusst zwischen produktiv bestätigtem Stand und Komponenten, die noch getestet werden müssen.

## 1. Systemübersicht

Der Raspberry Pi läuft headless als Docker-Host. Die grafische Oberfläche und xrdp wurden entfernt. Standard-Target ist `multi-user.target`.

Host:

```text
Hostname: raspi
LAN-IP: 192.168.1.115
Zeitzone: Europe/Berlin
```

Die IP ist per UniFi DHCP Reservation fest zugeordnet.

Bestätigter Systemzustand nach der Headless-Umstellung:

```text
systemctl get-default -> multi-user.target
lightdm -> inactive
xrdp -> inactive
systemctl --failed -> 0
```

## 2. Storage

System-/Datenlaufwerk ist eine externe USB-SATA-SSD:

```text
Samsung SSD 840 Series
Nominal: 120 GB
Filesystem: /dev/sda2
Belegt zuletzt: ca. 19 GB
Frei zuletzt: ca. 86 GB
SMART: PASSED
Power-on Hours: ca. 723 h
Host Writes: ca. 1.562 TB
Wear Indicator: 98
```

`fstrim.timer` ist aktiviert und läuft wöchentlich.

## 3. Docker und Portainer

Docker ist die zentrale Laufzeitumgebung. Portainer verwaltet die Stacks.

Gemeinsames externes Reverse-Proxy-Netz:

```text
proxy
```

Wichtige Container:

```text
caddy
n8n
uptime-kuma
paperless-ngx-webserver-1
paperless-ngx-db-1
paperless-ngx-broker-1
portainer
ocr
ground-truth
```

Portainer wurde ursprünglich per `docker run` erzeugt und verwendet das persistente Volume `portainer_data`. Portainer ist zusätzlich mit dem externen Docker-Netz `proxy` verbunden.

## 4. Lokales DNS und HTTPS

Lokale DNS-Namen zeigen über UniFi auf `192.168.1.115`:

```text
raspi.home.pemacviper.de
n8n.home.pemacviper.de
kuma.home.pemacviper.de
paperless.home.pemacviper.de
portainer.home.pemacviper.de
webmin.home.pemacviper.de
```

Es gibt keine dafür notwendige Router-Portweiterleitung. Caddy terminiert HTTPS lokal. Zertifikate werden über Let's Encrypt und die IONOS DNS-01 Challenge beschafft.

Caddy-Repository:

```text
pemacviper/raspi-caddy
```

Der Caddy-Build enthält das Modul:

```text
github.com/caddy-dns/ionos
```

Der IONOS API Token liegt nicht im Git-Repository, sondern wird als Stack-Environment-Variable `IONOS_API_TOKEN` bereitgestellt.

Zertifikatsausstellung wurde für die konfigurierten lokalen Domains erfolgreich beobachtet. Die Funktionsprüfung von Webmin und Portainer über die jeweiligen Caddy-URLs war im letzten dokumentierten Stand noch nicht ausdrücklich abgeschlossen.

## 5. Zentrale Secrets

Für hostseitige Secrets ist vorgesehen:

```text
/etc/raspi-secrets/
owner: root:root
directory mode: 700
files: 600
```

Der Caddy-/IONOS-Token wurde nach:

```text
/etc/raspi-secrets/caddy.env
```

verschoben und die Berechtigungen wurden geprüft.

n8n verwendet seinen persistenten Datenbereich für den automatisch erzeugten Encryption Key. Ein neuer `N8N_ENCRYPTION_KEY` darf nicht nachträglich ohne Migrationsplanung gesetzt werden.

## 6. n8n

n8n läuft als Docker-Container und ist über:

```text
https://n8n.home.pemacviper.de
```

vorgesehen.

Wesentliche Einstellungen:

```text
TZ=Europe/Berlin
GENERIC_TIMEZONE=Europe/Berlin
N8N_HOST=n8n.home.pemacviper.de
N8N_PROTOCOL=https
N8N_PORT=5678
N8N_EDITOR_BASE_URL=https://n8n.home.pemacviper.de
WEBHOOK_URL=https://n8n.home.pemacviper.de/
```

Google OAuth Callback:

```text
https://n8n.home.pemacviper.de/rest/oauth2-credential/callback
```

Google Drive und Gmail wurden in n8n erfolgreich verwendet. Das Gmail-Senden wurde erfolgreich getestet.

AWS Credential in n8n:

```text
AWS (IAM) account
```

Nova Micro wurde erfolgreich getestet:

```text
eu.amazon.nova-micro-v1:0
```

## 7. Uptime Kuma

Uptime Kuma läuft im Docker-Netz `proxy` und ist vorgesehen unter:

```text
https://kuma.home.pemacviper.de
```

Der Container war im letzten bestätigten Systemcheck healthy.

## 8. Paperless-ngx

Paperless verwendet PostgreSQL und Redis. Persistente Host-Verzeichnisse liegen unter:

```text
/home/jessey/paperless/data
/home/jessey/paperless/media
/home/jessey/paperless/export
/home/jessey/paperless/consume
/home/jessey/paperless/dbdata
```

Externe URL:

```text
https://paperless.home.pemacviper.de
```

Wichtige Konfiguration:

```text
PAPERLESS_TIME_ZONE=Europe/Berlin
PAPERLESS_OCR_LANGUAGE=deu
PAPERLESS_OCR_LANGUAGES=deu eng
PAPERLESS_URL=https://paperless.home.pemacviper.de
USERMAP_UID=1000
USERMAP_GID=1000
```

Der frühere CSRF-403 über Caddy wurde durch Setzen von `PAPERLESS_URL` behoben. Zugriff wurde anschließend bestätigt.

Sicherheits-To-do: DB-Passwort und Paperless Secret Key wurden während der Einrichtung im Chat sichtbar verwendet bzw. waren schwach/placeholder-artig. Sie sollten kontrolliert rotiert werden.

## 9. Webmin

Webmin läuft direkt auf dem Host:

```text
0.0.0.0:10000
```

Caddy greift über `host.docker.internal:10000` per HTTPS darauf zu und ignoriert dabei die interne Zertifikatsprüfung. Die Host-/Forwarded-Header wurden angepasst, um Redirects auf `host.docker.internal` zu vermeiden.

Die abschließende Browser-Funktionsprüfung ist im dokumentierten Stand noch offen.

## 10. Lokaler OCR-Service

Repository:

```text
pemacviper/raspi-ocr
```

Container:

```text
ocr
```

Interne URL:

```text
http://ocr:8000
```

Der Service verwendet FastAPI, OCRmyPDF, Tesseract, Poppler, Ghostscript, qpdf und unpaper. Sprachen:

```text
deu+eng
```

n8n kann den OCR-Service erreichen.

### OCR API

```text
POST /ocr
GET  /ocr-pdf/{job_id}
```

`POST /ocr` prüft zunächst, ob bereits eine brauchbare Textebene vorhanden ist. Ist OCR notwendig, wird ein durchsuchbares PDF temporär erzeugt.

Antwort enthält unter anderem:

```text
text
ocr_pdf_available
ocr_job_id
```

Temporäre OCR-PDFs liegen unter:

```text
/tmp/raspi-ocr-jobs/
```

und werden nach Abruf bzw. durch Cleanup entfernt.

## 11. ScanSnap Dokumentenpipeline

Google-Drive-Inbox:

```text
Folder ID: 1DgrmzJS8NVvqrykeXduBDv5qjavXfinl
```

Grundprinzip:

```text
ScanSnap / Google Drive Inbox
        |
        v
       n8n
        |
        v
  lokaler OCR-Service
        |
        v
 Dokumentanalyse
        |
        +--> Ground Truth
        +--> semantische Präzedenzfälle
        |
        v
   sichere Zuordnung?
      /        \
    ja          nein
    |            |
    v            v
 bestehender   Human Review
 Ordner        Dokument bleibt
               in Inbox
```

Der Workflow arbeitet at-least-once. Erfolgreich verarbeitete Dokumente werden aus der Inbox verschoben; Fehler bleiben zur erneuten Verarbeitung erhalten.

### Sicherheitsprinzip für Ordner

Der Klassifikationsworkflow darf **niemals automatisch einen neuen Drive-Ordner erzeugen**.

Wenn kein vorhandener Ordner sicher passt:

```text
review_required
```

Das ist ein normaler Workflowzustand und kein technischer Fehler.

Ein neuer Ordner darf erst nach expliziter Nutzerentscheidung entstehen. Danach:

```text
Drive-Ordner erstellen
        |
        v
/register-folder
        |
        v
/confirm
        |
        v
zukünftige Dokumente dürfen diesen Ordner verwenden
```

## 12. Ground Truth Service

Repository:

```text
pemacviper/raspi-ground-truth
```

Container:

```text
ground-truth
```

Datenbank:

```text
Container: /data/ground-truth.db
Host: /var/lib/docker/volumes/raspi-ground-truth_ground_truth_data/_data/ground-truth.db
Volume: raspi-ground-truth_ground_truth_data
```

SQLite bleibt die autoritative Wissensbasis.

Wichtige Tabellen:

```text
canonical_folders
entities
aliases
contracts
signals
rules
ground_truth_cases
case_embeddings
review_queue
audit_log
```

### Ground Truth 2.0

Ground Truth 2.0 kombiniert zwei Arten von Evidenz.

Deterministische Evidenz:

```text
Canonical Folder IDs
Entities
Aliases
Contracts
Signals
Rules
Confirmed Cases
```

Semantische Evidenz:

```text
Embedding eines neuen Dokuments
        |
        v
Cosine Similarity
        |
        v
ähnlichste bestätigte Ground-Truth-Fälle
```

Die semantische Suche ersetzt die strukturierte Ground Truth nicht.

Priorität:

```text
strukturierte Ground Truth
        +
semantische Präzedenzfälle
        |
        v
Klassifikation
        |
        v
bei Unsicherheit Human Review
```

Nur bestätigte oder korrigierte Fälle dürfen zum semantischen Gedächtnis werden. Ungeprüfte LLM-Entscheidungen dürfen nicht als Ground Truth zurückgeschrieben werden.

## 13. Ground Truth API

```text
GET  /health
POST /context
POST /confirm
POST /register-folder
POST /review
GET  /reviews
GET  /reviews/approved
GET  /reviews/{id}
POST /reviews/{id}/resolve
POST /reviews/{id}/complete
GET  /export

GET  /embeddings/missing
POST /embeddings/upsert
```

Die Review-Oberfläche ist derzeit im LAN vorgesehen unter:

```text
http://192.168.1.115:8011/reviews/<ID>
```

## 14. Embeddings / semantisches Gedächtnis

Vorgesehene Konfiguration:

```text
Amazon Titan Text Embeddings V2
Model ID: amazon.titan-embed-text-v2:0
Dimensionen: 512
Normalize: true
Region: eu-central-1
```

Embeddings werden in SQLite in `case_embeddings` gespeichert. Bei der aktuellen kleinen Datenmenge wird Cosine Similarity direkt im Python-Service berechnet. Eine zusätzliche Vector-DB ist derzeit nicht erforderlich.

Die Architektur erlaubt später eine Migration auf sqlite-vec oder eine dedizierte Vector DB, ohne die Ground-Truth-Semantik zu verändern.

## 15. ScanSnap Workflow V4

V4 erweitert den bisherigen Workflow um semantische Präzedenzfälle.

Für jedes neue Dokument:

```text
OCR
 |
 v
Titan Embedding
 |
 v
POST /context
 |
 +-- Regeln
 +-- Entities
 +-- bestätigte Fälle
 +-- semantic_matches
 |
 v
Nova Micro
```

Zusätzlich ist ein Backfill vorgesehen:

```text
GET /embeddings/missing
        |
        v
Titan Embedding
        |
        v
POST /embeddings/upsert
```

Der Backfill ist im V4-Workflow auf sechs Stunden vorgesehen.

### Aktueller Teststatus

Ground Truth 2.0 wurde im Repository implementiert. Ein DB-Scope-Fehler in der semantischen Suche wurde korrigiert.

Beim ersten V4-Test meldete der Node `Titan Embedding - bestätigter Fall` einen Fehler im Bedrock-Subnode. Ursache in der erzeugten Workflow-Datei war ein falscher Authentication-Wert. n8n erwartet für das bestehende IAM-Credential:

```text
authentication: iam
credential: AWS (IAM) account
```

Die korrigierte Workflow-Datei ist Version 4.0.1.

**Noch nicht als produktiv bestätigt:** erfolgreicher Titan-Backfill mit V4.0.1 und vollständiger End-to-End-Lauf eines neuen ScanSnap-Dokuments mit `semantic_matches`.

## 16. Zentrale Ablageregeln

Wichtige bereits bestätigte Ground-Truth-Regeln:

```text
Absender != Ablageziel
Person != Ablageziel
Leistungserbringer != Abrechnungsstelle
bestehender spezifischer kanonischer Ordner > generischer Sammelordner
bei Unsicherheit keine Zuordnung erfinden
keine automatische Ordnererzeugung
```

Beispiele:

```text
PVS / DZR
-> typischerweise Abrechnungsstelle
-> tatsächlichen medizinischen Leistungserbringer bestimmen

AWS + DEÜV / §28a SGB IV
-> Sozialversicherung

Deutsche Rentenversicherung Bund + Renteninformation/Rentenauskunft
-> # Versicherungen / Rentenversicherung Bund

Bundesnotarkammer / Zentrales Vorsorgeregister
+ Vorsorgevollmacht / Vertrauensperson / Betreuung
-> Medizin / Patientenverfügung, Vollmacht, Betreuung
```

## 17. Backup

raspiBackup:

```text
Version: 0.7.4
Config: /usr/local/etc/raspiBackup.conf
Backupziel: /mnt/synology_backup
Backup-Unterordner: /mnt/synology_backup/raspi
Retention: 3
Zeitplan: täglich 04:00
```

Docker wird während des Backups gestoppt.

Synology-Mount:

```text
//192.168.1.128/Time Machine
-> /mnt/synology_backup
```

Der letzte dokumentierte manuelle Backup-Lauf am 17.09.2026 war erfolgreich.

Achtung: Das NAS war zuletzt zu ca. 98 % belegt, mit nur ungefähr 93 GB freiem Speicher. Das ist ein relevantes Betriebsrisiko.

## 18. Docker Logging

Aktuell wurde `json-file` ohne bestätigte globale Größenbegrenzung beobachtet.

Empfohlene Docker-Konfiguration:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

Datei:

```text
/etc/docker/daemon.json
```

Der Einbau wurde noch nicht als durchgeführt bestätigt. Änderungen der Default-Logging-Konfiguration greifen für neu erzeugte bzw. neu erzeugte Container.

## 19. Netzwerk-Hardening

Sobald alle HTTPS-Zugriffe über Caddy abschließend verifiziert sind, können unnötige Host-Port-Bindings reduziert werden.

Kandidaten:

```text
n8n :5678
Uptime Kuma :3001
Paperless :8010
Portainer :9443 / :8000
```

Caddy benötigt weiterhin Host-Ports 80/443. Webmin läuft als Host-Service weiterhin auf 10000.

Diese Härtung ist noch nicht als durchgeführt dokumentiert.

## 20. Bekannte offene Punkte

1. V4.0.1 Titan-Embedding-Backfill erfolgreich testen.
2. Danach vollständigen ScanSnap-End-to-End-Lauf mit semantischen Matches testen.
3. Prüfen, ob neue bestätigte Review-Entscheidungen unmittelbar oder über Backfill eingebettet werden sollen.
4. Webmin über `https://webmin.home.pemacviper.de` final verifizieren.
5. Portainer über `https://portainer.home.pemacviper.de` final verifizieren.
6. Docker Log Rotation umsetzen und verifizieren.
7. Nicht benötigte Host-Port-Bindings nach erfolgreichem HTTPS-Test entfernen.
8. Paperless DB-Passwort und Secret Key kontrolliert rotieren.
9. NAS-Kapazität für raspiBackup bereinigen/erweitern.
10. Review-UI später authentifizieren bzw. besser absichern.
11. Prüfen, ob Review-Verarbeitung idempotent genug gegen doppelte Seiteneffekte ist.
12. Nach wachsendem Ground-Truth-Bestand bewerten, ob direkte Cosine-Suche weiterhin genügt oder sqlite-vec sinnvoll wird.

## 21. Betriebsphilosophie

Die Dokumentenautomation soll konservativ arbeiten:

```text
Automatisierung darf bekannte Struktur nutzen.
Automatisierung darf Ähnlichkeit als Evidenz nutzen.
Automatisierung darf keine neue Taxonomie erfinden.
Unsicherheit führt zu Review.
Menschliche Bestätigung erzeugt Ground Truth.
Ground Truth verbessert zukünftige Entscheidungen.
Dokumente werden nicht automatisch gelöscht.
```

Damit bleibt das System lernfähig, ohne durch selbstverstärkende Fehlklassifikationen die Archivstruktur zu beschädigen.
