# ==============================================================================
# SISTEM PREDICTIVE MAINTENANCE BEARING BOR BERBASIS IOT (STANDALONE SINGLE FILE)
# RASPBERRY PI PICO / PICO W / PICO 2 W - WOKWI SIMULATOR KE THINGSBOARD (EU CLOUD)
# SENSOR : DS18B20 (Suhu) & MPU-6050 / ADXL345 (Getaran 3-Sumbu)
# OUTPUT : BUZZER ALARM (GP15) + TELEMETRI MQTT THINGSBOARD
# ==============================================================================
# CATATAN: Berkas ini 100% standalone (mandiri). Seluruh driver sensor & MQTT
# sudah digabung ke dalam satu berkas, sehingga cukup copy-paste file ini ke Wokwi.
# ==============================================================================

import network
import time
import ujson
import math
from machine import Pin, I2C, PWM
import onewire, ds18x20

try:
    import usocket as socket
except ImportError:
    import socket
try:
    import ustruct as struct
except ImportError:
    import struct


# ==============================================================================
# BAGIAN 1: DRIVER MQTT CLIENT (MicroPython Lean MQTT)
# ==============================================================================
class MQTTException(Exception):
    pass

class MQTTClient:
    def __init__(self, client_id, server, port=0, user=None, password=None, keepalive=60, ssl=False, ssl_params={}):
        if port == 0:
            port = 8883 if ssl else 1883
        self.client_id = client_id
        self.sock = None
        self.server = server
        self.port = port
        self.ssl = ssl
        self.ssl_params = ssl_params
        self.user = user
        self.pswd = password
        self.keepalive = keepalive

    def _send_str(self, s):
        if isinstance(s, str):
            s = s.encode("utf-8")
        self.sock.write(struct.pack("!H", len(s)))
        self.sock.write(s)

    def connect(self, clean_session=True):
        self.sock = socket.socket()
        # Set timeout 3.0 detik agar simulator tidak freeze/hang jika offline / tanpa gateway
        self.sock.settimeout(3.0)
        addr = socket.getaddrinfo(self.server, self.port)[0][-1]
        self.sock.connect(addr)
        if self.ssl:
            try:
                import ussl as ssl
            except ImportError:
                import ssl
            self.sock = ssl.wrap_socket(self.sock, **self.ssl_params)
        
        premsg = bytearray(b"\x10\0\0\0\0\0")
        msg = bytearray(b"\x04MQTT\x04\x02\0\0")

        cid = self.client_id.encode("utf-8") if isinstance(self.client_id, str) else self.client_id
        user_b = self.user.encode("utf-8") if isinstance(self.user, str) else self.user
        pwd_b = self.pswd.encode("utf-8") if isinstance(self.pswd, str) else self.pswd

        sz = 10 + 2 + len(cid)
        msg[6] = clean_session << 1
        if user_b is not None:
            sz += 2 + len(user_b)
            if pwd_b is not None:
                sz += 2 + len(pwd_b)
                msg[6] |= 0xC0
            else:
                msg[6] |= 0x80
        if self.keepalive:
            assert self.keepalive < 65536
            msg[7] |= self.keepalive >> 8
            msg[8] |= self.keepalive & 0x00FF

        i = 1
        while sz > 0x7f:
            premsg[i] = (sz & 0x7f) | 0x80
            sz >>= 7
            i += 1
        premsg[i] = sz
        self.sock.write(premsg[:i + 2])
        self.sock.write(msg)
        self._send_str(cid)
        if user_b is not None:
            self._send_str(user_b)
            if pwd_b is not None:
                self._send_str(pwd_b)
        resp = self.sock.read(4)
        assert resp[0] == 0x20 and resp[1] == 0x02
        if resp[3] != 0:
            raise MQTTException(resp[3])
        return resp[2] & 1

    def disconnect(self):
        try:
            self.sock.write(b"\xe0\0")
            self.sock.close()
        except Exception:
            pass

    def publish(self, topic, msg, retain=False, qos=0):
        if isinstance(topic, str):
            topic = topic.encode("utf-8")
        if isinstance(msg, str):
            msg = msg.encode("utf-8")
        pkt = bytearray(b"\x30\0\0\0")
        pkt[0] |= qos << 1 | retain
        sz = 2 + len(topic) + len(msg)
        assert sz < 2097152
        i = 1
        while sz > 0x7f:
            pkt[i] = (sz & 0x7f) | 0x80
            sz >>= 7
            i += 1
        pkt[i] = sz
        self.sock.write(pkt[:i + 1])
        self._send_str(topic)
        self.sock.write(msg)


