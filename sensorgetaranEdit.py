
import time
import board
import busio
import adafruit_adxl34x
import math
import numpy as np
import glob
import paho.mqtt.client as mqtt
import json
import RPi.GPIO as GPIO
import os
from collections import deque
from typing import Optional, Tuple


# =====================================
# KONFIGURASI BUZZER GPIO 17
# =====================================

BUZZER_PIN = 17

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.output(BUZZER_PIN, GPIO.LOW)


# =====================================
# THRESHOLD PREDICTIVE MAINTENANCE
# =====================================

RMS_THRESHOLD = 5.0
PEAK_THRESHOLD = 10.0
TEMP_THRESHOLD = 40.0


# =====================================
# KONFIGURASI THINGSBOARD MQTT
# =====================================

MQTT_BROKER = "eu.thingsboard.cloud"
MQTT_PORT = 1883

# Simpan token sebagai environment variable TB_ACCESS_TOKEN
ACCESS_TOKEN = "gjo4EaecLjR4ZwdHW4HZ"

if not ACCESS_TOKEN:
    raise SystemExit("ACCESS_TOKEN belum dikonfigurasi!")

MQTT_TOPIC_TELEMETRY = "v1/devices/me/telemetry"
MQTT_TOPIC_ATTRIBUTES = "v1/devices/me/attributes"

INTERVAL_KIRIM = 1.0


# =====================================
# INISIALISASI MQTT
# =====================================

client = mqtt.Client(
    client_id="RaspberryPi4_Monitoring"
)

client.username_pw_set(ACCESS_TOKEN)

client.reconnect_delay_set(
    min_delay=1,
    max_delay=30
)

print("Menghubungkan ke ThingsBoard...")

try:

    client.connect(
        MQTT_BROKER,
        MQTT_PORT,
        keepalive=60
    )

    client.loop_start()

    print("Permintaan koneksi MQTT berhasil!")

except Exception as e:

    print("Koneksi MQTT gagal:", e)

    GPIO.output(BUZZER_PIN, GPIO.LOW)
    GPIO.cleanup()

    raise SystemExit


# =====================================
# FUNGSI PENGIRIMAN MQTT
# =====================================

def kirim_mqtt(topic, data):

    try:

        if not client.is_connected():
            print("MQTT terputus. Menunggu koneksi kembali...")
            return False

        payload = json.dumps(data)

        hasil = client.publish(
            topic,
            payload,
            qos=1
        )

        if hasil.rc != mqtt.MQTT_ERR_SUCCESS:

            print("Gagal memasukkan pesan ke antrean MQTT!")
            return False

        hasil.wait_for_publish(timeout=3)

        if hasil.is_published():

            print("Berhasil mengirim:", topic)
            print("Payload:", payload)

            return True

        else:

            print("Pengiriman MQTT belum terkonfirmasi:", topic)

            return False

    except Exception as e:

        print("Kesalahan pengiriman MQTT:", e)

        return False


# =====================================
# KONFIGURASI DAN DRIVER OLED SSD1306 / SH1106 128x64
# =====================================

# Pilih chipset panel OLED: "SSD1306" (umum 0.96") atau "SH1106" (umum 1.3")
OLED_CHIP: str = "SSD1306"
# Isi None untuk deteksi otomatis (0x3C / 0x3D), atau isi eksplisit misal 0x3C
OLED_I2C_ADDR: Optional[int] = None
OLED_CANDIDATE_ADDRS: Tuple[int, ...] = (0x3C, 0x3D)
OLED_WIDTH: int = 128
OLED_HEIGHT: int = 64
OLED_PAGES: int = 8
OLED_COLS: int = 21
OLED_ROWS: int = 8

