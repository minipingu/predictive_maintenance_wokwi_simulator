# ==============================================================================
# main.py - Raspberry Pi Pico W (MicroPython Standalone Single-File)
# DS18B20 + MPU6050/ADXL345 + LCD 16x2 I2C + Buzzer Alarm + ThingsBoard MQTT
# ==============================================================================
# CATATAN: Berkas ini 100% standalone (mandiri). Seluruh driver sensor, LCD 16x2,
# FFT radix-2, dan client MQTT sudah digabung dalam satu file tanpa dependensi luar.
# Bisa langsung di-copy-paste ke Wokwi Web maupun dijalankan di VS Code!
# ==============================================================================

from machine import Pin, I2C, PWM
import onewire, ds18x20
import time, math, struct, json
import network, socket
from array import array

# ===================== KONFIGURASI =====================
SENSOR    = "MPU6050"   # "MPU6050" (Wokwi) atau "ADXL345" (Hardware Fisik)
SIM_GETAR = True        # True = getaran dinamis Wokwi. Hardware fisik: False
REMOVE_DC = False       # False di Wokwi agar slider langsung terbaca sebagai getaran!
FS_TARGET = 200         # Laju sampling (Hz)
N         = 256         # Jumlah sampel per jendela FFT (pangkat 2)
G         = 9.80665     # 1 g dalam m/s^2

# ===================== PARAMETER OPERASIONAL MESIN BOR DUDUK =====================
RPM_SPINDLE = 1500      # Kecepatan putaran spindle bor duduk (RPM). Standar industri: 900, 1500, 2400 RPM
F_SHAFT     = RPM_SPINDLE / 60.0  # Frekuensi fundamental putaran poros (Hz): 1500 / 60 = 25.0 Hz
F_HARMONIC2 = 2.0 * F_SHAFT       # Harmonik ke-2 (Misalignment belt / pulley bor): 50.0 Hz
F_BEARING   = 3.1 * F_SHAFT       # Frekuensi cacat lintasan bantalan (BPFO ball bearing 6205): 77.5 Hz

# Ambang Batas Alarm Predictive Maintenance (ISO 10816-3 Mesin Industri Kelas 1/2)
TEMP_ALARM_THRESHOLD = 50.0        # Ambang batas suhu (°C) - Indikasi degradasi pelumas/gesekan
RMS_ALARM_THRESHOLD  = 2.50 * G    # 24.52 m/s^2 (setara 2.50 g) - Indikasi kerusakan dinamis
PEAK_ALARM_THRESHOLD = 4.50 * G    # 44.13 m/s^2 (setara 4.50 g) - Indikasi kejutan impak spalling

# Pinout Raspberry Pi Pico W
PIN_BZ  = 28            # Buzzer PWM -> GP15
PIN_DS  = 16            # DS18B20 DQ -> GP16
PIN_SDA = 20            # I2C0 SDA   -> GP20
PIN_SCL = 21            # I2C0 SCL   -> GP21

# WiFi & ThingsBoard EU
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASS = ""
TB_HOST   = "eu.thingsboard.cloud"
TB_PORT   = 1883
TB_TOKEN  = "4M6BoXXbvLbZ5MzS56NS"  # Token aktif ThingsBoard EU
TB_TOPIC  = "v1/devices/me/telemetry"

# Profil getaran mekanis bor duduk (Frekuensi Hz, Amplitudo baseline m/s^2)
SIM_X = (F_SHAFT, 1.5)      # Sumbu X: getaran unbalance putaran spindle (25 Hz)
SIM_Y = (F_HARMONIC2, 1.0)  # Sumbu Y: getaran transmisi sabuk/pulley bor (50 Hz)
SIM_Z = (F_BEARING, 0.5)    # Sumbu Z: getaran aksial lintasan bola bearing (77.5 Hz)

# ===================== DRIVER BUZZER ALARM =====================
buzzer_pwm = PWM(Pin(PIN_BZ))
buzzer_pwm.duty_u16(0)

def set_buzzer(active=True):
    if active:
        buzzer_pwm.freq(2500)
        buzzer_pwm.duty_u16(32768)  # 50% duty cycle, suara aktif
    else:
        buzzer_pwm.duty_u16(0)      # Senyap (duty 0%)

