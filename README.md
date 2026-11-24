# Sistem Produksi Buku — PT PIS

Web app lokal untuk memantau produksi buku (Syafii Kids, Darsyafii, Penerjemah, PIS).
Program berjalan di satu PC, data disimpan di file `produksi.db` (SQLite), dan dibuka lewat browser di
**http://localhost:5000**. Tidak perlu hosting atau internet. Satu-satunya yang butuh internet adalah font Inter;
tanpa internet, tampilan otomatis memakai font sistem.

## Fitur

Tampilan mengikuti wireframe Figma "Wireframe UI UX PIS".

| Halaman | Isi |
|---|---|
| **Login** | Masuk dengan akun internal. "Ingat saya" menyimpan sesi 30 hari. |
| **Beranda** | Salam, 3 menu (Buku dalam Produksi, Display Progres, Tambah Data Buku), dan Pengumuman. Admin bisa mengubah pengumuman. |
| **Buku dalam Produksi** | Daftar buku aktif dengan filter tahap (pill) dan pencarian. Tahap diubah langsung dari kolom Tahap dan tercatat otomatis di riwayat. Pilih **Selesai (arsipkan)** untuk memindahkan buku ke arsip. Tombol **Ekspor Excel** mengunduh daftar yang sedang tampil (sesuai filter) ke file `.xlsx`, dengan sheet *Buku* dan *Riwayat Tahap*. |
| **Tambah Data** | Wizard 3 langkah: Isi data → Periksa (wajib centang) → Selesai. Kode dibuat otomatis (`PIS-001`, `PIS-002`, ...). |
| **Display Progres** | Satu kolom per bagian: persentase progres, jumlah buku berjalan & siap cetak, progres 5 tahap tiap buku. Muat ulang otomatis tiap 60 detik. |
| **Detail buku** *(klik judul)* | Info lengkap, ubah tahap + catatan, riwayat tahap (siapa & kapan), unggah/unduh file, edit, hapus. |
| **File** | Semua file per folder (Naskah, Sampul, Layout, PDF Final, Lainnya), unggah banyak file sekaligus, lihat/unduh/hapus. |
| **Pengguna** *(admin)* | Tambah akun, atur akses bagian, reset kata sandi, hapus akun. |
| **Akun saya** | Ganti kata sandi sendiri. |

Tahap produksi: **Naskah → Sunting → Desain → Pracetak → Cetak** (Cetak = siap cetak), ditambah status arsip **Selesai**.
Persentase per bagian = rata-rata tahap semua buku aktif di bagian itu (Naskah 20%, Sunting 40%, ... Cetak 100%).
Daftar tahap, bagian, dan folder bisa diubah di bagian atas `app.py` (`TAHAP`, `BAGIAN`, `FOLDER`).

**Peran:** *Staf* bisa menambah dan mengubah buku serta mengunggah file, tetapi tidak bisa menghapus.
*Admin* bisa semuanya, termasuk menghapus buku/file, mengubah pengumuman, dan mengelola pengguna.

**Akses per bagian:** di menu *Pengguna*, staf bisa dibatasi ke satu bagian (misalnya "Hanya Darsyafii").
Staf itu hanya melihat dan mengubah buku dan file bagiannya: Buku dalam Produksi, Tambah Data, detail, File.
Staf dengan pilihan "Semua bagian" dan semua admin bisa mengakses seluruh bagian.
Halaman Display tetap menampilkan semua bagian sebagai ringkasan, tetapi judul buku bagian lain tidak bisa diklik.

**Display untuk monitor:** buka `http://localhost:5000/display?kiosk=1`. Mode ini tampil tanpa sidebar,
membesar sesuai lebar layar, menampilkan jam, dan menggulir pelan bila daftarnya panjang. Mode ini **bisa dibuka tanpa login**.

## Instalasi (sekali saja)

