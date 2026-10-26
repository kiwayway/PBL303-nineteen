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