# ===================== DRIVER LCD 16x2 I2C =====================
class I2cLcd:
    def __init__(self, i2c_bus, addr=0x27):
        self.i2c = i2c_bus
        self.addr = addr
        self._last_l1 = ""
        self._last_l2 = ""
        try:
            self.i2c.writeto(self.addr, b'\x00')
            time.sleep_ms(20)
            for _ in range(3):
                self._cmd_nibble(0x30)
                time.sleep_ms(5)
            self._cmd_nibble(0x20)
            self._write(0x28, 0)
            self._write(0x0C, 0)
            self.clear()
            self._write(0x06, 0)
        except Exception:
            pass

    def _cmd_nibble(self, val):
        b = (val & 0xF0) | 0x08
        self.i2c.writeto(self.addr, bytes([b | 0x04]))
        self.i2c.writeto(self.addr, bytes([b]))

    def _write(self, val, mode):
        hi = mode | (val & 0xF0) | 0x08
        lo = mode | ((val << 4) & 0xF0) | 0x08
        self.i2c.writeto(self.addr, bytes([hi | 0x04]))
        self.i2c.writeto(self.addr, bytes([hi]))
        self.i2c.writeto(self.addr, bytes([lo | 0x04]))
        self.i2c.writeto(self.addr, bytes([lo]))

    def clear(self):
        self._write(0x01, 0)
        time.sleep_ms(2)

    def write_lines(self, l1, l2):
        s1 = (str(l1)[:16] + " " * 16)[:16]
        s2 = (str(l2)[:16] + " " * 16)[:16]
        if self._last_l1 != s1:
            self._write(0x80, 0)
            for c in s1: self._write(ord(c), 1)
            self._last_l1 = s1
        if self._last_l2 != s2:
            self._write(0xC0, 0)
            for c in s2: self._write(ord(c), 1)
            self._last_l2 = s2

# ===================== INISIALISASI I2C & SENSOR =====================
i2c = I2C(0, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=100000)

lcd = None
try:
    devs = i2c.scan()
    for addr in [0x27, 0x3F]:
        if addr in devs:
            lcd = I2cLcd(i2c, addr)
            break
except Exception:
    pass

if lcd:
    lcd.write_lines("PREDICTIVE MAINT", "INITIALIZING...")

# Sensor Getaran
if SENSOR == "ADXL345":
    ADDR = 0x53
    ODR = {100: 0x0A, 200: 0x0B, 400: 0x0C, 800: 0x0D, 1600: 0x0E, 3200: 0x0F}
    try:
        i2c.writeto_mem(ADDR, 0x2C, bytes([ODR.get(FS_TARGET * 2, 0x0D)]))
        i2c.writeto_mem(ADDR, 0x31, b'\x0B')
        i2c.writeto_mem(ADDR, 0x2D, b'\x08')
    except Exception:
        pass
    SKALA = 0.0039 * G
    REG, FMT = 0x32, '<hhh'
else:  # MPU6050
    ADDR = 0x68
    try:
        i2c.writeto_mem(ADDR, 0x6B, b'\x00')
        i2c.writeto_mem(ADDR, 0x1C, b'\x08')   # +-4 g range
    except Exception:
        pass
    SKALA = (4.0 * G) / 32768.0
    REG, FMT = 0x3B, '>hhh'

def baca_xyz():
    try:
        d = i2c.readfrom_mem(ADDR, REG, 6)
        x, y, z = struct.unpack(FMT, d)
        return x * SKALA, y * SKALA, z * SKALA
    except Exception:
        return 0.0, 0.0, 1.0 * G

# DS18B20
ow = onewire.OneWire(Pin(PIN_DS))
ds = ds18x20.DS18X20(ow)
try:
    roms = ds.scan()
except Exception:
    roms = []

