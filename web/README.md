# NAADI Clinical OS — web preview

Playable Pratibimb ward UI (React 19 + Vite + TanStack Router). Runs the case
loop in-browser. When Pratibimb is running on `:8100`, Ramesh STEMI and
anaphylaxis sync dialogue + vitals via the Vite proxy (`/api/pratibimb`).

## Pratibimb backend (optional)

```bash
# from repo root — start engine on :8100
cd services/pratibimb
AUTH_MODE=development uvicorn app.main:app --port 8100
```

With `npm run dev` in `web/`, entering Ramesh or anaphylaxis shows a **Pratibimb :8100** badge when synced.

## Cases (seed library)

| ID | Persona | Backend hash pin |
|----|---------|------------------|
| `ramesh-stemi` | Ramesh Kale — inferior STEMI | `80e29c6e…` |
| `aarav-sepsis` | Baby Aarav — neonatal sepsis (proxy: Meera) | `75930a37…` |
| `sunita-pph` | Sunita Devi — postpartum hemorrhage | draft |
| `arjun-dengue` | Arjun Reddy — dengue critical phase | draft |
| `priya-snakebite` | Priya Nair — Russell's viper | draft |
| `sunita-preeclampsia` | Sunita — severe PET (legacy extra) | — |
| `arjun-asthma` | Arjun — paediatric asthma (legacy extra) | — |
| `opd-anaphylaxis` | Meena — anaphylaxis | — |

## Run locally (Windows)

From the repo root:

```powershell
.\start-web.ps1
```

Or manually:

```bash
cd web
npm install
npm run dev
```

Open http://localhost:8080 — case library at `/cases`, sim at `/sim/$caseId`.

If port 8080 is busy, stop the old server (Ctrl+C) or run:
`Get-NetTCPConnection -LocalPort 8080 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`

## Modules

Full OS map at `/os`: Nirikshak ledger, Dhaara CRS, Guru tutor, Drishti floor
observation, authoring harness, BEEMA signals.
