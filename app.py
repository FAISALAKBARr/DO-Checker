import os
import json
import base64
import re
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import google.generativeai as genai
from PIL import Image, ImageEnhance, ImageFilter
import io

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

# ══════════════════════════════════════════════════════════════════
# PROMPT — Hanya OCR, tidak menghitung apapun
# ══════════════════════════════════════════════════════════════════
EXTRACTION_PROMPT = """
Kamu adalah sistem OCR khusus untuk dokumen Delivery Order (DO) ternak milik DMC.
TUGASMU HANYA MEMBACA & MENGAMBIL ANGKA PERSIS SEPERTI TERTULIS. JANGAN hitung apapun.

════════════════════════════════════════
STRUKTUR DOKUMEN DO
════════════════════════════════════════

BAGIAN ATAS DOKUMEN (ringkasan global):
  - Kolom "Ekor"     → total ekor realisasi keseluruhan (baca persis)
  - Kolom "Kg"       → total kg realisasi keseluruhan (baca persis)
  - Kolom "Rata-rata"→ nilai rata-rata (baca persis, bisa desimal seperti 2.17 atau 2,17)

TABEL RINCIAN:
  Terdapat beberapa KELOMPOK kolom (biasanya 2: PESANAN di kiri, REALISASI di kanan).
  Masing-masing kelompok punya sub-kolom: "Ekor" dan "Kg".
  Setiap baris bernomor (1, 2, 3, ...) berisi angka ekor dan berat per kandang/pengiriman.

BARIS RINGKASAN DI BAWAH TABEL (baca persis, JANGAN hitung):
  Baris yang perlu dibaca per kelompok:
  - "Ekor"  → total ekor tertulis
  - "Bruto" → berat kotor tertulis (jika ada angka; jika kosong/tidak ada → null)
  - "Terra" → tara/potongan tertulis (jika ada angka; jika kosong/tidak ada → null)
  - "Netto" → berat bersih tertulis

════════════════════════════════════════
PANDUAN KHUSUS: MEMBACA ANGKA TULISAN TANGAN
════════════════════════════════════════

Tulisan tangan manusia sering menimbulkan ambiguitas. Perhatikan petunjuk berikut:

ANGKA 4 vs 9:
  → Angka 4: bagian atas TERBUKA atau berbentuk huruf V terbalik, bagian bawah LURUS ke bawah tanpa lengkungan.
  → Angka 9: bagian atas berbentuk LINGKARAN/oval tertutup, bagian bawah ada ekor yang MELENGKUNG atau menggantung.
  → Jika ragu antara 4 dan 9 → lihat konteks nilainya:
      Kolom Ekor per baris biasanya antara 10–50 ekor.
      Kolom Kg per baris biasanya antara 50–100 kg.
      Nilai Netto/Total biasanya kelipatan logis dari baris-baris di atasnya.

ANGKA 6 vs 0:
  → Angka 6: ada ekor melengkung di bagian atas, lingkaran di bawah.
  → Angka 0: oval penuh, tidak ada ekor.

ANGKA 1 vs 7:
  → Angka 7: ada garis serong di bagian atas.
  → Angka 1: lurus ke bawah, tidak ada garis serong.

ANGKA 3 vs 8:
  → Angka 8: dua lingkaran tertutup bertumpuk.
  → Angka 3: sisi kiri TERBUKA (tidak ada garis menutup ke kiri).

ANGKA 5 vs 6:
  → Angka 5: bagian atas lurus dengan sudut ke kiri.
  → Angka 6: ekor melengkung di atas tanpa sudut tajam.

ATURAN RAGU:
  Jika setelah menerapkan semua panduan di atas kamu MASIH TIDAK YAKIN pada satu angka
  di suatu baris, tambahkan field "ragu": true pada baris tersebut.
  Contoh: {"no": 3, "ekor": 30, "kg": 64.9, "ragu": true}
  Jika yakin → JANGAN tambahkan field "ragu" (atau isi false).

════════════════════════════════════════
FORMAT JSON YANG HARUS DIKEMBALIKAN
════════════════════════════════════════

Kembalikan HANYA JSON murni (tanpa markdown, tanpa ```, tanpa penjelasan apapun):

{
  "kelompok": [
    {
      "nama": "PESANAN",
      "posisi": "kiri",
      "baris": [
        {"no": 1, "ekor": 30, "kg": 81.0},
        {"no": 2, "ekor": 30, "kg": 80.5, "ragu": true}
      ],
      "tertulis_total_ekor": 300,
      "tertulis_bruto_kg": 809.5,
      "tertulis_terra_kg": 170.0,
      "tertulis_netto_kg": 639.5
    },
    {
      "nama": "REALISASI",
      "posisi": "kanan",
      "baris": [
        {"no": 1, "ekor": 30, "kg": 80.0}
      ],
      "tertulis_total_ekor": 175,
      "tertulis_bruto_kg": 471.0,
      "tertulis_terra_kg": 102.0,
      "tertulis_netto_kg": 369.0
    }
  ],
  "ringkasan_atas": {
    "tertulis_realisasi_ekor": 475,
    "tertulis_realisasi_kg": 1008.5,
    "tertulis_rata_rata": 2.12
  }
}

════════════════════════════════════════
ATURAN WAJIB
════════════════════════════════════════
1. "baris" hanya diisi baris yang ADA ANGKANYA (ada nilai di Ekor atau Kg). Baris kosong → ABAIKAN.
2. Jika kolom ekor/kg suatu baris hanya terisi salah satu → yang kosong = 0.
3. "tertulis_bruto_kg" dan "tertulis_terra_kg":
   - Jika di dokumen ADA ANGKA di baris Bruto/Terra → isi dengan angkanya
   - Jika di dokumen TIDAK ADA angka (kosong/tidak ada baris itu) → isi dengan null
4. "tertulis_netto_kg" → SELALU baca dari baris Netto yang tertulis di dokumen.
5. "tertulis_total_ekor" → baca dari baris Ekor di bawah tabel.
6. Semua nilai "tertulis_*" diambil PERSIS dari tulisan di dokumen, bukan hasil hitungan.
7. Jika ada lebih dari 2 kelompok di tabel, masukkan semuanya.
8. Nilai desimal dengan koma (misal 2,17) → ubah ke titik (2.17) dalam JSON.
9. Setelah selesai membaca, tinjau kembali setiap angka yang mengandung digit 4 atau 9.
   Pastikan sudah sesuai dengan panduan di atas sebelum mengembalikan JSON.
"""


