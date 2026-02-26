# DO Checker — Sistem Koreksi Laporan DMC

Aplikasi web untuk otomatis memverifikasi hitungan pada dokumen Delivery Order ternak menggunakan Gemini AI (gratis).

---

## Cara Setup

### 1. Ambil Gemini API Key (GRATIS)

1. Buka https://aistudio.google.com/app/apikey
2. Login dengan akun Google
3. Klik **"Create API Key"**
4. Copy API key-nya

### 2. Install Dependencies

> **Jika muncul error "No space left on device"**, jalankan ini dulu di PowerShell untuk memindahkan folder temp pip ke drive yang masih ada ruangnya (ganti E: sesuai drive kamu):
>
> ```powershell
> $env:TMPDIR = "E:\tmp"
> $env:TEMP = "E:\tmp"
> $env:TMP = "E:\tmp"
> mkdir E:\tmp -Force
> ```

Lalu install:

```bash
pip install -r requirements.txt
```

### 3. Buat file `.env`

Duplikat file `.env.example`, rename jadi `.env`, lalu isi:

```
GEMINI_API_KEY=isi_api_key_kamu_disini
```

### 4. Jalankan Aplikasi

```bash
python app.py
```

### 5. Buka Browser

Buka: http://localhost:5000

---

## Cara Pakai

### Dokumen tanpa Bruto/Terra (Format A):

1. Upload foto DO
2. Klik **"BACA DOKUMEN"**
3. Sistem langsung validasi dan tampilkan hasil

### Dokumen dengan Bruto/Terra (Format B):

1. Upload foto DO
2. Klik **"BACA DOKUMEN"**
3. Sistem mendeteksi Bruto/Terra → minta input **Nilai Bandul**
4. Isi nilai bandul → klik **"MULAI VALIDASI"**
5. Hasil koreksi tampil lengkap

---

## Yang Dicek Otomatis

| #   | Poin                            | Rumus                                |
| --- | ------------------------------- | ------------------------------------ |
| 1   | Baris Ekor per kelompok         | Σ kolom Ekor baris rincian           |
| 2A  | Baris Netto (tanpa Bruto/Terra) | Σ kolom Kg baris rincian             |
| 2B  | Baris Bruto (ada Bruto/Terra)   | Σ kolom Kg baris rincian             |
| 3   | Baris Terra                     | Nilai Bandul × Jumlah Baris Terisi   |
| 4   | Baris Netto (ada Bruto/Terra)   | Bruto − Terra                        |
| 5   | Realisasi Ekor (ringkasan atas) | Σ semua Ekor tertulis tiap kelompok  |
| 6   | Realisasi Kg (ringkasan atas)   | Σ semua Netto tertulis tiap kelompok |
| 7   | Rata-rata                       | Realisasi Kg ÷ Realisasi Ekor        |

---

## Model Gemini yang Digunakan

- **gemini-1.5-flash** → Gratis, cepat, bagus baca tulisan tangan
- Limit gratis: 15 request/menit, 1500 request/hari

---

## Kompatibilitas Python

- Python 3.10, 3.11, 3.12, 3.13 ✅

---

## Struktur File

```
do-checker/
├── app.py                  ← Backend Flask + logic validasi lengkap
├── templates/
│   └── index.html          ← Frontend UI
├── requirements.txt
├── .env.example
└── README.md
```
