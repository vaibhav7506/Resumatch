# Resume Analyzer v2 — FastAPI + LangGraph + RAG

An upgrade of [ResumeAnalyzer](https://github.com/vaibhav7506/ResumeAnalyzer):
same PDF-ingestion approach (pdfplumber with an OCR fallback via pdf2image +
pytesseract), rebuilt as a proper async service with retrieval-augmented
scoring instead of a single flat LLM call.

## What changed from v1, and why

| v1 (ResumeAnalyzer) | v2 (this project) | Why |
|---|---|---|
| Streamlit | FastAPI, async, SSE streaming | Streamlit is a UI toolkit, not an API — most Gen AI Developer JDs want FastAPI specifically |
| Single flat prompt to Gemini | LangGraph pipeline: parse → retrieve → score → suggest | Demonstrates multi-step agentic orchestration, not just "call an LLM" |
| No storage, no memory between runs | pgvector — resume/JD chunks embedded and stored | Real RAG retrieval instead of stuffing the whole document into one prompt |
| No PII handling | Guardrail redacts emails/phone before anything is embedded or sent to the LLM | Matches the "PII detection" requirement directly, and is just good practice — resumes are full of PII |
| `.env` and sample PDFs committed to git | `.env` gitignored, no sample resumes committed | Basic repo hygiene — don't commit secrets or other people's PII to a public repo |

## What's kept from v1

- **pdfplumber** as the primary text extractor — it's fast and accurate for
  text-based PDFs, no reason to replace it.
- **pdf2image + pytesseract OCR fallback** — if pdfplumber returns near-empty
  text (a scanned resume, an image-based PDF), we fall back to rasterizing
  pages and running Tesseract OCR. Same approach as v1, just wrapped in a
  function with a clear trigger condition instead of always running OCR.

## Architecture

```
Uploaded resume.pdf
        │
        ▼
[extract_text()] ── pdfplumber first; OCR fallback if text is too sparse
        │
        ▼
[PII Guardrail] ── redact emails/phone before storage or LLM calls
        │
        ▼
[Ingest & Embed] ── chunk, embed (Voyage AI), store in pgvector
        │
        ▼
[LangGraph Pipeline]
    ├─ parse_node     → extract structured skills/experience claims from resume
    ├─ retrieve_node  → pgvector: pull matching JD requirement chunks (if a JD was also ingested)
    ├─ score_node     → deterministic overlap + LLM-assisted match score
    └─ suggest_node   → streamed, honest improvement suggestions
        │
        ▼
FastAPI endpoint (async, SSE streaming)
```

## Setup

```powershell
# Windows PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# system dependency for OCR fallback (same as v1 needed)
# Ubuntu/Debian: sudo apt install tesseract-ocr poppler-utils
# Mac: brew install tesseract poppler

docker run -d --name pgvector -e POSTGRES_PASSWORD=postgres -p 5432:5432 ankane/pgvector
Copy-Item .env.example .env   # set GROQ_API_KEY and remaining keys
python -m app.db.init_db
python -m uvicorn app.main:app --reload
```

Set `GROQ_API_KEY` in `.env`. The default `LLM_MODEL` is
`llama-3.3-70b-versatile`. Groq handles all language-model requests; Voyage AI
continues to provide the embeddings used by pgvector.

Open `/docs` for the interactive Swagger UI, or `POST /analyze` with a resume
PDF (and optionally a job description) to run the full pipeline.

## Migration checklist (things to actually do in the real repo)

- [ ] Remove `temp_resume.pdf` and `uploaded_resume.pdf` from git history
      (not just `git rm` — use `git filter-repo` or BFG so they're gone from
      history, since they may contain someone's real PII)
- [ ] Add `.env` to `.gitignore` and remove the currently-committed one from
      history too, even though the key value is empty right now
- [ ] Add a real repo description + topics on GitHub (currently blank)
- [ ] Write this README into the actual repo once the code is ported over