# ══════════════════════════════════════════════════════════════════
# PROMPT RETRY — digunakan saat pass pertama gagal validasi
# ══════════════════════════════════════════════════════════════════
def buat_retry_prompt(checks_gagal: list, raw_data_lama: dict) -> str:
    """
    Buat prompt retry yang memberi tahu Gemini di mana tepatnya kemungkinan
    kesalahan baca terjadi, berdasarkan hasil validasi pass pertama.
    """
    info_gagal = []
    for c in checks_gagal:
        info_gagal.append(
            f"  - {c['label']}: hasil hitungan = {c['hitung']} {c['satuan']}, "
            f"tertulis di dokumen = {c['tertulis']} {c['satuan']}, "
            f"selisih = {c['selisih']:+.3g} {c['satuan']}"
        )
    gagal_str = '\n'.join(info_gagal)

    return f"""
Kamu adalah sistem OCR khusus untuk dokumen Delivery Order (DO) ternak milik DMC.

PERHATIAN: Ini adalah PEMBACAAN ULANG karena ada ketidaksesuaian pada pembacaan sebelumnya.

Pada pembacaan sebelumnya ditemukan perbedaan berikut:
{gagal_str}

Perbedaan ini kemungkinan besar disebabkan oleh kesalahan baca satu atau beberapa digit,
terutama pasangan yang sering mirip dalam tulisan tangan:
  → 4 vs 9  (paling sering salah!)
  → 6 vs 0
  → 1 vs 7
  → 3 vs 8

Cara membedakan 4 vs 9:
  → 4: bagian atas TERBUKA, bawah LURUS (tidak ada ekor melengkung).
  → 9: bagian atas LINGKARAN TERTUTUP, bawah ada EKOR yang menggantung.

TUGASMU: Baca ulang dokumen ini dengan sangat teliti.
Fokus khusus pada digit yang ada di baris-baris tabel rincian.
Tandai baris yang kamu RAGU dengan menambahkan "ragu": true.

Kembalikan HANYA JSON murni dengan format yang SAMA persis seperti sebelumnya:
{EXTRACTION_PROMPT.split('FORMAT JSON')[1].split('ATURAN WAJIB')[0].strip()}
"""


