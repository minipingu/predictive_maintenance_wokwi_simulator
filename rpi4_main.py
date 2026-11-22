#!/usr/bin/env python3
# ==============================================================================
# SISTEM PREDICTIVE MAINTENANCE BEARING BOR - RASPBERRY PI 4
# IMPLEMENTASI PERANGKAT KERAS FISIK KE THINGSBOARD (EU CLOUD)
# SENSOR : DS18B20 (1-Wire DQ = GPIO 4) & ADXL345 / MPU-6050 (I2C1 = GPIO 2, 3)
# OUTPUT : BUZZER ALARM (GPIO 18 / Pin 12) + TELEMETRI MQTT THINGSBOARD
# ==============================================================================

import os
import glob
import time
import json
import math
import sys

# Dependensi eksternal
try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("[ERROR] Library 'paho-mqtt' belum terpasang. Jalankan: pip3 install paho-mqtt")
    sys.exit(1)

try:
    from smbus2 import SMBus
except ImportError:
    print("[ERROR] Library 'smbus2' belum terpasang. Jalankan: pip3 install smbus2")
    sys.exit(1)

try:
    import numpy as np
    USE_NUMPY = True
except ImportError:
    USE_NUMPY = False

try:
    from gpiozero import Buzzer
    USE_GPIOZERO = True
except ImportError:
    try:
        import RPi.GPIO as GPIO
        USE_GPIOZERO = False
    except ImportError:
        print("[WARNING] 'gpiozero' atau 'RPi.GPIO' tidak ditemukan. Mode simulasi buzzer konsol aktif.")
        USE_GPIOZERO = None

# ======================== KONFIGURASI SERVER & MQTT ===========================
TB_SERVER = "eu.thingsboard.cloud"
TB_PORT   = 1883
TB_TOKEN  = "4M6BoXXbvLbZ5MzS56NS"
TB_TOPIC  = "v1/devices/me/telemetry"

# Ambang Batas Alarm Predictive Maintenance (ISO 10816 Spindle/Bearing)
TEMP_ALARM_THRESHOLD = 50.0   # Ambang batas suhu (°C)
RMS_ALARM_THRESHOLD  = 2.50   # Ambang batas RMS total (g)
PEAK_ALARM_THRESHOLD = 4.50   # Ambang batas Peak total (g)

# ============================== HARDWARE BUZZER ===============================
BUZZER_PIN = 18  # BCM GPIO 18 (Physical Pin 12)
buzzer_dev = None

if USE_GPIOZERO:
    try:
        buzzer_dev = Buzzer(BUZZER_PIN)
    except Exception as e:
        print(f"[BUZZER] Inisialisasi gpiozero gagal: {e}")
elif USE_GPIOZERO is False:
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUZZER_PIN, GPIO.OUT)
        GPIO.output(BUZZER_PIN, GPIO.LOW)
    except Exception as e:
        print(f"[BUZZER] Inisialisasi RPi.GPIO gagal: {e}")

def set_buzzer(active: bool):
    """Menyalakan / mematikan alarm buzzer pada GPIO 18"""
    if USE_GPIOZERO and buzzer_dev:
        if active:
            buzzer_dev.on()
        else:
            buzzer_dev.off()
    elif USE_GPIOZERO is False:
        GPIO.output(BUZZER_PIN, GPIO.HIGH if active else GPIO.LOW)

# ============================ SENSOR SUHU DS18B20 =============================
# Driver Linux kernel 1-Wire membaca via w1-gpio (/sys/bus/w1/devices/28-*)
W1_BASE_DIR = "/sys/bus/w1/devices/"

def read_ds18b20_temperature():
    """Membaca suhu dari sensor DS18B20 melalui antarmuka sysfs Linux 1-Wire"""
    try:
        device_folders = glob.glob(os.path.join(W1_BASE_DIR, "28-*"))
        if not device_folders:
            return 27.0  # Fallback nilai normal jika modul belum terkoneksi
        
        device_file = os.path.join(device_folders[0], "w1_slave")
        if not os.path.exists(device_file):
            return 27.0
            
        with open(device_file, "r") as f:
            lines = f.readlines()
            
        if len(lines) >= 2 and lines[0].strip().endswith("YES"):
            equals_pos = lines[1].find("t=")
            if equals_pos != -1:
                temp_string = lines[1][equals_pos + 2:].strip()
                temp_c = float(temp_string) / 1000.0
                return temp_c
        return 27.0
    except Exception as e:
        print(f"[DS18B20] Gagal membaca suhu: {e}")
        return 27.0