_OLED_CHIP_PROFILES = {
    "SSD1306": {
        "col_offset": 0,
        "init_cmds": (
            0xAE,        # Display OFF
            0xD5, 0x80,  # Set display clock divide ratio/oscillator freq
            0xA8, 0x3F,  # Set multiplex ratio (1..64)
            0xD3, 0x00,  # Display offset = 0
            0x40,        # Display start line = 0
            0x8D, 0x14,  # Enable charge pump (wajib agar OLED SSD1306 menyala)
            0x20, 0x02,  # Page addressing mode (wajib untuk perintah 0xB0..0xB7)
            0xA1,        # Segment re-map (col 127 -> SEG0)
            0xC8,        # COM scan direction remapped
            0xDA, 0x12,  # COM pins hardware configuration (128x64)
            0x81, 0xCF,  # Contrast control
            0xD9, 0xF1,  # Pre-charge period (SSD1306 internal charge pump)
            0xDB, 0x40,  # VCOMH deselect level
            0xA4,        # Output follows RAM content
            0xA6,        # Normal (non-inverted) display
            0x2E,        # Nonaktifkan scroll
        ),
    },
    "SH1106": {
        "col_offset": 2,
        "init_cmds": (
            0xAE,        # Display OFF
            0xD5, 0x80,  # Set display clock divide ratio/oscillator freq
            0xA8, 0x3F,  # Set multiplex ratio (1..64)
            0xD3, 0x00,  # Display offset = 0
            0x40,        # Display start line = 0
            0xAD, 0x8B,  # Internal DC-DC enable (SH1106)
            0xA1,        # Segment re-map (col 127 -> SEG0)
            0xC8,        # COM scan direction remapped
            0xDA, 0x12,  # COM pins hardware configuration
            0x81, 0xCF,  # Contrast control
            0xD9, 0x22,  # Pre-charge period
            0xDB, 0x35,  # VCOMH deselect level
            0xA4,        # Display follows GDDRAM
            0xA6,        # Normal (non-inverted) display
        ),
    },
}
OLED_COL_OFFSET: int = _OLED_CHIP_PROFILES[OLED_CHIP]["col_offset"]

