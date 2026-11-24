"""Sistem Produksi Buku — PT PIS.

Web app lokal (Flask + SQLite). Jalankan:  py app.py
Lalu buka http://localhost:5000
"""
import io
import os
import secrets
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PIS_DB", os.path.join(BASE_DIR, "produksi.db"))
UPLOAD_DIR = os.environ.get("PIS_UPLOADS", os.path.join(BASE_DIR, "uploads"))
SECRET_FILE = os.path.join(BASE_DIR, ".secret_key")

BAGIAN = ["Syafii Kids", "Darsyafii", "Penerjemah", "PIS"]
# Tahap produksi sesuai wireframe. "Cetak" = siap cetak.
TAHAP = ["Naskah", "Sunting", "Desain", "Pracetak", "Cetak"]
# Status arsip: buku yang sudah selesai dicetak disembunyikan dari daftar & display.
SELESAI = "Selesai"
SEMUA_TAHAP = TAHAP + [SELESAI]
FOLDER = ["Naskah", "Sampul", "Layout", "PDF Final", "Lainnya"]
ALLOWED_EXT = {
    "pdf", "doc", "docx", "odt", "rtf", "txt", "md",
    "jpg", "jpeg", "png", "gif", "webp", "tif", "tiff", "bmp", "svg",
    "psd", "ai", "eps", "indd", "idml", "cdr",
    "xls", "xlsx", "csv", "ppt", "pptx",
    "zip", "rar", "7z",
}
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nama TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'staf' CHECK (role IN ('admin', 'staf')),
    bagian TEXT,  -- staf: hanya boleh akses bagian ini; NULL = semua bagian
    dibuat TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS buku (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kode TEXT NOT NULL UNIQUE COLLATE NOCASE,
    judul TEXT NOT NULL,
    penulis TEXT NOT NULL DEFAULT '',
    bagian TEXT NOT NULL,
    pic TEXT NOT NULL DEFAULT '',
    tenggat TEXT,
    tahap TEXT NOT NULL,
    catatan TEXT NOT NULL DEFAULT '',
    dibuat TEXT NOT NULL,
    diubah TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS riwayat_tahap (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buku_id INTEGER NOT NULL REFERENCES buku(id) ON DELETE CASCADE,
    dari_tahap TEXT,
    ke_tahap TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    oleh TEXT NOT NULL,
    catatan TEXT NOT NULL DEFAULT '',
    waktu TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nama TEXT NOT NULL,
    nama_simpan TEXT NOT NULL,
    folder TEXT NOT NULL,
    buku_id INTEGER REFERENCES buku(id) ON DELETE SET NULL,
    bagian TEXT,  -- bagian pemilik file; NULL = umum (hanya terlihat oleh pengguna semua bagian)
    ukuran INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    oleh TEXT NOT NULL,
    diunggah TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pengaturan (
    kunci TEXT PRIMARY KEY,
    nilai TEXT NOT NULL DEFAULT '',
    diubah TEXT,
    oleh TEXT
);
CREATE INDEX IF NOT EXISTS idx_buku_tahap ON buku(tahap);
CREATE INDEX IF NOT EXISTS idx_riwayat_buku ON riwayat_tahap(buku_id);
CREATE INDEX IF NOT EXISTS idx_file_buku ON file(buku_id);
"""


def _load_secret():
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_FILE, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_load_secret(),
    MAX_CONTENT_LENGTH=500 * 1024 * 1024,  # 500 MB per unggahan
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


# --------------------------------------------------------------------------- DB
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    for f in FOLDER:
        os.makedirs(os.path.join(UPLOAD_DIR, f), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    # Migrasi database lama: tambah kolom bagian
    for tabel in ("users", "file"):
        if "bagian" not in [r[1] for r in db.execute(f"PRAGMA table_info({tabel})")]:
            db.execute(f"ALTER TABLE {tabel} ADD COLUMN bagian TEXT")
    db.execute("UPDATE file SET bagian = (SELECT bagian FROM buku WHERE buku.id = file.buku_id)"
               " WHERE bagian IS NULL AND buku_id IS NOT NULL")
    db.execute("CREATE INDEX IF NOT EXISTS idx_buku_bagian ON buku(bagian)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_file_bagian ON file(bagian)")
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        db.execute(
            "INSERT INTO users (username, nama, password_hash, role, dibuat) VALUES (?,?,?,?,?)",
            ("admin", "Administrator", generate_password_hash("admin123"), "admin", now_str()),
        )
        print(" * Akun awal dibuat -> username: admin  kata sandi: admin123 (segera ganti!)")
    db.commit()
    db.close()


def get_setting(key, default=""):
    row = get_db().execute("SELECT * FROM pengaturan WHERE kunci = ?", (key,)).fetchone()
    return row if row else {"nilai": default, "diubah": None, "oleh": None}


# ------------------------------------------------------------------- helpers
def current_user():
    uid = session.get("uid")
    if uid is None:
        return None
    if "user" not in g:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            session.clear()
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user()["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def bagian_user():
    """Bagian yang boleh diakses pengguna saat ini, atau None bila boleh semua bagian (admin / staf umum)."""
    user = current_user()
    if user is None or user["role"] == "admin":
        return None
    return user["bagian"] if user["bagian"] in BAGIAN else None


def boleh_bagian(bagian):
    bu = bagian_user()
    return bu is None or bagian == bu


def filter_bagian(kolom="bagian"):
    """Potongan SQL + parameter untuk membatasi data ke bagian pengguna."""
    bu = bagian_user()
    return (f" AND {kolom} = ?", [bu]) if bu else ("", [])


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token  # global agar terlihat juga di dalam macro


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Sesi kedaluwarsa. Muat ulang halaman lalu coba lagi.")


def safe_next(target, fallback):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


def tahap_ke(tahap):
    """Nomor urut tahap 1..5 (Selesai dihitung sebagai 5 = penuh)."""
    return len(TAHAP) if tahap == SELESAI else TAHAP.index(tahap) + 1 if tahap in TAHAP else 0


def annotate(row):
    """Tambahkan sisa hari & status tenggat ke baris buku."""
    d = dict(row)
    d["sisa"] = None
    if d.get("tenggat"):
        try:
            d["sisa"] = (date.fromisoformat(d["tenggat"]) - date.today()).days
        except ValueError:
            pass
    if d["tahap"] == SELESAI:
        d["status"] = "selesai"
    elif d["sisa"] is None:
        d["status"] = "tanpa"
    elif d["sisa"] < 0:
        d["status"] = "terlambat"
    elif d["sisa"] <= 3:
        d["status"] = "dekat"
    else:
        d["status"] = "aman"
    d["ke"] = tahap_ke(d["tahap"])
    return d


def catat_tahap(db, buku_id, dari, ke, catatan=""):
    user = current_user()
    db.execute(
        "INSERT INTO riwayat_tahap (buku_id, dari_tahap, ke_tahap, user_id, oleh, catatan, waktu)"
        " VALUES (?,?,?,?,?,?,?)",
        (buku_id, dari, ke, user["id"] if user else None, user["nama"] if user else "Sistem", catatan, now_str()),
    )


def kode_otomatis(db):
    prefix = "PIS-"
    rows = db.execute("SELECT kode FROM buku WHERE kode LIKE ?", (prefix + "%",)).fetchall()
    nomor = [int(r["kode"][len(prefix):]) for r in rows if r["kode"][len(prefix):].isdigit()]
    return f"{prefix}{(max(nomor) + 1) if nomor else 1:03d}"


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


# ----------------------------------------------------------- template helpers
@app.context_processor
def inject_globals():
    return {
        "user": current_user(),
        "BAGIAN": BAGIAN,
        "BAGIAN_BOLEH": [bagian_user()] if bagian_user() else BAGIAN,
        "boleh_bagian": boleh_bagian,
        "TAHAP": TAHAP,
        "SELESAI": SELESAI,
        "SEMUA_TAHAP": SEMUA_TAHAP,
        "FOLDER": FOLDER,
        "today": date.today(),
    }


@app.template_filter("tgl")
def fmt_tgl(value, with_time=False):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return value
    s = f"{dt.day} {BULAN[dt.month - 1]} {dt.year}"
    if with_time:
        s += f", {dt:%H:%M}"
    return s


@app.template_filter("ddmm")
def fmt_ddmm(value, with_year=False):
    if not value:
        return "—"
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return value
    return d.strftime("%d/%m/%Y" if with_year else "%d/%m")


@app.template_filter("sisa")
def fmt_sisa(d):
    if d["status"] == "selesai":
        return "Selesai"
    if d["sisa"] is None:
        return "Tanpa tenggat"
    if d["sisa"] < 0:
        return f"Terlambat {-d['sisa']} hari"
    if d["sisa"] == 0:
        return "Hari ini"
    if d["sisa"] == 1:
        return "Besok"
    return f"{d['sisa']} hari lagi"


@app.template_filter("ukuran")
def fmt_ukuran(n):
    n = n or 0
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


@app.template_filter("slug")
def fmt_slug(s):
    return (s or "").lower().replace(" ", "-")


@app.errorhandler(RequestEntityTooLarge)
def too_large(_e):
    flash("Ukuran file terlalu besar (maksimal 500 MB per unggahan).", "error")
    return redirect(url_for("file_list"))


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", kode=403, pesan="Anda tidak punya akses ke halaman ini."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", kode=404, pesan="Halaman atau data tidak ditemukan."), 404


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", kode=400, pesan=e.description), 400


# ---------------------------------------------------------------------- auth
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("beranda"))
    error = None
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            csrf = session.get("csrf")
            session.clear()
            session["uid"] = row["id"]
            session["csrf"] = csrf or secrets.token_urlsafe(32)
            session.permanent = bool(request.form.get("remember"))
            return redirect(safe_next(request.args.get("next"), url_for("beranda")))
        error = "Username atau kata sandi salah."
    return render_template("login.html", error=error, username=username)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/akun", methods=["GET", "POST"])
@login_required
def akun():
    if request.method == "POST":
        lama = request.form.get("lama", "")
        baru = request.form.get("baru", "")
        ulang = request.form.get("ulang", "")
        user = current_user()
        if not check_password_hash(user["password_hash"], lama):
            flash("Kata sandi lama salah.", "error")
        elif len(baru) < 6:
            flash("Kata sandi baru minimal 6 karakter.", "error")
        elif baru != ulang:
            flash("Konfirmasi kata sandi tidak sama.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(baru), user["id"]))
            db.commit()
            flash("Kata sandi berhasil diganti.", "ok")
            return redirect(url_for("akun"))
    return render_template("akun.html", active="akun")


# ------------------------------------------------------------------- beranda
@app.route("/")
@login_required
def beranda():
    return render_template("beranda.html", pengumuman=get_setting("pengumuman"))


@app.post("/pengumuman")
@admin_required
def pengumuman_simpan():
    db = get_db()
    db.execute(
        "INSERT INTO pengaturan (kunci, nilai, diubah, oleh) VALUES ('pengumuman', ?, ?, ?)"
        " ON CONFLICT(kunci) DO UPDATE SET nilai = excluded.nilai, diubah = excluded.diubah, oleh = excluded.oleh",
        (request.form.get("isi", "").strip(), now_str(), current_user()["nama"]),
    )
    db.commit()
    flash("Pengumuman diperbarui.", "ok")
    return redirect(url_for("beranda"))


# ------------------------------------------------------------------ produksi
def _cari(rows, q):
    if not q:
        return rows
    ql = q.lower()
    return [b for b in rows if any(ql in (b[k] or "").lower() for k in ("judul", "kode", "pic", "penulis"))]


def _daftar_buku(tahap, q):
    """Buku aktif sesuai filter halaman Produksi (tahap, pencarian, bagian pengguna)."""
    fb, fp = filter_bagian()
    aktif = [annotate(r) for r in get_db().execute(f"SELECT * FROM buku WHERE tahap != ?{fb} ORDER BY kode",
                                                   [SELESAI, *fp])]
    jumlah = {t: sum(1 for b in aktif if b["tahap"] == t) for t in TAHAP}
    return _cari([b for b in aktif if tahap not in TAHAP or b["tahap"] == tahap], q), aktif, jumlah


def jumlah_selesai():
    fb, fp = filter_bagian()
    return get_db().execute(f"SELECT COUNT(*) FROM buku WHERE tahap = ?{fb}", [SELESAI, *fp]).fetchone()[0]


def _ke_tanggal(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _ke_waktu(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def _daftar_selesai(bagian="", q="", tahun=None):
    """Buku selesai beserta tanggal selesai (dari riwayat), lama produksi, dan ketepatan tenggat.

    Mengembalikan (daftar buku, daftar tahun yang tersedia).
    """
    fb, fp = filter_bagian("b.bagian")
    sql = ("SELECT b.*, COALESCE((SELECT MAX(r.waktu) FROM riwayat_tahap r"
           " WHERE r.buku_id = b.id AND r.ke_tahap = ?), b.diubah) AS selesai"
           f" FROM buku b WHERE b.tahap = ?{fb}")
    params = [SELESAI, SELESAI, *fp]
    if bagian in BAGIAN:
        sql += " AND b.bagian = ?"
        params.append(bagian)
    rows = []
    for r in get_db().execute(sql + " ORDER BY selesai DESC, b.id DESC", params):
        d = annotate(r)
        d["tgl_selesai"] = _ke_tanggal(d["selesai"]) or date.today()
        mulai, tenggat = _ke_tanggal(d["dibuat"]), _ke_tanggal(d["tenggat"])
        d["lama"] = (d["tgl_selesai"] - mulai).days if mulai else None
        d["telat"] = (d["tgl_selesai"] - tenggat).days if tenggat else None
        rows.append(d)
    tahun_ada = sorted({b["tgl_selesai"].year for b in rows}, reverse=True)
    if tahun:
        rows = [b for b in rows if b["tgl_selesai"].year == tahun]
    return _cari(rows, q), tahun_ada


@app.template_filter("ketepatan")
def fmt_ketepatan(d):
    if d["telat"] is None:
        return "Tanpa tenggat"
    if d["telat"] <= 0:
        return "Tepat waktu"
    return f"Terlambat {d['telat']} hari"


@app.route("/produksi")
@login_required
def produksi():
    q = request.args.get("q", "").strip()
    tahap = request.args.get("tahap", "")
    if tahap == SELESAI:  # alamat lama arsip
        return redirect(url_for("selesai", q=q or None))
    rows, aktif, jumlah = _daftar_buku(tahap, q)
    return render_template("produksi.html", active="produksi", buku=rows, jumlah=jumlah, total=len(aktif),
                           siap_cetak=jumlah["Cetak"], arsip=jumlah_selesai(), f={"q": q, "tahap": tahap})


def _filter_selesai():
    return {"bagian": request.args.get("bagian", ""), "q": request.args.get("q", "").strip(),
            "tahun": request.args.get("tahun", type=int)}


@app.route("/selesai")
@login_required
def selesai():
    f = _filter_selesai()
    rows, tahun_ada = _daftar_selesai(**f)
    lama = [b["lama"] for b in rows if b["lama"] is not None]
    bertenggat = [b for b in rows if b["telat"] is not None]
    bulan = date.today().strftime("%Y-%m")
    ringkasan = {
        "total": len(rows),
        "rata_lama": round(sum(lama) / len(lama)) if lama else None,
        "tepat": sum(1 for b in bertenggat if b["telat"] <= 0),
        "bertenggat": len(bertenggat),
        "bulan_ini": sum(1 for b in rows if b["tgl_selesai"].strftime("%Y-%m") == bulan),
    }
    return render_template("selesai.html", active="selesai", buku=rows, tahun_ada=tahun_ada, f=f,
                           ringkasan=ringkasan, semua=jumlah_selesai())


# ------------------------------------------------------------- ekspor Excel
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_sheet(ws, header, data, lebar, formats):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F6B4F")
        cell.alignment = Alignment(vertical="center")
    for baris in data:
        ws.append(baris)
    for i, w in enumerate(lebar, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for col, fmt in formats.items():
        for cell in ws[col][1:]:
            cell.number_format = fmt
    ws.freeze_panes = "A2"
    if data:
        ws.auto_filter.ref = ws.dimensions


def _xlsx_merah(ws, kolom, baris_list):
    from openpyxl.styles import Font
    for i in baris_list:
        for k in kolom:
            ws[f"{k}{i}"].font = Font(color="C0392B", bold=True)


def _xlsx_riwayat(wb, rows):
    riwayat = []
    if rows:
        ids = [b["id"] for b in rows]
        riwayat = get_db().execute(
            "SELECT r.*, b.kode, b.judul FROM riwayat_tahap r JOIN buku b ON b.id = r.buku_id"
            f" WHERE r.buku_id IN ({','.join('?' * len(ids))}) ORDER BY b.kode, r.waktu, r.id", ids).fetchall()
    _xlsx_sheet(
        wb.create_sheet("Riwayat Tahap"),
        ["Kode", "Judul", "Dari tahap", "Ke tahap", "Oleh", "Waktu", "Catatan"],
        [[r["kode"], r["judul"], r["dari_tahap"] or "—", r["ke_tahap"], r["oleh"], _ke_waktu(r["waktu"]), r["catatan"]]
         for r in riwayat],
        [10, 38, 12, 12, 18, 17, 40],
        {"F": "DD/MM/YYYY HH:MM"},
    )


def _xlsx_kirim(wb, nama):
    if bagian_user():
        nama += "-" + fmt_slug(bagian_user())
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{nama}_{date.today():%Y-%m-%d}.xlsx",
                     mimetype=XLSX_MIME)


@app.route("/produksi/export.xlsx")
@login_required
def produksi_export():
    """Ekspor buku aktif (sesuai filter yang sedang dipakai) + riwayat tahapnya ke Excel."""
    from openpyxl import Workbook

    q = request.args.get("q", "").strip()
    tahap = request.args.get("tahap", "")
    if tahap == SELESAI:
        return redirect(url_for("selesai_export", q=q or None))
    rows, _aktif, _jumlah = _daftar_buku(tahap, q)
    wb = Workbook()
    ws = wb.active
    ws.title = "Buku"
    _xlsx_sheet(
        ws,
        ["Kode", "Judul", "Penulis", "Bagian", "Tahap", "Tahap ke", "Progres", "PIC", "Tenggat", "Status tenggat",
         "Catatan", "Dibuat", "Terakhir diubah"],
        [[b["kode"], b["judul"], b["penulis"], b["bagian"], b["tahap"], f"{b['ke']}/{len(TAHAP)}",
          b["ke"] / len(TAHAP), b["pic"], _ke_tanggal(b["tenggat"]), fmt_sisa(b), b["catatan"],
          _ke_waktu(b["dibuat"]), _ke_waktu(b["diubah"])] for b in rows],
        [10, 38, 24, 14, 11, 9, 9, 14, 12, 18, 40, 17, 17],
        {"G": "0%", "I": "DD/MM/YYYY", "L": "DD/MM/YYYY HH:MM", "M": "DD/MM/YYYY HH:MM"},
    )
    _xlsx_merah(ws, "IJ", [i for i, b in enumerate(rows, 2) if b["status"] == "terlambat"])
    _xlsx_riwayat(wb, rows)
    return _xlsx_kirim(wb, "buku-produksi")


@app.route("/selesai/export.xlsx")
@login_required
def selesai_export():
    """Ekspor buku selesai (sesuai filter) + riwayat tahapnya ke Excel."""
    from openpyxl import Workbook

    rows, _tahun = _daftar_selesai(**_filter_selesai())
    wb = Workbook()
    ws = wb.active
    ws.title = "Buku Selesai"
    _xlsx_sheet(
        ws,
        ["Kode", "Judul", "Penulis", "Bagian", "PIC", "Mulai", "Selesai", "Lama (hari)", "Tenggat", "Ketepatan",
         "Catatan"],
        [[b["kode"], b["judul"], b["penulis"], b["bagian"], b["pic"], _ke_tanggal(b["dibuat"]), b["tgl_selesai"],
          b["lama"], _ke_tanggal(b["tenggat"]), fmt_ketepatan(b), b["catatan"]] for b in rows],
        [10, 38, 24, 14, 14, 12, 12, 11, 12, 18, 40],
        {"F": "DD/MM/YYYY", "G": "DD/MM/YYYY", "I": "DD/MM/YYYY"},
    )
    _xlsx_merah(ws, "J", [i for i, b in enumerate(rows, 2) if (b["telat"] or 0) > 0])
    _xlsx_riwayat(wb, rows)
    return _xlsx_kirim(wb, "buku-selesai")


def _form_buku():
    f = {k: request.form.get(k, "").strip() for k in ["kode", "judul", "penulis", "bagian", "pic", "tenggat", "tahap", "catatan"]}
    errors = []
    if not f["tahap"]:
        f["tahap"] = TAHAP[0]
    if bagian_user():  # staf satu bagian selalu menyimpan ke bagiannya sendiri
        f["bagian"] = bagian_user()
    if not f["judul"]:
        errors.append("Judul buku wajib diisi.")
    if f["bagian"] not in BAGIAN:
        errors.append("Pilih bagian.")
    if f["tahap"] not in SEMUA_TAHAP:
        errors.append("Pilih tahap.")
    if f["tenggat"]:
        try:
            date.fromisoformat(f["tenggat"])
        except ValueError:
            errors.append("Format tenggat tidak valid.")
    return f, errors


@app.route("/tambah", methods=["GET", "POST"])
@login_required
def buku_tambah():
    """Wizard 3 langkah: 1. Isi data → 2. Periksa → 3. Selesai."""
    kosong = {"judul": "", "penulis": "", "bagian": bagian_user() or "", "pic": "", "tenggat": "", "tahap": TAHAP[0], "catatan": ""}
    if request.method == "GET":
        return render_template("tambah.html", active="tambah", step=1, b=kosong, errors=[])

    f, errors = _form_buku()
    if f["tahap"] == SELESAI:
        errors.append("Tahap awal tidak boleh Selesai.")
    aksi = request.form.get("aksi")
    if aksi == "ubah" or errors:
        return render_template("tambah.html", active="tambah", step=1, b=f, errors=errors if aksi != "ubah" else [])
    if aksi == "periksa":
        return render_template("tambah.html", active="tambah", step=2, b=f, errors=[])
    if aksi == "simpan":
        if not request.form.get("benar"):
            return render_template("tambah.html", active="tambah", step=2, b=f,
                                   errors=["Centang “Data sudah saya periksa dan benar” sebelum menyimpan."])
        db = get_db()
        now = now_str()
        cur = db.execute(
            "INSERT INTO buku (kode, judul, penulis, bagian, pic, tenggat, tahap, catatan, dibuat, diubah)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kode_otomatis(db), f["judul"], f["penulis"], f["bagian"], f["pic"], f["tenggat"] or None,
             f["tahap"], f["catatan"], now, now),
        )
        catat_tahap(db, cur.lastrowid, None, f["tahap"], "Buku ditambahkan")
        db.commit()
        return redirect(url_for("buku_tambah_selesai", buku_id=cur.lastrowid))
    abort(400, "Aksi tidak dikenal.")


@app.route("/tambah/selesai/<int:buku_id>")
@login_required
def buku_tambah_selesai(buku_id):
    return render_template("tambah.html", active="tambah", step=3, b=_get_buku(buku_id), errors=[])


def _get_buku(buku_id):
    row = get_db().execute("SELECT * FROM buku WHERE id = ?", (buku_id,)).fetchone()
    if row is None:
        abort(404)
    if not boleh_bagian(row["bagian"]):
        abort(403)
    return row


@app.route("/produksi/<int:buku_id>")
@login_required
def buku_detail(buku_id):
    db = get_db()
    b = annotate(_get_buku(buku_id))
    riwayat = db.execute("SELECT * FROM riwayat_tahap WHERE buku_id = ? ORDER BY waktu DESC, id DESC", (buku_id,)).fetchall()
    files = db.execute("SELECT * FROM file WHERE buku_id = ? ORDER BY diunggah DESC", (buku_id,)).fetchall()
    return render_template("buku_detail.html", active="produksi", b=b, riwayat=riwayat, files=files)


@app.route("/produksi/<int:buku_id>/edit", methods=["GET", "POST"])
@login_required
def buku_edit(buku_id):
    db = get_db()
    lama = _get_buku(buku_id)
    if request.method == "POST":
        f, errors = _form_buku()
        if not f["kode"]:
            errors.append("Kode wajib diisi.")
        elif db.execute("SELECT 1 FROM buku WHERE kode = ? AND id != ?", (f["kode"], buku_id)).fetchone():
            errors.append(f"Kode {f['kode']} sudah dipakai.")
        if errors:
            for e in errors:
                flash(e, "error")
            f["id"] = buku_id
            return render_template("buku_form.html", active="produksi", b=f)
        db.execute(
            "UPDATE buku SET kode=?, judul=?, penulis=?, bagian=?, pic=?, tenggat=?, tahap=?, catatan=?, diubah=?"
            " WHERE id = ?",
            (f["kode"], f["judul"], f["penulis"], f["bagian"], f["pic"], f["tenggat"] or None,
             f["tahap"], f["catatan"], now_str(), buku_id),
        )
        if f["bagian"] != lama["bagian"]:
            db.execute("UPDATE file SET bagian = ? WHERE buku_id = ?", (f["bagian"], buku_id))
        if f["tahap"] != lama["tahap"]:
            catat_tahap(db, buku_id, lama["tahap"], f["tahap"], "Diubah lewat form edit")
        db.commit()
        flash("Perubahan disimpan.", "ok")
        return redirect(url_for("buku_detail", buku_id=buku_id))
    return render_template("buku_form.html", active="produksi", b=dict(lama))


@app.post("/produksi/<int:buku_id>/tahap")
@login_required
def buku_tahap(buku_id):
    db = get_db()
    b = _get_buku(buku_id)
    ke = request.form.get("tahap", "")
    catatan = request.form.get("catatan", "").strip()
    if ke not in SEMUA_TAHAP:
        flash("Tahap tidak valid.", "error")
    elif ke == b["tahap"]:
        flash("Tahap tidak berubah.", "error")
    else:
        db.execute("UPDATE buku SET tahap = ?, diubah = ? WHERE id = ?", (ke, now_str(), buku_id))
        catat_tahap(db, buku_id, b["tahap"], ke, catatan)
        db.commit()
        if ke == SELESAI:
            flash(f"“{b['judul']}” ditandai selesai dan dipindah ke menu Buku Selesai.", "ok")
        else:
            flash(f"“{b['judul']}”: {b['tahap']} → {ke}. Tercatat di riwayat.", "ok")
    return redirect(safe_next(request.form.get("next"), url_for("buku_detail", buku_id=buku_id)))


@app.post("/produksi/<int:buku_id>/hapus")
@admin_required
def buku_hapus(buku_id):
    db = get_db()
    b = _get_buku(buku_id)
    db.execute("DELETE FROM buku WHERE id = ?", (buku_id,))
    db.commit()
    flash(f"Buku “{b['judul']}” dihapus. File terkait tetap ada di menu File.", "ok")
    return redirect(url_for("produksi"))


# ---------------------------------------------------------------------- file
@app.route("/file")
@login_required
def file_list():
    db = get_db()
    folder = request.args.get("folder", "")
    q = request.args.get("q", "").strip()
    buku_id = request.args.get("buku", type=int)
    fb, fp = filter_bagian("f.bagian")
    sql = "SELECT f.*, b.judul, b.kode FROM file f LEFT JOIN buku b ON b.id = f.buku_id WHERE 1=1" + fb
    params = list(fp)
    if folder in FOLDER:
        sql += " AND f.folder = ?"
        params.append(folder)
    if q:
        sql += " AND (f.nama LIKE ? OR b.judul LIKE ? OR b.kode LIKE ?)"
        params += [f"%{q}%"] * 3
    if buku_id:
        sql += " AND f.buku_id = ?"
        params.append(buku_id)
    sql += " ORDER BY f.diunggah DESC, f.id DESC"
    files = db.execute(sql, params).fetchall()
    jumlah = {f: 0 for f in FOLDER}
    for r in db.execute(f"SELECT folder, COUNT(*) n FROM file f WHERE 1=1{fb} GROUP BY folder", fp):
        jumlah[r["folder"]] = r["n"]
    fb2, fp2 = filter_bagian()
    daftar_buku = db.execute(f"SELECT id, kode, judul FROM buku WHERE 1=1{fb2} ORDER BY judul", fp2).fetchall()
    return render_template("file.html", active="file", files=files, jumlah=jumlah,
                           total=sum(jumlah.values()), daftar_buku=daftar_buku,
                           f={"folder": folder, "q": q, "buku": buku_id})


@app.post("/file/unggah")
@login_required
def file_unggah():
    db = get_db()
    folder = request.form.get("folder", "")
    buku_id = request.form.get("buku_id", type=int)
    back = safe_next(request.form.get("next"), url_for("file_list"))
    if folder not in FOLDER:
        flash("Pilih folder tujuan.", "error")
        return redirect(back)
    bagian = bagian_user()
    if buku_id:
        bk = db.execute("SELECT bagian FROM buku WHERE id = ?", (buku_id,)).fetchone()
        if bk and boleh_bagian(bk["bagian"]):
            bagian = bk["bagian"]
        else:
            buku_id = None
    uploads = [u for u in request.files.getlist("files") if u and u.filename]
    if not uploads:
        flash("Pilih minimal satu file.", "error")
        return redirect(back)
    user = current_user()
    ok, ditolak = 0, []
    for up in uploads:
        nama = os.path.basename(up.filename.replace("\\", "/"))
        if ext_of(nama) not in ALLOWED_EXT:
            ditolak.append(nama)
            continue
        aman = secure_filename(nama) or f"file.{ext_of(nama)}"
        simpan = f"{uuid.uuid4().hex[:12]}_{aman}"
        path = os.path.join(UPLOAD_DIR, folder, simpan)
        up.save(path)
        db.execute(
            "INSERT INTO file (nama, nama_simpan, folder, buku_id, bagian, ukuran, user_id, oleh, diunggah)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (nama, simpan, folder, buku_id, bagian, os.path.getsize(path), user["id"], user["nama"], now_str()),
        )
        ok += 1
    db.commit()
    if ok:
        flash(f"{ok} file berhasil diunggah ke folder {folder}.", "ok")
    if ditolak:
        flash("Jenis file tidak diizinkan: " + ", ".join(ditolak), "error")
    return redirect(back)


def _get_file(file_id):
    row = get_db().execute("SELECT * FROM file WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        abort(404)
    if bagian_user() and row["bagian"] != bagian_user():
        abort(403)
    return row


@app.route("/file/<int:file_id>/unduh")
@login_required
def file_unduh(file_id):
    f = _get_file(file_id)
    inline = request.args.get("lihat") == "1" and ext_of(f["nama"]) in {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt"}
    return send_from_directory(os.path.join(UPLOAD_DIR, f["folder"]), f["nama_simpan"],
                               as_attachment=not inline, download_name=f["nama"])


@app.post("/file/<int:file_id>/hapus")
@admin_required
def file_hapus(file_id):
    f = _get_file(file_id)
    db = get_db()
    db.execute("DELETE FROM file WHERE id = ?", (file_id,))
    db.commit()
    try:
        os.remove(os.path.join(UPLOAD_DIR, f["folder"], f["nama_simpan"]))
    except FileNotFoundError:
        pass
    flash(f"File “{f['nama']}” dihapus.", "ok")
    return redirect(safe_next(request.form.get("next"), url_for("file_list")))


# ------------------------------------------------------------------- display
@app.route("/display")
def display():
    """Display Progres per bagian.

    Saat login: tampil di dalam aplikasi (dengan sidebar).
    Tanpa login atau dengan ?kiosk=1: layar penuh untuk monitor, tanpa perlu login.
    """
    kiosk = current_user() is None or request.args.get("kiosk") == "1"
    pilih = request.args.get("bagian", "")
    rows = [annotate(r) for r in get_db().execute("SELECT * FROM buku WHERE tahap != ? ORDER BY kode", (SELESAI,))]
    kolom = []
    for bg in BAGIAN:
        if pilih in BAGIAN and bg != pilih:
            continue
        items = sorted([b for b in rows if b["bagian"] == bg], key=lambda b: (b["ke"], b["kode"]))
        persen = round(sum(b["ke"] for b in items) / (len(TAHAP) * len(items)) * 100) if items else 0
        kolom.append({
            "bagian": bg, "buku": items, "persen": persen,
            "siap_cetak": sum(1 for b in items if b["tahap"] == "Cetak"),
        })
    return render_template("display.html", active="display", kiosk=kiosk, kolom=kolom, pilih=pilih,
                           refresh=max(10, request.args.get("refresh", 60, type=int)),
                           diperbarui=datetime.now().strftime("%H:%M"))


# ------------------------------------------------------------------ pengguna
@app.route("/pengguna", methods=["GET", "POST"])
@admin_required
def pengguna():
    db = get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        nama = request.form.get("nama", "").strip()
        pw = request.form.get("password", "")
        role = request.form.get("role", "staf")
        bagian = request.form.get("bagian", "")
        if not username or not nama:
            flash("Username dan nama wajib diisi.", "error")
        elif len(pw) < 6:
            flash("Kata sandi minimal 6 karakter.", "error")
        elif role not in ("admin", "staf"):
            flash("Peran tidak valid.", "error")
        elif bagian and bagian not in BAGIAN:
            flash("Bagian tidak valid.", "error")
        elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            flash(f"Username {username} sudah dipakai.", "error")
        else:
            db.execute("INSERT INTO users (username, nama, password_hash, role, bagian, dibuat) VALUES (?,?,?,?,?,?)",
                       (username, nama, generate_password_hash(pw), role,
                        bagian if role == "staf" and bagian else None, now_str()))
            db.commit()
            flash(f"Akun {username} dibuat.", "ok")
        return redirect(url_for("pengguna"))
    users = db.execute("SELECT * FROM users ORDER BY role, nama").fetchall()
    return render_template("pengguna.html", active="pengguna", users=users)


@app.post("/pengguna/<int:user_id>/bagian")
@admin_required
def pengguna_bagian(user_id):
    bagian = request.form.get("bagian", "")
    if bagian and bagian not in BAGIAN:
        flash("Bagian tidak valid.", "error")
    else:
        db = get_db()
        db.execute("UPDATE users SET bagian = ? WHERE id = ? AND role = 'staf'", (bagian or None, user_id))
        db.commit()
        flash("Bagian pengguna diperbarui.", "ok")
    return redirect(url_for("pengguna"))


@app.post("/pengguna/<int:user_id>/reset")
@admin_required
def pengguna_reset(user_id):
    pw = request.form.get("password", "")
    if len(pw) < 6:
        flash("Kata sandi baru minimal 6 karakter.", "error")
    else:
        db = get_db()
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(pw), user_id))
        db.commit()
        flash("Kata sandi direset.", "ok")
    return redirect(url_for("pengguna"))


@app.post("/pengguna/<int:user_id>/hapus")
@admin_required
def pengguna_hapus(user_id):
    db = get_db()
    if user_id == current_user()["id"]:
        flash("Tidak bisa menghapus akun sendiri.", "error")
    else:
        target = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        admins = db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        if target and target["role"] == "admin" and admins <= 1:
            flash("Harus ada minimal satu admin.", "error")
        else:
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            db.commit()
            flash("Akun dihapus.", "ok")
    return redirect(url_for("pengguna"))


# ---------------------------------------------------------------------- demo
DEMO_PASSWORD = "demo123"


def seed_demo_akun(db):
    """Satu akun staf demo per bagian, mis. darsyafii / demo123 (hanya bisa akses Darsyafii)."""
    dibuat = []
    for bg in BAGIAN:
        username = bg.lower().replace(" ", "")
        if not db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            db.execute("INSERT INTO users (username, nama, password_hash, role, bagian, dibuat) VALUES (?,?,?,?,?,?)",
                       (username, f"Staf {bg}", generate_password_hash(DEMO_PASSWORD), "staf", bg, now_str()))
            dibuat.append(username)
    db.commit()
    if dibuat:
        print(f" * Akun demo dibuat ({', '.join(dibuat)}), kata sandi: {DEMO_PASSWORD}")


def seed_demo():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    seed_demo_akun(db)
    if db.execute("SELECT COUNT(*) FROM buku").fetchone()[0]:
        print(" * Data buku sudah ada, data contoh tidak ditambahkan.")
        db.close()
        return
    t = date.today()
    contoh = [
        ("Kisah Nabi untuk Anak", "Ust. Ahmad Fauzi", "Syafii Kids", "Rina", 6, "Desain"),
        ("Belajar Huruf Hijaiyah", "Tim Syafii Kids", "Syafii Kids", "Dewi", 12, "Sunting"),
        ("Doa Harian Si Kecil", "Siti Aminah", "Syafii Kids", "Rina", 3, "Pracetak"),
        ("Cerita Sahabat Nabi", "Tim Syafii Kids", "Syafii Kids", "Dewi", 25, "Naskah"),
        ("Fiqih Ibadah Praktis", "Dr. Hasan Basri", "Darsyafii", "Budi", 9, "Sunting"),
        ("Tafsir Juz Amma", "KH. Abdullah", "Darsyafii", "Budi", 2, "Cetak"),
        ("Sirah Nabawiyah Ringkas", "Tim Darsyafii", "Darsyafii", "Laila", -1, "Desain"),
        ("Bulughul Maram", "Ibnu Hajar", "Penerjemah", "Fajar", 30, "Naskah"),
        ("Adab Penuntut Ilmu", "Imam An-Nawawi", "Penerjemah", "Fajar", 14, "Sunting"),
        ("Riyadhus Shalihin Jilid 1", "Imam An-Nawawi", "Penerjemah", "Laila", 18, "Sunting"),
        ("Katalog Produk 2027", "Tim PIS", "PIS", "Andi", 4, "Pracetak"),
        ("Profil Perusahaan PIS", "Tim PIS", "PIS", "Andi", 10, "Desain"),
        ("Kalender Hijriah 1448", "Tim PIS", "PIS", "Andi", 1, "Cetak"),
        ("Panduan Tajwid", "Tim PIS", "PIS", "Dewi", 20, "Sunting"),
        ("Hadits Arbain", "Imam An-Nawawi", "Penerjemah", "Fajar", -20, SELESAI),
    ]
    admin = db.execute("SELECT id, nama FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    for i, (judul, penulis, bagian, pic, hari, tahap) in enumerate(contoh, 1):
        dibuat = (datetime.now() - timedelta(days=30 - i)).strftime("%Y-%m-%d %H:%M:%S")
        cur = db.execute(
            "INSERT INTO buku (kode, judul, penulis, bagian, pic, tenggat, tahap, catatan, dibuat, diubah)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"PIS-{i:03d}", judul, penulis, bagian, pic, (t + timedelta(days=hari)).isoformat(),
             tahap, "", dibuat, dibuat),
        )
        bid = cur.lastrowid
        alur = SEMUA_TAHAP[: SEMUA_TAHAP.index(tahap) + 1]
        for j, th in enumerate(alur):
            waktu = (datetime.now() - timedelta(days=len(alur) - j, hours=i)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO riwayat_tahap (buku_id, dari_tahap, ke_tahap, user_id, oleh, catatan, waktu)"
                " VALUES (?,?,?,?,?,?,?)",
                (bid, alur[j - 1] if j else None, th, admin["id"], admin["nama"],
                 "Buku ditambahkan" if j == 0 else "", waktu),
            )
    db.execute(
        "INSERT OR IGNORE INTO pengaturan (kunci, nilai, diubah, oleh) VALUES ('pengumuman', ?, ?, ?)",
        ("Rapat produksi mingguan hari Senin pukul 09.00. Tenggat cetak Kalender Hijriah 1448 besok.",
         now_str(), admin["nama"]),
    )
    db.commit()
    db.close()
    print(f" * {len(contoh)} buku contoh ditambahkan.")


init_db()

if __name__ == "__main__":
    if "--demo" in sys.argv:
        seed_demo()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    if "--dev" in sys.argv:
        app.run(host=host, port=port, debug=True)
    else:
        try:
            from waitress import serve
        except ImportError:
            print(" * Waitress belum terpasang, memakai server bawaan Flask.")
            app.run(host=host, port=port)
        else:
            print(f" * Sistem Produksi Buku berjalan di http://{'localhost' if host == '127.0.0.1' else host}:{port}")
            serve(app, host=host, port=port, threads=8)
