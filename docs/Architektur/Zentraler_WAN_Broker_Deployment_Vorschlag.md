# ZENTRALER WAN-BROKER

## Deployment-Vorschlag (Entwurf, noch keine Umsetzung)

**Status:** Entwurf zur Prüfung — **kein Code geändert, keine Software installiert,
kein Server aufgesetzt, kein Managed-Broker-Konto angelegt.** Beantwortet die in
`Kommunikations_und_Sicherheitsarchitektur.md` Abschnitt 66 ("Infrastruktur"/
"Security") als bewusst offen markierten Punkte für `mqtt.shareomat.ch`. Reine
Konzeptarbeit — Freigabe/Diskussion vor jeder Umsetzung erforderlich.
**Update 2026-08-22:** Zweiphasiger Plan ergänzt (Abschnitt 0/0a) — Pilot
zunächst über einen kostenlosen Managed-Broker (EMQX Cloud Serverless), eigener
VPS unter `mqtt.shareomat.ch` (Abschnitt 1-7) erst für die Produktionsphase.

**Bezug:** Baut auf bereits verbindlich entschiedenen Punkten auf, die hier NICHT
neu zur Debatte stehen:
- Abschnitt 23-25 (`mqtt.shareomat.ch` als einziger zentraler Endpunkt, Cloudflare
  nur Deployment-Infrastruktur, keine fachliche Rolle)
- Abschnitt 26 (keine transparente Bridge Cloud-MQTT ↔ lokales Emsomat-MQTT)
- Abschnitt 29-31 (Identität aus Authentifizierung, eigenes Credential pro
  Shareomat, LEG-Isolation am Broker)
- `Shareomat_CrossHouse_Sparkplug_Vertrag.md` Abschnitt 6 (die genaue ACL-Regel:
  Publish nur unter eigener `group_id`/`edge_node_id`, Subscribe nur innerhalb
  eigener `group_id`, NCMD/DCMD WAN-seitig vollständig verboten)

Dieses Dokument übersetzt diese bereits getroffenen Entscheidungen in einen
konkreten, prüfbaren Deployment-Plan — es ändert oder erweitert die
Kommunikationslogik selbst nicht.

---

## 0. Phasenmodell (Update 2026-08-22)

Nach Prüfung kostenloser Managed-Broker-Angebote (EMQX Cloud Serverless,
HiveMQ Cloud Serverless) wird der ursprüngliche Plan **zeitlich entkoppelt**:

- **Phase 1 — Pilot, sofort möglich, keine eigene Infrastruktur:** ein
  kostenloser Managed-Broker (Empfehlung: **EMQX Cloud Serverless**, siehe
  Abschnitt 0a) übernimmt vorübergehend die Rolle von `mqtt.shareomat.ch`.
  Beantwortet zuerst die eigentliche fachliche Frage — funktioniert das
  gesamte Shareomat-Cross-House-Konzept real über zwei unabhängige
  Internetanschlüsse — bevor überhaupt in eigene Infrastruktur investiert
  wird. Kein VPS, kein DNS auf `mqtt.shareomat.ch` nötig, kein Zertifikats-
  Handling, keine Portfreigabe.
- **Phase 2 — Produktion, nach erfolgreichem Pilot:** Abschnitt 1-7 unten
  (eigener VPS unter `mqtt.shareomat.ch`) bleiben als spätere,
  produktionsreife Option bestehen — insbesondere sobald Verbindungs-/
  Traffic-Limits eines Free-Tiers real erreicht werden oder ein SLA
  gebraucht wird (Managed-Free-Tiers haben ausdrücklich kein SLA).

Diese Dokumentstruktur ändert nichts an den bereits verbindlichen
ACL-/Isolations-Anforderungen (Abschnitt 6.3 des Cross-House-Vertrags) — sie
gelten in Phase 1 genauso streng wie in Phase 2, nur auf einem gemieteten statt
einem selbst betriebenen Broker.

---

## 0a. Phase 1: EMQX Cloud Serverless (empfohlen für den Piloten)

**Zwei unabhängig gegengeprüfte Kernfakten (Stand August 2026):**