# ==============================================================================
# BAGIAN 2: DRIVER SENSOR GETARAN (MPU6050 & ADXL345 + FFT & RMS)
# ==============================================================================
class MPU6050:
    """Driver MPU-6050 untuk Wokwi Simulator & Uji Fisik"""
    def __init__(self, i2c, addr=0x68):
        self.i2c = i2c
        self.addr = addr
        self.name = "MPU-6050 (Wokwi)"
        try:
            self.i2c.writeto_mem(self.addr, 0x6B, b'\x00')  # Bangunkan MPU6050
            time.sleep_ms(5)
            self.i2c.writeto_mem(self.addr, 0x1C, b'\x08')  # Rentang +-4g (8192 LSB/g)
        except Exception:
            pass
        self.scale = 8192.0

    def read_accel(self):
        """Membaca percepatan 3-sumbu (X, Y, Z) dalam g"""
        try:
            data = self.i2c.readfrom_mem(self.addr, 0x3B, 6)
            x = (data[0] << 8) | data[1]
            y = (data[2] << 8) | data[3]
            z = (data[4] << 8) | data[5]
            if x > 32767: x -= 65536
            if y > 32767: y -= 65536
            if z > 32767: z -= 65536
            return (x / self.scale, y / self.scale, z / self.scale)
        except Exception:
            return (0.0, 0.0, 1.0)

class ADXL345:
    """Driver ADXL345 untuk Real Hardware Fisik"""
    def __init__(self, i2c, addr=0x53):
        self.i2c = i2c
        self.addr = addr
        self.name = "ADXL345 (Hardware)"
        try:
            self.i2c.writeto_mem(self.addr, 0x2D, b'\x08')  # Measure bit = 1
            time.sleep_ms(5)
            self.i2c.writeto_mem(self.addr, 0x31, b'\x09')  # Full res +-4g (256 LSB/g)
        except Exception:
            pass
        self.scale = 256.0

    def read_accel(self):
        try:
            data = self.i2c.readfrom_mem(self.addr, 0x32, 6)
            x = (data[1] << 8) | data[0]
            y = (data[3] << 8) | data[2]
            z = (data[5] << 8) | data[4]
            if x > 32767: x -= 65536
            if y > 32767: y -= 65536
            if z > 32767: z -= 65536
            return (x / self.scale, y / self.scale, z / self.scale)
        except Exception:
            return (0.0, 0.0, 1.0)

def detect_sensor(i2c):
    """Mendeteksi otomatis sensor getaran pada I2C"""
    try:
        devices = i2c.scan()
    except Exception:
        devices = []
    print("[I2C SCAN] Alamat terdeteksi:", [hex(d) for d in devices])
    if 0x68 in devices:
        print("[SENSOR] MPU-6050 terdeteksi di 0x68 (Wokwi)")
        return MPU6050(i2c, 0x68)
    elif 0x69 in devices:
        print("[SENSOR] MPU-6050 terdeteksi di 0x69")
        return MPU6050(i2c, 0x69)
    elif 0x53 in devices:
        print("[SENSOR] ADXL345 terdeteksi di 0x53")
        return ADXL345(i2c, 0x53)
    elif 0x1D in devices:
        print("[SENSOR] ADXL345 terdeteksi di 0x1D")
        return ADXL345(i2c, 0x1D)
    else:
        print("[SENSOR] Fallback: Membuka MPU-6050 di 0x68...")
        return MPU6050(i2c, 0x68)

