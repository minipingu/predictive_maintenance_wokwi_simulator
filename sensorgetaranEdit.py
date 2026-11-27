
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
# KONFIGURASI DAN DRIVER LCD I2C 16x2
# =====================================

# Isi None untuk deteksi otomatis (0x27 / 0x3F), atau isi eksplisit misal 0x27
LCD_I2C_ADDR: Optional[int] = None
LCD_CANDIDATE_ADDRS: Tuple[int, ...] = (
    0x27, 0x3F, 0x26, 0x25, 0x24, 0x23, 0x22, 0x21, 0x20,
    0x3E, 0x3D, 0x3C, 0x3B, 0x3A, 0x39, 0x38,
)
LCD_COLS: int = 16
LCD_ROWS: int = 2


class I2CLcd16x2:
    """Driver LCD karakter 16x2 berbasis PCF8574 menggunakan busio.I2C."""

    _RS: int = 0x01
    _EN: int = 0x04
    _BACKLIGHT: int = 0x08
    _ROW_OFFSETS: Tuple[int, int] = (0x80, 0xC0)

    def __init__(self, i2c_bus: busio.I2C, address: int) -> None:
        self._i2c: busio.I2C = i2c_bus
        self.address: int = address
        self._addr: int = address
        self._backlight: int = self._BACKLIGHT
        self._last_rows: Optional[Tuple[str, str]] = None
        self._init_display()

    def is_connected(self) -> bool:
        """Periksa apakah modul PCF8574 masih merespons ACK pada bus I2C."""
        self._acquire_bus()
        try:
            self._i2c.writeto(self._addr, bytes([self._backlight]))
            return True
        except OSError:
            return False
        finally:
            self._i2c.unlock()

    def _acquire_bus(self) -> None:
        deadline: float = time.monotonic() + 1.0
        while not self._i2c.try_lock():
            if time.monotonic() >= deadline:
                raise RuntimeError("Timeout mengambil kunci bus I2C untuk LCD.")
            time.sleep(0.001)

    def _write_nibble_locked(self, nibble_and_mode: int) -> None:
        data: int = nibble_and_mode | self._backlight
        self._i2c.writeto(self._addr, bytes([data]))
        self._i2c.writeto(self._addr, bytes([data | self._EN]))
        time.sleep(0.0002)
        self._i2c.writeto(self._addr, bytes([data]))
        time.sleep(0.0001)

    def _write_byte_locked(self, value: int, mode: int = 0) -> None:
        high_nibble: int = (value & 0xF0) | mode
        low_nibble: int = ((value << 4) & 0xF0) | mode
        self._write_nibble_locked(high_nibble)
        self._write_nibble_locked(low_nibble)

    def _init_display(self) -> None:
        time.sleep(0.05)
        self._acquire_bus()
        try:
            for delay in (0.005, 0.005, 0.001):
                self._write_nibble_locked(0x30)
                time.sleep(delay)
            self._write_nibble_locked(0x20)
            time.sleep(0.001)

            for cmd in (0x28, 0x08, 0x01, 0x06, 0x0C):
                self._write_byte_locked(cmd, mode=0)
                if cmd == 0x01:
                    time.sleep(0.003)
        finally:
            self._i2c.unlock()

    def clear(self) -> None:
        """Hapus seluruh karakter pada layar LCD."""
        self._acquire_bus()
        try:
            self._write_byte_locked(0x01, mode=0)
            time.sleep(0.002)
            self._last_rows = None
        finally:
            self._i2c.unlock()

    def set_backlight(self, enabled: bool) -> None:
        """Nyalakan atau matikan lampu latar LCD."""
        self._backlight = self._BACKLIGHT if enabled else 0x00
        self._acquire_bus()
        try:
            self._i2c.writeto(self._addr, bytes([self._backlight]))
        finally:
            self._i2c.unlock()

    def display_lines(self, line1: str, line2: str, force: bool = False) -> None:
        """Tampilkan 2 baris teks (16 karakter per baris) tanpa flicker dan skip I2C jika teks sama."""
        rows: Tuple[str, str] = (
            line1[:LCD_COLS].ljust(LCD_COLS),
            line2[:LCD_COLS].ljust(LCD_COLS),
        )
        if not force and rows == self._last_rows:
            return
        self._acquire_bus()
        try:
            for row_idx, text in enumerate(rows):
                self._write_byte_locked(self._ROW_OFFSETS[row_idx], mode=0)
                for ch in text:
                    self._write_byte_locked(ord(ch), mode=self._RS)
            self._last_rows = rows
        finally:
            self._i2c.unlock()


