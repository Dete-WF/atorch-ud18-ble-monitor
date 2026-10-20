"""ATORCH UD18_BLE Monitor V2.4. Python 3.10+, bleak, matplotlib.
Start: py ud18_gui_v2.py     Vorschau ohne Gerät: py ud18_gui_v2.py --demo
"""
import asyncio
import csv
import math
import queue
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
KEYS = ("voltage", "current", "power", "mah", "wh", "temperature")
LABELS = ("Spannung", "Strom", "Leistung", "Kapazität", "Energie", "Temperatur")
UNITS = ("V", "A", "W", "mAh", "Wh", "°C")
STATS_KEYS = ("voltage", "current", "power", "temperature")
CHART_KEYS = ("voltage", "current", "power", "temperature")
# Temperaturbereich laut UD18-Spezifikation (0 bis 80 °C).
# lygte-info.dk/review/USBmeter%20USB%20tester%20Atorch%20UD18%20(Hidance)%20UK.html
TEMP_MIN, TEMP_MAX = 0, 80
MISSING_TEMPERATURE = " -- "


def valid_temperature(value):
    return value is not None and math.isfinite(value) and TEMP_MIN <= value <= TEMP_MAX

# Einzelne Zählerbefehle statt eines allgemeinen Geräte-Resets.
# Quelle: github.com/msillano/UD18-protocol-and-node-red/UD18_protocol.txt
RESET_COMMANDS = (
    ("Energie (Wh)", bytes.fromhex("ff 55 11 03 01 00 00 00 00 51")),
    ("Kapazität (mAh)", bytes.fromhex("ff 55 11 03 02 00 00 00 00 52")),
    ("Messzeit", bytes.fromhex("ff 55 11 03 03 00 00 00 00 53")),
)