# Font bitmap 5x7 kolom-per-byte (LSB di atas) untuk tampilan teks 21x8 pada OLED 128x64
_FONT_5X7 = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00),
    "!": (0x00, 0x00, 0x5F, 0x00, 0x00),
    "%": (0x23, 0x13, 0x08, 0x64, 0x62),
    "&": (0x36, 0x49, 0x55, 0x22, 0x50),
    "(": (0x00, 0x1C, 0x22, 0x41, 0x00),
    ")": (0x00, 0x41, 0x22, 0x1C, 0x00),
    "*": (0x14, 0x08, 0x3E, 0x08, 0x14),
    "+": (0x08, 0x08, 0x3E, 0x08, 0x08),
    ",": (0x00, 0x50, 0x30, 0x00, 0x00),
    "-": (0x08, 0x08, 0x08, 0x08, 0x08),
    ".": (0x00, 0x60, 0x60, 0x00, 0x00),
    "/": (0x20, 0x10, 0x08, 0x04, 0x02),
    "0": (0x3E, 0x51, 0x49, 0x45, 0x3E),
    "1": (0x00, 0x42, 0x7F, 0x40, 0x00),
    "2": (0x42, 0x61, 0x51, 0x49, 0x46),
    "3": (0x21, 0x41, 0x45, 0x4B, 0x31),
    "4": (0x18, 0x14, 0x12, 0x7F, 0x10),
    "5": (0x27, 0x45, 0x45, 0x45, 0x39),
    "6": (0x3C, 0x4A, 0x49, 0x49, 0x30),
    "7": (0x01, 0x71, 0x09, 0x05, 0x03),
    "8": (0x36, 0x49, 0x49, 0x49, 0x36),
    "9": (0x06, 0x49, 0x49, 0x29, 0x1E),
    ":": (0x00, 0x36, 0x36, 0x00, 0x00),
    ";": (0x00, 0x56, 0x36, 0x00, 0x00),
    "<": (0x08, 0x14, 0x22, 0x41, 0x00),
    "=": (0x14, 0x14, 0x14, 0x14, 0x14),
    ">": (0x00, 0x41, 0x22, 0x14, 0x08),
    "?": (0x02, 0x01, 0x51, 0x09, 0x06),
    "A": (0x7E, 0x11, 0x11, 0x11, 0x7E),
    "B": (0x7F, 0x49, 0x49, 0x49, 0x36),
    "C": (0x3E, 0x41, 0x41, 0x41, 0x22),
    "D": (0x7F, 0x41, 0x41, 0x22, 0x1C),
    "E": (0x7F, 0x49, 0x49, 0x49, 0x41),
    "F": (0x7F, 0x09, 0x09, 0x09, 0x01),
    "G": (0x3E, 0x41, 0x49, 0x49, 0x7A),
    "H": (0x7F, 0x08, 0x08, 0x08, 0x7F),
    "I": (0x00, 0x41, 0x7F, 0x41, 0x00),
    "J": (0x20, 0x40, 0x41, 0x3F, 0x01),
    "K": (0x7F, 0x08, 0x14, 0x22, 0x41),
    "L": (0x7F, 0x40, 0x40, 0x40, 0x40),
    "M": (0x7F, 0x02, 0x0C, 0x02, 0x7F),
    "N": (0x7F, 0x04, 0x08, 0x10, 0x7F),
    "O": (0x3E, 0x41, 0x41, 0x41, 0x3E),
    "P": (0x7F, 0x09, 0x09, 0x09, 0x06),
    "Q": (0x3E, 0x41, 0x51, 0x21, 0x5E),
    "R": (0x7F, 0x09, 0x19, 0x29, 0x46),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31),
    "T": (0x01, 0x01, 0x7F, 0x01, 0x01),
    "U": (0x3F, 0x40, 0x40, 0x40, 0x3F),
    "V": (0x1F, 0x20, 0x40, 0x20, 0x1F),
    "W": (0x3F, 0x40, 0x38, 0x40, 0x3F),
    "X": (0x63, 0x14, 0x08, 0x14, 0x63),
    "Y": (0x07, 0x08, 0x70, 0x08, 0x07),
    "Z": (0x61, 0x51, 0x49, 0x45, 0x43),
    "[": (0x00, 0x7F, 0x41, 0x41, 0x00),
    "]": (0x00, 0x41, 0x41, 0x7F, 0x00),
    "^": (0x04, 0x02, 0x01, 0x02, 0x04),
    "_": (0x40, 0x40, 0x40, 0x40, 0x40),
    "a": (0x20, 0x54, 0x54, 0x54, 0x78),
    "b": (0x7F, 0x48, 0x44, 0x44, 0x38),
    "c": (0x38, 0x44, 0x44, 0x44, 0x20),
    "d": (0x38, 0x44, 0x44, 0x48, 0x7F),
    "e": (0x38, 0x54, 0x54, 0x54, 0x18),
    "f": (0x08, 0x7E, 0x09, 0x01, 0x02),
    "g": (0x0C, 0x52, 0x52, 0x52, 0x3E),
    "h": (0x7F, 0x08, 0x04, 0x04, 0x78),
    "i": (0x00, 0x44, 0x7D, 0x40, 0x00),
    "j": (0x20, 0x40, 0x44, 0x3D, 0x00),
    "k": (0x7F, 0x10, 0x28, 0x44, 0x00),
    "l": (0x00, 0x41, 0x7F, 0x40, 0x00),
    "m": (0x7C, 0x04, 0x18, 0x04, 0x78),
    "n": (0x7C, 0x08, 0x04, 0x04, 0x78),
    "o": (0x38, 0x44, 0x44, 0x44, 0x38),
    "p": (0x7C, 0x14, 0x14, 0x14, 0x08),
    "q": (0x08, 0x14, 0x14, 0x18, 0x7C),
    "r": (0x7C, 0x08, 0x04, 0x04, 0x08),
    "s": (0x48, 0x54, 0x54, 0x54, 0x20),
    "t": (0x04, 0x3F, 0x44, 0x40, 0x20),
    "u": (0x3C, 0x40, 0x40, 0x20, 0x7C),
    "v": (0x1C, 0x20, 0x40, 0x20, 0x1C),
    "w": (0x3C, 0x40, 0x30, 0x40, 0x3C),
    "x": (0x44, 0x28, 0x10, 0x28, 0x44),
    "y": (0x0C, 0x50, 0x50, 0x50, 0x3C),
    "z": (0x44, 0x64, 0x54, 0x4C, 0x44),
    "|": (0x00, 0x00, 0x7F, 0x00, 0x00),
    "\u00b0": (0x06, 0x09, 0x09, 0x06, 0x00),
}


