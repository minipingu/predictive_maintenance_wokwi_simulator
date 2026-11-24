# ==============================================================================
# calibrate_adxl.py — Script Kalibrasi & Live Monitor ADXL345 (Raspberry Pi Pico)
# ==============================================================================
# Deskripsi : Skrip mandiri MicroPython untuk menguji koneksi I2C, melakukan
#             kalibrasi offset sensor accelerometer ADXL345 (sumbu X, Y, Z),
#             dan menampilkan streaming nilai gravitasi/akselerasi secara live
#             di terminal serial / Thonny IDE Shell.
#
# Wiring Pico :
#   - ADXL345 VCC ➔ Pin 36 (3V3) / Pin 1 RPi
#   - ADXL345 GND ➔ Pin 38 (GND)
#   - ADXL345 SDA ➔ Pin 26 (GP20)
#   - ADXL345 SCL ➔ Pin 27 (GP21)
#   - ADXL345 CS  ➔ NC (Pull-up internal modul GY-291)
#   - ADXL345 SDO ➔ NC (Pull-down internal, alamat I2C: 0x53)
# ==============================================================================

import time
import struct
import math
import json
from machine import Pin, I2C

# Konstanta Gravitasi Standar
G = 9.80665

# Pin I2C Raspberry Pi Pico
PIN_SDA = 20
PIN_SCL = 21
I2C_FREQ = 400000

# Register ADXL345
REG_DEVID       = 0x00
REG_BW_RATE     = 0x2C
REG_POWER_CTL   = 0x2D
REG_DATA_FORMAT = 0x31
REG_DATAX0      = 0x32
REG_OFSX        = 0x1E
REG_OFSY        = 0x1F
REG_OFSZ        = 0x20

# Skala Sensitivitas ADXL345 (Full Resolution Mode = 3.9 mg / LSB)
SCALE_G = 0.0039          # g per LSB
SCALE_MS2 = 0.0039 * G    # m/s^2 per LSB

FILE_CALIB = "calib_adxl.json"

