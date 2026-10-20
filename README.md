# ATORCH UD18 BLE Monitor

Eine deutschsprachige Tkinter-Oberfläche zur Anzeige und CSV-Aufzeichnung von Messwerten eines ATORCH UD18 über Bluetooth Low Energy (BLE).

## Funktionen

- Live-Anzeige von Spannung, Strom, Leistung, Kapazität, Energie und optionaler Temperatur
- Minima, Maxima und arithmetische Mittelwerte für Spannung, Strom, Leistung und Temperatur
- Vier Diagramme mit den jeweils letzten 1.800 Messpunkten
- Anzeige der vom Gerät übertragenen Messzeit sowie der unabhängigen Dauer der CSV-Aufzeichnung
- CSV-Aufzeichnung mit auswählbarem Speicherordner
- Option, die Temperatur nur bei bestätigtem angeschlossenem NTC-Fühler anzuzeigen und aufzuzeichnen
- Automatische Suche nach einem UD18 und Wiederverbindungsversuche
- Optionaler Demo-Modus mit simulierten Messwerten und simuliertem Zähler-Reset
- Nach Bestätigung einzelne BLE-Befehle zum Zurücksetzen von Kapazität, Energie und Messzeit

Die BLE-Kommunikation, Wiederverbindung und Reset-Befehle sind noch nicht hardwareseitig verifiziert. Die Unterstützung anderer Gerätemodelle ist nicht bestätigt. Das Programm bestätigt nicht, ob das Gerät einen gesendeten Reset-Befehl tatsächlich ausgeführt hat; kontrollieren Sie die Zähler am Gerät.

## Voraussetzungen und Installation

- Windows
- Python 3.10 oder neuer mit Tkinter
- Bluetooth-Adapter und aktiviertes Bluetooth für den Betrieb mit einem Gerät

Im Projektordner die Abhängigkeiten installieren:

```powershell
py -m pip install -r requirements.txt
```

Die Laufzeitabhängigkeiten sind [Bleak](https://github.com/hbldh/bleak) für BLE und [Matplotlib](https://matplotlib.org/) für die Diagramme. Tkinter wird mit der üblichen Windows-Python-Installation bereitgestellt.

## Start

Für eine Verbindung mit einem eingeschalteten UD18:

```powershell
py .\ud18_gui_v2.py
```

Das Programm sucht nach einem Gerät mit „UD18“ im Namen. Eine andere Anwendung darf die BLE-Verbindung nicht gleichzeitig belegen.

## Demo-Modus

Die Oberfläche kann ohne UD18 gestartet werden:

```powershell
py .\ud18_gui_v2.py --demo
```

Im Demo-Modus werden ausschließlich simulierte Messwerte verarbeitet; es wird keine BLE-Verbindung aufgebaut. Der Demo-Reset setzt nur die simulierten Zähler zurück und sendet keine Befehle an ein Gerät.

## CSV-Dateien

„Aufzeichnung starten“ erstellt eine neue Datei im Ordner `Messdaten` neben dem Programm. Mit „Speicherordner wählen“ kann ein anderer Ordner ausgewählt werden. Der Ordner wird bei Bedarf angelegt.

CSV-Eigenschaften:

- UTF-8 mit BOM, Semikolon als Trennzeichen und Dezimalkomma
- Spalten: `Zeitstempel`, `Aufnahmezeit_s`, Spannung, Strom, Leistung, Kapazität, Energie, Temperatur, Temperatur-Rohwert sowie Gerätemesszeit in Sekunden und `HH:MM:SS`
- `Aufnahmezeit_s` ist die Zeit seit Beginn der aktuellen Aufzeichnung. Sie läuft auch während einer BLE-Unterbrechung weiter.
- `Geraetemesszeit_s` und `Geraetemesszeit_HH_MM_SS` stammen aus dem Gerätezähler; sie sind nicht die PC-Aufzeichnungsdauer.
- Bei deaktiviertem oder ungültigem NTC-Wert bleiben die Temperatur-Anzeigefelder leer. Der übertragene Temperatur-Rohwert bleibt zur Diagnose erhalten.
- Während Empfangslücken werden keine künstlichen Messzeilen geschrieben. Der letzte gültige Messwert bleibt in der Oberfläche sichtbar; sein Alter wird angezeigt.

## BLE-Protokoll und Quellen

Der Decoder verarbeitet 36-Byte-Messpakete mit Header `FF 55`, Typ `01 03` und Prüfsumme. Die Messzeit wird aus Stunden (UInt16 Big Endian an `0x17–0x18`), Minuten (`0x19`) und Sekunden (`0x1A`) zusammengesetzt; das Byte `0x1B` ist nicht Teil der Messzeit. Diese Interpretation folgt der Referenzimplementierung und wird mit der UD18-Protokolldokumentation verglichen.

- [USB-Messpaket bei CursedHardware/atorch-console](https://github.com/CursedHardware/atorch-console/blob/master/src/service/atorch-packet/packet-meter-usb.ts)
- [Hilfsfunktionen, `readDuration` und Prüfsumme bei CursedHardware/atorch-console](https://github.com/CursedHardware/atorch-console/blob/master/src/service/atorch-packet/utils.ts)
- [UD18-Datenkarte und Reset-Befehle bei msillano](https://github.com/msillano/UD18-protocol-and-node-red/blob/master/UD18_protocol.txt)
- [Temperaturbereich laut UD18-Übersicht](https://lygte-info.dk/review/USBmeter%20USB%20tester%20Atorch%20UD18%20(Hidance)%20UK.html)

Die Protokollquellen dienen als technische Referenzen; dieses Projekt enthält keine mitgelieferte Icon-Datei. Das CursedHardware-Referenzprojekt weist eine MIT-Lizenz aus. Für msillanos Protokolldatei war im verlinkten Projekt keine Lizenzdatei ausgewiesen. Es werden hier Quellen und Protokollinformationen referenziert, nicht deren Quellcode oder vollständige Dokumentation übernommen. Die Rechte der Quellen und Abhängigkeiten bleiben unberührt.

## Tests und Hardwarestatus

Decoder-, CSV- und Demo-GUI-Tests wurden bestanden. Syntaxprüfung wurde bestanden. Die Tests decken unter anderem fragmentierte und zusammengefügte Pakete, Prüfsummenfehler, Zeitwerte, CSV-Zeitfelder und das Starten/Beenden der Demo-Oberfläche ab.

Eine echte BLE-Verbindung, Wiederverbindung (Reconnect) und der Reset am Gerät sind noch nicht hardwareseitig verifiziert. Auch die Unterstützung anderer Gerätemodelle ist nicht bestätigt.

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Details stehen in [LICENSE](./LICENSE). Die Lizenz gilt nicht für separat genannte Quellen oder die installierten Abhängigkeiten.