class OledI2C128x64:
    """Driver OLED 128x64 (SSD1306 / SH1106) berbasis I2C menggunakan busio.I2C tanpa dependensi eksternal."""

    _CTRL_CMD: int = 0x00
    _CTRL_DATA: int = 0x40
    _CMD_NOP: int = 0xE3
    _DATA_CHUNK: int = 16

    def __init__(self, i2c_bus: busio.I2C, address: int, chip: str = OLED_CHIP) -> None:
        chip_key: str = chip.strip().upper()
        if chip_key not in _OLED_CHIP_PROFILES:
            raise ValueError("Chip OLED tidak didukung: {} (pilih SSD1306 atau SH1106)".format(chip))
        self.chip: str = chip_key
        profile = _OLED_CHIP_PROFILES[chip_key]
        self._i2c: busio.I2C = i2c_bus
        self.address: int = address
        self._addr: int = address
        self._col_offset: int = profile["col_offset"]
        self._init_cmds: Tuple[int, ...] = profile["init_cmds"]
        self._last_pages: Optional[Tuple[bytes, ...]] = None
        self._init_display()

    def _acquire_bus(self) -> None:
        deadline: float = time.monotonic() + 1.0
        while not self._i2c.try_lock():
            if time.monotonic() >= deadline:
                raise RuntimeError("Timeout mengambil kunci bus I2C untuk OLED {}.".format(self.chip))
            time.sleep(0.001)

    def _write_cmds_locked(self, cmds: Tuple[int, ...]) -> None:
        for cmd in cmds:
            self._i2c.writeto(self._addr, bytes((self._CTRL_CMD, cmd)))

    def _write_page_locked(self, page: int, data: bytes) -> None:
        low_col: int = self._col_offset & 0x0F
        high_col: int = (self._col_offset >> 4) & 0x0F
        self._write_cmds_locked((0xB0 | (page & 0x07), 0x00 | low_col, 0x10 | high_col))
        for offset in range(0, OLED_WIDTH, self._DATA_CHUNK):
            chunk = data[offset:offset + self._DATA_CHUNK]
            self._i2c.writeto(self._addr, bytes((self._CTRL_DATA,)) + chunk)

    def _render_line_to_page(self, text: str) -> bytes:
        buf = bytearray(OLED_WIDTH)
        pos = 0
        for ch in text[:OLED_COLS]:
            glyph = _FONT_5X7.get(ch, _FONT_5X7["?"])
            buf[pos:pos + 5] = glyph
            pos += 6
        return bytes(buf)

    def _init_display(self) -> None:
        blank_page = bytes(OLED_WIDTH)
        self._acquire_bus()
        try:
            self._write_cmds_locked(self._init_cmds)
            for page in range(OLED_PAGES):
                self._write_page_locked(page, blank_page)
            self._write_cmds_locked((0xAF,))  # Display ON
            self._last_pages = None
        finally:
            self._i2c.unlock()

    def is_connected(self) -> bool:
        """Periksa apakah panel OLED masih merespons ACK pada bus I2C."""
        self._acquire_bus()
        try:
            self._i2c.writeto(self._addr, bytes((self._CTRL_CMD, self._CMD_NOP)))
            return True
        except OSError:
            return False
        finally:
            self._i2c.unlock()

    def clear(self) -> None:
        """Hapus seluruh piksel GDDRAM pada layar OLED."""
        blank_page = bytes(OLED_WIDTH)
        self._acquire_bus()
        try:
            for page in range(OLED_PAGES):
                self._write_page_locked(page, blank_page)
            self._last_pages = None
        finally:
            self._i2c.unlock()

    def set_power(self, enabled: bool) -> None:
        """Nyalakan (0xAF) atau matikan (0xAE) panel OLED."""
        cmd = 0xAF if enabled else 0xAE
        self._acquire_bus()
        try:
            self._write_cmds_locked((cmd,))
        finally:
            self._i2c.unlock()

    def display_lines(self, lines: Tuple[str, ...], force: bool = False) -> None:
        """Render hingga 8 baris teks (21 karakter/baris) ke OLED dan hanya kirim page yang berubah."""
        normalized = [
            (lines[i] if i < len(lines) else "")[:OLED_COLS].ljust(OLED_COLS)
            for i in range(OLED_ROWS)
        ]
        rendered = tuple(self._render_line_to_page(row) for row in normalized)
        if not force and rendered == self._last_pages:
            return

        self._acquire_bus()
        try:
            for page, page_data in enumerate(rendered):
                if force or self._last_pages is None or page_data != self._last_pages[page]:
                    self._write_page_locked(page, page_data)
            self._last_pages = rendered
        finally:
            self._i2c.unlock()


