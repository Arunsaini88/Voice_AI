# voice-notes-frontend

React 19 + Vite + TypeScript client for Voice Notes. Setup, configuration, and the dictation protocol are documented in the repository root: see `../README.md` and `../docs/DEVELOPMENT.md`.

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # type-check + production bundle in dist/
npm run lint     # oxlint
```

`VITE_API_URL` in `.env` points at the backend (default `http://localhost:8000`).
