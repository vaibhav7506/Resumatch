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
import re
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.guardrails import sanitize_input
from app.core.logging import get_logger
from app.core.pdf_extract import extract_text
from app.db.vectorstore import ingest_document
from app.graph.pipeline import analysis_graph, complete

logger = get_logger(__name__)
router = APIRouter()
quiz_sessions: dict[str, dict] = {}


class IngestResumeResponse(BaseModel):
    document_id: str
    chunks_stored: int
    pii_redacted: bool
    extraction_method: str  # "pdfplumber" or "ocr" — useful to see which path ran


class IngestStudyMaterialResponse(IngestResumeResponse):
    material_text: str


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


@router.post("/ingest-study-material", response_model=IngestStudyMaterialResponse)
async def ingest_study_material(file: UploadFile = File(...)) -> IngestStudyMaterialResponse:
    """Extract and store a study PDF for an adaptive quiz session."""
    if file.content_type != "application/pdf":
        raise HTTPException(400, "Only PDF uploads are supported")

    pdf_bytes = await file.read()
    text, method = extract_text(pdf_bytes)
    if not text.strip():
        raise HTTPException(422, "Could not extract any text from this PDF, even with OCR")

    guard = sanitize_input(text)
    document_id = str(uuid.uuid4())
    chunk_count = ingest_document(document_id, "study_material", guard.clean_text)
    return IngestStudyMaterialResponse(
        document_id=document_id,
        chunks_stored=chunk_count,
        pii_redacted=guard.had_pii,
        extraction_method=method,
        material_text=guard.clean_text,
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
    try:
        result = await analysis_graph.ainvoke(
            {
                "resume_text": payload.resume_text,
                "resume_document_id": payload.resume_document_id,
                "jd_text": payload.jd_text,
            }
        )
    except Exception as exc:
        logger.exception("resume_analysis_failed")
        raise HTTPException(502, "The AI analysis provider is unavailable. Check the configured model and try again.") from exc
    return AnalyzeResponse(
        score=result.get("score"),
        score_breakdown=result.get("score_breakdown", {}),
        suggestions=result["suggestions"],
        had_pii=result.get("had_pii", False),
        had_injection_flag=result.get("had_injection_flag", False),
    )


class QuizQuestion(BaseModel):
    question: str
    choices: list[str] = Field(min_length=2, max_length=4)
    topic: str
    difficulty: str


class StartQuizRequest(BaseModel):
    material_document_id: str
    material_text: str = Field(min_length=1, max_length=60000)
    question_count: int = Field(default=5, ge=1, le=10)


class StartQuizResponse(BaseModel):
    session_id: str
    question: QuizQuestion
    target_questions: int


class SubmitQuizAnswerRequest(BaseModel):
    session_id: str
    answer: str = Field(min_length=1, max_length=2000)


class SubmitQuizAnswerResponse(BaseModel):
    correct: bool
    feedback: str
    expected_answer: str
    answered_count: int
    correct_count: int
    target_questions: int
    next_question: QuizQuestion | None = None


def _quiz_public_question(question: dict) -> QuizQuestion:
    return QuizQuestion(
        question=question["question"],
        choices=question["choices"],
        topic=question["topic"],
        difficulty=question["difficulty"],
    )


def _json_object(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        raise ValueError("The AI response did not contain JSON")
    return json.loads(match.group(0))


def _generate_quiz_question(material_text: str, difficulty: str, prior_topics: list[str]) -> dict:
    history = ", ".join(prior_topics[-5:]) or "none yet"
    try:
        raw = complete(
            system=(
                "You are an adaptive study tutor. Create one fair multiple-choice question using only "
                "the supplied material. Do not repeat these previous topics: " + history + ". "
                "Return only strict JSON with question, choices, answer, explanation, topic, and difficulty. "
                "choices must contain 2 to 4 distinct plain-text options; answer must exactly equal one choice."
            ),
            user=f"Difficulty: {difficulty}\n\nStudy material:\n{material_text[:16000]}",
            max_tokens=700,
        )
        question = _json_object(raw)
        choices = [str(choice).strip() for choice in question.get("choices", []) if str(choice).strip()]
        answer = str(question.get("answer", "")).strip()
        if len(choices) < 2 or len(choices) > 4 or len(set(choices)) != len(choices) or answer not in choices:
            raise ValueError("The generated question did not meet the quiz schema")
        return {
            "question": str(question["question"]).strip(),
            "choices": choices,
            "answer": answer,
            "explanation": str(question.get("explanation", "")).strip(),
            "topic": str(question.get("topic", "Study material")).strip(),
            "difficulty": str(question.get("difficulty", difficulty)).strip().lower(),
        }
    except Exception as exc:
        logger.exception("quiz_question_generation_failed")
        raise HTTPException(502, "The quiz provider could not create a question. Check the configured model and try again.") from exc


@router.post("/quiz/start", response_model=StartQuizResponse)
async def start_quiz(payload: StartQuizRequest) -> StartQuizResponse:
    guard = sanitize_input(payload.material_text)
    first_question = _generate_quiz_question(guard.clean_text, "medium", [])
    session_id = str(uuid.uuid4())
    quiz_sessions[session_id] = {
        "material_document_id": payload.material_document_id,
        "material_text": guard.clean_text,
        "target_questions": payload.question_count,
        "answered_count": 0,
        "correct_count": 0,
        "prior_topics": [],
        "current_question": first_question,
    }
    return StartQuizResponse(
        session_id=session_id,
        question=_quiz_public_question(first_question),
        target_questions=payload.question_count,
    )


@router.post("/quiz/answer", response_model=SubmitQuizAnswerResponse)
async def submit_quiz_answer(payload: SubmitQuizAnswerRequest) -> SubmitQuizAnswerResponse:
    session = quiz_sessions.get(payload.session_id)
    if not session:
        raise HTTPException(404, "This quiz session is no longer available. Start a new quiz.")

    current = session["current_question"]
    correct = payload.answer.strip().casefold() == current["answer"].casefold()
    session["answered_count"] += 1
    session["correct_count"] += int(correct)
    session["prior_topics"].append(current["topic"])
    feedback = (
        f"Correct. {current['explanation']}"
        if correct
        else f"Not quite. The best answer is {current['answer']}. {current['explanation']}"
    )

    next_question = None
    if session["answered_count"] < session["target_questions"]:
        next_difficulty = "hard" if correct else "easy"
        generated = _generate_quiz_question(session["material_text"], next_difficulty, session["prior_topics"])
        session["current_question"] = generated
        next_question = _quiz_public_question(generated)
    else:
        quiz_sessions.pop(payload.session_id, None)

    return SubmitQuizAnswerResponse(
        correct=correct,
        feedback=feedback,
        expected_answer=current["answer"],
        answered_count=session["answered_count"],
        correct_count=session["correct_count"],
        target_questions=session["target_questions"],
        next_question=next_question,
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
