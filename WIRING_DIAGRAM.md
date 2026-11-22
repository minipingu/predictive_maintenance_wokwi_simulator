# PANDUAN LENGKAP WIRING DETAIL & PREDICTIVE MAINTENANCE BEARING BOR

Proyek ini merealisasikan sistem **Predictive Maintenance Bearing Bor** menggunakan simulator **Wokwi** (dengan Raspberry Pi Pico W) dan terhubung langsung secara real-time ke **ThingsBoard Cloud EU** melalui protokol **MQTT**.

---

## 1. Analisis Visual Gambar Dashboard ThingsBoard (Gambar 1)
Berdasarkan visual dashboard ThingsBoard pada Gambar 1, parameter telemetri dan komponen visual yang wajib dikirim adalah:

1. **Grafik Getaran RMS XYZ:**
   - `rms_x`: Nilai RMS getaran dinamis sumbu X (g)
   - `rms_y`: Nilai RMS getaran dinamis sumbu Y (g)
   - `rms_z`: Nilai RMS getaran dinamis sumbu Z (g)
2. **Grafik Getaran RMS Total:**
   - `rms_total`: $\sqrt{\text{rms}_x^2 + \text{rms}_y^2 + \text{rms}_z^2}$ (g)
3. **Grafik Frekuensi Dominan Total:**
   - `frekuensi_dominan_total`: Frekuensi spektral tertinggi hasil FFT (Hz)
4. **Grafik Getaran Peak XYZ:**
   - `peak_x`: Nilai puncak getaran absolut sumbu X (g)
   - `peak_y`: Nilai puncak getaran absolut sumbu Y (g)
   - `peak_z`: Nilai puncak getaran absolut sumbu Z (g)
5. **Grafik Getaran Peak Total:**
   - `peak_total`: $\sqrt{\text{peak}_x^2 + \text{peak}_y^2 + \text{peak}_z^2}$ (g)
6. **Grafik Amplitudo Dominan Total:**
   - `amplitudo_dominan_total`: Amplitudo getaran pada frekuensi dominan (g)
7. **Radial Gauge Suhu ("Temperature"):**
   - `temperature`: Pembacaan temperatur bearing bor dari sensor DS18B20 (°C). Pada gauge Gambar 1, zona merah/alarm dimulai dari 50 °C.
8. **Widget Indikator Alarm Bulat ("ALARM"):**
   - `alarm`: Boolean (`false` = mati/normal, `true` = menyala merah/alarm).
9. **Widget Card Teks ("Status Alarm : NORMAL"):**
   - `status_alarm`: String (`"NORMAL"` saat aman, `"ALARM"` saat batas getaran atau suhu terlampaui).

---

## 2. Analisis Komponen Hardware (Gambar 2) & Pinout Wiring

| Komponen | Pin Modul | Warna Kabel | Pin Raspberry Pi Pico | Nama Pin Pico | Deskripsi / Catatan Khusus |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **DS18B20**<br>*(Waterproof)* | **VCC** (Merah) | **Merah** | **Pin 36** | **3V3 (OUT)** | **Daya 3.3V** aman untuk logic GPIO Pico RP2040 (3.0V–5.5V) |
| | **GND** (Hitam) | **Hitam** | **Pin 38** | **GND** | Grounding bersama |
| | **DQ** (Kuning/Hijau) | **Hijau / Kuning**| **Pin 21** | **GP16** | Pin data OneWire |
| **Resistor 4.7kΩ** | Pin 1 | Hijau | Pin 21 | GP16 | Terhubung ke DQ DS18B20 |
| | Pin 2 | Oranye | Pin 36 | 3V3 (OUT) | Pull-up ke 3.3V agar level logika GP16 aman |
| **MPU-6050** *(Wokwi)*<br>*(Emulasi ADXL345)* | **VCC** | **Oranye / Merah**| **Pin 36** | **3V3 (OUT)** | Tegangan logika 3.3V |
| | **GND & AD0** | **Hitam** | **Pin 28** | **GND** | Ground & Alamat I2C `0x68` |
| | **SCL** | **Kuning / Emas** | **Pin 27** | **GP21** | Jalur I2C0 SCL |
| | **SDA** | **Biru** | **Pin 26** | **GP20** | Jalur I2C0 SDA |
| **Buzzer Alarm**<br>*(Wokwi: Pin 2=+, Pin 1=-)* | **Pin 2 / Positif (+)** (Kabel Merah) | **Merah** | **Pin 34** | **GP28** | Kontrol PWM Alarm frekuensi 2500 Hz |
| | **Pin 1 / Negatif (-)** (Kabel Hitam) | **Hitam** | **Pin 38** | **GND** | Grounding Buzzer |
| **LCD 16x2 I2C**<br>*(PCF8574 Backpack)* | **VCC** | **Merah** | **Pin 40** | **VBUS (5V)** | Catu daya modul I2C LCD & lampu latar backlight |
| | **GND** | **Hitam** | **Pin 23** | **GND** | Grounding bersama (Pin 23 Pico GND.5) |
| | **SDA** | **Biru** | **Pin 26** | **GP20** | Jalur I2C0 Data (Alamat default `0x27`) |
| | **SCL** | **Kuning / Emas** | **Pin 27** | **GP21** | Jalur I2C0 Clock |

