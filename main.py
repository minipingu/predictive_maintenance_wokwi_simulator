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
SENSOR    = "AUTO"      # "AUTO" = deteksi cerdas otomatis (ADXL345 di Hardware Fisik, MPU6050 di Simulator Wokwi)
                        # Pilihan manual jika ingin mengunci: "ADXL345" atau "MPU6050"
SIM_GETAR = None        # None = otomatis (False jika ADXL fisik, True jika simulasi Wokwi)
REMOVE_DC = None        # None = otomatis (True jika ADXL fisik, False jika simulasi Wokwi)
FS_TARGET = 200         # Laju sampling (Hz)
N         = 256         # Jumlah sampel per jendela FFT (pangkat 2)
G         = 9.80665     # 1 g dalam m/s^2

# ===================== PARAMETER OPERASIONAL MESIN BOR DUDUK =====================
RPM_SPINDLE = 1500      # Kecepatan putaran spindle bor duduk (RPM). Standar industri: 900, 1500, 2400 RPM
F_SHAFT     = RPM_SPINDLE / 60.0  # Frekuensi fundamental putaran poros (Hz): 1500 / 60 = 25.0 Hz
F_HARMONIC2 = 2.0 * F_SHAFT       # Harmonik ke-2 (Misalignment belt / pulley bor): 50.0 Hz
F_BEARING   = 3.1 * F_SHAFT       # Frekuensi cacat lintasan bantalan (BPFO ball bearing 6205): 77.5 Hz

# Ambang Batas Alarm Predictive Maintenance (Disamakan dengan sensorgetaranEdit.py)
TEMP_ALARM_THRESHOLD = 30.0        # Ambang batas suhu (°C)
RMS_ALARM_THRESHOLD  = 5.0         # 5.0 m/s^2 (~0.51 g)
PEAK_ALARM_THRESHOLD = 10.0        # 10.0 m/s^2 (~1.02 g)

# Pinout Raspberry Pi Pico W (Mengikuti port GPIO sensorgetaranEdit.py)
PIN_BZ  = 17            # Buzzer PWM -> GP17 (Pin Fisik 22)
PIN_DS  = 4             # DS18B20 DQ -> GP4  (Pin Fisik 6)
PIN_SDA = 2             # I2C1 SDA   -> GP2  (Pin Fisik 4)
PIN_SCL = 3             # I2C1 SCL   -> GP3  (Pin Fisik 5)

# WiFi & ThingsBoard EU
WIFI_SSID = "iot_bbpvp"
WIFI_PASS = "Guntur01!"
TB_HOST   = "eu.thingsboard.cloud"
TB_PORT   = 1883
TB_TOKEN  = "4M6BoXXbvLbZ5MzS56NS"  # Token aktif ThingsBoard EU
TB_TOPIC  = "v1/devices/me/telemetry"

# Profil getaran mekanis bor duduk (Frekuensi Hz, Amplitudo baseline m/s^2)
SIM_X = (F_SHAFT, 1.5)      # Sumbu X: getaran unbalance putaran spindle (25 Hz)
SIM_Y = (F_HARMONIC2, 1.0)  # Sumbu Y: getaran transmisi sabuk/pulley bor (50 Hz)
SIM_Z = (F_BEARING, 0.5)    # Sumbu Z: getaran aksial lintasan bola bearing (77.5 Hz)

# ===================== DRIVER BUZZER ALARM (SIRINE AMBULANS) =====================
buzzer_pwm = PWM(Pin(PIN_BZ))
buzzer_pwm.duty_u16(0)

# Frekuensi nada sirine ambulans standar (Dua Nada Hi-Lo: 960 Hz & 770 Hz)
NADA_SIREN_HI = 960   # Hz (Nada tinggi "Nee")
NADA_SIREN_LO = 770   # Hz (Nada rendah "Naw")

def set_buzzer(active=True, freq=NADA_SIREN_HI):
    """Mengontrol status buzzer alarm (PWM 50% duty saat aktif)"""
    if active:
        buzzer_pwm.freq(freq)
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

