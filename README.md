# ResuMatch — Agentic Resume ↔ JD Matching (v2)
### FastAPI · LangGraph · pgvector RAG

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async%20%2B%20SSE-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-agentic%20pipeline-1C3C3C)
![pgvector](https://img.shields.io/badge/PostgreSQL-pgvector-336791?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![Status](https://img.shields.io/badge/status-active-brightgreen)

**Turn a resume and a job description into a structured, explainable match score — through a multi-step LangGraph pipeline instead of one flat prompt to an LLM.**

🔗 **Live demo:** [resumatchvaibhav7506.up.railway.app](https://resumatchvaibhav7506.up.railway.app/)
📚 **Interactive API docs:** append `/docs` to the URL above for the Swagger UI
🧱 **v1 (predecessor):** [ResumeAnalyzer](https://github.com/vaibhav7506/ResumeAnalyzer)

> Give it a ⭐ if you find the parse → retrieve → score → suggest pattern useful — it generalizes well beyond resumes to any "match unstructured document A against unstructured document B" problem.

---

## Table of contents
- [Why this exists](#why-this-exists)
- [Key features](#key-features)
- [Architecture](#architecture)
- [What changed from v1, and why](#what-changed-from-v1-and-why)
- [What's kept from v1](#whats-kept-from-v1)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [API usage](#api-usage)
- [Roadmap](#roadmap)
- [Connect](#connect)

---

## Why this exists

Most "AI resume matcher" side projects are a PDF parser bolted to a single LLM prompt — stuff the whole resume and JD into context, ask for a score, done. That's fast to build and fine for a demo, but it's a black box: no retrieval, no intermediate reasoning you can inspect, no way to explain *why* a score landed where it did.

ResuMatch instead treats matching as a **multi-step agentic pipeline**: parse the resume into structured claims, retrieve the JD requirements that are actually relevant via vector search, score with a hybrid of deterministic overlap + LLM judgment, and only then generate suggestions — streamed back node-by-node so the frontend can show its work in real time.

## Key features

- 🧠 **Agentic LangGraph pipeline** — `parse → retrieve → score → suggest`, not a single prompt
- ⚡ **Real-time streaming** — Server-Sent Events (`/analyze/stream`) show each pipeline node completing live
- 🔍 **Real RAG, not context-stuffing** — resume/JD chunks are embedded (Voyage AI) and retrieved via PostgreSQL + pgvector cosine-similarity search
- 🛡️ **PII-safe by default** — emails and phone numbers are redacted *before* anything is embedded or sent to an LLM
- 📄 **Robust ingestion** — pdfplumber for text-based PDFs, with an automatic pdf2image + Tesseract OCR fallback for scanned/image-based resumes
- 🐳 **Container-first** — Dockerized with a one-line pgvector database spin-up

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

## What changed from v1, and why

| v1 (ResumeAnalyzer) | v2 (this project) | Why |
|---|---|---|
| Streamlit | FastAPI, async, SSE streaming | Streamlit is a UI toolkit, not an API — most Gen AI Developer roles want a real service, not a script with a UI |
| Single flat prompt to Gemini | LangGraph pipeline: parse → retrieve → score → suggest | Demonstrates multi-step agentic orchestration, not just "call an LLM" |
| No storage, no memory between runs | pgvector — resume/JD chunks embedded and stored | Real RAG retrieval instead of stuffing the whole document into one prompt |
| No PII handling | Guardrail redacts emails/phone before anything is embedded or sent to the LLM | Matches the "PII detection" requirement directly, and is just good practice — resumes are full of PII |
| `.env` and sample PDFs committed to git | `.env` gitignored, no sample resumes committed | Basic repo hygiene — don't commit secrets or other people's PII to a public repo |

## What's kept from v1

- **pdfplumber** as the primary text extractor — fast and accurate for text-based PDFs, no reason to replace it.
- **pdf2image + pytesseract OCR fallback** — if pdfplumber returns near-empty text (a scanned resume, an image-based PDF), the pipeline falls back to rasterizing pages and running Tesseract OCR. Same approach as v1, now wrapped in a function with a clear trigger condition instead of always running OCR.

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI (async, SSE streaming) |
| Orchestration | LangGraph |
| LLM | Groq (`llama-3.3-70b-versatile`) |
| Embeddings | Voyage AI |
| Vector store | PostgreSQL + pgvector |
| PDF parsing | pdfplumber → pdf2image + Tesseract OCR fallback |
| Deployment | Docker, Railway |

## Getting started

**Prerequisites:** Python 3.11+, Docker, and the Tesseract/Poppler system binaries for the OCR fallback.

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# system dependency for OCR fallback
# install Tesseract + Poppler for Windows and add both to PATH

docker run -d --name pgvector -e POSTGRES_PASSWORD=postgres -p 5432:5432 ankane/pgvector
Copy-Item .env.example .env   # set GROQ_API_KEY and remaining keys
python -m app.db.init_db
python -m uvicorn app.main:app --reload
```
</details>

<details>
<summary><strong>macOS / Linux</strong></summary>

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# system dependency for OCR fallback
# Ubuntu/Debian: sudo apt install tesseract-ocr poppler-utils
# macOS:         brew install tesseract poppler

docker run -d --name pgvector -e POSTGRES_PASSWORD=postgres -p 5432:5432 ankane/pgvector
cp .env.example .env          # set GROQ_API_KEY and remaining keys
python -m app.db.init_db
python -m uvicorn app.main:app --reload
```
</details>

Set `GROQ_API_KEY` in `.env`. The default `LLM_MODEL` is `llama-3.3-70b-versatile`. Groq handles all language-model requests; Voyage AI continues to provide the embeddings used by pgvector.

Open `/docs` for the interactive Swagger UI, or hit the endpoint directly (see below).

## API usage

```bash
curl -X POST "http://localhost:8000/analyze/stream" \
  -F "resume=@resume.pdf" \
  -F "job_description=@jd.pdf"
```

This streams each pipeline node's output as it completes (parse → retrieve → score → suggest) via Server-Sent Events, so a frontend can render progress in real time instead of waiting on one long request.

## Roadmap

Things still to do before this fully replaces v1 in production:

- [ ] Purge `temp_resume.pdf` and `uploaded_resume.pdf` from git history — not just `git rm`, but `git filter-repo` or BFG, since they may contain real PII
- [ ] Add `.env` to `.gitignore` and scrub the currently-committed one from history too, even though the key value is empty right now
- [ ] Add a LICENSE file (MIT recommended for a portfolio project)
- [ ] Add repo topics/description on GitHub for discoverability (see suggested description above)
- [ ] Port this README into the actual repo once the v2 code lands

## Connect

Built by **Vaibhav Sharma** — Gen AI / Full Stack Engineer.
[Portfolio](https://vaibhav7506portfolio.vercel.app/) · [LinkedIn](https://linkedin.com/in/vaibhav-sharma-996aa8249) · [GitHub](https://github.com/vaibhav7506) · [LeetCode](https://leetcode.com/u/vaibhav7506/)

If this pipeline pattern (parse → retrieve → score → suggest) is useful for something you're building, feel free to open an issue or fork it.
