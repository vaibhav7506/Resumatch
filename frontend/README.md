# ResuMatch frontend

Vite + React client for the ResuMatch margin-review interface.

```powershell
npm install
npm run dev
```

Open the local URL printed by Vite. The UI reads the selected PDF in-browser,
then calls the FastAPI service at `http://localhost:8000` by default:

1. `POST /ingest-resume` with the selected PDF as `multipart/form-data`
2. `POST /analyze` with `resume_document_id`, required `resume_text`, and
   `jd_text` (or `null` when no role is supplied)

Set `VITE_API_URL` to use a different API base URL. See `.env.example`.

```powershell
npm run build
```

The production bundle is written to `dist/`.
