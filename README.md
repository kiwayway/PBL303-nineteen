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


## Fitur Autentikasi
Halaman: `/register`, `/verify` (OTP email), `/login`, `/forgot-password`, `/reset-password`, `POST /logout`.
- Migrasi database jalan otomatis saat `docker compose up` (`flask db upgrade`).
- Saat dev (`MAIL_SERVER` kosong), kode OTP dicetak di log: `docker compose logs web`.
- Tombol "Masuk dengan Google" masih nonaktif (belum diimplementasikan).

## Konfigurasi Keamanan
- `SECRET_KEY` wajib diisi (minimal 32 karakter) dan aplikasi menolak start jika kosong atau masih nilai contoh. Buat dengan:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `DATABASE_URL` wajib diisi di luar mode test (docker compose sudah mengaturnya).
- Login dibatasi: 5 gagal per akun dan 20 gagal per IP dalam 15 menit, lalu diblokir sementara (HTTP 429). Bisa diubah lewat `LOGIN_MAX_FAILS_PER_ACCOUNT`, `LOGIN_MAX_FAILS_PER_IP`, `LOGIN_WINDOW_SECONDS`. Pembatas ini disimpan di memori per proses (cukup untuk 1 worker gunicorn).
- Setelah situs diakses lewat HTTPS, set `COOKIE_SECURE=1` di `.env` agar cookie sesi hanya dikirim lewat HTTPS.
- Log keamanan (logger `nineteen.audit`) ditulis ke stderr dengan format `event="login_failed" ip="..." ident="..."`. Event: `register`, `otp_failed`, `otp_ok`, `login_success`, `login_failed`, `login_blocked`, `password_reset_requested`, `password_reset`. Dibaca Wazuh agent lewat `docker logs`/file log. Password dan kode OTP tidak pernah dicatat.
- Jika berada di belakang reverse proxy (nginx), alamat IP klien perlu diteruskan dengan `ProxyFix`; tambahkan saat nginx dipasang.