def init_oled(
    i2c_bus: busio.I2C,
    preferred_addr: Optional[int] = None,
    chip: str = OLED_CHIP,
) -> Optional[OledI2C128x64]:
    """Deteksi alamat OLED I2C (SSD1306/SH1106), cek koneksi, dan kembalikan instance OledI2C128x64 jika tersedia."""
    while not i2c_bus.try_lock():
        time.sleep(0.001)
    try:
        found: list = i2c_bus.scan()
    finally:
        i2c_bus.unlock()

    print("[I2C] Perangkat terdeteksi:", [hex(a) for a in found])

    target_addr: Optional[int] = None
    if preferred_addr is not None:
        if preferred_addr in found:
            target_addr = preferred_addr
    else:
        for candidate in OLED_CANDIDATE_ADDRS:
            if candidate in found:
                target_addr = candidate
                break

    if target_addr is None:
        print(
            "Status OLED {} : TIDAK TERHUBUNG (alamat {} tidak ditemukan di bus I2C)".format(
                chip,
                [hex(a) for a in OLED_CANDIDATE_ADDRS],
            )
        )
        return None

    try:
        oled_device: OledI2C128x64 = OledI2C128x64(i2c_bus, target_addr, chip=chip)
        terhubung: bool = oled_device.is_connected()
        print(
            "Status OLED {} : {} (alamat {})".format(
                oled_device.chip,
                "TERHUBUNG" if terhubung else "TIDAK MERESPONS",
                hex(target_addr),
            )
        )
        return oled_device if terhubung else None
    except Exception as exc:
        print("Status OLED {} : GAGAL INISIALISASI ({}) - {}".format(chip, hex(target_addr), exc))
        return None


# Jendela waktu (detik) untuk rata-rata Frekuensi Dominan Total (selaras dengan widget Avg ThingsBoard ~5 menit)
FREQ_AVG_WINDOW_SECONDS: float = 300.0


def _fmt_val4(val: float) -> str:
    """Format nilai magnitudo (RMS/Peak per sumbu) ke tepat 4 karakter."""
    val = abs(val)
    if val < 9.995:
        return "{:4.2f}".format(val)
    if val < 99.95:
        return "{:4.1f}".format(val)
    return "{:4.0f}".format(min(val, 9999.0))


def _fmt_val6(val: float, decimals_small: int = 2) -> str:
    """Format nilai float menjadi tepat 6 karakter untuk baris tunggal OLED."""
    if decimals_small >= 3 and -9.99 < val < 99.999:
        return "{:6.3f}".format(val)
    if -99.99 < val < 999.99:
        return "{:6.2f}".format(val)
    return "{:6.1f}".format(max(min(val, 9999.9), -999.9))


def _format_alert_row(alert_status: bool, alert_message: str) -> str:
    """Format baris status alarm sesuai widget Status Alarm dashboard ThingsBoard."""
    clean_msg = (alert_message or ("ALERT!" if alert_status else "NORMAL")).strip()
    if not alert_status:
        return "Status : {}".format(clean_msg)[:OLED_COLS]
    if "GETARAN" in clean_msg and "SUHU" in clean_msg:
        return "GETARAN+SUHU TINGGI!"
    if len(clean_msg) <= 12:
        return "Status : {}".format(clean_msg)
    return clean_msg[:OLED_COLS]


def format_oled_lines(
    suhu: Optional[float],
    rms_x: float,
    rms_y: float,
    rms_z: float,
    rms_total: float,
    peak_x: float,
    peak_y: float,
    peak_z: float,
    peak_total: float,
    frekuensi_dominan_avg: float,
    amplitudo_dominan_total: float,
    alert_status: bool = False,
    alert_message: str = "",
) -> Tuple[str, ...]:
    """Format 8 panel parameter dashboard ThingsBoard menjadi 8 baris OLED 128x64 (maks 21 char/baris)."""
    row0 = "RX:{} Y:{} Z:{}".format(_fmt_val4(rms_x), _fmt_val4(rms_y), _fmt_val4(rms_z))
    row1 = "RMS Tot:{} m/s2".format(_fmt_val6(rms_total, decimals_small=2))
    row2 = "PX:{} Y:{} Z:{}".format(_fmt_val4(peak_x), _fmt_val4(peak_y), _fmt_val4(peak_z))
    row3 = "PeakTot:{} m/s2".format(_fmt_val6(peak_total, decimals_small=2))
    row4 = "FreqAvg:{} Hz".format(_fmt_val6(frekuensi_dominan_avg, decimals_small=2))
    row5 = "Amp Dom:{} m/s2".format(_fmt_val6(amplitudo_dominan_total, decimals_small=3))
    if suhu is not None:
        row6 = "Temp   :{}\u00b0C".format(_fmt_val6(suhu, decimals_small=2))
    else:
        row6 = "Temp   : Error"
    row7 = _format_alert_row(alert_status, alert_message)

    return tuple(row[:OLED_COLS] for row in (row0, row1, row2, row3, row4, row5, row6, row7))


