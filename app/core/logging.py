"""
Minimal structured logging so pipeline steps, token usage, and guardrail
hits are observable — this is the "observability tools and error handling
strategies" bullet from the JD, kept honest: it's structured stdlib logging,
not a claim of integrating Datadog/Prometheus/etc unless you actually add one.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager

from app.core.config import settings


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.setLevel(settings.log_level)
    return logger


@contextmanager
def timed_step(logger: logging.Logger, step_name: str):
    """Wrap a pipeline node to log its duration — cheap, effective
    observability for a LangGraph pipeline with almost no code."""
    start = time.perf_counter()
    logger.info("step_start step=%s", step_name)
    try:
        yield
    except Exception:
        logger.exception("step_failed step=%s", step_name)
        raise
    else:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("step_done step=%s elapsed_ms=%.1f", step_name, elapsed_ms)