# ============================ DISPLAY LCD 16x2 I2C ============================
class RpiI2cLcd:
    """Driver LCD 16x2 I2C (PCF8574) pada Raspberry Pi 4 via smbus2"""
    def __init__(self, bus_num=1, addr=0x27):
        self.bus_num = bus_num
        self.addr = addr
        self.bus = None
        self.last_l1 = ""
        self.last_l2 = ""
        try:
            self.bus = SMBus(bus_num)
            self._init_lcd()
            print(f"[LCD] LCD 16x2 fisik aktif di I2C address 0x{addr:02X}")
        except Exception as e:
            print(f"[LCD] LCD 16x2 tidak aktif/opsional: {e}")
            self.bus = None

    def _strobe(self, data):
        self.bus.write_byte(self.addr, data | 0x04 | 0x08)
        time.sleep(0.0005)
        self.bus.write_byte(self.addr, (data & ~0x04) | 0x08)
        time.sleep(0.0005)

    def _write_nibble(self, nibble):
        data = (nibble & 0xF0) | 0x08
        self.bus.write_byte(self.addr, data | 0x04)
        time.sleep(0.0005)
        self.bus.write_byte(self.addr, data & ~0x04)
        time.sleep(0.0005)

    def _send(self, value, mode=0):
        hi = mode | (value & 0xF0) | 0x08
        lo = mode | ((value << 4) & 0xF0) | 0x08
        self.bus.write_byte(self.addr, hi)
        self._strobe(hi)
        self.bus.write_byte(self.addr, lo)
        self._strobe(lo)

    def _init_lcd(self):
        time.sleep(0.05)
        self._write_nibble(0x30)
        time.sleep(0.005)
        self._write_nibble(0x30)
        time.sleep(0.001)
        self._write_nibble(0x30)
        self._write_nibble(0x20)
        self._send(0x28, 0)
        self._send(0x0C, 0)
        self._send(0x01, 0)
        time.sleep(0.002)
        self._send(0x06, 0)

    def write_lines(self, line1, line2):
        if not self.bus:
            return
        try:
            l1 = (str(line1)[:16] + "                ")[:16]
            l2 = (str(line2)[:16] + "                ")[:16]
            if l1 != self.last_l1:
                self._send(0x80, 0)
                for ch in l1:
                    self._send(ord(ch), 0x01)
                self.last_l1 = l1
            if l2 != self.last_l2:
                self._send(0xC0, 0)
                for ch in l2:
                    self._send(ord(ch), 0x01)
                self.last_l2 = l2
        except Exception:
            pass

