"""
Guardrails applied to any text before it is embedded or sent to an LLM.

Two concerns, kept deliberately separate:

1. PII redaction — resumes are full of emails, phone numbers, and sometimes
   addresses. We redact these BEFORE the text is embedded or logged, so raw
   PII never lands in the vector store or in LLM provider logs.

2. Prompt-injection sanitization — a JD or resume is untrusted input (someone
   could paste "ignore all previous instructions..." into a resume field).
   We don't try to be clever here; we just neutralize the highest-risk
   patterns and always keep instructions and user content in clearly
   separated message roles downstream (see graph/pipeline.py).

This is intentionally simple, heuristic, regex-based — the same honest
framing you already use in LLMGuard Lab. It is NOT a claim of state-of-the-art
PII detection (that would need a proper NER model); it's a first line of
defense, and the docstring says so on purpose so you never oversell it in
an interview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(\+?\d{1,3}[\s.-]?)?\(?\d{3,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}")
# Very rough — flags obvious street-address-looking lines. Deliberately
# conservative (prefer false negatives over mangling resume content).
ADDRESS_HINT_RE = re.compile(
    r"\b\d{1,5}\s+\w+(\s\w+){0,3}\s(street|st|avenue|ave|road|rd|lane|ln|block|sector)\b",
    re.IGNORECASE,
)

INJECTION_PATTERNS = [
    re.compile(r"ignore (all )?(previous|above) instructions", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"disregard (your|the) (rules|guidelines)", re.IGNORECASE),
]


@dataclass
class GuardrailResult:
    clean_text: str
    redactions: list[str] = field(default_factory=list)
    injection_flags: list[str] = field(default_factory=list)

    @property
    def had_pii(self) -> bool:
        return len(self.redactions) > 0

    @property
    def had_injection_attempt(self) -> bool:
        return len(self.injection_flags) > 0


def redact_pii(text: str) -> GuardrailResult:
    """Redact emails, phone numbers, and likely street addresses.

    Returns the redacted text plus a list of what kind of PII was found,
    so callers can log/alert on PII presence without ever logging the PII
    itself.
    """
    redactions: list[str] = []

    def _sub(pattern: re.Pattern, label: str, s: str) -> str:
        if pattern.search(s):
            redactions.append(label)
        return pattern.sub(f"[REDACTED_{label}]", s)

    clean = text
    clean = _sub(EMAIL_RE, "EMAIL", clean)
    clean = _sub(PHONE_RE, "PHONE", clean)
    clean = _sub(ADDRESS_HINT_RE, "ADDRESS", clean)

    return GuardrailResult(clean_text=clean, redactions=redactions)


def flag_prompt_injection(text: str) -> list[str]:
    """Return a list of injection-pattern names matched in the text.

    We do NOT try to strip these — stripping can mangle legitimate content
    (e.g. a resume bullet that happens to say "system design"). Instead we
    flag them so the caller can decide: log, reject, or just ensure the
    text is passed to the LLM strictly as untrusted user content, never
    concatenated into the system/instruction prompt.
    """
    flags = []
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(pattern.pattern)
    return flags


def sanitize_input(text: str) -> GuardrailResult:
    """Single entry point: redact PII and flag injection attempts."""
    result = redact_pii(text)
    result.injection_flags = flag_prompt_injection(text)
    return result