# =====================================
# INISIALISASI KOMUNIKASI I2C
# =====================================

oled: Optional[OledI2C128x64] = None

try:

    i2c = busio.I2C(
        board.SCL,
        board.SDA
    )

    oled = init_oled(i2c, OLED_I2C_ADDR, chip=OLED_CHIP)

    # =====================================
    # INISIALISASI SENSOR ADXL345
    # =====================================

    accelerometer = adafruit_adxl34x.ADXL345(i2c)

    print("Sensor ADXL345 berhasil diinisialisasi")

    # =====================================
    # INISIALISASI SENSOR DS18B20
    # =====================================

    base_dir = "/sys/bus/w1/devices/"

    device_folder = glob.glob(base_dir + "28*")

    if not device_folder:

        raise RuntimeError(
            "Sensor DS18B20 tidak ditemukan!"
        )

    device_file = device_folder[0] + "/w1_slave"

    print("Sensor DS18B20 berhasil ditemukan")
    print("GPIO 4 - Komunikasi 1-Wire")

except Exception as e:

    print("Kesalahan inisialisasi sensor:", e)

    GPIO.output(BUZZER_PIN, GPIO.LOW)
    GPIO.cleanup()

    client.loop_stop()
    client.disconnect()

    raise SystemExit


# =====================================
# FUNGSI MEMBACA SUHU DS18B20
# =====================================

def baca_suhu():

    try:

        with open(device_file, "r") as file:
            lines = file.readlines()

        if len(lines) < 2:
            return None

        if "YES" not in lines[0]:
            return None

        posisi = lines[1].find("t=")

        if posisi == -1:
            return None

        suhu = float(
            lines[1][posisi + 2:]
        ) / 1000.0

        return suhu

    except (OSError, ValueError):

        return None


# =====================================
# KALIBRASI OTOMATIS ADXL345
# =====================================

JUMLAH_SAMPEL = 100
INTERVAL = 0.01

print("--------------------------------------")
print("Memulai kalibrasi otomatis...")
print("Pastikan sensor dalam keadaan diam!")

if oled is not None:
    try:
        oled.display_lines((
            "PREDICTIVE MAINT.",
            "Kalibrasi ADXL345...",
            "Jangan gerakkan",
            "sensor...",
        ))
    except Exception as oled_err:
        print("Gagal menulis ke OLED {}:".format(oled.chip), oled_err)

total_x = 0.0
total_y = 0.0
total_z = 0.0

for i in range(JUMLAH_SAMPEL):

    x, y, z = accelerometer.acceleration

    total_x += x
    total_y += y
    total_z += z

    time.sleep(INTERVAL)


offset_x = total_x / JUMLAH_SAMPEL
offset_y = total_y / JUMLAH_SAMPEL
offset_z = total_z / JUMLAH_SAMPEL


print("\nKalibrasi selesai!")

print("Offset X : {:.3f} m/s^2".format(offset_x))
print("Offset Y : {:.3f} m/s^2".format(offset_y))
print("Offset Z : {:.3f} m/s^2".format(offset_z))


# =====================================
# PENGATURAN PENGUKURAN GETARAN
# =====================================

JUMLAH_DATA = 100
INTERVAL_BACA = 0.01

FREKUENSI_SAMPLING = 1 / INTERVAL_BACA

print("--------------------------------------")

print("Frekuensi Sampling : {:.1f} Hz".format(
    FREKUENSI_SAMPLING
))

print("Pengiriman data setiap 1 detik")
print("Tekan CTRL+C untuk berhenti")


# =====================================
# PROGRAM UTAMA
# =====================================

freq_history = deque()