class ADXL345:
    def __init__(self, sda_pin=PIN_SDA, scl_pin=PIN_SCL, freq=I2C_FREQ):
        self.i2c = I2C(0, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=freq)
        self.addr = self._find_sensor()
        if not self.addr:
            raise RuntimeError("Sensor ADXL345 TIDAK TERDETEKSI! Periksa kabel VCC, GND, SDA(GP20), SCL(GP21).")
        
        # Konfigurasi ADXL345
        # 1. Bandwidth / Output Data Rate (100 Hz = 0x0A, 400 Hz = 0x0C)
        self.i2c.writeto_mem(self.addr, REG_BW_RATE, b'\x0A')
        # 2. Data format: Full Resolution, +/- 16g range (0x0B)
        self.i2c.writeto_mem(self.addr, REG_DATA_FORMAT, b'\x0B')
        # 3. Power Control: Measure Mode (0x08)
        self.i2c.writeto_mem(self.addr, REG_POWER_CTL, b'\x08')
        time.sleep_ms(20)

        # Offset software default
        self.offset_x_g = 0.0
        self.offset_y_g = 0.0
        self.offset_z_g = 0.0
        self.load_calibration()

    def _find_sensor(self):
        devices = self.i2c.scan()
        print(f"[I2C SCAN] Alamat I2C terdeteksi: {[hex(d) for d in devices]}")
        for addr in [0x53, 0x1D]:
            if addr in devices:
                try:
                    dev_id = self.i2c.readfrom_mem(addr, REG_DEVID, 1)[0]
                    if dev_id == 0xE5:
                        print(f"[OK] ADXL345 ditemukan di alamat 0x{addr:02X} (Device ID: 0x{dev_id:02X})")
                        return addr
                except Exception:
                    pass
        return None

    def read_raw(self):
        """Membaca data 16-bit mentah (two's complement) sumbu X, Y, Z"""
        data = self.i2c.readfrom_mem(self.addr, REG_DATAX0, 6)
        return struct.unpack('<hhh', data)

    def read_g(self, apply_calib=True):
        """Membaca percepatan dalam satuan 'g'"""
        rx, ry, rz = self.read_raw()
        gx = rx * SCALE_G
        gy = ry * SCALE_G
        gz = rz * SCALE_G
        if apply_calib:
            gx -= self.offset_x_g
            gy -= self.offset_y_g
            gz -= self.offset_z_g
        return gx, gy, gz

    def read_ms2(self, apply_calib=True):
        """Membaca percepatan dalam satuan m/s^2"""
        gx, gy, gz = self.read_g(apply_calib)
        return gx * G, gy * G, gz * G

    def calibrate(self, samples=300):
        """
        Prosedur kalibrasi saat sensor berada pada permukaan datar dan diam.
        Kondisi ideal saat diam datar (Z menghadap atas):
          X = 0.0 g, Y = 0.0 g, Z = +1.0 g
        """
        print("\n" + "=" * 65)
        print("          MEMULAI PROSES KALIBRASI SENSOR ADXL345")
        print("=" * 65)
        print("PERHATIAN:")
        print("1. Letakkan modul ADXL345 pada bidang DATAR dan STABIL (meja).")
        print("2. Pastikan sisi chip komponen menghadap ke ATAS (Z = +1g).")
        print("3. JANGAN sentuh atau gerakkan sensor selama pengambilan sampel!")
        print("=" * 65)
        
        for countdown in range(3, 0, -1):
            print(f"Mengambil sampel dalam {countdown} detik...")
            time.sleep(1)

        print(f"\nSedang mengambil {samples} sampel data...")
        sum_x = 0.0
        sum_y = 0.0
        sum_z = 0.0

        for i in range(samples):
            rx, ry, rz = self.read_raw()
            sum_x += rx * SCALE_G
            sum_y += ry * SCALE_G
            sum_z += rz * SCALE_G
            time.sleep_ms(10)
            if (i + 1) % 50 == 0:
                print(f"  Progress: {i + 1}/{samples} sampel selesai...")

        # Rata-rata pembacaan mentah
        mean_x = sum_x / samples
        mean_y = sum_y / samples
        mean_z = sum_z / samples

        # Offset kalibrasi:
        # Target X = 0.0 g, Target Y = 0.0 g, Target Z = 1.0 g (Gravitasi Bumi)
        self.offset_x_g = mean_x - 0.0
        self.offset_y_g = mean_y - 0.0
        self.offset_z_g = mean_z - 1.0

        print("\n" + "-" * 65)
        print("HASIL KALIBRASI SELESAI:")
        print(f"  Rata-rata Mentah : X = {mean_x:+.4f} g | Y = {mean_y:+.4f} g | Z = {mean_z:+.4f} g")
        print(f"  Offset Dihitung  : X = {self.offset_x_g:+.4f} g | Y = {self.offset_y_g:+.4f} g | Z = {self.offset_z_g:+.4f} g")
        print(f"  Offset (m/s^2)   : X = {self.offset_x_g*G:+.4f} | Y = {self.offset_y_g*G:+.4f} | Z = {self.offset_z_g*G:+.4f}")
        print("-" * 65)

        self.save_calibration()

    def save_calibration(self):
        """Menyimpan offset ke berkas JSON di flash MicroPython"""
        calib_data = {
            "offset_x_g": round(self.offset_x_g, 5),
            "offset_y_g": round(self.offset_y_g, 5),
            "offset_z_g": round(self.offset_z_g, 5),
            "offset_x_ms2": round(self.offset_x_g * G, 5),
            "offset_y_ms2": round(self.offset_y_g * G, 5),
            "offset_z_ms2": round(self.offset_z_g * G, 5),
            "timestamp_calib": time.time()
        }
        try:
            with open(FILE_CALIB, "w") as f:
                json.dump(calib_data, f)
            print(f"[OK] Parameter kalibrasi berhasil disimpan ke berkas '{FILE_CALIB}'!")
        except Exception as e:
            print(f"[ERROR] Gagal menyimpan kalibrasi: {e}")

    def load_calibration(self):
        """Memuat offset dari berkas JSON jika sudah pernah dikalibrasi"""
        try:
            with open(FILE_CALIB, "r") as f:
                data = json.load(f)
                self.offset_x_g = data.get("offset_x_g", 0.0)
                self.offset_y_g = data.get("offset_y_g", 0.0)
                self.offset_z_g = data.get("offset_z_g", 0.0)
                print(f"[LOAD] Offset kalibrasi dimuat dari '{FILE_CALIB}': X={self.offset_x_g:+.4f}g, Y={self.offset_y_g:+.4f}g, Z={self.offset_z_g:+.4f}g")
                return True
        except Exception:
            print("[INFO] Belum ada berkas kalibrasi tersimpan. Menggunakan offset default (0.0).")
            return False

    def write_hardware_offset(self):
        """
        Menuliskan offset langsung ke register internal ADXL345 (OFSX, OFSY, OFSZ).
        Skala register offset: 15.6 mg/LSB (sekitar 4 LSB per satuan LSB Full-Res).
        """
        try:
            # Nilai register = - (offset dalam g / 0.0156)
            ofs_x = int(-round(self.offset_x_g / 0.0156))
            ofs_y = int(-round(self.offset_y_g / 0.0156))
            ofs_z = int(-round(self.offset_z_g / 0.0156))
            
            # Batasi rentang signed 8-bit (-128 s/d 127)
            ofs_x = max(-128, min(127, ofs_x)) & 0xFF
            ofs_y = max(-128, min(127, ofs_y)) & 0xFF
            ofs_z = max(-128, min(127, ofs_z)) & 0xFF

            self.i2c.writeto_mem(self.addr, REG_OFSX, bytes([ofs_x]))
            self.i2c.writeto_mem(self.addr, REG_OFSY, bytes([ofs_y]))
            self.i2c.writeto_mem(self.addr, REG_OFSZ, bytes([ofs_z]))
            print(f"[OK] Hardware Offset Registers ditulis: OFSX=0x{ofs_x:02X}, OFSY=0x{ofs_y:02X}, OFSZ=0x{ofs_z:02X}")
        except Exception as e:
            print(f"[ERROR] Gagal menulis ke hardware offset: {e}")

