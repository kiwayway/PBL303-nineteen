# Upload gambar kategori

Admin dapat mengunggah logo dan background pada halaman `/admin/categories`.

- Logo: JPEG, PNG, atau WebP; maksimum 2 MiB; disarankan 1:1.
- Background: JPEG, PNG, atau WebP; maksimum 5 MiB; disarankan 16:9.
- SVG dan semua format selain JPEG/PNG/WebP ditolak.
- Backend memeriksa konten gambar dengan Pillow, bukan hanya ekstensi atau MIME dari browser.
- Gambar di-decode dan di-encode ulang untuk menghilangkan metadata/payload tambahan, diberi nama acak, dan disimpan sebagai static asset.
- Gambar kategori lama dapat diganti atau dihapus. Jika belum ada logo, huruf pertama nama kategori tetap menjadi fallback; jika belum ada background, gradasi dan gambar jasa tetap menjadi fallback.

Setelah menggabungkan patch, install dependency baru dan jalankan migrasi:

```powershell
python -m pip install -r requirements.txt
flask --app wsgi db upgrade
python -m pytest -v
```
