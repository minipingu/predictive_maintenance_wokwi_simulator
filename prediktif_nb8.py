
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
import threading
from typing import Callable, Optional, Tuple


# =====================================
# KONFIGURASI BUZZER GPIO 17
# =====================================

# Catatan Pin:
# - BCM GPIO 17 = Pin Fisik 11 (bawaan prediktif_nb8.py)
# - Jika kabel Anda mengikuti WIRING_DIAGRAM_RPI4.svg (Pin Fisik 12), ganti ke 18.
BUZZER_PIN = 17

# Mode buzzer:
# - "AMBULANCE" : PWM Sirine Ambulans dua nada 960 Hz <-> 770 Hz (rekomendasi untuk buzzer pasif)
# - "PASSIVE"   : Sama dengan "AMBULANCE" (PWM 960 Hz <-> 770 Hz)
# - "AUTO"      : Bergantian PWM sirine & DC HIGH (untuk deteksi ganda pasif/aktif)
# - "ACTIVE"    : Pulsa DC HIGH/LOW (khusus buzzer aktif)
BUZZER_MODE = "AMBULANCE"

# Frekuensi sirine ambulans (Dua Nada Hi-Lo)
NADA_AMBULANCE_HI = 960   # Hz (Nada tinggi "Nee")
NADA_AMBULANCE_LO = 770   # Hz (Nada rendah "Naw")
DURASI_NADA_AMBULANCE = 0.35  # Detik per nada

# Ubah ke True jika modul buzzer 3-pin Anda bertipe Active-LOW (berbunyi saat diberi LOW)
BUZZER_ACTIVE_LOW = False

# False = buzzer tetap diam saat booting sebelum threshold tercapai
BUZZER_TEST_STARTUP = False