# ══════════════════════════════════════════════════════════════════
# PREPROCESSING GAMBAR — tingkatkan kualitas sebelum dikirim ke Gemini
# ══════════════════════════════════════════════════════════════════
def preprocess_image(image_bytes: bytes) -> bytes:
    """
    Meningkatkan kualitas gambar untuk membantu Gemini membaca
    tulisan tangan lebih akurat, terutama digit yang mirip (4 vs 9, dll).

    Langkah:
    1. Resize ke lebar minimum 1800px agar digit tidak buram
    2. Tingkatkan kontras → batas antara tinta dan kertas lebih tegas
    3. Tingkatkan ketajaman → tepi huruf lebih crisp
    4. Slight brightness boost → dokumen yang agak gelap jadi lebih terbaca
    """
    img = Image.open(io.BytesIO(image_bytes))

    # Konversi mode non-RGB
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGB')
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    # 1. Resize — min lebar 1800px, jaga aspek rasio
    w, h = img.size
    MIN_WIDTH = 1200
    if w < MIN_WIDTH:
        scale = MIN_WIDTH / w
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # 2. Tingkatkan kontras (1.0 = original, 2.0 = 2x lebih kontras)
    img = ImageEnhance.Contrast(img).enhance(1.8)

    # 3. Tingkatkan ketajaman
    img = ImageEnhance.Sharpness(img).enhance(2.5)

    # 4. Sedikit naikkan brightness untuk dokumen yang terkesan abu-abu
    img = ImageEnhance.Brightness(img).enhance(1.05)

    # Simpan dengan kualitas tinggi
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=95)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════
# HELPER
# ══════════════════════════════════════════════════════════════════
def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    m = re.search(r'\{[\s\S]+\}', text)
    if m:
        text = m.group(0)
    return json.loads(text)


def fmt(n) -> str:
    """Format angka bersih: 300.0 → '300', 66.2 → '66.2'"""
    if n is None:
        return '—'
    f = float(n)
    return f'{f:g}'


def buat_check(id_check: str, label: str, kategori: str,
               nilai_list: list, formula_extra: str,
               hitung: float, tertulis: float, satuan: str) -> dict:
    """
    Buat satu objek check lengkap.
    formula_extra: string tambahan untuk menjelaskan langkah turunan (misal Netto = Bruto - Terra)
    """
    TOLERANSI = 0.15
    selisih   = round(hitung - tertulis, 4)
    ok        = abs(selisih) < TOLERANSI

    # Bangun rincian penjumlahan
    if nilai_list:
        str_addend = ' + '.join(fmt(v) for v in nilai_list)
        rincian_sum = f'{str_addend} = {fmt(hitung)} {satuan}'
    else:
        rincian_sum = ''

    # Gabung formula_extra (misal "= Bruto - Terra = 809.5 - 170 = 639.5 kg")
    rincian_full = rincian_sum
    if formula_extra:
        rincian_full = formula_extra if not rincian_sum else rincian_sum + '\n' + formula_extra

    kesimpulan = (
        f'Hasil hitungan {fmt(hitung)} {satuan} '
        f'{"SAMA" if ok else "TIDAK SAMA"} dengan yang tertulis {fmt(tertulis)} {satuan}.'
        + ('' if ok else f' Selisih: {selisih:+.3g} {satuan}.')
    )

    return {
        'id': id_check,
        'label': label,
        'kategori': kategori,
        'nilai_list': [float(v) for v in nilai_list],
        'formula_extra': formula_extra,
        'hitung': float(hitung),
        'tertulis': float(tertulis),
        'selisih': float(selisih),
        'ok': ok,
        'satuan': satuan,
        'rincian': rincian_full,
        'kesimpulan': kesimpulan,
    }