---

## 3. Kompatibilitas ADXL345 vs MPU-6050 (100% Plug-and-Play)

Sensor fisik asli Anda adalah **ADXL345**, sedangkan di Wokwi hanya tersedia **MPU-6050**. Sistem ini dirancang agar **100% identik dan tanpa perubahan kode** saat Anda berpindah dari simulasi ke modul fisik:

| Parameter | MPU-6050 (Wokwi Simulator) | ADXL345 (Real Hardware Fisik) |
| :--- | :--- | :--- |
| **Antarmuka** | I2C (SDA=GP20, SCL=GP21) | I2C (SDA=GP20, SCL=GP21) |
| **Tegangan Kerja** | 3.3V (Pin 36 Pico) | 3.3V (Pin 36 Pico) |
| **Alamat I2C** | `0x68` | `0x53` (atau `0x1D`) |
| **Driver Terintegrasi (`main.py`)** | Otomatis mendeteksi `0x68` dan membaca percepatan 3-sumbu dalam satuan $g$ | Otomatis mendeteksi `0x53` dan membaca percepatan 3-sumbu dalam satuan $g$ |

> **Wiring Real ADXL345 ke Pico:**
> - VCC -> Pin 36 (3V3)
> - GND -> Pin 28 (GND)
> - CS -> Pin 36 (3V3) *(mengaktifkan mode I2C)*
> - SDO -> Pin 28 (GND) *(memilih alamat 0x53)*
> - SDA -> Pin 26 (GP20)
> - SCL -> Pin 27 (GP21)

---

## 4. Ambang Batas (Thresholds) Predictive Maintenance Bearing Bor

Berdasarkan standar vibrasi mesin putar **ISO 10816-3 (Class 1 / Class 2)** dan data operasional bearing spindle bor:

1. **Ambang Batas Suhu Bearing (`TEMP_ALARM_THRESHOLD = 50.0 °C`):**
   - Normal operasional bearing: 25 °C s/d 45 °C.
   - Suhu &ge; 50.0 °C mengindikasikan pelumasan berkurang (kurang grease/oli), gesekan berlebih antar bola/roller bearing, atau beban berlebih (*overload*).
2. **Ambang Batas RMS Getaran (`RMS_ALARM_THRESHOLD = 2.50 g`):**
   - Mengindikasikan kerusakan dini pada lintasan dalam/luar (*inner/outer race defect*).
3. **Ambang Batas Peak Getaran (`PEAK_ALARM_THRESHOLD = 4.50 g`):**
   - Mengindikasikan spalling/chipping pada bearing yang menimbulkan kejutan impulsif (*impact vibration*).
4. **Respon Sistem Saat Alarm Terpicu:**
   - Buzzer hardware aktif dan mengeluarkan bunyi frekuensi 2500 Hz.
   - Mengirimkan `alarm: true` dan `status_alarm: "ALARM"` ke ThingsBoard EU.

---

## 5. Konfigurasi Koneksi ThingsBoard EU (MQTT)
- **Broker Host:** `eu.thingsboard.cloud`
- **Port:** `1883`
- **Device Access Token:** `4M6BoXXbvLbZ5MzS56NS`
- **MQTT Username:** `4M6BoXXbvLbZ5MzS56NS`
- **MQTT Password:** *(kosong)*
- **Topic Telemetri:** `v1/devices/me/telemetry`