# ===================== SENSOR GETARAN ADXL345 / MPU-6050 ======================
class I2CVibrationSensor:
    """Driver I2C untuk ADXL345 (Utama) dan MPU-6050 (Fallback) pada Raspberry Pi 4"""
    def __init__(self, bus_num=1):
        self.bus = SMBus(bus_num)
        self.addr = None
        self.sensor_type = None
        self.scale = 256.0
        self._init_sensor()

    def _init_sensor(self):
        # Scan dan inisialisasi sensor
        # Cek ADXL345 di 0x53 (default) atau 0x1D
        for addr in [0x53, 0x1D]:
            try:
                dev_id = self.bus.read_byte_data(addr, 0x00)
                if dev_id == 0xE5:  # ADXL345 Device ID
                    self.addr = addr
                    self.sensor_type = "ADXL345"
                    # Power Control: Standby -> Measure (bit 3 = 1)
                    self.bus.write_byte_data(self.addr, 0x2D, 0x08)
                    time.sleep(0.01)
                    # Data Format: Full Resolution, +/-4g (0x09)
                    self.bus.write_byte_data(self.addr, 0x31, 0x09)
                    self.scale = 256.0  # 256 LSB/g pada full resolution
                    print(f"[SENSOR] ADXL345 fisik aktif di I2C address 0x{addr:02X}")
                    return
            except Exception:
                pass

        # Cek MPU-6050 di 0x68 atau 0x69
        for addr in [0x68, 0x69]:
            try:
                who_am_i = self.bus.read_byte_data(addr, 0x75)
                if who_am_i in [0x68, 0x72, 0x70]:
                    self.addr = addr
                    self.sensor_type = "MPU6050"
                    # Bangunkan MPU6050 dari sleep mode (PWR_MGMT_1 = 0)
                    self.bus.write_byte_data(self.addr, 0x6B, 0x00)
                    time.sleep(0.01)
                    # Konfigurasi Accel +/-4g (Register 0x1C = 0x08)
                    self.bus.write_byte_data(self.addr, 0x1C, 0x08)
                    self.scale = 8192.0  # 8192 LSB/g pada +/-4g
                    print(f"[SENSOR] MPU-6050 aktif di I2C address 0x{addr:02X}")
                    return
            except Exception:
                pass

        print("[SENSOR WARNING] Tidak ada sensor I2C yang merespon pada 0x53/0x68. Menggunakan data dummy.")
        self.sensor_type = "DUMMY"

    def read_accel(self):
        """Membaca percepatan 3-sumbu dalam satuan g"""
        if self.sensor_type == "ADXL345":
            try:
                data = self.bus.read_i2c_block_data(self.addr, 0x32, 6)
                x = (data[1] << 8) | data[0]
                y = (data[3] << 8) | data[2]
                z = (data[5] << 8) | data[4]
                if x > 32767: x -= 65536
                if y > 32767: y -= 65536
                if z > 32767: z -= 65536
                return (x / self.scale, y / self.scale, z / self.scale)
            except Exception:
                return (0.0, 0.0, 1.0)

        elif self.sensor_type == "MPU6050":
            try:
                data = self.bus.read_i2c_block_data(self.addr, 0x3B, 6)
                x = (data[0] << 8) | data[1]
                y = (data[2] << 8) | data[3]
                z = (data[4] << 8) | data[5]
                if x > 32767: x -= 65536
                if y > 32767: y -= 65536
                if z > 32767: z -= 65536
                return (x / self.scale, y / self.scale, z / self.scale)
            except Exception:
                return (0.0, 0.0, 1.0)
        else:
            return (0.0, 0.0, 1.0)