- EMQX Cloud Serverless unterstützt ACL-Regeln pro **Client-ID oder
  Username**, mit Topic-Filter + Aktion (`pub`/`sub`/`pubsub`) +
  `allow`/`deny`, ausgewertet in der Reihenfolge Client-ID/Username-Regeln
  zuerst, danach "All Users"-Regeln. Da der Serverless-Tarif keinen
  Autorisierungs-Modus-Switch unterstützt, empfiehlt EMQX selbst explizit
  einen globalen Deny-All-Fallback (`#`, `pubsub`, `deny` unter "All Users")
  als Whitelist-Basis. Bis zu 100'000 Regel-Einträge (2× Verbindungslimit).
  [EMQX Cloud Docs — Default Authorization](https://docs.emqx.com/en/cloud/latest/deployments/default_authz.html)
- HiveMQ Cloud Serverless erlaubt dagegen nur **eine Permission pro
  Credential** — bestätigt über den HiveMQ-eigenen Community-Support, nicht
  nur Drittquellen. [HiveMQ Community Forum](https://community.hivemq.com/t/free-serverless-permissions/2584)

**Warum das für unser Sicherheitsmodell den Ausschlag zugunsten EMQX gibt:**

Unsere Architektur hat pro Shareomat ohnehin schon zwei getrennte WAN-
Verbindungen mit je eigenem `client_id` (`wan_uplink.py`/`wan_downlink.py`:
`f"{...}-wan-uplink"` / `f"{...}-wan-downlink"`). Damit reicht **ein**
EMQX-Credential (Username/Passwort) pro Shareomat-Standort; die eigentliche
Trennung passiert über Client-ID-Regeln:

```text
Regel 1 (Client-ID = "<standort>-wan-uplink"):
  publish  spBv1.0/<group_id>/NBIRTH/<edge_node_id>            → allow
  publish  spBv1.0/<group_id>/NDATA/<edge_node_id>             → allow
  publish  spBv1.0/<group_id>/NDEATH/<edge_node_id>            → allow
  publish  spBv1.0/<group_id>/DBIRTH/<edge_node_id>/#          → allow
  publish  spBv1.0/<group_id>/DDATA/<edge_node_id>/#           → allow
  publish  spBv1.0/<group_id>/DDEATH/<edge_node_id>/#          → allow

Regel 2 (Client-ID = "<standort>-wan-downlink"):
  subscribe  spBv1.0/<group_id>/#                              → allow

Regel 3 (All Users, Fallback):
  #  pubsub  → deny
```

Weil jede Regel den Message-Type **einzeln** auflistet (kein `+`-Platzhalter
an dieser Stelle), kann der Uplink strukturell **nicht** auf `NCMD`/`DCMD`
publizieren — die Broker-ACL setzt Cross-House-Vertrag Abschnitt 6.3 damit
exakt um, nicht nur näherungsweise.

**Bei HiveMQ Free wäre das nicht so sauber möglich:** Mit nur einer
Permission pro Credential müsste die Publish-Regel entweder
`spBv1.0/<group_id>/+/<edge_node_id>/#` lauten (ein `+` für den Message-Type)
— das würde technisch auch NCMD/DCMD-Publish auf die eigene `edge_node_id`
erlauben, ein am Broker messbarer (wenn auch durch die bereits vorhandene
Anwendungslogik in `WanDownlink`/`build_wan_uplink(enable_ncmd=False)`
entschärfter) Abweichung von der exakten Spezifikation. Zwei Credentials
(Uplink/Downlink getrennt, je eine Permission) würden das umgehen, sind aber
in der Kombination aus Aufwand und Traffic-Volumen (10 GB, grosszügiger als
EMQX) gegenüber EMQX Serverless kein klarer Vorteil mehr.

**Kapazitätsrechnung (bestätigt deine eigene Abschätzung):** 1 Mio.
Session-Minuten/Monat ≈ 23 dauerhaft verbundene Clients. Bei 2 Verbindungen
pro Shareomat (Uplink + Downlink) ≈ 11 Shareomat-Standorte 24/7 kostenlos —
für den geplanten Piloten (2-5 Häuser, siehe
`project_quartier_koordination`-Notizen: erster Nachbar testet bereits)
komfortabel ausreichend. Traffic-Limit (1 GB/Monat) real gegen die
tatsächliche Publish-Frequenz messen, sobald der Pilot läuft — darüber hinaus
$0.15/GB, keine Überraschungskosten.

**TLS/Auth in Phase 1:** TLS ist bei EMQX Serverless verpflichtend
(deckungsgleich mit Abschnitt 24/29 — kein Klartext-Fallback möglich, sogar
strenger als selbst konfiguriert). Username/Passwort pro Shareomat-Standort
erfüllt Abschnitt 30 (eigenes Credential pro Installation) unverändert.

**Offen, vor dem ersten echten Pilot-Test zu verifizieren (noch nicht
getan):** ob EMQX Serverless MQTT-Wildcards (`+`/`#`) tatsächlich im
Topic-Feld einer Client-ID-Regel akzeptiert (die obige Tabelle geht davon
aus) — das ist aus den bisher gesichteten Docs wahrscheinlich, aber nicht
Wort für Wort bestätigt. Sollte vor dem ersten Anlegen echter Regeln kurz an
einem Test-Credential nachvollzogen werden.

---

## 1. Broker-Standort (Phase 2 — Produktion, nach dem Piloten)

**Zwei Varianten stehen laut Abschnitt 24 offen. Empfehlung: Variante A (VPS),
nicht Variante B (zuhause + Cloudflare Tunnel) — für GENAU diesen zentralen
Broker.**

Begründung:

- Der zentrale Broker ist kein einzelnes Haus mehr, sondern die gemeinsame
  Infrastruktur potenziell vieler unabhängiger Haushalte/LEGs. Ein Ausfall der
  Internetleitung, des Stroms oder der Hardware bei einer Privatperson legt
  dann nicht mehr nur deren eigenes Zuhause lahm, sondern die Cross-House-
  Kommunikation aller angeschlossenen Standorte gleichzeitig.
- Ein kleiner VPS (z.B. Hetzner CX22, Netcup, Infomaniak — alle mit
  Rechenzentrum in der EU/Schweiz verfügbar) kostet in der Grössenordnung von
  4-6 EUR/Monat und bietet eine feste öffentliche IP ohne Tunnel-Abhängigkeit.
  Sparkplug-B-Verkehr (kleine, seltene Birth/Data-Nachrichten pro Teilnehmer)
  ist bei den aktuell geplanten Grössenordnungen (ein Pilot-LEG, perspektivisch
  ~30 Häuser) verschwindend gering — keine Kapazitätsfrage.
- Cloudflare Tunnel (Variante B) bleibt für den Piloten oder als Fallback eine
  valide Option, falls kein Budget für einen VPS gewünscht ist — dann läuft der
  Broker weiterhin zuhause, wie in Abschnitt 24 Variante B beschrieben. Diese
  Variante bringt aber eine zusätzliche Abhängigkeit (Tunnel-Verfügbarkeit,
  WebSocket statt natives `mqtts://`) und macht die zentrale Infrastruktur an
  eine einzelne Privatinstallation.
- **Standortempfehlung (Rechenzentrum):** Schweiz oder EU, wegen der
  Datenresidenz für Schweizer LEG-Teilnehmer — konkreter Anbieter ist eine
  offene Entscheidung für dich (Budget/Präferenz), keine technische
  Notwendigkeit für einen bestimmten Anbieter.

**Offen für dich:** VPS ja/nein, welcher Anbieter, wer administriert ihn
(Zugriff/Wartung/Patching).

---

## 2. DNS

- `mqtt.shareomat.ch` als A-/AAAA-Record direkt auf die VPS-IP — kein CNAME auf
  einen Cloudflare-Tunnel-Hostnamen nötig, wenn Variante A (VPS) gewählt wird.
- Bestätigt Abschnitt 65.14/65.15: Eine Domain (`shareomat.ch`) für das gesamte
  System reicht, keine Domain pro Haus oder pro LEG.
- TTL niedrig halten (z.B. 300s) während der ersten Inbetriebnahme, damit ein
  IP-Wechsel (Migration auf einen anderen VPS) ohne lange Downtime möglich
  bleibt; danach auf einen normalen Wert (z.B. 3600s) erhöhen.
- Falls Variante B (Cloudflare Tunnel) gewählt wird: `mqtt.shareomat.ch` zeigt
  stattdessen auf den von Cloudflare vergebenen Tunnel-Hostnamen (CNAME) — rein
  eine DNS-Frage, ändert an der Sparkplug-Schicht nichts (Abschnitt 25).

---

## 3. TLS

- Natives `mqtts://mqtt.shareomat.ch:8883` (MQTT over TLS), wie in Abschnitt 24
  Variante A bereits vorgesehen — kein WebSocket-Umweg nötig, wenn der Broker
  eine öffentliche IP hat.
- Zertifikat: Let's Encrypt über certbot, automatisierte Erneuerung (90-Tage-
  Gültigkeit). Mosquitto kann Let's Encrypts eigenes Verzeichnis nicht direkt
  lesen (Rechte/Pfad) — certbot-Renewal-Hook kopiert `fullchain.pem`/
  `privkey.pem` an den von Mosquitto konfigurierten Pfad und lädt Mosquitto neu
  (`systemctl reload mosquitto`, kein Full-Restart nötig — laufende
  Verbindungen bleiben bestehen).
- Minimum TLS 1.2, moderne Cipher-Suites (Mosquitto-Standardkonfiguration ab
  Version 2.x ist hier bereits sinnvoll vorbelegt).
- Port 1883 (Klartext) **nicht** öffentlich exponieren — falls für lokale
  Diagnose auf dem VPS selbst gewünscht, nur auf `127.0.0.1` binden.
- Langfristige Option (nicht jetzt umzusetzen): Client-Zertifikate/mTLS statt
  oder zusätzlich zu Username/Passwort, bereits in Abschnitt 30 als spätere
  Option vorgemerkt.

---

## 4. Authentifizierung pro Shareomat

Abschnitt 30 fordert ein eigenes Credential pro Shareomat-Installation.
**Empfehlung: Mosquittos Dynamic-Security-Plugin (`dynsec`)** statt einer
statischen `password_file` + `acl_file`:

- `dynsec` verwaltet Clients/Rollen/ACLs in einer einzigen JSON-Datei, änderbar
  zur Laufzeit über `mosquitto_ctrl dynsec` (kein Broker-Neustart beim
  Onboarding eines neuen Hauses nötig — passt zum erwarteten Wachstum, ein Haus
  nach dem anderen).
- Seit Mosquitto 2.1.0 (Januar 2026) unterstützt `dynsec` `%c`/`%u`-Platzhalter
  in ACL-Mustern (Client-ID/Username), was rollenbasierte Vorlagen erlaubt,
  sobald mehr als eine Handvoll LEGs/Häuser verwaltet werden.
- **Für den Start (ein Pilot-LEG, Grössenordnung ~30 Häuser) empfohlen: pro
  Client eine explizite, von Hand angelegte Rolle** statt sofort auf
  Platzhalter-Vorlagen zu setzen — bei dieser Grössenordnung bleibt das
  auditierbar und einfach nachvollziehbar; eine Umstellung auf generische
  `%u`-Rollen ist eine spätere, rein technische Optimierung, keine
  Architekturentscheidung.
- Jedes Shareomat erhält: einen eigenen MQTT-Username (Vorschlag:
  `SH-000017` — die in Abschnitt 28 bereits skizzierte Geräteidentität lässt
  sich direkt als Username verwenden), ein eigenes Passwort (oder später
  Client-Zertifikat), und genau eine dynsec-Rolle mit den ACLs aus Abschnitt 5.

**Offen für dich:** Wer generiert/verteilt die Credentials an neue
Teilnehmer (manueller Prozess durch dich vs. später ein Self-Service-Flow) —
für den Piloten reicht ein manueller Prozess.

---

## 5. Broker-ACLs

Setzt **exakt** die bereits verbindliche Regel aus
`Shareomat_CrossHouse_Sparkplug_Vertrag.md` Abschnitt 6.3 um, hier nicht neu
erfunden:

```text
Publish:    nur unter spBv1.0/<eigene group_id>/+/<eigene edge_node_id>/#
Subscribe:  nur innerhalb spBv1.0/<eigene group_id>/#
NCMD/DCMD:  WAN-seitig vollständig verboten (weder senden noch empfangen)
Cross-LEG:  jeder Zugriff auf eine fremde group_id verboten
```

Konkret pro dynsec-Rolle (ein Rollen-Template, mit den Werten des jeweiligen
Standorts befüllt):

- `publishClientSend`-ACL auf `spBv1.0/<group_id>/NBIRTH/<edge_node_id>`,
  `.../NDATA/<edge_node_id>`, `.../NDEATH/<edge_node_id>`,
  `.../DBIRTH/<edge_node_id>/#`, `.../DDATA/<edge_node_id>/#`,
  `.../DDEATH/<edge_node_id>/#` — bewusst **einzeln pro Message-Type**
  aufgelistet statt eines einzigen `.../+/<edge_node_id>/#`-Musters, damit
  NCMD/DCMD strukturell gar nicht in der erlaubten Publish-Menge auftauchen
  (kein Verlass auf eine zusätzliche Deny-Regel).
- `subscribeLiteral`/`subscribePattern`-ACL auf `spBv1.0/<group_id>/#` für den
  Downlink — liest damit zwangsläufig auch fremde NBIRTH/NCMD-Topics anderer
  Häuser mit; das ist unschädlich, weil die WAN-Downlink-Anwendungslogik
  (`WanDownlink`, bereits implementiert) NCMD/DCMD-Nachrichten ohnehin
  ignoriert (Cross-House-Vertrag Abschnitt 2.2) — die Broker-ACL ist hier die
  äussere, die Anwendungslogik die innere Verteidigungslinie.
- Cross-LEG-Isolation ergibt sich strukturell daraus, dass jedes Topic-Muster
  einer Rolle bereits die eigene `group_id` fest enthält — ein Client kann
  technisch keinen Topic-String für eine fremde `group_id` konstruieren, der
  von seiner Rolle erlaubt wäre.
- **Vor der ersten produktiven Rolle:** exakte ACL-Auswertungsreihenfolge/
  -Präzedenz von dynsec (Allow/Deny-Priorität zwischen mehreren Rollen eines
  Clients) anhand der aktuellen Mosquitto-Dokumentation gegenprüfen, bevor die
  erste Rolle scharf geschaltet wird — hier bewusst keine ungeprüfte Behauptung
  über Präzedenz-Details.

---

## 6. Netzwerkisolation

- VPS-Firewall (z.B. `ufw`/Anbieter-Firewall): nur `8883/tcp` (mqtts) und
  `22/tcp` (SSH, nur Key-Auth, idealerweise auf eine feste Admin-IP
  eingeschränkt) eingehend erlaubt. Kein `1883/tcp` öffentlich.
- `allow_anonymous false` in der Mosquitto-Konfiguration — jede Verbindung
  muss authentifiziert sein, keine Ausnahme.
- Keine Bridge/Route zu einem Heimnetz — der VPS ist eigenständige,
  unabhängige Infrastruktur, konsistent mit Abschnitt 26 (keine transparente
  Cloud↔Local-Bridge).
- Kein Haus benötigt einen eingehenden Port — alle Shareomat-Instanzen bauen
  die Verbindung selbst ausgehend auf (bereits entschieden, Abschnitt 12/13/25;
  `WanDownlink`/`build_wan_uplink` sind bereits so implementiert).
- `fail2ban` (oder Anbieter-äquivalent) für SSH auf dem VPS als
  Standard-Härtung, unabhängig von der Sparkplug-Anwendungslogik.

---

## 7. Backup/Recovery der Broker-Konfiguration

- **dynsec-Zustand** (`dynamic-security.json` — enthält alle Client-
  Credentials/Rollen/ACLs): kleine JSON-Datei, einfach zu sichern. Vorschlag:
  automatisches Backup bei jeder Änderung (neues Haus onboarded) plus
  täglicher Snapshot, verschlüsselt an einen Ort ausserhalb des VPS (z.B.
  `restic`/`rsync` auf eigenen Speicher — kein Cloud-Anbieter-Zwang).
- **Mosquitto-Hauptkonfiguration** (`mosquitto.conf`, Listener-/TLS-Pfade):
  klein und versionierbar. Offene Frage für dich: in einem privaten
  Ops-Repository versionieren (empfohlen, da Hostname/Pfade der Infrastruktur
  darin stehen — nicht im öffentlichen `shareomat`-Repo) oder ausschliesslich
  über die verschlüsselten Backups sichern.
- **Wiederherstellungsplan:** frischer VPS + wiederhergestellte
  `dynamic-security.json` + Zertifikat (entweder aus Backup oder frisch per
  Let's Encrypt neu ausgestellt, da DNS unverändert auf den neuen VPS zeigt) —
  ein kurzes, konkretes Runbook dafür lohnt sich erst, sobald der Broker
  produktiv läuft, nicht vorher.
- **Bewusst NICHT Teil dieses Backups:** retained/live Sparkplug-Zustand
  (NBIRTH/DBIRTH-Nachrichten) — das sind flüchtige Live-Daten, die sich nach
  einem Broker-Ausfall automatisch neu aufbauen, sobald sich die Clients neu
  verbinden (Rebirth-Mechanismus, bereits implementiert). Die eigentlichen
  Abrechnungsdaten liegen ohnehin in der SQLite-Datenbank jedes einzelnen
  Shareomat, nicht im Broker — Backup/Recovery dieser Datenbank ist ein
  separates, in Abschnitt 66 ("Shareomat: Backup-Strategie") bereits als offen
  markiertes Thema, hier bewusst nicht mitbehandelt.

---

## Zusammenfassung der Empfehlung

| Bereich | Empfehlung |
|---|---|
| Standort | Kleiner VPS (EU/Schweiz), nicht zuhause |
| DNS | `mqtt.shareomat.ch` → VPS-IP, niedrige TTL anfangs |
| TLS | Natives `mqtts://:8883`, Let's Encrypt + Renewal-Hook |
| Auth | Mosquitto `dynsec`, ein Credential + eine Rolle pro Shareomat |
| ACL | Exakt Cross-House-Vertrag Abschnitt 6.3, pro Message-Type einzeln gelistet |
| Netzwerk | Nur 8883+22 offen, `allow_anonymous false`, kein Heimnetz-Bezug |
| Backup | `dynamic-security.json` verschlüsselt sichern, `mosquitto.conf` separat versionieren |

## Offene Entscheidungen für dich

**Für Phase 1 (Pilot), jetzt relevant:**

1. EMQX Cloud Serverless als Pilot-Broker bestätigen (statt HiveMQ) — Begründung
   siehe Abschnitt 0a.
2. Vor dem ersten echten Regel-Setup kurz verifizieren, dass EMQX
   Client-ID-Regeln MQTT-Wildcards im Topic-Feld akzeptieren (Abschnitt 0a,
   letzter Punkt).
3. Wer legt das EMQX-Konto und die ersten Credentials an, wer verteilt sie an
   die 2-5 Pilot-Häuser (manueller Prozess für den Piloten angenommen).

**Für Phase 2 (Produktion), erst nach erfolgreichem Pilot relevant:**

4. VPS ja/nein, welcher Anbieter, wer administriert.
5. Variante A (VPS) oder doch Variante B (Cloudflare Tunnel zuhause).
6. Ops-Konfiguration (`mosquitto.conf` etc.) in einem privaten Zusatz-Repo
   versionieren oder nur verschlüsselt sichern.

Keine dieser Fragen wird durch dieses Dokument entschieden — Punkt 1-3 sind
der konkrete nächste Gesprächspunkt, bevor am EMQX-Konto irgendetwas angelegt
wird. Punkt 4-6 sind bewusst vertagt, bis der Pilot zeigt, ob und wann eigene
Infrastruktur überhaupt nötig wird.
