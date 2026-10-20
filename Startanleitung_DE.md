# ATORCH UD18 BLE Monitor V2.4

## Start unter Windows

1. Python ab Version 3.10 mit Tkinter installieren. Bei der Windows-Installation „Add Python to PATH“ aktivieren.
2. `ud18_gui_v2.py` und das mitgelieferte `UD18.ico` im selben Ordner belassen. Das Icon wird beim Start automatisch für das Programmfenster verwendet.
3. Im Datei-Explorer diesen Ordner öffnen, in die Adresszeile `powershell` eingeben und Enter drücken.
4. Einmalig die benötigten Pakete installieren:

   ```powershell
   py -m pip install bleak matplotlib
   ```

5. Vorschau ohne Gerät:

   ```powershell
   py .\ud18_gui_v2.py --demo
   ```

6. Für echte Messungen UD18 einschalten, Bluetooth am PC aktivieren und starten:

   ```powershell
   py .\ud18_gui_v2.py
   ```

Das Programm sucht automatisch nach einem Gerät mit „UD18“ im Namen. Eine andere App darf die BLE-Verbindung nicht gleichzeitig belegen. Nach Verbindungsverlust versucht das Programm erneut, dasselbe Gerät zu erreichen. Nach „Trennen“ lässt sich die Verbindung mit „Verbinden“ neu starten.

## Zeit und CSV

- **Gerätemesszeit / Ladezeit:** der vom UD18 übertragene Zähler, dargestellt als HH:MM:SS. Stunden werden nicht nach 24 Stunden umgebrochen. Es handelt sich um die Messzeit des Gerätes; das Programm leitet daraus keine eigene Ladeerkennung ab.
- **CSV-Aufzeichnungsdauer:** eigenständige PC-Zeit seit dem Start der aktuellen Aufzeichnung. Sie läuft während BLE-Unterbrechungen weiter und bleibt nach dem Stoppen stehen. Der nächste Aufzeichnungsstart setzt sie auf null.
- „Aufzeichnung starten“ erstellt eine neue CSV-Datei. Standardordner ist `Messdaten` neben der Python-Datei. Mit „Speicherordner wählen“ lässt sich der Ordner ändern.
- CSV verwendet UTF-8 mit BOM, Semikolon und Dezimalkomma. `Aufnahmezeit_s` enthält die Zeit des Messwerts seit Aufzeichnungsbeginn. `Geraetemesszeit_s` und `Geraetemesszeit_HH_MM_SS` enthalten den Gerätezähler. Während Empfangslücken entstehen keine künstlichen Messzeilen.
- Vor dem ersten gültigen Paket steht bei der Gerätemesszeit „—“. Bei Minuten oder Sekunden außerhalb 0–59 bleibt die Gerätemesszeit in CSV leer. Bei Empfangsausfall bleibt der zuletzt empfangene Wert sichtbar; der Hinweis zum Alter des letzten Messwerts zeigt den Ausfall an.

## Anzeige und Bedienung

Min/Max/Mittelwert werden nur für Spannung, Strom, Leistung und Temperatur berechnet. Kapazität und Energie bleiben als Live-Kacheln und in CSV erhalten. Die vier Diagramme zeigen weiterhin die letzten 1800 Messpunkte.

„NTC-Fühler angeschlossen“ nur bei tatsächlich angeschlossenem Fühler aktivieren. Ohne Haken bleiben Temperaturanzeige und CSV-Temperatur leer; der Rohwert bleibt zu Diagnosezwecken in CSV erhalten. Im Demo-Modus ist der Fühler aktiviert.

„UD18-Zähler löschen“ setzt nach Bestätigung Kapazität, Energie und Messzeit am Gerät zurück. Eine laufende CSV-Aufzeichnung muss vorher gestoppt werden. Die Ausführung der BLE-Löschbefehle am Gerät prüfen. Im Demo-Modus werden ausschließlich simulierte Zähler zurückgesetzt.

## Protokollprüfung

36-Byte-Messpakete: Header `FF 55`, Typ `01 03`, vorhandene Prüfsumme. Alle Offsets zählen ab Paketbeginn bei null:

| Feld | Offset | Kodierung |
| --- | --- | --- |
| Temperatur | `0x15–0x16` | signed 16 Bit, Big Endian |
| Stunden | `0x17–0x18` | unsigned 16 Bit, Big Endian |
| Minuten | `0x19` | unsigned Byte, 0–59 |
| Sekunden | `0x1A` | unsigned Byte, 0–59 |
| Beleuchtungszeit | `0x1B` | kein Bestandteil der Messzeit |

Die Datenkarte von msillano benennt nur das niederwertige Stundenbyte an `0x18`. Die Referenzimplementierung liest ausdrücklich zwei Stundenbytes ab `0x17`:

- [USB-Messpaket](https://github.com/CursedHardware/atorch-console/blob/master/src/service/atorch-packet/packet-meter-usb.ts)
- [readDuration und Prüfsumme](https://github.com/CursedHardware/atorch-console/blob/master/src/service/atorch-packet/utils.ts)
- [UD18-Datenkarte und Reset-Befehle](https://github.com/msillano/UD18-protocol-and-node-red/blob/master/UD18_protocol.txt)

Ein Test mit einem echten UD18 ist zusätzlich erforderlich, um die Anzeige mit der konkreten Gerätefirmware zu vergleichen.

## Durchgeführte Tests

Syntaxprüfung mit Python 3.14 bestanden. Decoder mit fragmentierten und zusammengefügten Paketen, Störbytes, falscher Prüfsumme, Nullzeit, 300 Stunden und ungültigen Minuten/Sekunden geprüft. CSV-Zeitfelder und eingefrorene Aufzeichnungsdauer nach Stopp geprüft. GUI im Demo-Modus mit Matplotlib 3.11.2 getestet: Messwerte, vier Statistikzeilen, Diagramme, beide Zeitanzeigen, CSV, NTC-Umschaltung, Demo-Reset und sauberes Beenden bestanden. BLE-Reconnect und echte Reset-Befehle benötigen einen Hardwaretest.