1. Instal Python 3.10+ dari python.org (centang **Add python.exe to PATH**).
2. Buka Command Prompt di folder ini, lalu jalankan:
   ```
   py -m pip install -r requirements.txt
   ```

## Menjalankan

```
py app.py            # jalankan normal
py app.py --demo     # isi 15 buku contoh (jika belum ada buku) + 4 akun staf demo per bagian:
                     # syafiikids, darsyafii, penerjemah, pis — kata sandi demo123
```

Atau klik dua kali **`jalankan.bat`**. Buka http://localhost:5000.

Saat pertama kali jalan, database dibuat otomatis beserta akun:

- **username:** `admin`
- **kata sandi:** `admin123` → **segera ganti** lewat menu *Akun saya*.

## Jalan otomatis saat PC menyala

1. Buka **Task Scheduler** → *Create Task…*
2. Tab *Triggers* → *New* → **At log on**.
3. Tab *Actions* → *New* → Program: path lengkap ke `jalankan.bat`; *Start in*: folder aplikasi ini.
4. Untuk monitor Display, buat task kedua dengan action **`display-kiosk.bat`**. Script ini menunggu server siap,
   lalu membuka Chrome (atau Edge) layar penuh di `/display?kiosk=1`. Tutup dengan **Alt+F4**.

Interval muat ulang Display bisa diatur lewat URL, misalnya `http://localhost:5000/display?kiosk=1&refresh=30`.

## Backup

```
backup.bat "E:\Backup-Produksi"
```

Script ini menyalin `produksi.db` (aman walau aplikasi sedang jalan) dan folder `uploads/` ke subfolder bertanggal,
misalnya `E:\Backup-Produksi\2026-11-20_1700\`. Backup buatan script yang lebih tua dari 30 hari dihapus otomatis.
Pasang di Task Scheduler (misalnya harian pukul 17.00) agar berjalan rutin. Tanpa argumen, backup disimpan ke folder `backup\`.

**Restore:** hentikan aplikasi, salin `produksi.db` dan `uploads\` dari folder backup ke folder aplikasi, jalankan lagi.

## Dibuka dari PC lain di jaringan kantor

Jalankan dengan host `0.0.0.0`:

```
set HOST=0.0.0.0
py app.py
```

Lalu izinkan port 5000 di Windows Firewall (Inbound Rule → Port → TCP 5000), dan buka `http://<IP-PC-ini>:5000`
dari PC lain. Aplikasi sudah memakai server **Waitress** (bukan server bawaan Flask), jadi aman untuk beberapa pengguna sekaligus.
Ingat bahwa halaman Display bisa dibuka tanpa login oleh siapa pun di jaringan.

## Struktur

```
app.py               server Flask: route, login, database, unggah file
produksi.db          database SQLite (dibuat otomatis)
uploads/             file yang diunggah, per folder (Naskah, Sampul, ...)
templates/           halaman HTML (Jinja)
static/css, js       tampilan & interaksi
jalankan.bat         menjalankan server
display-kiosk.bat    membuka Display layar penuh
backup.bat           backup database + uploads
.secret_key          kunci sesi (dibuat otomatis; jangan dibagikan)
```

### Tabel database

- `users`: akun login (kata sandi di-hash), peran, dan bagian yang boleh diakses.
- `buku`: kode, judul, penulis, bagian, PIC, tenggat, tahap, catatan.
- `riwayat_tahap`: setiap perubahan tahap, siapa yang mengubah, kapan, dan catatannya.
- `file`: nama asli, folder, buku terkait, bagian, ukuran, pengunggah, dan nama file di `uploads/`.
- `pengaturan`: isi pengumuman di Beranda.

## Pengembangan

`py app.py --dev` menjalankan server bawaan Flask dengan auto-reload. Variabel lingkungan opsional:
`HOST`, `PORT`, `PIS_DB` (lokasi database), `PIS_UPLOADS` (lokasi folder unggahan).
