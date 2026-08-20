"""
FastAPI routes.

- POST /ingest-resume   upload a resume PDF -> extract, redact PII, embed, store
- POST /analyze         run the full LangGraph pipeline (resume + optional JD)
- POST /analyze/stream  same, streamed as Server-Sent Events

This is the direct upgrade of v1's "upload a PDF in Streamlit, get Gemini's
opinion" flow — the file upload UX is preserved, everything behind it is
rebuilt.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.guardrails import sanitize_input
from app.core.logging import get_logger
from app.core.pdf_extract import extract_text
from app.db.vectorstore import ingest_document
from app.graph.pipeline import analysis_graph

logger = get_logger(__name__)
router = APIRouter()


class IngestResumeResponse(BaseModel):
    document_id: str
    chunks_stored: int
    pii_redacted: bool
    extraction_method: str  # "pdfplumber" or "ocr" — useful to see which path ran


@router.post("/ingest-resume", response_model=IngestResumeResponse)
async def ingest_resume(file: UploadFile = File(...)) -> IngestResumeResponse:
    if file.content_type != "application/pdf":
        raise HTTPException(400, "Only PDF uploads are supported")

    pdf_bytes = await file.read()
    text, method = extract_text(pdf_bytes)

    if not text.strip():
        raise HTTPException(422, "Could not extract any text from this PDF, even with OCR")

    guard = sanitize_input(text)
    document_id = str(uuid.uuid4())
    chunk_count = ingest_document(document_id, "resume", guard.clean_text)

    if guard.had_pii:
        logger.warning("pii_redacted document_id=%s types=%s", document_id, guard.redactions)

    return IngestResumeResponse(
        document_id=document_id,
        chunks_stored=chunk_count,
        pii_redacted=guard.had_pii,
        extraction_method=method,
    )


class AnalyzeRequest(BaseModel):
    resume_document_id: str
    resume_text: str  # kept for the pipeline's parse step context
    jd_text: str | None = None  # omit for a general resume review, no JD needed


class AnalyzeResponse(BaseModel):
    score: float | None
    score_breakdown: dict
    suggestions: str
    had_pii: bool
    had_injection_flag: bool


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(payload: AnalyzeRequest) -> AnalyzeResponse:
    result = await analysis_graph.ainvoke(
        {
            "resume_text": payload.resume_text,
            "resume_document_id": payload.resume_document_id,
            "jd_text": payload.jd_text,
        }
    )
    return AnalyzeResponse(
        score=result.get("score"),
        score_breakdown=result.get("score_breakdown", {}),
        suggestions=result["suggestions"],
        had_pii=result.get("had_pii", False),
        had_injection_flag=result.get("had_injection_flag", False),
    )


@router.post("/analyze/stream")
async def analyze_stream(payload: AnalyzeRequest):
    async def event_generator():
        state = {
            "resume_text": payload.resume_text,
            "resume_document_id": payload.resume_document_id,
            "jd_text": payload.jd_text,
        }
        async for step_output in analysis_graph.astream(state):
            for node_name, node_state in step_output.items():
                yield f"data: {json.dumps({'node': node_name, 'update': _safe_preview(node_state)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _safe_preview(node_state: dict, max_len: int = 300) -> dict:
    preview = {}
    for k, v in node_state.items():
        s = str(v)
        preview[k] = s[:max_len] + ("..." if len(s) > max_len else "")
    return preview