try:

    while True:

        waktu_mulai = time.monotonic()

        # =====================================
        # PENAMPUNG DATA GETARAN
        # =====================================

        data_x = []
        data_y = []
        data_z = []

        # =====================================
        # PENGAMBILAN DATA ADXL345
        # =====================================

        for i in range(JUMLAH_DATA):

            x, y, z = accelerometer.acceleration

            # Menghilangkan offset dan gravitasi
            getaran_x = x - offset_x
            getaran_y = y - offset_y
            getaran_z = z - offset_z

            data_x.append(getaran_x)
            data_y.append(getaran_y)
            data_z.append(getaran_z)

            time.sleep(INTERVAL_BACA)

        # =====================================
        # MEMBACA SUHU DS18B20
        # =====================================

        suhu = baca_suhu()

        # =====================================
        # KONVERSI ARRAY NUMPY
        # =====================================

        data_x = np.array(data_x)
        data_y = np.array(data_y)
        data_z = np.array(data_z)

        # =====================================
        # PERHITUNGAN RMS XYZ
        # =====================================

        rms_x = math.sqrt(np.mean(data_x**2))
        rms_y = math.sqrt(np.mean(data_y**2))
        rms_z = math.sqrt(np.mean(data_z**2))

        # =====================================
        # PERHITUNGAN RMS TOTAL
        # =====================================

        rms_total = math.sqrt(
            rms_x**2 +
            rms_y**2 +
            rms_z**2
        )

        # =====================================
        # PERHITUNGAN PEAK XYZ
        # =====================================

        peak_x = np.max(np.abs(data_x))
        peak_y = np.max(np.abs(data_y))
        peak_z = np.max(np.abs(data_z))

        # =====================================
        # PERHITUNGAN PEAK TOTAL
        # =====================================

        data_total = np.sqrt(
            data_x**2 +
            data_y**2 +
            data_z**2
        )

        peak_total = np.max(data_total)

        # =====================================
        # PERHITUNGAN FFT TOTAL
        # =====================================

        data_total_fft = (
            data_total - np.mean(data_total)
        )

        window = np.hanning(JUMLAH_DATA)

        data_window = data_total_fft * window

        fft_result = np.fft.rfft(data_window)

        amplitudo = (
            2.0 * np.abs(fft_result) / np.sum(window)
        )

        frekuensi = np.fft.rfftfreq(
            JUMLAH_DATA,
            d=INTERVAL_BACA
        )

        amplitudo[0] = 0

        indeks_dominan = np.argmax(amplitudo)

        frekuensi_dominan_total = frekuensi[
            indeks_dominan
        ]

        amplitudo_dominan_total = amplitudo[
            indeks_dominan
        ]

        # Hitung rata-rata bergulir frekuensi dominan (jendela 5 menit seperti widget Avg ThingsBoard)
        waktu_freq = time.monotonic()
        freq_history.append((waktu_freq, float(frekuensi_dominan_total)))
        while freq_history and (waktu_freq - freq_history[0][0]) > FREQ_AVG_WINDOW_SECONDS:
            freq_history.popleft()
        frekuensi_dominan_avg = sum(val for _, val in freq_history) / len(freq_history)

        # =====================================
        # PREDICTIVE MAINTENANCE ALERT
        # =====================================

        alerts = []

        # Pengecekan getaran
        vib_alert = (
            rms_total > RMS_THRESHOLD or
            peak_total > PEAK_THRESHOLD
        )

        # Pengecekan suhu
        temp_alert = (
            suhu is not None and
            suhu > TEMP_THRESHOLD
        )

        if vib_alert:

            alerts.append("GETARAN TIDAK NORMAL!")

        if temp_alert:

            alerts.append("SUHU TINGGI!")

        # =====================================
        # STATUS ALERT DAN BUZZER
        # =====================================

        if alerts:

            alert_status = True

            alert_message = ", ".join(alerts)

            GPIO.output(
                BUZZER_PIN,
                GPIO.HIGH
            )

        else:

            alert_status = False

            alert_message = "NORMAL"

            GPIO.output(
                BUZZER_PIN,
                GPIO.LOW
            )

        # =====================================
        # MENAMPILKAN HASIL PENGUKURAN
        # =====================================

        print("\n========== HASIL PENGUKURAN ==========")

        print("\n--- SUHU ---")

        if suhu is not None:

            print("Suhu DS18B20 : {:.2f} Â°C".format(suhu))

        else:

            print("Suhu DS18B20 : Gagal membaca sensor")

        print("\n--- GETARAN RMS ---")

        print("RMS X     : {:.3f} m/s^2".format(rms_x))
        print("RMS Y     : {:.3f} m/s^2".format(rms_y))
        print("RMS Z     : {:.3f} m/s^2".format(rms_z))
        print("RMS Total : {:.3f} m/s^2".format(rms_total))

        print("\n--- GETARAN PEAK ---")

        print("Peak X     : {:.3f} m/s^2".format(peak_x))
        print("Peak Y     : {:.3f} m/s^2".format(peak_y))
        print("Peak Z     : {:.3f} m/s^2".format(peak_z))
        print("Peak Total : {:.3f} m/s^2".format(peak_total))

        print("\n--- FREKUENSI DAN AMPLITUDO DOMINAN TOTAL ---")

        print(
            "Frekuensi Dominan Total : {:.2f} Hz".format(
                frekuensi_dominan_total
            )
        )

        print(
            "Frekuensi Dominan Avg   : {:.2f} Hz ({} sampel)".format(
                frekuensi_dominan_avg,
                len(freq_history),
            )
        )

        print(
            "Amplitudo Dominan Total : {:.3f} m/s^2".format(
                amplitudo_dominan_total
            )
        )

        print("\n--- PREDICTIVE MAINTENANCE ALERT ---")

        print("Alert Status  :", alert_status)
        print("Alert Message :", alert_message)

        print("\n--- OLED {} 128x64 ---".format(oled.chip if oled is not None else OLED_CHIP))

        if oled is not None:
            try:
                oled_rows = format_oled_lines(
                    suhu=suhu,
                    rms_x=rms_x,
                    rms_y=rms_y,
                    rms_z=rms_z,
                    rms_total=rms_total,
                    peak_x=peak_x,
                    peak_y=peak_y,
                    peak_z=peak_z,
                    peak_total=peak_total,
                    frekuensi_dominan_avg=frekuensi_dominan_avg,
                    amplitudo_dominan_total=amplitudo_dominan_total,
                    alert_status=alert_status,
                    alert_message=alert_message,
                )
                oled.display_lines(oled_rows)
                print("Status OLED : TERHUBUNG ({})".format(hex(oled.address)))
                for idx, baris in enumerate(oled_rows, start=1):
                    print("Baris {}     : {}".format(idx, baris))
            except Exception as oled_err:
                print("Status OLED : TERPUTUS / GAGAL ({}) - {}".format(hex(oled.address), oled_err))
        else:
            print("Status OLED : TIDAK TERHUBUNG (tidak terdeteksi saat inisialisasi)")

        # =====================================
        # PENGIRIMAN ATTRIBUTES THINGSBOARD
        # =====================================

        attr_payload = {

            "alert_status": alert_status,

            "alert_message": alert_message

        }

        print("\n--- MQTT ATTRIBUTES ---")

        kirim_mqtt(
            MQTT_TOPIC_ATTRIBUTES,
            attr_payload
        )

        # =====================================
        # DATA TELEMETRY THINGSBOARD
        # =====================================

        telemetry = {

            "rms_x": float(rms_x),
            "rms_y": float(rms_y),
            "rms_z": float(rms_z),
            "rms_total": float(rms_total),

            "peak_x": float(peak_x),
            "peak_y": float(peak_y),
            "peak_z": float(peak_z),
            "peak_total": float(peak_total),

            "frekuensi_dominan_total": float(
                frekuensi_dominan_total
            ),

            "amplitudo_dominan_total": float(
                amplitudo_dominan_total
            )

        }

        # Suhu hanya dikirim jika pembacaan berhasil
        if suhu is not None:

            telemetry["suhu"] = float(suhu)

        # =====================================
        # PENGIRIMAN TELEMETRY THINGSBOARD
        # =====================================

        print("\n--- MQTT TELEMETRY ---")

        kirim_mqtt(
            MQTT_TOPIC_TELEMETRY,
            telemetry
        )

        print("--------------------------------------")

        # =====================================
        # PENGATURAN INTERVAL PENGIRIMAN
        # =====================================

        waktu_proses = (
            time.monotonic() - waktu_mulai
        )

        waktu_tunggu = (
            INTERVAL_KIRIM - waktu_proses
        )

        if waktu_tunggu > 0:

            time.sleep(waktu_tunggu)


except KeyboardInterrupt:

    print("\nProgram dihentikan oleh pengguna.")

except Exception as e:

    print("\nTerjadi kesalahan pada program:", e)

finally:

    # =====================================
    # PEMBERSIHAN OLED DAN GPIO
    # =====================================

    if oled is not None:
        try:
            oled.clear()
            oled.set_power(False)
        except Exception:
            pass

    GPIO.output(
        BUZZER_PIN,
        GPIO.LOW
    )

    GPIO.cleanup()

    # =====================================
    # MEMUTUSKAN KONEKSI MQTT
    # =====================================

    print("\nMemutuskan koneksi MQTT...")

    client.loop_stop()

    client.disconnect()

    print("Koneksi ThingsBoard ditutup.")
    print("Pembacaan sensor selesai.")