# ===================== INISIALISASI I2C & LCD =====================
# RP2040: GP2/3, GP6/7, GP10/11, GP14/15, GP18/19, GP26/27 menggunakan I2C(1); lainnya I2C(0)
_I2C_ID = 1 if (PIN_SDA, PIN_SCL) in ((2, 3), (6, 7), (10, 11), (14, 15), (18, 19), (26, 27)) else 0
i2c = I2C(_I2C_ID, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=100000)

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
    lcd.write_lines("PREDICTIVE MAINT", "DIAGNOSTIK HW...")

# ===================== PENGECEKAN & DETEKSI SENSOR =====================
sensor_vibr_ok = False
sensor_temp_ok = False
ADDR = 0x53
SKALA = 0.0039 * G
REG, FMT = 0x32, '<hhh'

# 1. Pengecekan & Deteksi Sensor Getaran (ADXL345 Fisik vs MPU-6050 Wokwi)
try:
    devs = i2c.scan()
    detected_type = None

    # a. Cek apakah ada ADXL345 fisik di bus I2C (0x53 atau 0x1D)
    if SENSOR in ("AUTO", "ADXL345"):
        for a in [0x53, 0x1D]:
            if a in devs:
                try:
                    dev_id = i2c.readfrom_mem(a, 0x00, 1)[0]
                    if dev_id == 0xE5:
                        ADDR = a
                        detected_type = "ADXL345"
                        break
                except Exception:
                    pass

    # b. Jika ADXL345 tidak ada, cek apakah ada MPU-6050 (Wokwi / 0x68 / 0x69)
    if not detected_type and SENSOR in ("AUTO", "MPU6050"):
        for a in [0x68, 0x69]:
            if a in devs:
                ADDR = a
                detected_type = "MPU6050"
                break

    # Konfigurasi parameter operasional berdasarkan sensor yang terdeteksi
    if detected_type == "ADXL345":
        SENSOR = "ADXL345"
        sensor_vibr_ok = True
        ADDR = ADDR if ADDR in [0x53, 0x1D] else 0x53
        SKALA = 0.0039 * G
        REG, FMT = 0x32, '<hhh'
        ODR = {100: 0x0A, 200: 0x0B, 400: 0x0C, 800: 0x0D, 1600: 0x0E, 3200: 0x0F}
        i2c.writeto_mem(ADDR, 0x2C, bytes([ODR.get(FS_TARGET * 2, 0x0D)]))
        i2c.writeto_mem(ADDR, 0x31, b'\x0B')  # Full resolution +/- 16g
        i2c.writeto_mem(ADDR, 0x2D, b'\x08')  # Measure mode
        if SIM_GETAR is None: SIM_GETAR = False  # Hardware fisik: baca getaran nyata mesin bor
        if REMOVE_DC is None: REMOVE_DC = True   # Filter DC aktif untuk hilangkan 1g gravitasi
        print(f"[HW OK] Terdeteksi Hardware Fisik: ADXL345 di I2C 0x{ADDR:02X} (SIM_GETAR={SIM_GETAR}, REMOVE_DC={REMOVE_DC})")

    elif detected_type == "MPU6050":
        SENSOR = "MPU6050"
        sensor_vibr_ok = True
        ADDR = ADDR if ADDR in [0x68, 0x69] else 0x68
        SKALA = (4.0 * G) / 32768.0
        REG, FMT = 0x3B, '>hhh'
        i2c.writeto_mem(ADDR, 0x6B, b'\x00')  # Bangunkan MPU6050 dari sleep
        i2c.writeto_mem(ADDR, 0x1C, b'\x08')  # Range +/- 4g
        if SIM_GETAR is None: SIM_GETAR = True   # Mode Wokwi: getaran sintetis aktif
        if REMOVE_DC is None: REMOVE_DC = False  # Mode Wokwi: slider Wokwi langsung terbaca
        print(f"[WOKWI OK] Terdeteksi Simulator Wokwi: MPU-6050 di I2C 0x{ADDR:02X} (SIM_GETAR={SIM_GETAR}, REMOVE_DC={REMOVE_DC})")

    else:
        # Jika tidak terdeteksi sama sekali
        sensor_vibr_ok = False
        if SENSOR == "AUTO": SENSOR = "ADXL345"  # Asumsikan target fisik adalah ADXL345
        if SIM_GETAR is None: SIM_GETAR = False
        if REMOVE_DC is None: REMOVE_DC = True
        print("[HW FAIL] Tidak ada sensor getaran (ADXL345 / MPU-6050) terdeteksi di bus I2C!")
