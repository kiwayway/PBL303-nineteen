# Konfigurasi Login Google (dev, staging, produksi)

Fitur ini memakai OpenID Connect (Authorization Code + PKCE). Tanda tangan ID token
(RS256) diverifikasi dengan kunci publik Google (`https://www.googleapis.com/oauth2/v3/certs`)
memakai PyJWT; `iss`, `aud`, `exp`, `iat`, `nonce`, dan `email_verified` juga divalidasi.

## Variabel lingkungan

| Variabel | Isi |
|---|---|
| `GOOGLE_CLIENT_ID` | Client ID dari Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | Client secret (rahasia: hanya di `.env` / secret manager) |
| `GOOGLE_REDIRECT_URI` | URL callback lengkap, harus SAMA PERSIS dengan yang didaftarkan di Google |
| `COOKIE_SECURE` | `1` di staging/produksi (HTTPS) |

Kosongkan `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` untuk menonaktifkan tombol Google.

## Satu OAuth client per lingkungan

Pakai client terpisah untuk dev, staging, dan produksi supaya secret bisa dirotasi tanpa
mengganggu lingkungan lain.

| Lingkungan | Authorized redirect URI | Authorized JavaScript origins |
|---|---|---|
| Dev lokal | `http://localhost:5000/auth/google/callback` | tidak perlu |
| Staging | `https://<domain-staging>/auth/google/callback` | tidak perlu |
| Produksi | `https://<domain-produksi>/auth/google/callback` | tidak perlu |

Catatan:
- Google hanya mengizinkan `http://` untuk `localhost`. Staging dan produksi wajib `https://`.
- Pakai `localhost`, bukan `127.0.0.1`, dan akses aplikasi lewat host yang sama dengan yang
  didaftarkan (cookie sesi menyimpan `state`; host berbeda = sesi tidak terbaca).
- Jangan pakai wildcard; daftarkan URL persis.

## Langkah di Google Cloud Console
1. Buat project (atau pakai yang ada) -> **APIs & Services -> OAuth consent screen**.
   Isi nama aplikasi, email dukungan, dan domain. Scope cukup `openid`, `email`, `profile`
   (scope non-sensitif, tidak perlu verifikasi aplikasi Google).
2. **Publishing status**: selama *Testing*, hanya akun yang ditambahkan sebagai *Test users*
   yang bisa masuk. Sebelum dipakai pengguna sungguhan, ubah ke **In production**.
3. **Credentials -> Create credentials -> OAuth client ID -> Web application**, isi redirect URI
   sesuai tabel di atas.
4. Salin Client ID dan Client secret ke `.env` atau secret manager server. Jangan masukkan ke
   repository, log, screenshot, atau chat.

## Di belakang reverse proxy (nginx) / HTTPS
- Set `GOOGLE_REDIRECT_URI` eksplisit dengan URL `https://...` agar tidak bergantung pada
  header proxy.
- Set `COOKIE_SECURE=1`.
- Jika memakai `ProxyFix`, pastikan hanya mempercayai proxy sendiri.
- Server harus bisa keluar (egress HTTPS) ke `oauth2.googleapis.com` dan `www.googleapis.com`.
  Jika JWKS tidak bisa diambil, login Google gagal tertutup (ditolak), bukan dilewati.
- Aplikasi menyimpan limiter login di memori per proses; callback Google belum memakai limiter.

## Rotasi secret
1. Di Google Cloud Console, buka client -> **Add secret** (client mendukung dua secret aktif).
2. Perbarui `GOOGLE_CLIENT_SECRET` di server, restart aplikasi, uji login Google.
3. Nonaktifkan/hapus secret lama di Google Cloud Console.
Rotasi segera jika secret pernah terekspos.

## Uji manual (akun Google sungguhan)
1. Isi `.env`, jalankan `docker compose up --build`, buka `http://localhost:5000/login`.
2. **Daftar**: klik "Daftar dengan Google" dengan akun yang belum ada -> kembali ke `/` sebagai
   pengguna baru (role `customer`).
3. **Masuk ulang**: logout, klik "Masuk dengan Google" -> masuk ke akun yang sama (tidak ada
   akun ganda).
4. **Batal**: klik tombol lalu tekan "Batal" di layar izin Google -> kembali ke `/login`
   dengan pesan dibatalkan.
5. **Akun lokal yang sama emailnya**: daftar + verifikasi dengan email X (password), logout,
   lalu masuk dengan Google email X -> akun yang sama (terhubung, tidak ganda).
6. Periksa `docker compose logs web`: ada event `google_start`/`google_register`/`google_login`
   dan tidak ada token, code, atau secret.

## Pemecahan masalah
| Gejala | Penyebab umum |
|---|---|
| Google: `redirect_uri_mismatch` | URI di Console tidak sama persis dengan `GOOGLE_REDIRECT_URI` (skema, host, port, path) |
| Google: `access_blocked` / hanya test user | Publishing status masih *Testing* |
| Balik ke `/login` "Sesi masuk kedaluwarsa" | Host berbeda saat mulai vs callback, cookie diblokir, atau > 10 menit |
| Log `reason="jwks_unavailable"` | Server tidak bisa mengambil kunci Google (jaringan/firewall) |
| Log `reason="token_http_400"` | Code kedaluwarsa/dipakai ulang, atau client secret/redirect URI salah |
| Log `reason="identity_conflict"` | Email itu sudah terhubung ke akun Google lain (sub berbeda) |