# ══════════════════════════════════════════════════════════════════
# HELPER SAFE — semua operasi None-safe
# ══════════════════════════════════════════════════════════════════

def safe_float(v, default: float = 0.0) -> float:
    """Konversi nilai ke float dengan aman. None/falsy → default."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def has_value(v) -> bool:
    """True jika v adalah angka nyata (bukan None dan bukan 0)."""
    if v is None:
        return False
    try:
        return float(v) != 0.0
    except (TypeError, ValueError):
        return False


def grp_pakai_bruto_terra(grp: dict) -> bool:
    """
    Cek apakah kelompok ini pakai mode Bruto/Terra.
    Keputusan berdasarkan ada tidaknya NILAI ANGKA NYATA
    di KEDUA field tertulis_bruto_kg DAN tertulis_terra_kg.
    Jika salah satu atau keduanya null/0 → mode langsung (Netto).
    """
    return has_value(grp.get('tertulis_bruto_kg')) and \
           has_value(grp.get('tertulis_terra_kg'))


# ══════════════════════════════════════════════════════════════════
# VALIDASI UTAMA
# ══════════════════════════════════════════════════════════════════
def validate_do(data: dict, bandul: float | None = None) -> dict:
    """
    Validasi semua nilai total di dokumen DO.

    ═══════════════════════════════════════════════════════════════
    LOGIKA PER KELOMPOK (PESANAN, REALISASI, dst):

    Deteksi mode per-kelompok:
      → Jika tertulis_bruto_kg DAN tertulis_terra_kg keduanya
        memiliki nilai angka nyata (bukan null/0) → MODE BRUTO/TERRA
      → Jika tidak → MODE LANGSUNG (Σ kg = Netto)

    [1] Baris EKOR
        hitung  = Σ kolom Ekor semua baris rincian
        cek vs  tertulis_total_ekor

    MODE LANGSUNG:
    [2] Baris NETTO
        hitung  = Σ kolom Kg semua baris rincian
        cek vs  tertulis_netto_kg

    MODE BRUTO/TERRA:
    [2] Baris BRUTO
        hitung  = Σ kolom Kg semua baris rincian
        cek vs  tertulis_bruto_kg

    [3] Baris TERRA
        hitung  = nilai_bandul × jumlah_baris_terisi
        cek vs  tertulis_terra_kg

    [4] Baris NETTO
        hitung  = hitung_bruto − hitung_terra
        cek vs  tertulis_netto_kg

    ═══════════════════════════════════════════════════════════════
    SETELAH SEMUA KELOMPOK:

    [5] Realisasi EKOR (ringkasan atas)
        hitung  = Σ tertulis_total_ekor dari semua kelompok
        cek vs  ringkasan_atas.tertulis_realisasi_ekor

    [6] Realisasi KG (ringkasan atas)
        hitung  = Σ tertulis_netto_kg dari semua kelompok
        cek vs  ringkasan_atas.tertulis_realisasi_kg

    [7] RATA-RATA
        hitung  = tertulis_realisasi_kg ÷ tertulis_realisasi_ekor
        cek vs  ringkasan_atas.tertulis_rata_rata
    ═══════════════════════════════════════════════════════════════
    """
    kelompok_list  = data['kelompok']
    ringkasan_atas = data.get('ringkasan_atas', {})
    checks         = []

    # Flag global: apakah ADA kelompok yang pakai Bruto/Terra
    ada_bruto_terra_global = any(grp_pakai_bruto_terra(g) for g in kelompok_list)

    # ── Per kelompok ──────────────────────────────────────────────
    for grp in kelompok_list:
        nama   = grp.get('nama', 'KELOMPOK')
        posisi = grp.get('posisi', '')
        baris  = grp.get('baris', [])

        # Hanya baris yang ada isinya (safe: handle key tidak ada)
        baris_terisi = [
            r for r in baris
            if safe_float(r.get('ekor')) != 0 or safe_float(r.get('kg')) != 0
        ]
        ekor_list = [safe_float(r.get('ekor')) for r in baris_terisi]
        kg_list   = [safe_float(r.get('kg'))   for r in baris_terisi]
        n_baris   = len(baris_terisi)

        hitung_ekor  = sum(ekor_list)
        hitung_bruto = round(sum(kg_list), 2)

        # Semua tertulis di-safe_float agar tidak ada None di operasi
        tertulis_ekor  = safe_float(grp.get('tertulis_total_ekor'))
        tertulis_bruto = safe_float(grp.get('tertulis_bruto_kg'))
        tertulis_terra = safe_float(grp.get('tertulis_terra_kg'))
        tertulis_netto = safe_float(grp.get('tertulis_netto_kg'))

        # Deteksi mode untuk kelompok ini
        mode_bt = grp_pakai_bruto_terra(grp)

        # ── [1] CEK EKOR ──────────────────────────────────────────
        checks.append(buat_check(
            id_check      = f'ekor_{nama.lower()}',
            label         = f'Baris EKOR — {nama} ({posisi})',
            kategori      = 'Baris Ekor',
            nilai_list    = ekor_list,
            formula_extra = '',
            hitung        = hitung_ekor,
            tertulis      = tertulis_ekor,
            satuan        = 'ekor',
        ))

        if not mode_bt:
            # ── [2] MODE LANGSUNG: Σ Kg = Netto ───────────────────
            checks.append(buat_check(
                id_check      = f'netto_{nama.lower()}',
                label         = f'Baris NETTO — {nama} ({posisi})',
                kategori      = 'Baris Netto',
                nilai_list    = kg_list,
                formula_extra = '',
                hitung        = hitung_bruto,
                tertulis      = tertulis_netto,
                satuan        = 'kg',
            ))

        else:
            # ── MODE BRUTO/TERRA ───────────────────────────────────
            bandul_val   = safe_float(bandul)
            hitung_terra = round(bandul_val * n_baris, 2)
            hitung_netto = round(hitung_bruto - hitung_terra, 2)

            # ── [2] CEK BRUTO ──
            checks.append(buat_check(
                id_check      = f'bruto_{nama.lower()}',
                label         = f'Baris BRUTO — {nama} ({posisi})',
                kategori      = 'Baris Bruto',
                nilai_list    = kg_list,
                formula_extra = '',
                hitung        = hitung_bruto,
                tertulis      = tertulis_bruto,
                satuan        = 'kg',
            ))

            # ── [3] CEK TERRA ──
            terra_formula = (
                f'Terra = Nilai Bandul × Jumlah Baris Terisi\n'
                f'Terra = {fmt(bandul_val)} × {n_baris} = {fmt(hitung_terra)} kg'
            )
            checks.append(buat_check(
                id_check      = f'terra_{nama.lower()}',
                label         = f'Baris TERRA — {nama} ({posisi})',
                kategori      = 'Baris Terra',
                nilai_list    = [],
                formula_extra = terra_formula,
                hitung        = hitung_terra,
                tertulis      = tertulis_terra,
                satuan        = 'kg',
            ))

            # ── [4] CEK NETTO = Bruto − Terra ──
            netto_formula = (
                f'Netto = Bruto − Terra\n'
                f'Netto = {fmt(hitung_bruto)} − {fmt(hitung_terra)} = {fmt(hitung_netto)} kg'
            )
            checks.append(buat_check(
                id_check      = f'netto_{nama.lower()}',
                label         = f'Baris NETTO — {nama} ({posisi})',
                kategori      = 'Baris Netto',
                nilai_list    = [],
                formula_extra = netto_formula,
                hitung        = hitung_netto,
                tertulis      = tertulis_netto,
                satuan        = 'kg',
            ))

    # ── [5] Realisasi EKOR ────────────────────────────────────────
    semua_ekor         = [safe_float(grp.get('tertulis_total_ekor')) for grp in kelompok_list]
    hitung_real_ekor   = sum(semua_ekor)
    tertulis_real_ekor = safe_float(ringkasan_atas.get('tertulis_realisasi_ekor'))

    checks.append(buat_check(
        id_check      = 'realisasi_ekor',
        label         = 'Realisasi EKOR (ringkasan atas)',
        kategori      = 'Realisasi',
        nilai_list    = semua_ekor,
        formula_extra = 'Penjumlahan semua nilai "Baris Ekor tertulis" dari tiap kelompok',
        hitung        = hitung_real_ekor,
        tertulis      = tertulis_real_ekor,
        satuan        = 'ekor',
    ))

    # ── [6] Realisasi KG ──────────────────────────────────────────
    semua_netto        = [safe_float(grp.get('tertulis_netto_kg')) for grp in kelompok_list]
    hitung_real_kg     = round(sum(semua_netto), 2)
    tertulis_real_kg   = safe_float(ringkasan_atas.get('tertulis_realisasi_kg'))

    checks.append(buat_check(
        id_check      = 'realisasi_kg',
        label         = 'Realisasi KG (ringkasan atas)',
        kategori      = 'Realisasi',
        nilai_list    = semua_netto,
        formula_extra = 'Penjumlahan semua nilai "Baris Netto tertulis" dari tiap kelompok',
        hitung        = hitung_real_kg,
        tertulis      = tertulis_real_kg,
        satuan        = 'kg',
    ))

    # ── [7] RATA-RATA ─────────────────────────────────────────────
    if tertulis_real_ekor != 0:
        hitung_rata = round(tertulis_real_kg / tertulis_real_ekor, 2)
    else:
        hitung_rata = 0.0

    rata_formula = (
        f'Rata-rata = Realisasi KG ÷ Realisasi Ekor\n'
        f'Rata-rata = {fmt(tertulis_real_kg)} ÷ {fmt(tertulis_real_ekor)} = {fmt(hitung_rata)} kg/ekor'
    )
    tertulis_rata = safe_float(ringkasan_atas.get('tertulis_rata_rata'))

    checks.append(buat_check(
        id_check      = 'rata_rata',
        label         = 'Kolom RATA-RATA (ringkasan atas)',
        kategori      = 'Rata-rata',
        nilai_list    = [],
        formula_extra = rata_formula,
        hitung        = hitung_rata,
        tertulis      = tertulis_rata,
        satuan        = 'kg/ekor',
    ))

    semua_benar  = all(c['ok'] for c in checks)
    jumlah_salah = sum(1 for c in checks if not c['ok'])

    return {
        'checks':          checks,
        'semua_benar':     semua_benar,
        'jumlah_salah':    jumlah_salah,
        'ada_bruto_terra': ada_bruto_terra_global,
        'bandul_digunakan': bandul,
        'kelompok':        kelompok_list,
        'ringkasan_atas':  ringkasan_atas,
    }


# ══════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/extract', methods=['POST'])
def api_extract():
    """
    Step 1: Kirim gambar ke Gemini → ekstrak data mentah.
    - Gambar di-preprocess dulu untuk meningkatkan akurasi baca digit.
    - Jika validasi awal gagal, otomatis retry dengan prompt khusus.
    - Kembalikan raw_data terbaik + flag ada_bruto_terra + baris_ragu.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'Tidak ada file yang dikirim'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'File tidak dipilih'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in {'jpg', 'jpeg', 'png', 'webp'}:
        return jsonify({'error': f'Format .{ext} tidak didukung'}), 400

    try:
        model = genai.GenerativeModel('gemini-2.5-flash')

        # ── Preprocessing gambar ──────────────────────────────────
        raw_bytes        = file.read()
        processed_bytes  = preprocess_image(raw_bytes)
        img_b64          = base64.b64encode(processed_bytes).decode()
        img_part         = {'inline_data': {'mime_type': 'image/jpeg', 'data': img_b64}}

        # ── Pass 1: Baca normal ───────────────────────────────────
        response1  = model.generate_content([img_part, EXTRACTION_PROMPT])
        raw_data1  = extract_json(response1.text)
        ada_bt1    = any(grp_pakai_bruto_terra(g) for g in raw_data1.get('kelompok', []))

        # Validasi sementara (tanpa bandul jika tidak diperlukan)
        bandul_tmp = None
        if not ada_bt1:
            result1        = validate_do(raw_data1, bandul=None)
            checks_gagal1  = [c for c in result1['checks'] if not c['ok']]
        else:
            # Ada bruto/terra → belum bisa validasi penuh tanpa bandul,
            # skip retry, langsung kembalikan hasil pass 1
            checks_gagal1 = []

        # ── Pass 2: Retry jika ada yang gagal ────────────────────
        raw_data_final = raw_data1
        retry_dilakukan = False

        if checks_gagal1:
            retry_prompt = buat_retry_prompt(checks_gagal1, raw_data1)
            response2    = model.generate_content([img_part, retry_prompt])

            try:
                raw_data2 = extract_json(response2.text)
                result2   = validate_do(raw_data2, bandul=None)

                jumlah_benar1 = sum(1 for c in result1['checks'] if c['ok'])
                jumlah_benar2 = sum(1 for c in result2['checks'] if c['ok'])

                # Pakai hasil yang lebih banyak benarnya
                if jumlah_benar2 > jumlah_benar1:
                    raw_data_final  = raw_data2
                    retry_dilakukan = True
            except Exception:
                # Retry gagal parse → tetap pakai pass 1
                pass

        # ── Kumpulkan semua baris yang ditandai ragu ──────────────
        baris_ragu = []
        for grp in raw_data_final.get('kelompok', []):
            for r in grp.get('baris', []):
                if r.get('ragu'):
                    baris_ragu.append({
                        'kelompok': grp.get('nama', ''),
                        'posisi':   grp.get('posisi', ''),
                        'no':       r.get('no'),
                        'ekor':     r.get('ekor'),
                        'kg':       r.get('kg'),
                    })

        ada_bt_final = any(grp_pakai_bruto_terra(g) for g in raw_data_final.get('kelompok', []))

        return jsonify({
            'success':          True,
            'raw_data':         raw_data_final,
            'ada_bruto_terra':  ada_bt_final,
            'baris_ragu':       baris_ragu,
            'retry_dilakukan':  retry_dilakukan,
        })

    except json.JSONDecodeError as e:
        return jsonify({'error': f'Gagal parsing respons Gemini: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Terjadi kesalahan: {str(e)}'}), 500


@app.route('/api/validate', methods=['POST'])
def api_validate():
    """
    Step 2: Terima raw_data + bandul (jika ada Bruto/Terra),
    jalankan semua validasi, kembalikan hasil lengkap.
    """
    body = request.get_json()
    if not body or 'raw_data' not in body:
        return jsonify({'error': 'raw_data tidak ditemukan'}), 400

    raw_data = body['raw_data']
    bandul   = body.get('bandul')  # None jika tidak dikirim

    # Cek kebutuhan bandul dari nilai nyata di data
    ada_bt = any(grp_pakai_bruto_terra(g) for g in raw_data.get('kelompok', []))
    if ada_bt and bandul is None:
        return jsonify({'error': 'Dokumen memiliki Bruto/Terra — nilai bandul wajib diisi'}), 400

    try:
        bandul_float = float(bandul) if bandul is not None else None
        result = validate_do(raw_data, bandul=bandul_float)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'error': f'Terjadi kesalahan validasi: {str(e)}'}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