except Exception as e:
    print("[HW WARN] Gagal inisialisasi sensor getaran:", e)

# 2. Pengecekan Sensor Suhu DS18B20 (OneWire)
ow = onewire.OneWire(Pin(PIN_DS))
ds = ds18x20.DS18X20(ow)
roms = []
try:
    for _ in range(3):
        roms = ds.scan()
        if roms:
            sensor_temp_ok = True
            print(f"[HW OK] Sensor DS18B20 terdeteksi ({len(roms)} sensor di GP{PIN_DS})")
            break
        time.sleep_ms(100)
    if not sensor_temp_ok:
        print(f"[HW FAIL] Sensor DS18B20 TIDAK TERDETEKSI di GP{PIN_DS}!")
except Exception as e:
    print("[HW WARN] Gagal memindai DS18B20:", e)

# 3. Prompt Peringatan di LCD jika Sensor Tidak Terdeteksi
if not sensor_vibr_ok or not sensor_temp_ok:
    # Bip peringatan (2x nada sirine ambulans pendek)
    try:
        set_buzzer(True, NADA_SIREN_HI)
        time.sleep_ms(120)
        set_buzzer(True, NADA_SIREN_LO)
        time.sleep_ms(120)
        set_buzzer(True, NADA_SIREN_HI)
        time.sleep_ms(120)
        set_buzzer(False)
    except Exception:
        pass

    if not sensor_vibr_ok and not sensor_temp_ok:
        err_l1, err_l2 = "ERR: NO SENSORS!", "CEK ADXL & DS18!"
        print(f"[LCD PROMPT] ERR: SENSOR HILANG! Cek kabel ADXL345 (I2C GP{PIN_SDA}/GP{PIN_SCL}) & DS18B20 (GP{PIN_DS}).")
    elif not sensor_vibr_ok:
        tag_v = "ADXL345" if SENSOR == "ADXL345" else "MPU-6050"
        err_l1, err_l2 = f"ERR: NO {tag_v[:7]}!", "CEK KABEL I2C!"
        print(f"[LCD PROMPT] ERR: NO {tag_v}! Cek kabel I2C SDA(GP{PIN_SDA}) & SCL(GP{PIN_SCL}).")
    else:
        err_l1, err_l2 = "ERR: NO DS18B20!", "CEK KABEL DATA!"
        print(f"[LCD PROMPT] ERR: NO DS18B20! Cek kabel DQ(GP{PIN_DS}) & Resistor Pull-up 4.7k.")

    if lcd:
        lcd.write_lines(err_l1, err_l2)
    time.sleep(3)  # Tampilkan prompt peringatan selama 3 detik di LCD

# ===================== KALIBRASI 1X OTOMATIS =====================
OFFSET_X, OFFSET_Y, OFFSET_Z = 0.0, 0.0, 0.0

# Muat kalibrasi sebelumnya jika ada
try:
    with open("calib_adxl.json", "r") as _f:
        _cal = json.load(_f)
        OFFSET_X = _cal.get("offset_x_ms2", 0.0)
        OFFSET_Y = _cal.get("offset_y_ms2", 0.0)
        OFFSET_Z = _cal.get("offset_z_ms2", 0.0)
        print(f"[CALIB LOAD] Offset tersimpan: X={OFFSET_X:+.3f}, Y={OFFSET_Y:+.3f}, Z={OFFSET_Z:+.3f} m/s^2")
except Exception:
    pass

