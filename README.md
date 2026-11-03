# PBL303-nineteen


## Alur Kerja Tim

### Setup lokal
```powershell
git clone https://github.com/kiwayway/PBL303-nineteen.git
cd PBL303-nineteen
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env      # lalu isi nilainya, JANGAN di-commit
docker compose up --build
python -m pytest -v         # jalankan test lokal
```

### Alur branch
1. Mulai dari `develop`: `git checkout develop` lalu `git pull`
2. Buat branch: `feat/...`, `fix/...`, `ops/...`, atau `chore/...`
3. Commit dan push: `git push -u origin nama-branch`
4. Buka PR dengan base `develop`
5. Tunggu check `test` dan `docker-build` hijau, minta review, lalu merge
6. Rilis stabil: PR `develop` -> `main`

### Siapa yang review
| Area | Reviewer |
|------|----------|
| `app/`, `tests/` | Dev: @kiwayway, @Haikal-ai-tech |
| `Dockerfile`, `docker-compose*.yml`, `.env.example`, `.github/` | Ops: @Farris10, @moseshendika, @Uzzayri |

### Aturan
- Dilarang push langsung ke `main` dan `develop`.
- PR harus 1 approval dan CI hijau.
- Jangan commit `.env` atau rahasia apa pun.
- Pesan commit pakai awalan: `feat:`, `fix:`, `ops:`, `chore:`, `docs:`.


## Dashboard Admin

Dashboard admin tersedia di `/admin/` dan hanya dapat diakses oleh akun dengan role `admin`.
Admin masuk melalui halaman `/login`; setelah login, admin otomatis diarahkan ke dashboard.

**Membuat akun admin pertama (tidak ada password default):**

```powershell
flask --app wsgi create-admin
```

Ikuti prompt untuk mengisi nama, email, nomor HP, dan password. Password disimpan dalam bentuk hash dan akun dibuat terverifikasi. Jalankan perintah ini dari environment proyek yang sudah memiliki konfigurasi database di `.env`. Jika aplikasi berjalan di Docker:

```powershell
docker compose exec web flask --app wsgi create-admin
```

**Menu dashboard:**
- Ringkasan jumlah pengguna, customer, seller, jasa, pesanan, ulasan, dan nilai pesanan berstatus `approved`.
- Pengguna: pencarian dan aktivasi/nonaktivasi akun. Admin tidak dapat menonaktifkan akunnya sendiri.
- Jasa: pencarian dan aktivasi/nonaktivasi jasa.
- Pesanan dan ulasan: monitoring data marketplace.
- Kategori: menambah dan menghapus kategori yang belum dipakai jasa.

Semua halaman admin dilindungi pemeriksaan role server-side dan form perubahan data menggunakan token CSRF yang sudah dipakai aplikasi. Tidak ada migrasi database baru untuk dashboard ini.

## Fitur Autentikasi
Halaman: `/register`, `/verify` (OTP email), `/login`, `/forgot-password`, `/reset-password`, `POST /logout`.
- Migrasi database jalan otomatis saat `docker compose up` (`flask db upgrade`).
- Saat dev (`MAIL_SERVER` kosong), kode OTP dicetak di log: `docker compose logs web`.
- Daftar/masuk dengan Google: lihat bagian "Login dengan Google" di bawah. Tombol otomatis nonaktif jika `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` kosong.

## Login dengan Google
Route: `GET /auth/google` (mulai) dan `GET /auth/google/callback`. Alur OpenID Connect Authorization Code + PKCE, dengan `state` dan `nonce` sekali pakai di sesi. Tanda tangan ID token (RS256) diverifikasi dengan kunci publik Google (PyJWT + JWKS); algoritma lain ditolak. Panduan dev/staging/produksi, rotasi secret, dan uji manual: [`docs/GOOGLE-OAUTH.md`](docs/GOOGLE-OAUTH.md).

**Setup (sekali):**
1. Google Cloud Console -> APIs & Services -> OAuth consent screen, lalu Credentials -> Create OAuth client ID -> *Web application*.
2. Authorized redirect URI (harus sama persis): `http://localhost:5000/auth/google/callback` untuk dev (pakai `localhost`, bukan `127.0.0.1`); URL `https://` domain produksi untuk server.
3. Isi `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` di `.env` (nama variabel ada di `.env.example`). Jangan commit nilainya.
4. `docker compose up --build` (migrasi `0003` jalan otomatis).

**Aturan akun:**
- Identitas disimpan di tabel `oauth_identities` berdasarkan `(provider, sub)`, bukan email.
- Email Google harus `email_verified`; jika tidak, ditolak.
- Pengguna baru -> role `customer`, `is_verified=True`, `phone` kosong (kolom `phone` kini boleh NULL; tetap unik jika terisi). Password diisi hash acak yang tak diketahui siapa pun; akun Google bisa membuat password lewat "Lupa password".
- Email sudah dipakai akun lokal **terverifikasi** -> akun itu dihubungkan (role/data tidak berubah). Jika akun lokalnya **belum terverifikasi**, password lamanya dibuang lalu dihubungkan (mencegah pra-pembajakan email).
- Jika akun itu sudah terhubung ke identitas Google **lain** (sub berbeda, email sama), login ditolak (`identity_conflict`); akun tidak pernah digabung hanya karena email sama.
- Akun nonaktif ditolak. Role tidak pernah ditentukan dari request.
- Event audit: `google_start`, `google_register`, `google_link`, `google_login`, `google_conflict`, `google_failed` (dengan `reason`). Token, code, dan secret tidak pernah dicatat.
- Jika di belakang reverse proxy/HTTPS, set `GOOGLE_REDIRECT_URI` eksplisit dan `COOKIE_SECURE=1`.

## Konfigurasi Keamanan
- `SECRET_KEY` wajib diisi (minimal 32 karakter) dan aplikasi menolak start jika kosong atau masih nilai contoh. Buat dengan:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `DATABASE_URL` wajib diisi di luar mode test (docker compose sudah mengaturnya).
- Login dibatasi: 5 gagal per akun dan 20 gagal per IP dalam 15 menit, lalu diblokir sementara (HTTP 429). Bisa diubah lewat `LOGIN_MAX_FAILS_PER_ACCOUNT`, `LOGIN_MAX_FAILS_PER_IP`, `LOGIN_WINDOW_SECONDS`. Pembatas ini disimpan di memori per proses (cukup untuk 1 worker gunicorn).
- Setelah situs diakses lewat HTTPS, set `COOKIE_SECURE=1` di `.env` agar cookie sesi hanya dikirim lewat HTTPS.
- Log keamanan (logger `nineteen.audit`) ditulis ke stderr dengan format `event="login_failed" ip="..." ident="..."`. Event: `register`, `otp_failed`, `otp_ok`, `login_success`, `login_failed`, `login_blocked`, `password_reset_requested`, `password_reset`. Dibaca Wazuh agent lewat `docker logs`/file log. Password dan kode OTP tidak pernah dicatat.
- Jika berada di belakang reverse proxy (nginx), alamat IP klien perlu diteruskan dengan `ProxyFix`; tambahkan saat nginx dipasang.