def format_duration(seconds):
    """Stunden ohne Tagesumbruch; fehlende Gerätedaten bleiben erkennbar."""
    if seconds is None:
        return "—"
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class UD18Decoder:
    """36-Byte-Dekoder mit Prüfsumme und BLE-Fragmentpuffer."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        results = []
        while len(self.buffer) >= 2:
            pos = self.buffer.find(b"\xff\x55")
            if pos < 0:
                # Ein einzelnes FF kann der Anfang des nächsten Headers sein.
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1] == 255 else b""
                break
            del self.buffer[:pos]
            if len(self.buffer) < 36:
                break
            pkt = bytes(self.buffer[:36])
            if pkt[2] != 1 or ((sum(pkt[2:-1]) & 255) ^ 0x44) != pkt[-1]:
                del self.buffer[0]
                continue
            del self.buffer[:36]
            if pkt[3] != 3:
                continue
            number = lambda a, b: int.from_bytes(pkt[a:b], "big")
            voltage, current = number(4, 7) / 100, number(7, 10) / 100
            # Die Referenzimplementierung liest SIGNED 16 Bit ab 0x15.
            # Das folgende Byte gehört bereits zur Laufzeit (ab 0x17).
            # github.com/CursedHardware/atorch-console/blob/master/
            # src/service/atorch-packet/packet-meter-usb.ts
            temperature_raw = int.from_bytes(pkt[21:23], "big", signed=True)
            temperature = float(temperature_raw) if valid_temperature(temperature_raw) else None
            # packet-meter-usb.ts + utils.ts/readDuration (CursedHardware/atorch-console):
            # 0x17..0x18 = uint16 BE Stunden, 0x19 = Minuten, 0x1A = Sekunden.
            # 0x1B gehört bereits zur Displaybeleuchtung. Keine BCD-Kodierung.
            hours, minutes, seconds = number(23, 25), pkt[25], pkt[26]
            device_time_s = hours * 3600 + minutes * 60 + seconds if minutes < 60 and seconds < 60 else None
            results.append(dict(device_time_s=device_time_s, timestamp=time.time(), monotonic=time.monotonic(),
                                voltage=voltage, current=current,
                                power=voltage * current, mah=float(number(10, 13)),
                                wh=number(13, 17) / 100, temperature=temperature,
                                temperature_raw=temperature_raw))
        return results


class BLEWorker(threading.Thread):
    """Nur Queues verbinden diesen Thread mit Tkinter."""
    def __init__(self, events, demo=False):
        super().__init__(name="UD18-BLE", daemon=False)
        self.events, self.demo = events, demo
        self.stop_requested = threading.Event()
        self.loop = self.task = None
        self.cancel_sent = False
        self.connected = threading.Event()
        self.reset_requested = threading.Event()
        self.connection_lock = threading.Lock()

    def request_reset(self):
        with self.connection_lock:
            if self.stop_requested.is_set() or not self.connected.is_set():
                return False
            self.reset_requested.set()
            return True

    def finish_reset(self, success, text):
        self.events.put(("reset_result", (success, text)))

    async def reset_counters(self, client):
        """Ein Versuch pro Befehl; nach Fehler niemals automatisch wiederholen."""
        sent = []
        try:
            characteristic = client.services.get_characteristic(CHAR_UUID)
            if characteristic is None:
                raise RuntimeError("Schreib-Characteristic FFE1 nicht gefunden")
            properties = characteristic.properties
            if "write" in properties:
                response = True
            elif "write-without-response" in properties:
                response = False
            else:
                raise RuntimeError("FFE1 unterstützt laut Gerät keine Schreibbefehle")
            for name, command in RESET_COMMANDS:
                if self.stop_requested.is_set() or not client.is_connected:
                    raise RuntimeError("Verbindung wurde beendet")
                await asyncio.wait_for(
                    client.write_gatt_char(characteristic, command, response=response), timeout=5)
                sent.append(name)
                await asyncio.sleep(.25)
            self.finish_reset(True, "Löschbefehle für mAh, Wh und Messzeit gesendet. "
                              "Ausführung nicht bestätigt – Gerätezähler am UD18 prüfen. "
                              "Bei laufender Last steigen die Werte anschließend wieder an.")
        except asyncio.CancelledError:
            self.finish_reset(False, "Löschvorgang abgebrochen. Zähler eventuell teilweise "
                              "zurückgesetzt; am Gerät prüfen. Keine automatische Wiederholung.")
            raise
        except Exception as exc:
            self.finish_reset(False, f"Löschvorgang nicht bestätigt: {exc}. "
                              f"Gesendete Befehle: {', '.join(sent) or 'keine bestätigt'}. "
                              "Zähler am Gerät prüfen; keine automatische Wiederholung.")

    def stop(self):
        self.stop_requested.set()
        loop = self.loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self.cancel)
            except RuntimeError:
                pass

    def cancel(self):
        if self.task and not self.task.done() and not self.cancel_sent:
            self.cancel_sent = True
            self.task.cancel()

    def status(self, text):
        self.events.put(("status", text))

    def run(self):
        try:
            asyncio.run(self.main())
        except Exception as exc:
            self.status(f"BLE-Thread beendet: {exc}")
        finally:
            self.connected.clear()
            self.loop = None
            self.events.put(("ended", None))

    async def main(self):
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        if self.stop_requested.is_set():
            return
        try:
            if self.demo:
                await self.simulate()
            else:
                await self.receive()
        except asyncio.CancelledError:
            pass

    async def simulate(self):
        self.status("DEMO – simulierte Messwerte, kein Gerät verbunden")
        self.connected.set()
        start = time.monotonic()
        mah = wh = 0.0
        while True:
            if self.reset_requested.is_set():
                self.reset_requested.clear()
                mah = wh = 0.0
                start = time.monotonic()
                self.finish_reset(True, "DEMO: Simulierte Zähler zurückgesetzt. Kein Gerätebefehl gesendet.")
            t = time.monotonic() - start
            v, a = 5.1 + .04 * math.sin(t), .2 + .1 * math.sin(t / 3)
            mah += a * .2 / 3.6
            wh += v * a * .2 / 3600
            self.events.put(("data", dict(timestamp=time.time(), monotonic=time.monotonic(),
                                         voltage=v, current=a, power=v*a, mah=mah, wh=wh,
                                         temperature=float(round(25 + 2 * math.sin(t / 10))),
                                         temperature_raw=round(25 + 2 * math.sin(t / 10)),
                                         device_time_s=int(t))))
            await asyncio.sleep(.2)

    async def receive(self):
        from bleak import BleakClient, BleakScanner
        address = None
        while not self.stop_requested.is_set():
            client = None
            try:
                self.status("Suche UD18_BLE …")
                def matches(device, adv):
                    if address:
                        return device.address == address
                    return "UD18" in (adv.local_name or device.name or "").upper()
                device = await BleakScanner.find_device_by_filter(matches, timeout=8)
                if device is None:
                    self.status("UD18 nicht gefunden. Neuer Versuch in 3 Sekunden …")
                else:
                    address = device.address  # Bei Wiederverbindung dasselbe Gerät verwenden.
                    self.status(f"Verbinde mit {device.name or address} …")
                    client = BleakClient(device)
                    decoder = UD18Decoder()  # Keine Fragmente aus alter Verbindung übernehmen.
                    last_data = time.monotonic()
                    def notify(_sender, data):
                        nonlocal last_data
                        for item in decoder.feed(data):
                            last_data = time.monotonic()
                            self.events.put(("data", item))
                    await asyncio.wait_for(client.connect(), timeout=20)
                    await asyncio.wait_for(client.start_notify(CHAR_UUID, notify), timeout=10)
                    self.connected.set()
                    self.status("Verbunden – Messdatenempfang aktiv")
                    while client.is_connected:
                        await asyncio.sleep(.1)
                        if self.reset_requested.is_set():
                            self.reset_requested.clear()
                            await self.reset_counters(client)
                        if time.monotonic() - last_data > 15:
                            raise TimeoutError("Seit 15 Sekunden keine gültigen Messdaten")
                    self.status("Verbindung verloren. Neuer Versuch …")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status(f"BLE-Fehler: {exc}. Neuer Versuch …")
            finally:
                with self.connection_lock:
                    self.connected.clear()
                    if self.reset_requested.is_set():
                        self.reset_requested.clear()
                        self.finish_reset(False, "Verbindung beendet: Löschauftrag verworfen. "
                                          "Er wird nach Wiederverbindung nicht ausgeführt.")
                # Auch nach Abbruch während connect() explizit aufräumen.
                if client is not None:
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=8)
                    except Exception as exc:
                        self.status(f"Verbindungsabbau: {exc}")
            await asyncio.sleep(3)


class Recorder:
    def __init__(self):
        self.file = self.writer = None
        self.path = None
        self.count = 0
        self.start_time = 0
        self.elapsed_s = 0.0

    def start(self, directory):
        path = Path(directory) / ("UD18_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f") + ".csv")
        f = path.open("x", encoding="utf-8-sig", newline="")
        try:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Zeitstempel", "Aufnahmezeit_s", "Spannung_V", "Strom_A",
                             "Leistung_W", "Kapazitaet_mAh", "Energie_Wh", "Temperatur_C",
                             "Temperatur_Rohwert", "Geraetemesszeit_s", "Geraetemesszeit_HH_MM_SS"])
            f.flush()
        except Exception:
            f.close()
            raise
        self.file, self.writer, self.path = f, writer, path
        self.start_time, self.count = time.monotonic(), 0
        self.elapsed_s = 0.0

    def write(self, item):
        if self.file is None or item["monotonic"] < self.start_time:
            return
        stamp = datetime.fromtimestamp(item["timestamp"]).astimezone().isoformat(timespec="milliseconds")
        values = [item["monotonic"] - self.start_time] + [item[k] for k in KEYS]
        self.writer.writerow([stamp] + [f"{v:.6f}".replace(".", ",") if v is not None else ""
                                       for v in values] + [item.get("temperature_raw", ""),
                                           item.get("device_time_s") if item.get("device_time_s") is not None else "",
                                           format_duration(item["device_time_s"]) if item.get("device_time_s") is not None else ""])
        self.file.flush()
        self.count += 1

    def duration(self):
        return max(0.0, time.monotonic() - self.start_time) if self.file else self.elapsed_s

    def stop(self):
        self.elapsed_s = self.duration()
        f, self.file = self.file, None
        self.writer = None
        if f is not None:
            f.close()


class UD18App:
    def __init__(self, root, demo=False):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        self.root, self.demo = root, demo
        # Das Gerät sendet hier auch ohne NTC plausible Temperaturwerte.
        # Deshalb Fühleranwesenheit ausdrücklich vom Anwender bestätigen lassen.
        self.sensor_enabled = tk.BooleanVar(value=demo)
        self.events = queue.SimpleQueue()
        self.worker = None
        self.closing = False
        self.reset_pending = False
        self.recorder = Recorder()
        self.directory = Path(__file__).resolve().parent / "Messdaten"
        self.origin = time.monotonic()
        self.history = deque(maxlen=1800)
        self.stats = {}
        self.stats_counts = {}
        self.samples = 0
        self.last_sample = None
        root.title("ATORCH UD18 BLE Monitor – Version 2.4 · NTC" + (" [DEMO]" if demo else ""))
        icon = Path(__file__).resolve().parent / "UD18.ico"
        if icon.is_file():
            try:
                root.iconbitmap(str(icon))
            except tk.TclError:
                pass
        root.geometry("1180x900")
        root.minsize(950, 740)
        base = ttk.Frame(root, padding=12)
        base.pack(fill="both", expand=True)
        ttk.Label(base, text="ATORCH UD18 · Live Monitor V2.4 · NTC", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        self.status = tk.StringVar(value="Bereit")
        ttk.Label(base, textvariable=self.status, wraplength=1100).pack(anchor="w", pady=6)
        controls = ttk.Frame(base)
        controls.pack(fill="x", pady=5)
        self.buttons = []
        for label, command in [("Verbinden", self.connect), ("Trennen", self.disconnect),
                               ("Diagramme leeren", self.clear_graph), ("Statistik zurücksetzen", self.clear_stats),
                               ("Beenden", self.close)]:
            b = ttk.Button(controls, text=label, command=command)
            b.pack(side="left", padx=(0, 7))
            self.buttons.append(b)
        cards = ttk.Frame(base)
        cards.pack(fill="x")
        self.values = {}
        for i, (key, label, unit) in enumerate(zip(KEYS, LABELS, UNITS)):
            cards.columnconfigure(i, weight=1)
            card = ttk.LabelFrame(cards, text=f"{label} ({unit})", padding=9)
            card.grid(row=0, column=i, sticky="ew", padx=3, pady=8)
            self.values[key] = tk.StringVar(value=MISSING_TEMPERATURE if key == "temperature" else "—")
            ttk.Label(card, textvariable=self.values[key], font=("Consolas", 22, "bold")).pack()
        timing = ttk.Frame(base)
        timing.pack(fill="x", pady=3)
        self.device_time = tk.StringVar(value="—")
        self.record_duration = tk.StringVar(value="00:00:00")
        ttk.Label(timing, text="Gerätemesszeit / Ladezeit:").pack(side="left")
        ttk.Label(timing, textvariable=self.device_time, font=("Consolas", 16, "bold")).pack(side="left", padx=(8, 28))
        ttk.Label(timing, text="CSV-Aufzeichnungsdauer:").pack(side="left")
        ttk.Label(timing, textvariable=self.record_duration, font=("Consolas", 16, "bold")).pack(side="left", padx=8)
        record = ttk.LabelFrame(base, text="CSV-Aufzeichnung", padding=8)
        record.pack(fill="x", pady=5)
        self.record_button = ttk.Button(record, text="Aufzeichnung starten", command=self.toggle_record)
        self.record_button.pack(side="left", padx=(0, 8))
        self.folder_button = ttk.Button(record, text="Speicherordner wählen", command=self.choose_folder)
        self.folder_button.pack(side="left")
        self.buttons += [self.record_button, self.folder_button]
        self.record_status = tk.StringVar(value=f"Bereit · {self.directory}")
        ttk.Label(record, textvariable=self.record_status, wraplength=1050).pack(anchor="w", padx=10, pady=4)
        device_frame = ttk.LabelFrame(base, text="Gespeicherte Daten auf dem UD18", padding=8)
        device_frame.pack(fill="x", pady=5)
        self.reset_button = ttk.Button(device_frame, text="UD18-Zähler löschen …",
                                       command=self.reset_device, state="disabled")
        self.reset_button.pack(side="left", padx=(0, 10))
        self.buttons.append(self.reset_button)
        self.device_status = tk.StringVar(value="mAh, Wh und Messzeit am Gerät auf null setzen")
        ttk.Label(device_frame, textvariable=self.device_status, wraplength=800).pack(side="left", fill="x")
        self.table = ttk.Treeview(base, columns=("name", "min", "max", "mean"), show="headings", height=4)
        for col, title in zip(("name", "min", "max", "mean"), ("Messgröße", "Minimum", "Maximum", "Mittelwert")):
            self.table.heading(col, text=title)
            self.table.column(col, width=160, anchor="center")
        for key in STATS_KEYS:
            index = KEYS.index(key)
            label, unit = LABELS[index], UNITS[index]
            missing = MISSING_TEMPERATURE if key == "temperature" else "—"
            self.table.insert("", "end", iid=key, values=(f"{label} ({unit})", missing, missing, missing))
        self.table.pack(fill="x", pady=5)
        self.info = tk.StringVar(value="Statistik: 0 Messwerte · Mittelwert über empfangene Messwerte")
        ttk.Label(base, textvariable=self.info).pack(anchor="w")
        self.freshness = tk.StringVar(value="Noch keine Messdaten empfangen")
        ttk.Label(base, textvariable=self.freshness).pack(anchor="w")
        sensor_frame = ttk.Frame(base)
        sensor_frame.pack(fill="x", pady=(2, 0))
        sensor_switch = ttk.Checkbutton(sensor_frame, text="NTC-Fühler angeschlossen (manuell)",
                                       variable=self.sensor_enabled, command=self.sensor_changed)
        sensor_switch.pack(side="left", padx=(0, 12))
        self.buttons.append(sensor_switch)
        self.temperature_status = tk.StringVar(value="DEMO: warte auf Temperaturdaten" if demo else
                                               "NTC deaktiviert – Haken erst bei angeschlossenem Fühler setzen")
        ttk.Label(sensor_frame, textvariable=self.temperature_status, wraplength=740).pack(side="left")
        figure = Figure(figsize=(10, 5), dpi=100, layout="constrained")
        self.axes, self.lines = [], []
        for i, (key, color) in enumerate(zip(CHART_KEYS, ("#1976b5", "#d1780b", "#299451", "#b44968"))):
            index = KEYS.index(key)
            label, unit = LABELS[index], UNITS[index]
            ax = figure.add_subplot(2, 2, i + 1)
            line, = ax.plot([], [], color=color, linewidth=1.5)
            ax.set_ylabel(f"{label} ({unit})")
            ax.set_xlabel("Zeit seit Diagrammstart (s)")
            ax.grid(True, alpha=.3)
            self.axes.append(ax)
            self.lines.append(line)
        figure.suptitle("Letzte 1800 Messpunkte", fontsize=10)
        self.canvas = FigureCanvasTkAgg(figure, master=base)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        root.after(250, self.connect)

    def connect(self):
        if not self.closing and (self.worker is None or not self.worker.is_alive()):
            self.worker = BLEWorker(self.events, self.demo)
            self.worker.start()

    def disconnect(self):
        if self.worker and self.worker.is_alive():
            self.status.set("Verbindung wird beendet …")
            self.worker.stop()

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Ordner für CSV-Dateien wählen")
        if folder:
            self.directory = Path(folder)
            self.record_status.set(f"Bereit · {self.directory}")

    def reset_device(self):
        if self.closing or self.reset_pending:
            return
        if self.recorder.file:
            messagebox.showinfo("Aufzeichnung läuft", "Bitte zuerst die CSV-Aufzeichnung stoppen, "
                                "bevor du die Gerätezähler löschst.", parent=self.root)
            return
        if not self.worker or not self.worker.connected.is_set():
            messagebox.showinfo("Keine Verbindung", "Bitte zuerst mit dem UD18 verbinden.", parent=self.root)
            return
        worker = self.worker
        text = ("Kapazität (mAh), Energie (Wh) und Messzeit auf dem UD18 unwiderruflich "
                "auf null setzen?\n\nGespeicherte CSV-Dateien, Diagramme und die bisherige "
                "Programmstatistik bleiben erhalten.")
        if self.demo:
            text = "DEMO: Nur die simulierten Zähler zurücksetzen? Kein Gerät wird verändert."
        if not messagebox.askyesno("UD18-Zähler löschen", text, icon="warning",
                                  default="no", parent=self.root):
            return
        if worker is not self.worker or not worker.request_reset():
            self.device_status.set("Keine aktive Verbindung. Löschauftrag wurde nicht angenommen.")
            return
        self.reset_pending = True
        self.reset_button.configure(state="disabled")
        self.device_status.set("Löschbefehle werden gesendet …")

    def toggle_record(self):
        if self.reset_pending:
            messagebox.showinfo("Löschvorgang läuft", "Bitte den laufenden Löschvorgang abwarten.", parent=self.root)
            return
        if self.recorder.file:
            self.stop_record()
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.recorder.start(self.directory)
        except OSError as exc:
            messagebox.showerror("CSV-Datei konnte nicht geöffnet werden", str(exc), parent=self.root)
            return
        self.record_duration.set("00:00:00")
        self.record_button.configure(text="Aufzeichnung stoppen")
        self.folder_button.configure(state="disabled")
        self.record_status.set(f"Aufnahme läuft · {self.recorder.path}")

    def stop_record(self):
        try:
            self.recorder.stop()
            self.record_status.set(f"Gespeichert: {self.recorder.path} · {self.recorder.count} Messwerte")
        except OSError as exc:
            self.record_status.set(f"CSV-Speicherfehler: {exc}")
            messagebox.showerror("CSV-Speicherfehler", str(exc), parent=self.root)
        self.record_duration.set(format_duration(self.recorder.duration()))
        self.record_button.configure(text="Aufzeichnung starten")
        if not self.closing:
            self.folder_button.configure(state="normal")

    def clear_graph(self):
        self.origin = time.monotonic()
        self.history.clear()
        self.draw()

    def clear_stats(self):
        self.stats.clear()
        self.stats_counts.clear()
        self.samples = 0
        self.stats_since = time.monotonic()
        self.update_stats()

    def sensor_changed(self):
        # Beim Wechsel keine alten, womöglich fühlerlosen Werte weiterverwenden.
        self.values["temperature"].set(MISSING_TEMPERATURE)
        self.stats.pop("temperature", None)
        self.stats_counts.pop("temperature", None)
        for item in self.history:
            item["temperature"] = None
        self.update_stats()
        self.draw()
        self.temperature_status.set("NTC aktiviert – warte auf neuen Messwert" if self.sensor_enabled.get()
                                    else "NTC deaktiviert – Temperaturanzeige und CSV bleiben leer")

    def update_stats(self):
        for key in STATS_KEYS:
            index = KEYS.index(key)
            label, unit = LABELS[index], UNITS[index]
            values = self.stats.get(key)
            missing = MISSING_TEMPERATURE if key == "temperature" else "—"
            formatted = [f"{x:.3f}".replace(".", ",") for x in values] if values else [missing] * 3
            self.table.item(key, values=(f"{label} ({unit})", *formatted))
        self.info.set(f"Statistik: {self.samples} Messwerte · arithmetischer Mittelwert")

    def accept(self, item):
        if not self.sensor_enabled.get() or not valid_temperature(item.get("temperature")):
            item = dict(item, temperature=None)
        self.device_time.set(format_duration(item.get("device_time_s")))
        self.last_sample = item["monotonic"]
        if item["monotonic"] >= self.origin:
            self.history.append(item)
        if item["monotonic"] >= getattr(self, "stats_since", 0):
            self.samples += 1
            for key in STATS_KEYS:
                value = item[key]
                if value is None:
                    continue
                count = self.stats_counts.get(key, 0) + 1
                self.stats_counts[key] = count
                old = self.stats.get(key)
                self.stats[key] = [min(old[0], value), max(old[1], value),
                                   old[2] + (value - old[2]) / count] if old else [value]*3
        for key in KEYS:
            value = item[key]
            decimals = 1 if key == "temperature" else 2
            missing = MISSING_TEMPERATURE if key == "temperature" else "—"
            self.values[key].set(f"{value:.{decimals}f}".replace(".", ",") if value is not None else missing)
        if not self.sensor_enabled.get():
            self.temperature_status.set("NTC deaktiviert")
        elif item["temperature"] is None:
            self.temperature_status.set("Temperatur ungültig; Fühler fehlt oder Wert "
                                        "außerhalb 0–80 °C. CSV-Temperatur bleibt leer.")
        else:
            self.temperature_status.set("")
        if self.recorder.file:
            try:
                self.recorder.write(item)
            except OSError as exc:
                self.stop_record()
                self.record_status.set(f"Aufzeichnung wegen Schreibfehler gestoppt: {exc}")
                messagebox.showerror("CSV-Schreibfehler", str(exc), parent=self.root)

    def poll(self):
        changed = False
        # Begrenzte Arbeit pro GUI-Takt; alle Messwerte bleiben in der Queue.
        for _ in range(1000):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "data":
                self.accept(payload)
                changed = True
            elif kind == "status" and not self.closing:
                self.status.set(payload)
            elif kind == "reset_result":
                self.reset_pending = False
                success, text = payload
                self.device_status.set(text)
            elif kind == "ended" and not self.closing:
                self.reset_pending = False
                self.status.set("Getrennt – zum Neustart auf Verbinden klicken")
        if not self.closing:
            can_reset = (self.worker is not None and self.worker.connected.is_set()
                         and not self.worker.stop_requested.is_set()
                         and not self.reset_pending and self.recorder.file is None)
            self.reset_button.configure(state="normal" if can_reset else "disabled")
        if changed and not self.closing:
            self.update_stats()
            self.draw()
        if self.recorder.file:
            self.record_duration.set(format_duration(self.recorder.duration()))
            self.record_status.set(f"Aufnahme läuft · {self.recorder.count} Messwerte · {self.recorder.path}")
        if self.last_sample is not None and not self.closing:
            age = time.monotonic() - self.last_sample
            self.freshness.set(f"Letzter Messwert vor {age:.1f} s" + (" · Werte sind veraltet" if age > 3 else ""))
        if self.closing:
            if (self.worker is None or not self.worker.is_alive()) and self.events.empty():
                if self.worker:
                    self.worker.join()
                if self.recorder.file:
                    self.stop_record()
                self.root.destroy()
                return
        self.root.after(100, self.poll)

    def draw(self):
        data = list(self.history)
        # NaN trennt Kurven bei Empfangslücken, z. B. während Wiederverbindung.
        x, series = [], [[] for _ in CHART_KEYS]
        previous = None
        for item in data:
            t = item["monotonic"] - self.origin
            if previous is not None and t - previous > 3:
                x.append(float("nan"))
                for values in series:
                    values.append(float("nan"))
            x.append(t)
            for values, key in zip(series, CHART_KEYS):
                values.append(item[key] if item[key] is not None else float("nan"))
            previous = t
        for ax, line, values in zip(self.axes, self.lines, series):
            line.set_data(x, values)
            ax.set_autoscaley_on(True)
            ax.relim()
            ax.autoscale_view()
            if not data:
                ax.set_xlim(0, 10)
                ax.set_ylim(0, 1)
            else:
                ax.set_xlim(max(0, data[0]["monotonic"] - self.origin), max(1, x[-1] + .5))
        self.canvas.draw_idle()

    def close(self):
        if self.closing:
            return
        self.closing = True
        for button in self.buttons:
            button.configure(state="disabled")
        self.status.set("Beenden: Bluetooth trennen und CSV-Datei schließen …")
        if self.worker:
            self.worker.stop()
        # poll() leert restliche Messwerte, schließt CSV und wartet auf Thread-Ende.


def main():
    root = tk.Tk()
    try:
        UD18App(root, demo="--demo" in sys.argv)
    except ImportError as exc:
        messagebox.showerror("Fehlendes Python-Paket",
                             f"{exc}\n\nBitte ausführen:\npy -m pip install bleak matplotlib", parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