def kalibrasi_otomatis_1x(n_sampel=60):
    """Menjalankan kalibrasi offset otomatis 1x saat booting (posisi diam & rata)"""
    global OFFSET_X, OFFSET_Y, OFFSET_Z
    if not sensor_vibr_ok:
        print("[KALIB] Dilewati: Sensor getaran belum terdeteksi.")
        return

    tag = "ADXL" if SENSOR == "ADXL345" else "MPU"
    if lcd:
        lcd.write_lines(f"KALIBRASI {tag}...", "POSISI DIAM...")
    print(f"\n[KALIBRASI] Menjalankan kalibrasi 1x {tag} ({n_sampel} sampel)...")
    print("[KALIBRASI] Pastikan sensor berada pada posisi diam & datar.")

    time.sleep_ms(300)
    sx = sy = sz = 0.0
    valid = 0
    for i in range(n_sampel):
        try:
            d = i2c.readfrom_mem(ADDR, REG, 6)
            rx, ry, rz = struct.unpack(FMT, d)
            sx += rx * SKALA
            sy += ry * SKALA
            sz += rz * SKALA
            valid += 1
        except Exception:
            pass
        time.sleep_ms(15)

    if valid >= 10:
        mean_x = sx / valid
        mean_y = sy / valid
        mean_z = sz / valid

        # Target diam: hilangkan offset & bias gravitasi agar X=0, Y=0, Z=0 m/s^2 (seperti sensorgetaranEdit.py)
        OFFSET_X = mean_x
        OFFSET_Y = mean_y
        OFFSET_Z = mean_z

        print(f"[KALIBRASI OK] Offset Baru: X={OFFSET_X:+.3f}, Y={OFFSET_Y:+.3f}, Z={OFFSET_Z:+.3f} m/s^2")

        try:
            with open("calib_adxl.json", "w") as f:
                json.dump({
                    "offset_x_ms2": round(OFFSET_X, 4),
                    "offset_y_ms2": round(OFFSET_Y, 4),
                    "offset_z_ms2": round(OFFSET_Z, 4),
                    "sensor": SENSOR,
                    "timestamp": time.time()
                }, f)
            print("[KALIBRASI] Parameter kalibrasi disimpan ke 'calib_adxl.json'")
        except Exception:
            pass

        if lcd:
            lcd.write_lines("KALIBRASI OK!", "OFFSET TERSIMPAN")
        time.sleep_ms(1200)

# Jalankan kalibrasi 1x saat booting
kalibrasi_otomatis_1x(n_sampel=60)

