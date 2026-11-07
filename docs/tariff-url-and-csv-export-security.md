# Externe Tarif-URL und CSV-Berichte (S-05 / S-06)

Stand: 7. November 2026.

## S-05: Abruf der VNB-Tarifdatei

Auf der Seite «Tarife» kann ein Administrator eine Adresse für die
Tarif-JSON-Datei des Netzbetreibers eingeben. Bisher ging diese Adresse
ungeprüft an `urllib.request.urlopen()`. Dadurch waren möglich:

* andere Protokolle (`file://`, `ftp://`, `data:`), etwa das Lesen lokaler
  Dateien wie `/data/options.json`;
* Anfragen an interne Ziele (Router, `localhost`, Cloud-Metadaten
  `169.254.169.254`, Home-Assistant-Netz), auch über Weiterleitungen;
* Weiterleitungen von https auf http;
* beliebig grosse Antworten, die vollständig in den Speicher gelesen wurden.

`external_data/safe_http.py::fetch_public_https()` gilt jetzt für diesen Abruf.
Für jede Adresse, auch für jedes Weiterleitungsziel, gilt:

| Regel | Umsetzung |
|---|---|
| Protokoll | nur `https://` |
| Zugangsdaten in der Adresse | abgelehnt (`user:pass@`) |
| Port | nur 443 |
| Zieladresse | Servername wird **einmal** aufgelöst. **Jede** Adresse der Antwort muss öffentlich sein (kein privates Netz, Loopback, Link-Local, CGNAT `100.64/10`, Multicast, reserviert, IPv4-mapped intern). Verbunden wird genau mit dieser geprüften IP. Ein zweiter DNS-Abruf findet nicht statt, darum ist DNS-Rebinding ausgeschlossen. |
| TLS | Zertifikat und Servername werden geprüft (System-Truststore) |
| Weiterleitungen | höchstens 3, jedes Ziel wird erneut vollständig geprüft |
| Grösse | höchstens 5 MiB (EBL 2027: ca. 0,2 MB), geprüft über `Content-Length` und beim Lesen |
| Kodierung | `Accept-Encoding: identity`, komprimierte Antworten werden abgelehnt |
| Zeit | 20 s pro Netzwerkoperation, 60 s insgesamt |
| Status | nur HTTP 200 wird verarbeitet |

Ein Proxy wird nicht verwendet. Nach dem Download prüft der bestehende Parser
(F-03) Struktur, Beträge, Einheiten und Zeiträume. Ungültiges UTF-8 und JSON
sowie zu tief verschachteltes JSON ergeben `VnbTariffFormatError`. Eine
abgelehnte Adresse erscheint im Formular als «Adresse nicht erlaubt: …».

Legitimer Import: Der Abruf von `https://www.ebl.ch/hubfs/EBL%202027.json` mit
dem neuen Code wurde am 07.11.2026 einmal lesend ausgeführt: 40
Energieprodukte. Die mitgelieferte Beispieldatei wird in den Tests ebenfalls
über den gehärteten Pfad importiert.

**Nicht geändert:** BFE, ElCom, ENTSO-E und SNB verwenden fest eingebaute
Adressen und weiterhin `urllib`. Der BFE-Abruf übernimmt allerdings eine
`download_url` aus den CKAN-Metadaten von opendata.swiss, also eine Adresse,
die ein Dritter bestimmt. Das ist ein Restpunkt (siehe Bericht), kein Teil
dieser Korrektur.

Tests: `tests/test_vnb_tariff_fetch_security.py` (96 Fälle: Protokolle,
Zugangsdaten, Ports, Steuerzeichen, interne IPs und Hostnamen, gemischte
DNS-Antworten, IP-Pinning mit SNI, Zertifikatsprüfung, Weiterleitungen,
Grössen-, Zeit- und Statusgrenzen, manipulierte Inhalte, Web-Formular).

## S-06: Formelinjektion in CSV-Berichten

Tabellenprogramme werten eine Zelle als Formel oder DDE-Befehl aus, wenn sie
mit `=`, `+`, `-`, `@`, Tab, CR oder LF beginnt. Das gilt teilweise auch nach
Leerzeichen und für die Vollbreiten-Zeichen `＝ ＋ － ＠`. In den CSV-Berichten
(`billing_*.csv`, `energy_ledger_*.csv`) stehen Textfelder, die aus
Benutzereingaben oder Messdateien stammen: Teilnehmer-ID, Bezeichnung,
Zähler-IDs und Zählerbezeichnungen.

`core/report/leg_report.py::_text_cell()` stellt einer solchen **Textzelle**
ein Apostroph voran. Excel, LibreOffice und Google Sheets zeigen sie dann als
Text an.

* Zahlenspalten (kWh, CHF, Anzahl) werden nicht verändert und bleiben als
  Zahlen lesbar, auch negative Werte wie `-0.1234`.
* Harmlose Namen (`Haus Müller`, `1-Zimmer`, `a=b`) bleiben unverändert.
* `match_detail_*.csv` und `community_audit_*.csv` enthalten Zähler-IDs nur
  hinter einem festen Spaltenpräfix bzw. keine Textfelder. Sie sind
  unverändert und durch Tests abgesichert.
* Die Sortierung im Energy-Ledger richtet sich weiterhin nach der
  Originalbezeichnung.
* **Maschinelle Weiterverarbeitung:** Ein Programm, das die CSV liest, sieht
  bei betroffenen Namen das vorangestellte `'`. Die Rohwerte stehen
  unverändert in `billing_*.json`, das ist die vorgesehene maschinenlesbare
  Variante. Bereits geschriebene Berichte werden nicht nachträglich geändert.

Tests: `tests/test_csv_formula_injection.py` (27 Fälle).

## Abhängigkeiten

| Paket | Vorher | Neu | Grund |
|---|---|---|---|
| Jinja2 | `>=3.1,<4` | `>=3.1.6,<4` | CVE-2024-22195, CVE-2024-34064, CVE-2024-56201, CVE-2024-56326, CVE-2025-27516 (behoben bis 3.1.6). Für 3.1.6 ist keine neuere Meldung zur Bibliothek selbst bekannt. |
| protobuf | `>=5.28.2` | `>=5.29.6` | CVE-2025-4565 (Rekursion im Pure-Python-Backend, behoben 5.29.5) und CVE-2026-0994 (`json_format`, behoben 5.29.6/6.33.5). Unter Alpine (HA-Add-on) läuft typischerweise das Pure-Python-Backend, und der WAN-Downlink dekodiert Sparkplug-Payloads von anderen Standorten. `json_format` verwendet Shareomat nicht. |

Beide Container verwenden Python 3.12, lokal läuft 3.13. Beide Versionen
unterstützen die Mindestversionen. Der vendorte Sparkplug-Protobuf-Code
funktioniert mit Protobuf ab 4.x (lokal getestet mit 7.36.0). Da es keine
Lock-Datei gibt, lädt ein neuer Image-Build ohnehin die aktuelle Version. Die
Untergrenze verhindert nur, dass eine verwundbare Version installiert wird.
Es wurde nichts lokal installiert.
