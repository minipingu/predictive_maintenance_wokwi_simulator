# PREDICTIVE MAINTENANCE BEARING BOR (WOKWI & THINGSBOARD)

Proyek simulasi Wokwi dan implementasi perangkat keras untuk pemantauan kesehatan bantalan bor (*drill bearing*) secara real-time ke ThingsBoard Cloud EU (`eu.thingsboard.cloud`).

## 📁 Struktur Berkas

```
predictive_maintenance_wokwi_simulator/
├── diagram.json          # File rangkaian Wokwi Simulator lengkap (Raspberry Pi Pico W)
├── wokwi.toml            # Konfigurasi simulasi Wokwi
├── firmware.uf2          # MicroPython binary untuk Pico W
├── main.py               # Program utama Pico W standalone (Driver Sensor, FFT, RMS, Alarm, MQTT)
├── rpi4_main.py          # Program utama fisik Raspberry Pi 4 (Linux, Python 3, Paho MQTT)
├── WIRING_DIAGRAM.svg    # Skema diagram canvas Wokwi resolusi tinggi
├── WIRING_PREVIEW.html   # Pratinjau wiring interaktif di browser
├── WIRING_DIAGRAM.md     # Panduan detail wiring & pinout
└── README.md             # Panduan penggunaan proyek ini
```

---

## 🔌 Diagram Skematik Wiring

![Diagram Wiring Predictive Maintenance](WIRING_DIAGRAM.svg)

> **Ringkasan Pinout:**
> - **DS18B20 (Suhu):** VCC $\rightarrow$ `VBUS` (Pin 40, 5V), GND $\rightarrow$ `GND` (Pin 38), DQ $\rightarrow$ `GP16` (Pin 21) + Resistor Pull-up 4.7 kΩ ke `3V3` (Pin 36).
> - **MPU-6050 / ADXL345 (Getaran):** VCC $\rightarrow$ `3V3` (Pin 36), GND $\rightarrow$ `GND` (Pin 33), SDA $\rightarrow$ `GP20` (Pin 26), SCL $\rightarrow$ `GP21` (Pin 27).
> - **Buzzer Alarm:** Positif (+) $\rightarrow$ `GP15` (Pin 20), Negatif (-) $\rightarrow$ `GND` (Pin 18).
>
> *Panduan detail wiring fisik dan koneksi hardware lengkap dapat dibaca di [WIRING_DIAGRAM.md](WIRING_DIAGRAM.md).*

---

## 🚀 Cara Menjalankan di Wokwi Simulator

### Opsi A: Melalui VS Code (Ekstensi Wokwi)
1. Buka folder `predictive_maintenance_wokwi_simulator` di VS Code / Antigravity IDE.
2. Buka berkas `diagram.json`.
3. Tekan tombol **Play / Run Simulation** pada tab diagram Wokwi.
4. Perhatikan Serial Monitor: Pico akan terhubung ke Wi-Fi Wokwi, menyambungkan MQTT ke `eu.thingsboard.cloud`, lalu mulai mengirim telemetri.

