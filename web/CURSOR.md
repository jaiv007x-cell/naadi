# NAADI web — open this in Cursor

This is the **frontend** built in Grok Builder. It is a TanStack Start + React app.
Your GitHub repo (`jaiv007x-cell/naadi`) is the **Python engine**. They are not the same folder.

## Run it

```bash
npm install
npm run dev
```

Then open http://localhost:8080

On Windows (PowerShell or Git Bash), same commands.

## What you get

- Ward: Ramesh Kale, Sunita Devi, Arjun Reddy, Meena Pawar (anaphylaxis)
- OS map: Pratibimb, Nirikshak, Dhaara, Guru, Drishti, Niyukti, Sankalp, Aayam, BEEMA, Authoring
- Engine in TypeScript: `src/lib/naadi/` (cases, physio, grader)
- Snapshots of your Python sources: `engine/`

No login. Sessions live in the browser (`localStorage`).

## Drop this next to your Python repo

```
naadi/                 ← your GitHub clone
  services/            ← Pratibimb, Dhaara, BEEMA
  web/                 ← unzip this folder here
    src/
    package.json
    CURSOR.md
```

Do **not** merge this over `services/`. Keep Python and the web app side by side.

Later, Cursor can replace the in-browser store with `fetch` to Pratibimb (`/v1/sessions`). Until then this UI is self-contained.

## Do not commit

`node_modules/`, `.env`, `.vercel/`, `dist/`