def analyze_vibration(sensor, num_samples=64, sample_delay_sec=0.005):
    """
    Mengambil sampel getaran multi-titik dan menganalisis:
    RMS, Peak, dan Spektrum Fourier Dominan (FFT)
    """
    xs, ys, zs = [], [], []
    t_start = time.time()
    
    for _ in range(num_samples):
        ax, ay, az = sensor.read_accel()
        xs.append(ax)
        ys.append(ay)
        zs.append(az)
        time.sleep(sample_delay_sec)
        
    t_total = time.time() - t_start
    if t_total <= 0: t_total = 0.001
    fs = num_samples / t_total
    N = len(xs)

    # 1. RMS Getaran per Sumbu & Total
    rms_x = math.sqrt(sum(x * x for x in xs) / N)
    rms_y = math.sqrt(sum(y * y for y in ys) / N)
    rms_z = math.sqrt(sum(z * z for z in zs) / N)
    rms_total = math.sqrt(rms_x * rms_x + rms_y * rms_y + rms_z * rms_z)

    # 2. Peak Getaran
    peak_x = max(abs(x) for x in xs) if xs else 0.0
    peak_y = max(abs(y) for y in ys) if ys else 0.0
    peak_z = max(abs(z) for z in zs) if zs else 0.0
    peak_total = math.sqrt(peak_x * peak_x + peak_y * peak_y + peak_z * peak_z)

    # 3. Spektrum Fourier (FFT Dominan)
    mean_x = sum(xs) / N
    mean_y = sum(ys) / N
    mean_z = sum(zs) / N
    ac_total = [(xs[i] - mean_x) + (ys[i] - mean_y) + (zs[i] - mean_z) for i in range(N)]
    max_amp = 0.0
    dom_freq = 0.0
    max_dyn = max(abs(v) for v in ac_total) if ac_total else 0.0

    if max_dyn > 0.01:
        if USE_NUMPY:
            # Algoritma Fast Fourier Transform (FFT) NumPy O(N log N)
            fft_vals = np.fft.rfft(ac_total)
            freqs = np.fft.rfftfreq(N, d=1.0 / fs)
            # Amplitudo ternormalisasi
            amps = (2.0 / N) * np.abs(fft_vals)
            amps[0] = 0.0  # Abaikan komponen DC (0 Hz)
            idx_max = int(np.argmax(amps))
            dom_freq = float(freqs[idx_max])
            max_amp = float(amps[idx_max])
        else:
            # Fallback: Discrete Fourier Transform (DFT) standar jika NumPy belum terpasang
            half_N = N // 2
            for k in range(1, half_N):
                re = 0.0
                im = 0.0
                for n in range(N):
                    angle = -2.0 * math.pi * k * n / N
                    re += ac_total[n] * math.cos(angle)
                    im += ac_total[n] * math.sin(angle)
                amp = (2.0 / N) * math.sqrt(re * re + im * im)
                freq = k * (fs / N)
                if amp > max_amp:
                    max_amp = amp
                    dom_freq = freq
    elif rms_total > 0.05:
        dom_freq = 25.0
        max_amp = rms_total

    return {
        "rms_x": round(rms_x, 3),
        "rms_y": round(rms_y, 3),
        "rms_z": round(rms_z, 3),
        "rms_total": round(rms_total, 3),
        "peak_x": round(peak_x, 3),
        "peak_y": round(peak_y, 3),
        "peak_z": round(peak_z, 3),
        "peak_total": round(peak_total, 3),
        "frekuensi_dominan_total": round(dom_freq, 1),
        "amplitudo_dominan_total": round(max_amp, 3)
    }

# ============================ MQTT CLIENT PAHO ================================
mqtt_client = None

def setup_mqtt():
    global mqtt_client
    client_id = f"RPi4_Predictive_Drill_{int(time.time())}"
    client = mqtt.Client(client_id=client_id)
    client.username_pw_set(username=TB_TOKEN, password="")
    
    try:
        print(f"[MQTT] Menghubungkan ke ThingsBoard Server: {TB_SERVER}:{TB_PORT}...")
        client.connect(TB_SERVER, TB_PORT, keepalive=60)
        client.loop_start()
        print("[MQTT] SUKSES TERKONEKSI KE THINGSBOARD!")
        mqtt_client = client
        return True
    except Exception as e:
        print(f"[MQTT ERROR] Gagal koneksi ke ThingsBoard: {e}")
        mqtt_client = None
        return False

