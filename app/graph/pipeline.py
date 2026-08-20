"""
LangGraph pipeline: parse -> retrieve -> score -> suggest.

Adapted from the generic resume-match-engine pipeline to match v1's actual
use case: analyze a resume, optionally against a job description. If no JD
is provided, score/suggest still run but focus on general resume quality
(structure, clarity, quantified impact) instead of JD-fit.
"""

from __future__ import annotations

from typing import TypedDict

from groq import Groq
from langgraph.graph import StateGraph, END

from app.core.config import settings
from app.core.guardrails import sanitize_input
from app.core.logging import get_logger, timed_step
from app.db.vectorstore import retrieve_similar_batch

logger = get_logger(__name__)
llm = Groq(api_key=settings.groq_api_key)


_STOPWORDS = {
    "with", "and", "the", "for", "using", "from", "that", "this", "have",
    "experience", "work", "team", "system", "systems", "based", "including",
    "such", "into", "your", "will", "able", "strong", "good", "years",
    "skills", "knowledge", "ability", "including", "various", "some",
} 
def complete(system: str, user: str, max_tokens: int) -> str:
    """Run a plain-text Groq chat completion and return its text safely."""
    response = llm.chat.completions.create(
        model=settings.llm_model,
        max_completion_tokens=max_tokens,
        reasoning_effort="low",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""


class AnalysisState(TypedDict, total=False):
    resume_text: str
    resume_document_id: str
    jd_text: str | None  # optional — v1 supported JD-less "just review my resume" too

    parsed_requirements: list[str]
    retrieved_resume_chunks: list[str]
    score: float | None
    score_breakdown: dict
    suggestions: str

    had_pii: bool
    had_injection_flag: bool


def parse_node(state: AnalysisState) -> dict:
    """If a JD was provided, extract its requirements. If not, extract the
    resume's own claimed skills/experience so scoring has something to
    retrieve against."""
    with timed_step(logger, "parse"):
        source_text = state.get("jd_text") or state["resume_text"]
        guard = sanitize_input(source_text)
        if guard.had_injection_attempt:
            logger.warning("injection_flagged node=parse patterns=%s", guard.injection_flags)

        instruction = (
            "Extract the concrete technical requirements from this job "
            "description as a short bullet list."
            if state.get("jd_text")
            else "Extract this resume's claimed skills and technologies as a short bullet list."
        )

        text = complete(
            system=f"{instruction} Output only the bullet list, one item per line, no preamble.",
            user=guard.clean_text,
            max_tokens=500,
        )
        items = [line.strip("-• ").strip() for line in text.splitlines() if line.strip()]

        return {
            "parsed_requirements": items,
            "had_pii": state.get("had_pii", False) or guard.had_pii,
            "had_injection_flag": state.get("had_injection_flag", False) or guard.had_injection_attempt,
        }


def retrieve_node(state: AnalysisState) -> dict:
    """Pull the most relevant stored resume chunks for each parsed item.
    Only meaningful once a resume has been ingested via /ingest first."""
    with timed_step(logger, "retrieve"):
        requirements = state["parsed_requirements"][:8]
        results_by_requirement = retrieve_similar_batch(
         requirements,
         document_type="resume",
         document_id=state["resume_document_id"],
         top_k=3,
        )
        all_chunks: set[str] = set()
        for results in results_by_requirement.values():
            all_chunks.update(r.content for r in results)

        return {"retrieved_resume_chunks": list(all_chunks) or [state["resume_text"][:2000]]}


def score_node(state: AnalysisState) -> dict:
    """Only produces a numeric score when a JD was provided — general
    resume review (no JD) skips numeric scoring and goes straight to
    qualitative suggestions, matching how v1 behaved without a JD input."""
    with timed_step(logger, "score"):
        if not state.get("jd_text"):
            return {"score": None, "score_breakdown": {}}

        requirements = state["parsed_requirements"]
        resume_chunks_text = "\n".join(state["retrieved_resume_chunks"]).lower()

        matched = 0
        total_overlap = 0.0
        for req in requirements:
            sig_words = [
                w.lower() for w in req.split()
                if len(w) > 3 and w.lower() not in _STOPWORDS
            ]
            if not sig_words:
                continue
            hits = sum(1 for w in sig_words if w in resume_chunks_text)
            total_overlap += hits / len(sig_words)  # fractional credit, not 0/1

        deterministic_score = (total_overlap / len(requirements)) * 100 if requirements else 0.0

        llm_score_text = complete(
            system=(
                "Score resume fit 0-100 strictly. Only count a requirement met if "
                "excerpts show direct, specific evidence — not just topical overlap. "
                "Most resumes score 30-70 unless truly comprehensive. Output only the integer." 
                 "nothing else — no explanation, no reasoning shown."
                
            ),
            user=f"Requirements:\n{requirements}\n\nExcerpts:\n{resume_chunks_text[:1500]}",
            max_tokens=1000,
        )
        logger.info("llm_score_raw=%r", llm_score_text)  
        try:
            llm_score = float(llm_score_text.strip())
        except ValueError:
            llm_score = deterministic_score

        final_score = round((deterministic_score + llm_score) / 2, 1)
        return {
            "score": final_score,
            "score_breakdown": {
                "deterministic_overlap": round(deterministic_score, 1),
                "llm_assessed": llm_score,
            },
        }
def suggest_node(state: AnalysisState) -> dict:
    with timed_step(logger, "suggest"):
        context = (
            f"Requirements: {state['parsed_requirements']}\nScore: {state.get('score')}\n"
            if state.get("jd_text")
            else "No job description provided — give general resume quality feedback.\n"
        )
        suggestions = complete(
    system=(
        "You improve resumes honestly — never invent experience, metrics, "
        "or specific numbers the candidate hasn't confirmed they have. "
        "When suggesting a bullet for a gap, phrase it as a question — "
        "'Have you done X? If so, phrase it as...' — never as a ready-to-paste "
        "sentence with fabricated numbers or claims. For genuine gaps with no "
        "underlying work, say so plainly and suggest it as a learning/roadmap "
        "item, not a resume bullet. Keep each point brief — 1-2 sentences max."
    ),
    user=f"{context}Resume excerpts: {state['retrieved_resume_chunks']}",
    max_tokens=1600,
)
        return {"suggestions": suggestions}


def build_graph() -> StateGraph:
    graph = StateGraph(AnalysisState)
    graph.add_node("parse", parse_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("score_node", score_node)
    graph.add_node("suggest", suggest_node)

    graph.set_entry_point("parse")
    graph.add_edge("parse", "retrieve")
    graph.add_edge("retrieve", "score_node")
    graph.add_edge("score_node", "suggest")
    graph.add_edge("suggest", END)

    return graph.compile()


analysis_graph = build_graph()