class BuzzerController:
    """Pengendali buzzer non-blocking (PWM hanya aktif saat alarm menyala; mati total saat idle)."""

    def __init__(
        self,
        pin: int,
        mode: str = "AMBULANCE",
        active_low: bool = False,
        test_startup: bool = False,
    ) -> None:
        self.pin = pin
        self.mode = mode.upper()
        self.active_low = active_low
        self._alarm_on = False
        self._lock = threading.Lock()
        self._hw_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._pwm = None
        self._pwm_supported = self.mode in ("AMBULANCE", "PASSIVE", "AUTO")
        self._is_silent = False
        self._thread: Optional[threading.Thread] = None

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.pin, GPIO.OUT)
        self._force_off_locked()

        if test_startup:
            self.startup_test()

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        print(
            "[BUZZER] Siap di BCM GPIO {} (Mode: {}, Active-LOW: {}, Startup Test: {})".format(
                self.pin,
                self.mode,
                self.active_low,
                self._pwm_supported and test_startup,
            )
        )

    def _off_level(self) -> int:
        return GPIO.HIGH if self.active_low else GPIO.LOW

    def _on_level(self) -> int:
        return GPIO.LOW if self.active_low else GPIO.HIGH

    def _stop_pwm_locked(self) -> None:
        if self._pwm is not None:
            try:
                self._pwm.ChangeDutyCycle(100 if self.active_low else 0)
                self._pwm.stop()
            except Exception:
                pass
            self._pwm = None

    def _force_off_locked(self) -> None:
        self._stop_pwm_locked()
        GPIO.setup(self.pin, GPIO.OUT)
        GPIO.output(self.pin, self._off_level())
        self._is_silent = True

    def _ensure_pwm_locked(self, freq_hz: int) -> bool:
        if not self._pwm_supported:
            return False
        if self._pwm is None:
            try:
                self._pwm = GPIO.PWM(self.pin, freq_hz)
                self._pwm.start(50.0)
                self._is_silent = False
                return True
            except Exception as exc:
                print(
                    "[BUZZER WARN] PWM gagal di GPIO {} ({}), fallback ke mode DC".format(
                        self.pin, exc
                    )
                )
                self._pwm = None
                self._pwm_supported = False
                return False
        return True

    def _set_dc(self, active: bool) -> None:
        with self._hw_lock:
            if not active:
                self._force_off_locked()
                return
            self._is_silent = False
            if self._pwm is not None:
                self._pwm.ChangeDutyCycle(0 if self.active_low else 100)
            else:
                GPIO.output(self.pin, self._on_level())

    def _set_tone(self, freq_hz: int, duty: float = 50.0) -> None:
        with self._hw_lock:
            self._is_silent = False
            if self._ensure_pwm_locked(freq_hz):
                self._pwm.ChangeFrequency(freq_hz)
                self._pwm.ChangeDutyCycle(duty)
            else:
                GPIO.output(self.pin, self._on_level())

    def _silence(self) -> None:
        with self._hw_lock:
            if not self._is_silent or self._pwm is not None:
                self._force_off_locked()

    def startup_test(self) -> None:
        """Bunyi tes singkat 1x saat boot jika BUZZER_TEST_STARTUP=True."""
        try:
            self._set_tone(NADA_AMBULANCE_HI, 50.0)
            time.sleep(0.15)
            self._set_tone(NADA_AMBULANCE_LO, 50.0)
            time.sleep(0.15)
        finally:
            self._silence()

    def set_alarm(self, active: bool) -> None:
        changed = False
        with self._lock:
            if self._alarm_on != active:
                self._alarm_on = active
                changed = True
        if not active:
            self._silence()
        if changed:
            self._wake_event.set()

    def is_alarm_on(self) -> bool:
        with self._lock:
            return self._alarm_on

    def _wait_step(self, duration_sec: float) -> bool:
        """Tunggu selama duration_sec; kembalikan True jika harus keluar/berhenti lebih awal."""
        if self._stop_event.is_set() or not self.is_alarm_on():
            return True
        triggered = self._wake_event.wait(timeout=duration_sec)
        if triggered:
            self._wake_event.clear()
        return self._stop_event.is_set() or not self.is_alarm_on()

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            if not self.is_alarm_on():
                self._silence()
                # Tidur pasif sampai ada perubahan status alarm atau shutdown (nol polling PWM)
                self._wake_event.wait()
                self._wake_event.clear()
                continue

            if self.mode in ("AMBULANCE", "PASSIVE"):
                # Sirine ambulans dua nada kontinyu (960 Hz "Nee" <-> 770 Hz "Naw")
                self._set_tone(NADA_AMBULANCE_HI, 50.0)
                if self._wait_step(DURASI_NADA_AMBULANCE):
                    continue
                self._set_tone(NADA_AMBULANCE_LO, 50.0)
                self._wait_step(DURASI_NADA_AMBULANCE)
            elif self.mode == "ACTIVE":
                # Pulsa DC cepat menyerupai sirine untuk buzzer aktif
                self._set_dc(True)
                if self._wait_step(0.25):
                    continue
                with self._hw_lock:
                    GPIO.output(self.pin, self._off_level())
                self._wait_step(0.12)
            else:
                # Mode AUTO: Sirine PWM (Pasif) + Pulsa DC HIGH (Aktif)
                self._set_tone(NADA_AMBULANCE_HI, 50.0)
                if self._wait_step(0.25):
                    continue
                self._set_tone(NADA_AMBULANCE_LO, 50.0)
                if self._wait_step(0.25):
                    continue
                self._set_dc(True)
                if self._wait_step(0.15):
                    continue
                with self._hw_lock:
                    if self._pwm is not None:
                        self._pwm.ChangeDutyCycle(100 if self.active_low else 0)
                    else:
                        GPIO.output(self.pin, self._off_level())
                self._wait_step(0.08)

    def cleanup(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        with self._hw_lock:
            try:
                self._force_off_locked()
            except Exception:
                pass


buzzer = BuzzerController(
    pin=BUZZER_PIN,
    mode=BUZZER_MODE,
    active_low=BUZZER_ACTIVE_LOW,
    test_startup=BUZZER_TEST_STARTUP,
)


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

    buzzer.cleanup()
    GPIO.cleanup()

    raise SystemExit


# =====================================
# FUNGSI PENGIRIMAN MQTT
# =====================================

def kirim_mqtt(topic, data, wait=False):

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

        if wait:
            hasil.wait_for_publish(timeout=1.5)
            if hasil.is_published():
                print("Berhasil mengirim (terkonfirmasi):", topic)
                print("Payload:", payload)
                return True
            else:
                print("Pengiriman MQTT belum terkonfirmasi:", topic)
                return False

        print("Mengirim (async):", topic)
        print("Payload:", payload)
        return True

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
        # Set RS and data lines while EN=0 first so tAS setup time is satisfied before EN rises.
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
        """Tampilkan 2 baris teks (16 karakter per baris) tanpa flicker dan hanya kirim I2C jika berubah."""
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


def format_lcd_lines(
    suhu: Optional[float],
    rms_total: float,
    peak_total: float = 0.0,
    peak_alert: bool = False,
    rms_warning: bool = False,
    temp_alert: bool = False,
) -> Tuple[str, str]:
    """Format status alarm/warning atau suhu & RMS agar pas di layar 16x2."""
    suhu_txt = "{:.1f}C".format(suhu) if suhu is not None else "ERR"

    if peak_alert and temp_alert:
        return "BERGETAR KENCANG", "& SUHU TINGGI!"
    if peak_alert:
        return "BERGETAR KENCANG", "P:{:.1f} R:{:.1f}".format(peak_total, rms_total)
    if rms_warning and temp_alert:
        return "WARN:RMS & PANAS", "R:{:.2f} S:{}".format(rms_total, suhu_txt)
    if rms_warning:
        return "WARN: RMS TINGGI", "R:{:.2f} S:{}".format(rms_total, suhu_txt)
    if temp_alert:
        return "SUHU TINGGI!", "S:{} R:{:.2f}".format(suhu_txt, rms_total)

    if suhu is not None:
        line1: str = "Suhu:{:6.2f} C".format(suhu)
    else:
        line1 = "Suhu: Error"

    line2: str = "RMS:{:6.3f} m/s2".format(rms_total)
    return line1, line2


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

    # Target resolusi hardware DS18B20:
    # 9-bit = ~94 ms (step 0.50 C) | 10-bit = ~188 ms (step 0.25 C)
    # 11-bit = ~375 ms (step 0.125 C) | 12-bit = ~750 ms (step 0.0625 C)
    DS18B20_RESOLUTION = 10

    base_dir = "/sys/bus/w1/devices/"

    device_folder = glob.glob(base_dir + "28*")

    if not device_folder:

        raise RuntimeError(
            "Sensor DS18B20 tidak ditemukan!"
        )

    device_dir = device_folder[0]
    device_file = os.path.join(device_dir, "w1_slave")
    temp_sysfs_file = os.path.join(device_dir, "temperature")
    resolution_file = os.path.join(device_dir, "resolution")

    print("Sensor DS18B20 berhasil ditemukan:", os.path.basename(device_dir))
    print("GPIO 4 - Komunikasi 1-Wire")

    # Coba atur resolusi DS18B20 agar konversi hardware lebih cepat (opsional, tergantung izin sysfs)
    if os.path.exists(resolution_file):
        try:
            with open(resolution_file, "w") as rf:
                rf.write(str(DS18B20_RESOLUTION))
        except OSError as res_err:
            print(
                "[DS18B20 INFO] Tidak dapat ubah resolusi sysfs ({}): memakai resolusi saat ini.".format(
                    res_err
                )
            )
        try:
            with open(resolution_file, "r") as rf:
                cur_res = rf.read().strip()
            conv_est = {"9": "~94 ms", "10": "~188 ms", "11": "~375 ms", "12": "~750 ms"}.get(
                cur_res, "unknown"
            )
            print(
                "[DS18B20] Resolusi aktif : {}-bit (estimasi konversi chip {})".format(
                    cur_res, conv_est
                )
            )
        except OSError:
            pass

except Exception as e:

    print("Kesalahan inisialisasi sensor:", e)

    buzzer.cleanup()
    GPIO.cleanup()

    client.loop_stop()
    client.disconnect()

    raise SystemExit


# =====================================
# FUNGSI & THREAD REALTIME SUHU DS18B20
# =====================================

def _baca_suhu_raw() -> Optional[float]:
    """Membaca 1 sampel mentah suhu DS18B20 (sysfs 'temperature' cepat, fallback ke 'w1_slave')."""
    if os.path.exists(temp_sysfs_file):
        try:
            with open(temp_sysfs_file, "r") as f:
                raw = f.read().strip()
            if raw:
                val = int(raw) / 1000.0
                if -55.0 <= val <= 125.0:
                    return val
        except (OSError, ValueError):
            pass

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

        if -55.0 <= suhu <= 125.0:
            return suhu
        return None

    except (OSError, ValueError):

        return None


def baca_suhu() -> Optional[float]:
    """Membaca suhu DS18B20 dan menyaring glitch reset daya (85.0 C)."""
    val = _baca_suhu_raw()
    if val is not None and abs(val - 85.0) < 0.001:
        # 85.0 C adalah nilai power-on reset DS18B20; konfirmasi 1x lagi sebelum diterima
        time.sleep(0.1)
        val_confirm = _baca_suhu_raw()
        if val_confirm is None or abs(val_confirm - 85.0) >= 0.5:
            return val_confirm
    return val


class DS18B20RealtimeReader:
    """Membaca DS18B20 terus-menerus pada background thread agar main loop bebas blocking."""

    def __init__(
        self,
        on_update: Optional[Callable[[Optional[float]], None]] = None,
        stale_sec: float = 5.0,
    ) -> None:
        self._on_update = on_update
        self._stale_sec = stale_sec
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._latest_temp: Optional[float] = None
        self._last_update_mono: float = 0.0
        self._last_conv_ms: float = 0.0
        self._update_hz: float = 0.0
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        # Ambil 1 pembacaan awal agar nilai langsung siap saat loop utama mulai
        t0 = time.monotonic()
        init_val = baca_suhu()
        dt_ms = (time.monotonic() - t0) * 1000.0
        if init_val is not None:
            now = time.monotonic()
            with self._lock:
                self._latest_temp = init_val
                self._last_update_mono = now
                self._last_conv_ms = dt_ms
                if dt_ms > 0:
                    self._update_hz = 1000.0 / dt_ms
            if self._on_update is not None:
                try:
                    self._on_update(init_val)
                except Exception:
                    pass
        print(
            "[DS18B20] Waktu konversi awal: {:.1f} ms ({:.2f} Hz) | Background realtime aktif".format(
                dt_ms,
                (1000.0 / dt_ms) if dt_ms > 0 else 0.0,
            )
        )

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            val = baca_suhu()
            conv_ms = (time.monotonic() - t0) * 1000.0
            now = time.monotonic()

            if val is not None:
                with self._lock:
                    if self._last_update_mono > 0:
                        cycle_dt = now - self._last_update_mono
                        if cycle_dt > 0:
                            self._update_hz = 1.0 / cycle_dt
                    self._latest_temp = val
                    self._last_update_mono = now
                    self._last_conv_ms = conv_ms
                if self._on_update is not None:
                    try:
                        self._on_update(val)
                    except Exception:
                        pass
                if conv_ms < 10.0:
                    self._stop_event.wait(0.05)
            else:
                with self._lock:
                    if (
                        self._last_update_mono > 0
                        and (now - self._last_update_mono) > self._stale_sec
                    ):
                        self._latest_temp = None
                if self._on_update is not None:
                    try:
                        self._on_update(self.get_latest())
                    except Exception:
                        pass
                self._stop_event.wait(0.1)

    def get_latest(self) -> Optional[float]:
        with self._lock:
            if self._latest_temp is None:
                return None
            if (time.monotonic() - self._last_update_mono) > self._stale_sec:
                return None
            return self._latest_temp

    def get_metrics(self) -> Tuple[Optional[float], float, float, float]:
        """Mengembalikan (suhu, conv_ms, rate_hz, age_ms)."""
        with self._lock:
            now = time.monotonic()
            if self._latest_temp is None or (now - self._last_update_mono) > self._stale_sec:
                return None, self._last_conv_ms, 0.0, 0.0
            age_ms = max(0.0, (now - self._last_update_mono) * 1000.0)
            return self._latest_temp, self._last_conv_ms, self._update_hz, age_ms

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)