def analyze_vibration(sensor, num_samples=32, sample_delay_ms=8):
    """
    Akuisisi sinyal jendela waktu getaran dan analisis:
    RMS, Peak, dan Spektrum Fourier DFT (Dioptimalkan untuk Wokwi)
    """
    xs, ys, zs = [], [], []
    t_start = time.ticks_ms()
    
    for _ in range(num_samples):
        ax, ay, az = sensor.read_accel()
        xs.append(ax)
        ys.append(ay)
        zs.append(az)
        if sample_delay_ms > 0:
            time.sleep_ms(sample_delay_ms)
            
    t_total_ms = time.ticks_diff(time.ticks_ms(), t_start)
    if t_total_ms <= 0:
        t_total_ms = 1
        
    fs = (num_samples * 1000.0) / t_total_ms
    N = len(xs)

    # 1. Rata-rata komponen statis (DC offset)
    mean_x = sum(xs) / N
    mean_y = sum(ys) / N
    mean_z = sum(zs) / N

    # 2. Komponen AC dinamis
    ac_x = [x - mean_x for x in xs]
    ac_y = [y - mean_y for y in ys]
    ac_z = [z - mean_z for z in zs]

    # 3. RMS getaran
    rms_x = math.sqrt(sum(v * v for v in ac_x) / N)
    rms_y = math.sqrt(sum(v * v for v in ac_y) / N)
    rms_z = math.sqrt(sum(v * v for v in ac_z) / N)
    rms_total = math.sqrt(rms_x * rms_x + rms_y * rms_y + rms_z * rms_z)

    # 4. Nilai Peak (Puncak getaran)
    peak_x = max(abs(v) for v in ac_x) if ac_x else 0.0
    peak_y = max(abs(v) for v in ac_y) if ac_y else 0.0
    peak_z = max(abs(v) for v in ac_z) if ac_z else 0.0
    peak_total = math.sqrt(peak_x * peak_x + peak_y * peak_y + peak_z * peak_z)

    # 5. Spektrum Fourier (DFT)
    v_total = [ac_x[i] + ac_y[i] + ac_z[i] for i in range(N)]
    mean_v = sum(v_total) / N
    v_dynamic = [v - mean_v for v in v_total]

    max_amp = 0.0
    dom_freq = 0.0
    max_dyn = max(abs(v) for v in v_dynamic) if v_dynamic else 0.0

    # Optimasi simulator: Hanya hitung DFT jika ada getaran dinamis (> 0.01 g)
    if max_dyn > 0.01:
        half_N = N // 2
        for k in range(1, half_N):
            re = 0.0
            im = 0.0
            for n in range(N):
                angle = -2.0 * math.pi * k * n / N
                re += v_dynamic[n] * math.cos(angle)
                im += v_dynamic[n] * math.sin(angle)
            amp = (2.0 / N) * math.sqrt(re * re + im * im)
            freq = k * (fs / N)
            if amp > max_amp:
                max_amp = amp
                dom_freq = freq

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


# ==============================================================================
# BAGIAN 3: KONFIGURASI SISTEM & LOGIKA PREDICTIVE MAINTENANCE
# ==============================================================================

# Konfigurasi Jaringan & Server
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASS = ""

TB_SERVER = "eu.thingsboard.cloud"
TB_PORT   = 1883
TB_TOKEN  = "4M6BoXXbvLbZ5MzS56NS"
TB_TOPIC  = "v1/devices/me/telemetry"

# Ambang Batas Alarm Predictive Maintenance (ISO 10816 Spindle/Bearing)
TEMP_ALARM_THRESHOLD = 50.0   # Ambang batas suhu (°C)
RMS_ALARM_THRESHOLD  = 2.50   # Ambang batas RMS total (g)
PEAK_ALARM_THRESHOLD = 4.50   # Ambang batas Peak total (g)

# Inisialisasi Perangkat Keras
buzzer_pin = Pin(15, Pin.OUT)
buzzer_pwm = None

def buzzer_alarm(active=True):
    """Menyalakan / mematikan alarm buzzer frekuensi 2500 Hz dengan hemat CPU"""
    global buzzer_pwm
    if active:
        if buzzer_pwm is None:
            buzzer_pwm = PWM(buzzer_pin)
        buzzer_pwm.freq(2500)
        buzzer_pwm.duty_u16(32768)  # 50% duty cycle
    else:
        if buzzer_pwm is not None:
            buzzer_pwm.duty_u16(0)
            buzzer_pwm.deinit()
            buzzer_pwm = None

# Sensor Suhu DS18B20 (DQ ke GP16)
ow = onewire.OneWire(Pin(16))
ds = ds18x20.DS18X20(ow)

# Sensor Getaran I2C (SDA=GP20, SCL=GP21)
i2c = I2C(0, sda=Pin(20), scl=Pin(21), freq=400000)
vibr_sensor = detect_sensor(i2c)

# Jaringan & MQTT
client = None
last_mqtt_retry = 0

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("[WiFi] Menghubungkan ke Wi-Fi:", WIFI_SSID)
        wlan.connect(WIFI_SSID, WIFI_PASS)
        retry = 0
        while not wlan.isconnected() and retry < 15:
            time.sleep_ms(250)
            print(".", end="")
            retry += 1
        print("")
    
    if wlan.isconnected():
        print("[WiFi] Sukses Terhubung! IP:", wlan.ifconfig()[0])
        return True
    else:
        print("[WiFi] Gagal/Offline.")
        return False

