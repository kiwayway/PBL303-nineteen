# Implementasi Marketplace Nineteen

## Alur yang tersedia

- Katalog publik: `GET /jasa`, pencarian `q`, filter `kategori`, sorting `sort`, pagination `page`.
- Detail jasa: `GET /jasa/<service_id>`.
- Admin jasa/paket: `/admin/services`, `/admin/services/new`, `/admin/services/<id>/edit`.
- Seller jasa/paket milik sendiri: `/seller/jasa`, `/seller/jasa/baru`, `/seller/jasa/<id>/edit`; seller hanya dapat mengakses jasa dengan `seller_id` miliknya dan draft baru menunggu publikasi admin.
- Cart: `GET /keranjang`, tambah/hapus melalui POST.
- Checkout: `GET|POST /checkout`; harga dibaca ulang dari database.
- Pembayaran: `/pesanan/<id>/pembayaran`; unggah bukti melalui POST.
- Admin verifikasi: `/admin/orders`, preview bukti melalui route terlindungi, approve/reject.
- Seller: `/seller/pesanan`, seller dapat menandai item selesai miliknya.
- Laporan admin: `/admin/reports`, export `/admin/reports/export/pdf` dan `/admin/reports/export/xlsx`.

Semua aksi perubahan menggunakan token CSRF global. Order dan file bukti dibatasi berdasarkan pemilik/role. Bukti pembayaran disimpan di `instance/private_uploads/payments`, tidak disajikan melalui folder static. Bukti yang diterima: JPEG, PNG, WebP, PDF maksimal 5 MB.

## Pembayaran

Metode aktif adalah transfer manual dengan approval admin. Isi `PAYMENT_BANK_NAME`, `PAYMENT_BANK_ACCOUNT_NAME`, dan `PAYMENT_BANK_ACCOUNT_NUMBER` setelah detail rekening resmi ditetapkan. Jangan isi dengan rekening contoh. Integrasi Midtrans belum diaktifkan; untuk mengaktifkannya dibutuhkan keputusan bisnis dan credential Sandbox yang sah, webhook HTTPS, signature verification, serta pengujian callback idempotent.

## Migration

Migration baru `0007` menambah snapshot nama jasa, paket, durasi pada `order_items`, serta `customer_note` terpisah dari alasan penolakan pembayaran. Upgrade:

```powershell
docker compose run --rm web flask --app wsgi db upgrade
```

Pada Compose dev, perintah upgrade juga dijalankan saat service web start. Jangan mengedit migration yang sudah pernah diterapkan.

## Menjalankan di Windows

1. Install Docker Desktop dan pastikan WSL2/Linux containers aktif.
2. Extract ZIP, salin `.env.example` menjadi `.env`.
3. Isi `SECRET_KEY` acak minimal 32 karakter dan `DB_PASSWORD` yang kuat. Untuk pembayaran manual, isi detail rekening resmi; SMTP dan Google OAuth opsional.
4. Jalankan PowerShell di folder repository:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose logs -f web
```

5. Buka `http://localhost:5000`. Database disimpan pada volume Docker `pgdata`.
6. Jalankan test di container:

```powershell
docker compose run --rm web sh -c "pip install -r requirements-dev.txt && pytest -q"
```

Untuk menghentikan aplikasi: `docker compose down`. Jangan gunakan `docker compose down -v` kecuali memang ingin menghapus volume database.

## Persiapan produksi

- Gunakan `docker compose -f docker-compose.prod.yml up --build -d`.
- Simpan `.env` di host dengan permission terbatas; jangan masukkan ke Git/ZIP.
- Pasang reverse proxy TLS (HTTPS), batasi akses port aplikasi, set domain dan `GOOGLE_REDIRECT_URI` produksi bila OAuth digunakan.
- Atur backup dan uji restore PostgreSQL secara berkala.
- Backup volume `uploads` dan `private_uploads`; bukti pembayaran tidak boleh dipublikasikan.
- Siapkan SMTP produksi dan monitoring log/health check.
- Lakukan `flask db upgrade` pada maintenance window serta siapkan backup sebelum upgrade.
- Deployment live belum dilakukan dari sesi ini.

## Environment variables tambahan

| Nama | Fungsi |
|---|---|
| `PAYMENT_BANK_NAME` | Nama bank penerima transfer |
| `PAYMENT_BANK_ACCOUNT_NAME` | Nama pemilik rekening |
| `PAYMENT_BANK_ACCOUNT_NUMBER` | Nomor rekening resmi |
| `COOKIE_SECURE` | Aktifkan cookie Secure di HTTPS |
| `DATABASE_URL` | URL koneksi SQLAlchemy |
| `SECRET_KEY` | Kunci sesi Flask |
| `DB_PASSWORD` | Password PostgreSQL pada Compose |
| `MAIL_*` | Konfigurasi SMTP untuk OTP/notifikasi |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | OAuth Google |