def init_lcd(i2c_bus: busio.I2C, preferred_addr: Optional[int] = None) -> Optional[I2CLcd16x2]:
    """Deteksi alamat LCD I2C, cek koneksi, dan kembalikan instance I2CLcd16x2 jika tersedia."""
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
        for candidate in LCD_CANDIDATE_ADDRS:
            if candidate in found:
                target_addr = candidate
                break

    if target_addr is None:
        print(
            "Status LCD I2C : TIDAK TERHUBUNG (alamat {} tidak ditemukan di bus I2C)".format(
                [hex(a) for a in LCD_CANDIDATE_ADDRS[:2]]
            )
        )
        return None

    try:
        lcd_device: I2CLcd16x2 = I2CLcd16x2(i2c_bus, target_addr)
        terhubung: bool = lcd_device.is_connected()
        print(
            "Status LCD I2C : {} (alamat {})".format(
                "TERHUBUNG" if terhubung else "TIDAK MERESPONS",
                hex(target_addr),
            )
        )
        return lcd_device if terhubung else None
    except Exception as exc:
        print("Status LCD I2C : GAGAL INISIALISASI ({}) - {}".format(hex(target_addr), exc))
        return None


def _fmt_4char(val: float) -> str:
    """Format angka positif ke tepat 4 karakter agar muat di kolom LCD 16x2."""
    val = abs(val)
    if val < 10.0:
        return "{:4.2f}".format(val)
    if val < 100.0:
        return "{:4.1f}".format(val)
    return "{:4.0f}".format(min(val, 9999.0))


def format_lcd_lines(
    suhu: Optional[float],
    rms_total: float,
    peak_total: float,
    alert_status: bool = False,
) -> Tuple[str, str]:
    """Format output Temp, RMS, dan Peak ke dalam 2 baris LCD 16x2."""
    status_tag = "!!" if alert_status else "OK"
    if suhu is not None:
        line1 = "Temp:{:6.2f} C {}".format(suhu, status_tag)
    else:
        line1 = "Temp: Error   {}".format(status_tag)

    line2 = "RMS:{} PK:{}".format(_fmt_4char(rms_total), _fmt_4char(peak_total))
    return line1[:LCD_COLS], line2[:LCD_COLS]


# =====================================
# INISIALISASI KOMUNIKASI I2C
# =====================================

lcd: Optional[I2CLcd16x2] = None

try:

    i2c = busio.I2C(
        board.SCL,
        board.SDA
    )

    lcd = init_lcd(i2c, LCD_I2C_ADDR)

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

if lcd is not None:
    try:
        lcd.display_lines("Kalibrasi ADXL..", "Jangan gerak...")
    except Exception as lcd_err:
        print("Gagal menulis ke LCD:", lcd_err)

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
            "Amplitudo Dominan Total : {:.3f} m/s^2".format(
                amplitudo_dominan_total
            )
        )

        print("\n--- PREDICTIVE MAINTENANCE ALERT ---")

        print("Alert Status  :", alert_status)
        print("Alert Message :", alert_message)

        print("\n--- LCD I2C 16x2 ---")

        if lcd is not None:
            try:
                baris1, baris2 = format_lcd_lines(suhu, rms_total, peak_total, alert_status)
                lcd.display_lines(baris1, baris2)
                print("Status LCD : TERHUBUNG ({})".format(hex(lcd.address)))
                print("Baris 1    :", baris1)
                print("Baris 2    :", baris2)
            except Exception as lcd_err:
                print("Status LCD : TERPUTUS / GAGAL ({}) - {}".format(hex(lcd.address), lcd_err))
        else:
            print("Status LCD : TIDAK TERHUBUNG (tidak terdeteksi saat inisialisasi)")

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
    # PEMBERSIHAN LCD DAN GPIO
    # =====================================

    if lcd is not None:
        try:
            lcd.clear()
            lcd.set_backlight(False)
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

