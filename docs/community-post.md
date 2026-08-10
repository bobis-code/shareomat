# Shareomat + Emsomat — offenes Abrechnungs- und Steuerungs-Duo für Schweizer LEG/ZEV-Gemeinschaften

Hallo zusammen

Ich möchte zwei Projekte vorstellen, an denen ich seit einiger Zeit arbeite und die zusammen ein gemeinsames Ziel verfolgen: lokal produzierten Strom nicht nur abzurechnen, sondern auch möglichst sinnvoll vor Ort zu nutzen.

## Shareomat

Shareomat ist ein Home-Assistant-Add-on zur Verwaltung und Abrechnung von Schweizer LEG- und ZEV-Gemeinschaften. Entstanden ist das Projekt, weil ich keine wirklich schlanke Lösung gefunden habe, die zu meinem Anwendungsfall passt.

Der Fokus liegt auf dem Wesentlichen:

- Messdaten des Netzbetreibers einlesen (CSV, S-DAT, EBL-Excel) — per Upload oder wahlweise automatisch via Ordnerüberwachung bzw. E-Mail-Postfach
- Verbrauch zwischen lokal geteilter Energie und Netzbezug aufteilen
- nachvollziehbare und versionierte Abrechnungen erzeugen
- amtliche Referenzdaten automatisch abrufen: ElCom-Netztarife pro Gemeinde, den offiziellen BFE-Referenzmarktpreis, SNB-Wechselkurse sowie optional ENTSO-E-Day-Ahead-Preise als Prognosegrundlage — kein manuelles Nachschlagen mehr nötig
- Gemeinschaft, Teilnehmer, Messpunkte und Tarife über eine eigene Weboberfläche verwalten
- standalone oder als Home-Assistant-Add-on über Ingress betreiben
- LEG-Verträge und Beitrittserklärungen verwalten

Neu hinzugekommen ist eine eigene Vertragsverwaltung: Der LEG-Gesellschaftsvertrag wird aus den hinterlegten Daten und Vertragsparametern erzeugt und versioniert. Grundlage für die Struktur ist unter anderem der Mustervertrag von lokalerstrom.ch.

Preis- und Vertragsänderungen werden dabei nicht einfach rückwirkend überschrieben: Vertragsversionen, Gültigkeitszeiträume und die vereinbarten Ankündigungsfristen werden berücksichtigt. Neue Teilnehmer erhalten eine separate Beitrittserklärung zum jeweils gültigen Gesellschaftsvertrag.

Shareomat stellt ausserdem Preise, LEG-/Netzhistorie und Verbrauchsprognosen über MQTT bereit. Darüber kann Emsomat die Informationen optional verwenden.

GitHub: github.com/bobis-code/shareomat

## Emsomat

Emsomat ist eine Home-Assistant-Integration für intelligentes Energiemanagement im eigenen Haus.

Sie koordiniert beispielsweise Wärmepumpe, Batteriespeicher, Elektroauto und weitere Verbraucher anhand von Solarproduktion, Strompreis, Batteriestand und Zeitvorgaben.

Statt vieler starrer Regeln wie:

„Wenn PV > 2'000 W, dann einschalten"

bewertet Emsomat laufend die verfügbaren Zeitfenster. Geräte können entsprechend ihrer Aufgabe darauf reagieren und ihren Verbrauch möglichst sinnvoll verschieben.

Emsomat kann dabei über das eigene Haus hinausblicken: Über eine optionale Marktanbindung (kein echter Energiehandel, sondern reiner Informationsaustausch) können mehrere Emsomat-Installationen innerhalb einer Gemeinschaft austauschen, ob aktuell lokaler Überschuss oder Bedarf vorhanden ist.

Dieser Marktzustand der Nachbarschaft kann neben Netzstrom und eigener PV als zusätzliche Information in die Steuerung einfliessen.

## Wie beide zusammenspielen

Die beiden Projekte haben bewusst unterschiedliche Aufgaben:

Shareomat verwaltet und rechnet ab. Emsomat optimiert den Verbrauch.

Damit soll nicht nur nachträglich berechnet werden, wie viel Strom innerhalb der LEG geteilt wurde. Emsomat kann bereits bei der Verbrauchssteuerung berücksichtigen, wann innerhalb der Gemeinschaft voraussichtlich lokale Energie verfügbar ist.

Die formelle Seite des Energieteilens – Teilnehmer, Messpunkte, Messdaten, Verträge und Abrechnung – bleibt bei Shareomat.

Emsomat verändert diese Abrechnung nicht. Es versucht vielmehr, Verbraucher im Haus so zu steuern, dass lokal verfügbare Energie möglichst sinnvoll genutzt werden kann.

Die Verbindung erfolgt über MQTT.

Shareomat kann unter anderem LEG-Preisinformationen, aktuelle Netz-/Lokaldaten und eine 15-Minuten-Verbrauchsprognose für die Gemeinschaft veröffentlichen. Emsomat kann diese Daten als zusätzlichen Kontext für seine eigene Berechnung verwenden.

Dabei bleibt Emsomat unabhängig: Fehlen Shareomat-Daten oder sind sie veraltet, arbeitet Emsomat mit seiner lokalen Logik weiter.

Die Schnittstelle ist bewusst einseitig:

Shareomat liefert Informationen an Emsomat – Shareomat erteilt keine Steuerbefehle an Geräte.

GitHub: github.com/bobis-code/Emsomat

---

Beide Projekte befinden sich noch in Beta/Early Access und werden von mir nebenbei weiterentwickelt.

Feedback und Tester sind sehr willkommen – besonders von Leuten mit PV-Anlage, Batteriespeicher und/oder einer bestehenden oder geplanten LEG/ZEV.

Fehler, Ideen und Verbesserungsvorschläge am liebsten direkt als GitHub Issue im jeweiligen Repository.