def baca_xyz():
    global sensor_vibr_ok
    try:
        d = i2c.readfrom_mem(ADDR, REG, 6)
        x, y, z = struct.unpack(FMT, d)
        ax = (x * SKALA) - OFFSET_X
        ay = (y * SKALA) - OFFSET_Y
        az = (z * SKALA) - OFFSET_Z
        sensor_vibr_ok = True
        return ax, ay, az
    except Exception:
        sensor_vibr_ok = False
        return 0.0, 0.0, 0.0

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

        # Modulasi Sirine Ambulans (Non-Blocking: Bergantian 960 Hz & 770 Hz setiap 320 ms)
        if alarm_active and (i & 63 == 0):
            buzzer_pwm.freq(NADA_SIREN_HI if (i & 64 == 0) else NADA_SIREN_LO)
            buzzer_pwm.duty_u16(32768)
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
alarm_active = False
while True:
    no += 1
    t_konv = time.ticks_ms()
    if roms:
        try: ds.convert_temp()
        except Exception: pass

    ax, ay, az, fs = ambil_sampel()

    suhu = 27.0
    if roms and sensor_temp_ok:
        sisa = 300 - time.ticks_diff(time.ticks_ms(), t_konv)
        if sisa > 0:
            time.sleep_ms(sisa)
        try:
            val = ds.read_temp(roms[0])
            if val is not None and -50.0 < val < 130.0:
                suhu = val
            else:
                sensor_temp_ok = False
        except Exception:
            sensor_temp_ok = False
    elif not roms:
        try:
            roms = ds.scan()
            if roms:
                sensor_temp_ok = True
                print(f"[RECONNECT] Sensor DS18B20 terhubung kembali ({len(roms)} sensor)")
        except Exception:
            pass

    r = analisis(ax, ay, az, fs)
    rms_total = r[3]
    peak_total = r[7]

    # Evaluasi Ambang Batas (sesuai sensorgetaranEdit.py)
    is_temp_alarm = (suhu > TEMP_ALARM_THRESHOLD)
    is_vibr_alarm = (rms_total > RMS_ALARM_THRESHOLD) or (peak_total > PEAK_ALARM_THRESHOLD)
    alarm_active  = is_temp_alarm or is_vibr_alarm

    # Evaluasi Pesan Status Berdasarkan Ambang Batas (Threshold) & Kondisi Sensor
    if not sensor_vibr_ok and not sensor_temp_ok:
        status_str = "ERR: SENSOR HILANG!"
        line1_lcd, line2_lcd = "ERR: NO SENSORS!", "CEK ADXL & DS18!"
    elif not sensor_vibr_ok:
        tag_v = "ADXL" if SENSOR == "ADXL345" else "MPU"
        status_str = f"ERR: NO {tag_v}!"
        line1_lcd, line2_lcd = f"ERR: NO {tag_v}!", f"T:{suhu:.1f}C R:ERR"
    elif not sensor_temp_ok:
        status_str = "ERR: NO DS18B20!"
        line1_lcd, line2_lcd = "ERR: NO DS18B20!", f"T:ERR R:{rms_total:.2f}"
    elif is_vibr_alarm and is_temp_alarm:
        status_str = "ALAT BERGETAR & PANAS! RUSAK!!!"
        line1_lcd, line2_lcd = "ALAT BERGETAR &", "PANAS! RUSAK!!!"
    elif is_vibr_alarm:
        status_str = "ALAT BERGETAR, RUSAK!"
        line1_lcd, line2_lcd = "ALAT BERGETAR,", "RUSAK!"
    elif is_temp_alarm:
        status_str = "ALAT PANAS! RUSAK"
        line1_lcd, line2_lcd = "ALAT PANAS!", "RUSAK"
    else:
        status_str = "NORMAL"
        line1_lcd, line2_lcd = "STATUS: NORMAL", "T:{:.1f}C R:{:.2f}".format(suhu, rms_total)

    # Kontrol Tampilan Layar LCD 16x2
    if lcd:
        lcd.write_lines(line1_lcd, line2_lcd)

    # Kontrol Buzzer Alarm Sirine Ambulans
    if alarm_active:
        set_buzzer(True, NADA_SIREN_HI)
    else:
        set_buzzer(False)

    print("===== Pengukuran #%d [%s] =====" % (no, status_str))
    print("Suhu DS18B20 : %.2f °C (Alarm jika > %.1f °C)" % (suhu, TEMP_ALARM_THRESHOLD))
    print("RMS Total    : %.3f m/s^2 (%.2f g) [Alarm jika > %.1f m/s^2]" % (rms_total, rms_total / G, RMS_ALARM_THRESHOLD))
    print("Peak Total   : %.3f m/s^2 (%.2f g) [Alarm jika > %.1f m/s^2]" % (peak_total, peak_total / G, PEAK_ALARM_THRESHOLD))
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
        "status_alarm": status_str,
        "pesan_status": status_str,
        "status": status_str,
        "sensor_vibr": SENSOR
    }
    kirim_tb(data)
    print()

    # Jeda antar-pengukuran: jika alarm aktif, bunyikan sirine ambulans bergantian (400ms HI / 400ms LO)
    if alarm_active:
        buzzer_pwm.freq(NADA_SIREN_HI)
        buzzer_pwm.duty_u16(32768)
        time.sleep_ms(400)
        buzzer_pwm.freq(NADA_SIREN_LO)
        buzzer_pwm.duty_u16(32768)
        time.sleep_ms(400)
    else:
        set_buzzer(False)
        time.sleep_ms(800)