### Opsi B: Melalui Browser di Wokwi.com
1. Buka [https://wokwi.com](https://wokwi.com) dan pilih board **Raspberry Pi Pico W**.
2. Salin isi berkas `diagram.json` ke tab `diagram.json` di Wokwi.
3. Salin isi berkas `main.py` ke tab `main.py` di Wokwi (sudah all-in-one / single file, tidak perlu upload file lain).
4. Jalankan simulasi.

---

## 🧪 Cara Menguji Alarm & Buzzer di Simulator

1. **Uji Alarm Suhu:**
   - Klik modul **DS18B20** pada kanvas Wokwi saat simulasi berjalan.
   - Geser slider suhu ke atas **50.0 °C**.
   - **Hasil:** Komponen Buzzer langsung berbunyi, lampu `ALARM` di ThingsBoard menyala merah, dan teks berubah menjadi `Status Alarm : ALARM`.

2. **Uji Alarm Getaran:**
   - Klik modul **MPU-6050** pada kanvas Wokwi.
   - Ubah akselerasi pada sumbu X/Y/Z melebihi **2.50 g**.
   - **Hasil:** Komponen Buzzer berbunyi dan alarm terpicu.

3. **Kondisi Normal:**
   - Kembalikan suhu ke bawah 50 °C dan getaran di bawah 2.5 g.
   - **Hasil:** Buzzer otomatis berhenti dan status kembali `NORMAL`.

---

## 🍓 Panduan Penggunaan di Hardware Fisik: Raspberry Pi 4

Sistem ini dapat langsung dijalankan pada perangkat keras fisik **Raspberry Pi 4 Model B** yang terhubung ke mesin bor/spindle industri menggunakan skrip [rpi4_main.py](rpi4_main.py).

### 1. Tabel Pinout Header 40-Pin Raspberry Pi 4

| Modul / Sensor | Pin Sensor | Pin Fisik RPi 4 | Nama Pin / Fungsi BCM | Catatan Khusus |
| :--- | :--- | :--- | :--- | :--- |
| **ADXL345 (Getaran)** | **VCC** | **Pin 1** | `3V3 Power` | Daya sensor 3.3V |
| | **GND** | **Pin 9** | `Ground` | Jalur arde bersama |
| | **SDA** | **Pin 3** | `GPIO 2 (I2C1 SDA)` | Data bus I2C |
| | **SCL** | **Pin 5** | `GPIO 3 (I2C1 SCL)` | Clock bus I2C |
| | **CS** | **Pin 17** | `3V3 Power` | Hubungkan ke 3.3V untuk mode I2C |
| | **SDO** | **Pin 25** | `Ground` | Memilih alamat I2C `0x53` |
| **DS18B20 (Suhu)** | **VCC** (Merah) | **Pin 2** / **Pin 4** | `5V Power` | Daya probe waterproof |
| | **GND** (Hitam) | **Pin 6** | `Ground` | Jalur arde bersama |
| | **DQ** (Kuning) | **Pin 7** | `GPIO 4 (GPCLK0)` | Jalur data 1-Wire Linux kernel |
| | **Pull-Up** | Antara DQ & 3.3V | Resistor 4.7 kΩ | Pasang dari **Pin 7 (DQ)** ke **Pin 1 (3V3)** |
| **Active Buzzer** | **Positif (+)** | **Pin 12** | `GPIO 18 (PWM0)` | Pin kontrol pemicu alarm |
| | **Negatif (-)** | **Pin 14** | `Ground` | Jalur arde buzzer |

---

### 2. Langkah 1: Mengaktifkan Antarmuka I2C & 1-Wire di Raspberry Pi OS

Buka terminal di Raspberry Pi 4, lalu aktifkan modul kernel antarmuka:

```bash
# Buka menu konfigurasi Raspberry Pi
sudo raspi-config
```
1. Pilih **Interface Options** $\rightarrow$ **I2C** $\rightarrow$ Pilih **Yes** (Enable).
2. Pilih **Interface Options** $\rightarrow$ **1-Wire** $\rightarrow$ Pilih **Yes** (Enable).
3. Pilih **Finish** dan lakukan reboot:
   ```bash
   sudo reboot
   ```

*(Alternatif: Tambahkan langsung baris berikut di berkas `/boot/firmware/config.txt` atau `/boot/config.txt`:)*
```ini
dtparam=i2c_arm=on
dtoverlay=w1-gpio,gpiopin=4
```

---

### 3. Langkah 2: Menguji Deteksi Sensor di Terminal Linux

Setelah reboot, pastikan seluruh sensor terdeteksi oleh kernel Linux:

1. **Uji Sensor Getaran ADXL345 (I2C):**
   ```bash
   sudo apt install -y i2c-tools
   i2cdetect -y 1
   ```
   *Hasil:* Alamat **`53`** (ADXL345) atau **`68`** (MPU-6050) akan muncul pada tabel matriks I2C.

2. **Uji Sensor Suhu DS18B20 (1-Wire):**
   ```bash
   ls /sys/bus/w1/devices/
   ```
   *Hasil:* Akan muncul folder berawalan **`28-xxxxxxxxxxxx`**. Untuk melihat suhu:
   ```bash
   cat /sys/bus/w1/devices/28-*/w1_slave
   ```
   *Output contoh:* Baris kedua berisi `t=28125` (berarti suhu $28.125 \text{ }^\circ\text{C}$).

---

### 4. Langkah 3: Menginstal Dependensi Python 3

Jalankan perintah berikut di terminal Raspberry Pi 4:

```bash
sudo apt update
sudo apt install -y python3-pip python3-smbus python3-gpiozero python3-numpy

# Instal client MQTT Paho
pip3 install paho-mqtt
```

---

### 5. Langkah 4: Menjalankan Program Predictive Maintenance

Jalankan skrip yang telah disiapkan khusus untuk Raspberry Pi 4:

```bash
python3 rpi4_main.py
```

Serial terminal akan menampilkan log real-time:
```text
=================================================================
 PREDICTIVE MAINTENANCE BEARING BOR - RASPBERRY PI 4
 Target : ThingsBoard EU (eu.thingsboard.cloud)
=================================================================
[SENSOR] ADXL345 fisik aktif di I2C address 0x53
[MQTT] Menghubungkan ke ThingsBoard Server: eu.thingsboard.cloud:1883...
[MQTT] SUKSES TERKONEKSI KE THINGSBOARD!

-----------------------------------------------------------------
Suhu: 32.4 °C | Status: NORMAL [OK]
RMS XYZ: (0.124, 0.089, 0.985) g -> RMS Total: 0.997 g
Peak XYZ: (0.245, 0.150, 1.120) g -> Peak Total: 1.156 g
Spektrum: Freq Dominan = 14.5 Hz | Amp Dominan = 0.082 g
[MQTT] Telemetri Terkirim ke ThingsBoard EU:
        {"rms_x": 0.124, "rms_y": 0.089, "rms_z": 0.985, "rms_total": 0.997, ...}
```

---

### 6. Langkah 5: Menjalankan Otomatis Saat Booting (Systemd Service 24/7)

Agar monitoring terus berjalan otomatis di mesin pabrik saat Raspberry Pi 4 dinyalakan:

1. Buat berkas unit service systemd:
   ```bash
   sudo nano /etc/systemd/system/bearing_monitor.service
   ```

2. Masukkan konfigurasi berikut:
   ```ini
   [Unit]
   Description=Predictive Maintenance Bearing Bor Service
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=pi
   WorkingDirectory=/home/pi/predictive_maintenance_wokwi_simulator
   ExecStart=/usr/bin/python3 /home/pi/predictive_maintenance_wokwi_simulator/rpi4_main.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

3. Aktifkan dan jalankan service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable bearing_monitor.service
   sudo systemctl start bearing_monitor.service
   ```

4. Cek status service kapan saja dengan:
   ```bash
   sudo systemctl status bearing_monitor.service
   ```