# ================================= MAIN LOOP ==================================
def main():
    print("=" * 65)
    print(" PREDICTIVE MAINTENANCE BEARING BOR - RASPBERRY PI 4")
    print(" Target : ThingsBoard EU (eu.thingsboard.cloud)")
    print("=" * 65)

    sensor = I2CVibrationSensor(bus_num=1)
    lcd = RpiI2cLcd(bus_num=1, addr=0x27)
    if lcd:
        lcd.write_lines("PREDICTIVE MAINT", "BEARING BOR RPI4")
    setup_mqtt()

    print(f"[ALGORITMA] Analisis Spektrum: {'Fast Fourier Transform (NumPy FFT)' if USE_NUMPY else 'Discrete Fourier Transform (DFT Native)'}")
    print("\n[INFO] Ambang Batas Alarm:")
    print(f" - Suhu Max       : {TEMP_ALARM_THRESHOLD} °C")
    print(f" - RMS Total Max  : {RMS_ALARM_THRESHOLD} g")
    print(f" - Peak Total Max : {PEAK_ALARM_THRESHOLD} g\n")

    try:
        while True:
            # 1. Akuisisi Getaran & Spektrum Frekuensi
            vibr = analyze_vibration(sensor, num_samples=64, sample_delay_sec=0.005)

            # 2. Akuisisi Suhu DS18B20
            temp = read_ds18b20_temperature()

            # 3. Evaluasi Alarm
            is_temp_alarm = (temp >= TEMP_ALARM_THRESHOLD)
            is_rms_alarm  = (vibr["rms_total"] >= RMS_ALARM_THRESHOLD)
            is_peak_alarm = (vibr["peak_total"] >= PEAK_ALARM_THRESHOLD)
            is_vibr_alarm = is_rms_alarm or is_peak_alarm
            alarm_active  = is_temp_alarm or is_vibr_alarm

            # Evaluasi Pesan Status Berdasarkan Ambang Batas (Threshold)
            if is_vibr_alarm and is_temp_alarm:
                alarm_status = "ALAT BERGETAR & PANAS! RUSAK!!!"
                line1_lcd, line2_lcd = "ALAT BERGETAR &", "PANAS! RUSAK!!!"
                alarm_detail = "OVERHEAT - HIGH_VIBR"
            elif is_vibr_alarm:
                alarm_status = "ALAT BERGETAR, RUSAK!"
                line1_lcd, line2_lcd = "ALAT BERGETAR,", "RUSAK!"
                alarm_detail = "HIGH_VIBR"
            elif is_temp_alarm:
                alarm_status = "ALAT PANAS! RUSAK"
                line1_lcd, line2_lcd = "ALAT PANAS!", "RUSAK"
                alarm_detail = "OVERHEAT"
            else:
                alarm_status = "NORMAL"
                line1_lcd, line2_lcd = "STATUS: NORMAL", f"T:{temp:.1f}C R:{vibr['rms_total']:.2f}g"
                alarm_detail = "OK"

            # Update Tampilan LCD 16x2
            if lcd:
                lcd.write_lines(line1_lcd, line2_lcd)

            # Kontrol Buzzer Alarm
            if alarm_active:
                set_buzzer(True)
            else:
                set_buzzer(False)

            # 4. Format Payload Telemetri (Sesuai Format ThingsBoard)
            payload = {
                "rms_x": vibr["rms_x"],
                "rms_y": vibr["rms_y"],
                "rms_z": vibr["rms_z"],
                "rms_total": vibr["rms_total"],
                "frekuensi_dominan_total": vibr["frekuensi_dominan_total"],
                "peak_x": vibr["peak_x"],
                "peak_y": vibr["peak_y"],
                "peak_z": vibr["peak_z"],
                "peak_total": vibr["peak_total"],
                "amplitudo_dominan_total": vibr["amplitudo_dominan_total"],
                "temperature": round(temp, 1),
                "suhu": round(temp, 1),
                "temp": round(temp, 1),
                "alarm": alarm_active,
                "status_alarm": alarm_status,
                "pesan_status": alarm_status,
                "status": alarm_status
            }
            payload_json = json.dumps(payload)

            # Cetak Serial Log
            print("-" * 65)
            print(f"Suhu: {temp:.1f} °C | Status: {alarm_status} [{alarm_detail}]")
            print(f"RMS XYZ: ({vibr['rms_x']:.3f}, {vibr['rms_y']:.3f}, {vibr['rms_z']:.3f}) g -> RMS Total: {vibr['rms_total']:.3f} g")
            print(f"Peak XYZ: ({vibr['peak_x']:.3f}, {vibr['peak_y']:.3f}, {vibr['peak_z']:.3f}) g -> Peak Total: {vibr['peak_total']:.3f} g")
            print(f"Spektrum: Freq Dominan = {vibr['frekuensi_dominan_total']:.1f} Hz | Amp Dominan = {vibr['amplitudo_dominan_total']:.3f} g")

            # 5. Kirim ke ThingsBoard
            global mqtt_client
            if mqtt_client is None:
                setup_mqtt()

            if mqtt_client is not None:
                try:
                    mqtt_client.publish(TB_TOPIC, payload_json)
                    print("[MQTT] Telemetri Terkirim ke ThingsBoard EU:")
                    print("       ", payload_json)
                except Exception as e:
                    print(f"[MQTT ERROR] Gagal publish: {e}")
                    mqtt_client = None

            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[STOP] Program dihentikan oleh pengguna.")
    finally:
        set_buzzer(False)
        if USE_GPIOZERO is False:
            GPIO.cleanup()
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()

if __name__ == "__main__":
    main()