# ==============================================================================
# FUNGSI LIVE STREAMING MONITOR
# ==============================================================================
def draw_bar(val, max_val=2.0, length=12):
    """Membuat representasi grafis batang ASCII untuk visualisasi getaran"""
    norm = min(1.0, max(-1.0, val / max_val))
    pos = int((norm + 1.0) / 2.0 * length)
    pos = max(0, min(length, pos))
    bar = ["."] * (length + 1)
    center = length // 2
    bar[center] = "|"
    bar[pos] = "█"
    return "".join(bar)

def live_monitor(sensor, hz=10):
    """Menampilkan data X, Y, Z dan getaran resultan secara live di terminal"""
    delay = 1.0 / hz
    print("\n" + "=" * 78)
    print("        LIVE STREAMING MONITOR SENSOR ADXL345 (TEKAN CTRL+C UNTUK KEMBALI)")
    print("=" * 78)
    print(f" Frekuensi Update : {hz} Hz ({int(delay*1000)} ms per sampel)")
    print(" Status Kalibrasi : Terpasang otomatis (Software Offset)")
    print("-" * 78)
    print(" No |   Sumbu X (g)   |   Sumbu Y (g)   |   Sumbu Z (g)   | Resultan R | Visual")
    print("-" * 78)

    sample_num = 0
    try:
        while True:
            sample_num += 1
            gx, gy, gz = sensor.read_g(apply_calib=True)
            r = math.sqrt(gx**2 + gy**2 + gz**2)
            
            # Format bar grafik sumbu X
            bar = draw_bar(gx, max_val=1.5, length=10)

            # Baris output terminal
            print(f"{sample_num:04d}| {gx:+7.3f} g ({gx*G:+6.2f})| {gy:+7.3f} g ({gy*G:+6.2f})| {gz:+7.3f} g ({gz*G:+6.2f})| {r:6.3f} g   | [{bar}]")
            time.sleep(delay)
    except KeyboardInterrupt:
        print("\n\n[INFO] Live monitoring dihentikan oleh pengguna.")

# ==============================================================================
# MENU UTAMA INTERAKTIF
# ==============================================================================
def main():
    print("\n" + "=" * 65)
    print("    PROGRAM KALIBRASI & LIVE MONITOR ADXL345 — RASPBERRY PI PICO")
    print("=" * 65)

    try:
        sensor = ADXL345(PIN_SDA, PIN_SCL)
    except Exception as e:
        print(f"[FATAL] {e}")
        return

    while True:
        print("\n" + "=" * 45)
        print("              PILIHAN MENU:")
        print("=" * 45)
        print(" [1] Mulai Kalibrasi Sensor ADXL345 (Diam/Datar)")
        print(" [2] Tampilkan Live Streaming Sumbu X, Y, Z")
        print(" [3] Cek Status Offset Kalibrasi Saat Ini")
        print(" [4] Tulis Offset ke Register Hardware (OFSX/Y/Z)")
        print(" [5] Reset Offset Kalibrasi ke 0 (Hapus Offset)")
        print(" [0] Keluar")
        print("=" * 45)
        
        try:
            choice = input("Pilih menu (0-5) lalu tekan Enter: ").strip()
        except Exception:
            break

        if choice == "1":
            sensor.calibrate(samples=300)
        elif choice == "2":
            live_monitor(sensor, hz=10)
        elif choice == "3":
            print("\n--- STATUS OFFSET SAAT INI ---")
            print(f"  Offset X : {sensor.offset_x_g:+.5f} g ({sensor.offset_x_g*G:+.4f} m/s^2)")
            print(f"  Offset Y : {sensor.offset_y_g:+.5f} g ({sensor.offset_y_g*G:+.4f} m/s^2)")
            print(f"  Offset Z : {sensor.offset_z_g:+.5f} g ({sensor.offset_z_g*G:+.4f} m/s^2)")
            gx, gy, gz = sensor.read_g()
            r = math.sqrt(gx**2 + gy**2 + gz**2)
            print(f"  Bacaan Real-Time Saat Ini : X={gx:+.3f}g, Y={gy:+.3f}g, Z={gz:+.3f}g | R={r:.3f}g")
        elif choice == "4":
            sensor.write_hardware_offset()
        elif choice == "5":
            sensor.offset_x_g = 0.0
            sensor.offset_y_g = 0.0
            sensor.offset_z_g = 0.0
            sensor.save_calibration()
            print("[OK] Offset telah di-reset ke 0.0.")
        elif choice == "0":
            print("[INFO] Selesai. Program keluar.")
            break
        else:
            print("[WARNING] Pilihan tidak valid, silakan ketik angka 0 - 5.")

if __name__ == "__main__":
    main()