# ===================== FFT RADIX-2 =====================
COS = [math.cos(2 * math.pi * k / N) for k in range(N // 2)]
SIN = [-math.sin(2 * math.pi * k / N) for k in range(N // 2)]
WIN = [0.5 * (1 - math.cos(2 * math.pi * i / (N - 1))) for i in range(N)]
WSUM = sum(WIN)

def fft(re, im):
    n = len(re)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            re[i], re[j] = re[j], re[i]
            im[i], im[j] = im[j], im[i]
    length = 2
    while length <= n:
        half = length >> 1
        step = n // length
        for i in range(0, n, length):
            for k in range(half):
                wr = COS[k * step]
                wi = SIN[k * step]
                u = i + k
                v = u + half
                xr = re[v] * wr - im[v] * wi
                xi = re[v] * wi + im[v] * wr
                re[v] = re[u] - xr
                im[v] = im[u] - xi
                re[u] += xr
                im[u] += xi
        length <<= 1

# ===================== AKUISISI & ANALISIS =====================
def ambil_sampel():
    ax = array('f', [0.0] * N)
    ay = array('f', [0.0] * N)
    az = array('f', [0.0] * N)
    periode = 1000000 // FS_TARGET
    t0 = time.ticks_us()
    nxt = t0
    t_awal = t0
    t_akhir = t0
    for i in range(N):
        while time.ticks_diff(time.ticks_us(), nxt) < 0:
            pass
        ts = time.ticks_us()
        if i == 0: t_awal = ts
        t_akhir = ts
        x, y, z = baca_xyz()
        if SIM_GETAR:
            t = time.ticks_diff(ts, t0) / 1000000.0
            x += SIM_X[1] * math.sin(2 * math.pi * SIM_X[0] * t)
            y += SIM_Y[1] * math.sin(2 * math.pi * SIM_Y[0] * t)
            z += SIM_Z[1] * math.sin(2 * math.pi * SIM_Z[0] * t)
        ax[i] = x
        ay[i] = y
        az[i] = z
        nxt = time.ticks_add(nxt, periode)
    dt = time.ticks_diff(t_akhir, t_awal) / 1000000.0
    fs = (N - 1) / dt if dt > 0 else FS_TARGET
    return ax, ay, az, fs

def analisis(ax, ay, az, fs):
    if REMOVE_DC:
        mx = sum(ax) / N
        my = sum(ay) / N
        mz = sum(az) / N
        for i in range(N):
            ax[i] -= mx
            ay[i] -= my
            az[i] -= mz

    sx = sy = sz = 0.0
    px = py = pz = pt = 0.0
    for i in range(N):
        x, y, z = ax[i], ay[i], az[i]
        sx += x * x
        sy += y * y
        sz += z * z
        if abs(x) > px: px = abs(x)
        if abs(y) > py: py = abs(y)
        if abs(z) > pz: pz = abs(z)
        m = math.sqrt(x * x + y * y + z * z)
        if m > pt: pt = m

    rms_x = math.sqrt(sx / N)
    rms_y = math.sqrt(sy / N)
    rms_z = math.sqrt(sz / N)
    rms_t = math.sqrt((sx + sy + sz) / N)

    p2 = [0.0] * (N // 2)
    for sig in (ax, ay, az):
        re = [sig[i] * WIN[i] for i in range(N)]
        im = [0.0] * N
        fft(re, im)
        for k in range(N // 2):
            p2[k] += re[k] * re[k] + im[k] * im[k]

    best = 1
    for k in range(2, N // 2):
        if p2[k] > p2[best]:
            best = k
    f_dom = best * fs / N
    a_dom = 2.0 * math.sqrt(p2[best]) / WSUM

    return rms_x, rms_y, rms_z, rms_t, px, py, pz, pt, f_dom, a_dom

# ===================== WIFI & MQTT CLIENT =====================
def konek_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS)
        print("Menghubungkan WiFi", end="")
        for _ in range(100):
            if wlan.isconnected(): break
            print(".", end="")
            time.sleep(0.1)
        print()
    return wlan

def _str(b):
    return struct.pack("!H", len(b)) + b

def _panjang(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n: out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)

class MQTTRingan:
    def __init__(self, cid, host, port, user, keepalive=60):
        self.cid = cid.encode()
        self.host = host
        self.port = port
        self.user = user.encode()
        self.keepalive = keepalive
        self.s = None

    def connect(self):
        addr = socket.getaddrinfo(self.host, self.port)[0][-1]
        self.s = socket.socket()
        self.s.settimeout(5)
        self.s.connect(addr)
        vh = b"\x00\x04MQTT\x04\x82" + struct.pack("!H", self.keepalive)
        pl = _str(self.cid) + _str(self.user)
        self.s.send(b"\x10" + _panjang(len(vh) + len(pl)) + vh + pl)
        r = self.s.recv(4)
        if len(r) < 4 or r[0] != 0x20 or r[3] != 0:
            raise OSError("CONNACK ditolak")

    def publish(self, topic, msg):
        t = topic.encode()
        m = msg.encode()
        body = _str(t) + m
        self.s.send(b"\x30" + _panjang(len(body)) + body)

    def close(self):
        try: self.s.close()
        except Exception: pass
        self.s = None

wlan = konek_wifi()
mqtt = MQTTRingan("pico-bearing", TB_HOST, TB_PORT, TB_TOKEN)

def kirim_tb(data):
    try:
        if mqtt.s is None:
            if not wlan.isconnected():
                wlan.connect(WIFI_SSID, WIFI_PASS)
                time.sleep(2)
            mqtt.connect()
            print("[MQTT] Terhubung ke ThingsBoard EU")
        mqtt.publish(TB_TOPIC, json.dumps(data))
        print("[MQTT] Telemetri berhasil dikirim")
    except Exception as e:
        print("[MQTT Error]:", e)
        mqtt.close()

# ===================== LOOP UTAMA DENGAN THRESHOLD =====================
no = 0
while True:
    no += 1
    t_konv = time.ticks_ms()
    if roms:
        try: ds.convert_temp()
        except Exception: pass

    ax, ay, az, fs = ambil_sampel()

    suhu = 27.0
    if roms:
        sisa = 300 - time.ticks_diff(time.ticks_ms(), t_konv)
        if sisa > 0:
            time.sleep_ms(sisa)
        try:
            val = ds.read_temp(roms[0])
            if val is not None and -50.0 < val < 130.0:
                suhu = val
        except Exception:
            pass

    r = analisis(ax, ay, az, fs)
    rms_total = r[3]
    peak_total = r[7]

    # Evaluasi Ambang Batas
    is_temp_alarm = (suhu >= TEMP_ALARM_THRESHOLD)
    is_vibr_alarm = (rms_total >= RMS_ALARM_THRESHOLD) or (peak_total >= PEAK_ALARM_THRESHOLD)
    alarm_active  = is_temp_alarm or is_vibr_alarm

    # Kontrol Tampilan Layar LCD 16x2
    if lcd:
        if is_vibr_alarm and is_temp_alarm:
            lcd.write_lines("ALAT BERGETAR &", "PANAS! RUSAK!!!")
        elif is_vibr_alarm:
            lcd.write_lines("ALAT BERGETAR,", "RUSAK!")
        elif is_temp_alarm:
            lcd.write_lines("ALAT PANAS!", "RUSAK")
        else:
            lcd.write_lines("STATUS: NORMAL", "T:{:.1f}C R:{:.2f}g".format(suhu, rms_total / G))

    # Kontrol Buzzer Alarm (GP15)
    if alarm_active:
        set_buzzer(True)
        status_str = "ALARM"
    else:
        set_buzzer(False)
        status_str = "NORMAL"

    print("===== Pengukuran #%d [%s] =====" % (no, status_str))
    print("Suhu DS18B20 : %.2f °C (Alarm jika >= %.1f)" % (suhu, TEMP_ALARM_THRESHOLD))
    print("RMS Total    : %.3f m/s^2 (%.2f g) [Alarm jika >= %.2f g]" % (rms_total, rms_total / G, RMS_ALARM_THRESHOLD / G))
    print("Peak Total   : %.3f m/s^2 (%.2f g)" % (peak_total, peak_total / G))
    print("Freq Dominan : %.2f Hz | Amp Dominan: %.3f m/s^2" % (r[8], r[9]))

    # Payload Lengkap (Kompatibel dengan semua widget ThingsBoard)
    data = {
        "fs": round(fs, 1),
        "suhu": round(suhu, 2),
        "temperature": round(suhu, 2),
        "temp": round(suhu, 2),
        "rms_x": round(r[0], 3), "rms_y": round(r[1], 3), "rms_z": round(r[2], 3),
        "rms_total": round(rms_total, 3),
        "rms_total_g": round(rms_total / G, 3),
        "peak_x": round(r[4], 3), "peak_y": round(r[5], 3), "peak_z": round(r[6], 3),
        "peak_total": round(peak_total, 3),
        "peak_total_g": round(peak_total / G, 3),
        "freq_dom": round(r[8], 2), "frekuensi_dominan_total": round(r[8], 2),
        "amp_dom": round(r[9], 3),  "amplitudo_dominan_total": round(r[9], 3),
        "alarm": alarm_active,
        "status_alarm": status_str
    }
    kirim_tb(data)
    print()
    time.sleep_ms(800)