def connect_mqtt():
    global client, last_mqtt_retry
    now = time.ticks_ms()
    # Batasi percobaan koneksi (minimal jeda 8 detik agar tidak memblokir loop simulator)
    if time.ticks_diff(now, last_mqtt_retry) < 8000:
        return False
    last_mqtt_retry = now

    print("[MQTT] Menghubungkan ke ThingsBoard Server:", TB_SERVER)
    client_id = "PicoW_Predictive_Drill_" + str(time.ticks_ms())
    try:
        c = MQTTClient(
            client_id=client_id,
            server=TB_SERVER,
            port=TB_PORT,
            user=TB_TOKEN,
            password="",
            keepalive=60
        )
        c.connect()
        client = c
        print("[MQTT] SUKSES TERKONEKSI KE THINGSBOARD!")
        return True
    except Exception as e:
        print("[MQTT] Belum terhubung (Offline / Gateway belum aktif):", e)
        client = None
        return False

def read_temperature():
    """Membaca suhu dari sensor DS18B20 (GP16)"""
    try:
        roms = ds.scan()
        if not roms:
            return 27.0
        ds.convert_temp()
        time.sleep_ms(200)  # Di simulator Wokwi 200ms sudah mencukupi
        temp = ds.read_temp(roms[0])
        if temp is None or temp < -55.0 or temp > 125.0:
            return 27.0
        return temp
    except Exception as e:
        return 27.0


# ==============================================================================
# BAGIAN 4: LOOP UTAMA
# ==============================================================================
def main():
    print("=" * 65)
    print(" PREDICTIVE MAINTENANCE BEARING BOR - WOKWI SIMULATOR")
    print(" Target: ThingsBoard EU (eu.thingsboard.cloud)")
    print("=" * 65)
    
    connect_wifi()
    connect_mqtt()

    print("\n[INFO] Ambang Batas Alarm:")
    print(" - Suhu Max       : {} °C".format(TEMP_ALARM_THRESHOLD))
    print(" - RMS Total Max  : {} g".format(RMS_ALARM_THRESHOLD))
    print(" - Peak Total Max : {} g\n".format(PEAK_ALARM_THRESHOLD))

    while True:
        try:
            # 1. Akuisisi & Analisis Getaran (Jendela 32 sampel teroptimasi)
            vibr = analyze_vibration(vibr_sensor, num_samples=32, sample_delay_ms=8)
            
            # 2. Akuisisi Suhu Bearing
            temp = read_temperature()

            # 3. Evaluasi Alarm Predictive Maintenance
            is_temp_alarm = (temp >= TEMP_ALARM_THRESHOLD)
            is_rms_alarm  = (vibr["rms_total"] >= RMS_ALARM_THRESHOLD)
            is_peak_alarm = (vibr["peak_total"] >= PEAK_ALARM_THRESHOLD)
            
            alarm_active = is_temp_alarm or is_rms_alarm or is_peak_alarm
            
            if alarm_active:
                alarm_status = "ALARM"
                buzzer_alarm(True)
                reasons = []
                if is_temp_alarm: reasons.append("OVERHEAT")
                if is_rms_alarm:  reasons.append("HIGH_RMS")
                if is_peak_alarm: reasons.append("HIGH_PEAK")
                alarm_detail = " - ".join(reasons)
            else:
                alarm_status = "NORMAL"
                buzzer_alarm(False)
                alarm_detail = "OK"

            # 4. Format Payload Telemetri ThingsBoard
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
                "alarm": alarm_active,
                "status_alarm": alarm_status
            }
            payload_json = ujson.dumps(payload)

            # Cetak ke Serial Monitor
            print("-" * 65)
            print("Suhu: {:.1f} °C | Status: {} [{}]".format(temp, alarm_status, alarm_detail))
            print("RMS XYZ: ({:.3f}, {:.3f}, {:.3f}) g -> RMS Total: {:.3f} g".format(
                vibr["rms_x"], vibr["rms_y"], vibr["rms_z"], vibr["rms_total"]
            ))
            print("Peak XYZ: ({:.3f}, {:.3f}, {:.3f}) g -> Peak Total: {:.3f} g".format(
                vibr["peak_x"], vibr["peak_y"], vibr["peak_z"], vibr["peak_total"]
            ))
            print("Spektrum: Freq Dominan = {:.1f} Hz | Amp Dominan = {:.3f} g".format(
                vibr["frekuensi_dominan_total"], vibr["amplitudo_dominan_total"]
            ))

            # 5. Kirim Telemetri via MQTT ke ThingsBoard
            global client
            if client is None:
                connect_mqtt()

            if client is not None:
                try:
                    client.publish(TB_TOPIC, payload_json)
                    print("[MQTT] Telemetri Terkirim ke ThingsBoard EU:")
                    print("       ", payload_json)
                except Exception as e:
                    print("[MQTT ERROR] Gagal mengirim data:", e)
                    client = None

            time.sleep_ms(800)

        except Exception as err:
            print("[LOOP EXCEPTION]:", err)
            buzzer_alarm(False)
            time.sleep_ms(1000)

if __name__ == "__main__":
    main()