alert_lock = threading.Lock()
alarm_armed = False
vib_alert_state = False
temp_alert_state = False


def evaluate_and_drive_buzzer() -> bool:
    with alert_lock:
        active = alarm_armed and (vib_alert_state or temp_alert_state)
    buzzer.set_alarm(active)
    return active


def on_temp_update(val: Optional[float]) -> None:
    global temp_alert_state
    with alert_lock:
        temp_alert_state = (val is not None and val > TEMP_THRESHOLD)
    evaluate_and_drive_buzzer()


temp_reader = DS18B20RealtimeReader(on_update=on_temp_update)
temp_reader.start()


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

with alert_lock:
    alarm_armed = True
evaluate_and_drive_buzzer()

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
        # PENGAMBILAN DATA ADXL345 (PACING PRESISI)
        # =====================================

        next_sample = time.monotonic()
        for i in range(JUMLAH_DATA):

            x, y, z = accelerometer.acceleration

            # Menghilangkan offset dan gravitasi
            getaran_x = x - offset_x
            getaran_y = y - offset_y
            getaran_z = z - offset_z

            data_x.append(getaran_x)
            data_y.append(getaran_y)
            data_z.append(getaran_z)

            next_sample += INTERVAL_BACA
            sisa_tidur = next_sample - time.monotonic()
            if sisa_tidur > 0:
                time.sleep(sisa_tidur)

        # =====================================
        # AMBIL SUHU DS18B20 REALTIME (NON-BLOCKING)
        # =====================================

        suhu, temp_conv_ms, temp_rate_hz, temp_age_ms = temp_reader.get_metrics()

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

        # Pengecekan getaran (dipisah: peak_total memicu buzzer, rms_total hanya warning tanpa buzzer)
        peak_alert = peak_total > PEAK_THRESHOLD
        rms_warning = rms_total > RMS_THRESHOLD

        # Pengecekan suhu
        temp_alert = (
            suhu is not None and
            suhu > TEMP_THRESHOLD
        )

        if peak_alert:

            alerts.append("BERGETAR KENCANG!")

        if rms_warning:

            alerts.append("WARNING: GETARAN RMS TINGGI!")

        if temp_alert:

            alerts.append("SUHU TINGGI!")

        # =====================================
        # STATUS ALERT DAN BUZZER (NON-BLOCKING)
        # =====================================

        with alert_lock:
            vib_alert_state = peak_alert
            temp_alert_state = temp_alert

        alert_status = evaluate_and_drive_buzzer()
        alert_message = ", ".join(alerts) if alerts else "NORMAL"

        # =====================================
        # MENAMPILKAN HASIL PENGUKURAN
        # =====================================

        print("\n========== HASIL PENGUKURAN ==========")

        print("\n--- SUHU ---")

        if suhu is not None:

            print(
                "Suhu DS18B20 : {:.2f} °C (konversi: {:.1f} ms | rate: {:.2f} Hz | umur data: {:.0f} ms)".format(
                    suhu,
                    temp_conv_ms,
                    temp_rate_hz,
                    temp_age_ms,
                )
            )

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
        print("RMS Warning   :", rms_warning)
        print("Peak Alert    :", peak_alert)
        print("Alert Message :", alert_message)
        idle_level_str = "HIGH" if BUZZER_ACTIVE_LOW else "LOW"
        print(
            "Buzzer        : {} (GPIO {} | Mode {} | Pin Idle: {}{})".format(
                "AKTIF" if alert_status else "MATI",
                BUZZER_PIN,
                BUZZER_MODE,
                idle_level_str,
                ""
                if alert_status
                else " | Ubah BUZZER_ACTIVE_LOW=True jika tetap bunyi saat MATI",
            )
        )

        print("\n--- LCD I2C 16x2 ---")

        if lcd is not None:
            try:
                baris1, baris2 = format_lcd_lines(
                    suhu,
                    rms_total,
                    peak_total=peak_total,
                    peak_alert=peak_alert,
                    rms_warning=rms_warning,
                    temp_alert=temp_alert,
                )
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

            "warning_status": rms_warning,

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

        # =====================================
        # PENGATURAN INTERVAL PENGIRIMAN
        # =====================================

        waktu_proses = (
            time.monotonic() - waktu_mulai
        )

        print(
            "\nWaktu Siklus  : {:.3f} s ({:.2f} Hz) | Target: {:.2f} s".format(
                waktu_proses,
                (1.0 / waktu_proses) if waktu_proses > 0 else 0.0,
                INTERVAL_KIRIM,
            )
        )
        print("--------------------------------------")

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
    # PEMBERSIHAN THREAD, LCD DAN GPIO
    # =====================================

    temp_reader.stop()
    buzzer.cleanup()

    if lcd is not None:
        try:
            lcd.clear()
            lcd.set_backlight(False)
        except Exception:
            pass

    GPIO.cleanup()

    # =====================================
    # MEMUTUSKAN KONEKSI MQTT
    # =====================================

    print("\nMemutuskan koneksi MQTT...")

    client.loop_stop()

    client.disconnect()

    print("Koneksi ThingsBoard ditutup.")
    print("Pembacaan sensor selesai.")

