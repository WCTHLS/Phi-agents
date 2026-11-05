#!/usr/bin/env python3
"""Check that the language model set in .env works, with one harmless public question (S2-09).

Plain language: the answer pipeline can ask a hosted model (Groq) to pick the sentences that answer
a question. This script sends one fixed sample question and two fixed sample passages (public
regulatory facts, written into this file) through exactly the same code the API uses, and tells you
whether the model answered, how long it took, and whether its quote was verified word for word.

Usage (from the repository root, with your key in .env):
    python scripts/check_llm.py

It reads LLM_PROVIDER, LLM_MODEL and GROQ_API_KEY from your .env. It never prints the key, never
touches the database, and sends nothing but the sample text below. With LLM_PROVIDER=not_configured
there is nothing to check and nothing is sent.

Exit code 0: the model answered and its quote was verified. 1: the model was reached or tried but
the answer fell back to word matching, or came back empty or unverified (the reason is printed).
2: the settings are not usable (no provider, no key, a wrong address).
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pharma_ai.core.config import Settings, settings
from pharma_ai.features.research.answer_workflow import answer_question
from pharma_ai.features.research.search import FusedHit
from pharma_ai.features.research.writer_factory import (
    NOT_CONFIGURED,
    AnswerWriterConfigurationError,
    build_answer_writer,
)
from pharma_ai.infrastructure.db.repositories import ChunkHit

QUESTION = "What is the acceptable intake for NDMA?"
EXPECTED_QUOTE = "The acceptable intake for NDMA is 96 ng/day."
SAMPLE_PASSAGES = (
    (
        "Control of Nitrosamine Impurities (sample)",
        "APPENDIX B",
        43,
        (
            f"Table 1 lists the limits. {EXPECTED_QUOTE} "
            "The acceptable intake for NDEA is 26.5 ng/day. Other nitrosamines follow a different rule."
        ),
    ),
    (
        "Elemental Impurities (sample)",
        "CADMIUM",
        35,
        "Cadmium is a Class 1 element. The oral permitted daily exposure for cadmium is 5 micrograms per day.",
    ),
)


def sample_results() -> list[FusedHit]:
    found = []
    for rank, (title, section, page, text) in enumerate(SAMPLE_PASSAGES, start=1):
        hit = ChunkHit(
            chunk_id=uuid.uuid4(),
            source_id=f"SAMPLE-{rank}",
            version_id=uuid.uuid4(),
            version_label=None,
            version_number=1,
            sha256="0" * 64,
            page_start=page,
            page_end=page,
            section=section,
            text=text,
            attribution=None,
            title=title,
        )
        found.append(
            FusedHit(hit=hit, score=1.0 / (60 + rank), vector_rank=rank, keyword_rank=None)
        )
    return found


def main(
    argv: Sequence[str] | None = None,
    *,
    cfg: Settings = settings,
    transport: httpx.BaseTransport | None = None,
) -> int:
    argparse.ArgumentParser(description="Check the configured language model.").parse_args(argv)

    if cfg.llm_provider == NOT_CONFIGURED:
        print("LLM_PROVIDER is not_configured, so there is no language model to check.")
        print("To try Groq, set LLM_PROVIDER=groq and GROQ_API_KEY in your own .env.")
        return 2
    try:
        writer = build_answer_writer(cfg, transport=transport)
    except AnswerWriterConfigurationError as exc:
        print(f"The language-model settings are not usable: {exc}")
        return 2

    print(f"Provider: {cfg.llm_provider}   Model: {writer.mode}   Key: set (not shown)")
    print(f"Sending the sample question and {len(SAMPLE_PASSAGES)} sample passages ...")
    found = sample_results()
    started = time.monotonic()
    result = answer_question(QUESTION, found, writer)
    seconds = time.monotonic() - started

    print(f"Time: {seconds:.1f} s")
    print(f"Answer mode: {result.mode}   Sent to provider: {result.sent_to}")
    for quote in result.quotes:
        print(f'Quote chosen: "{quote.text}"')
    for gap in result.evidence_gaps:
        print(f"Note: {gap}")

    used_model = result.mode == writer.mode
    if used_model and result.verified and EXPECTED_QUOTE in (q.text for q in result.quotes):
        print("OK: the model answered and its quote was verified word for word.")
        return 0
    if not used_model:
        print(
            "FALLBACK: the model could not be used; word matching chose the quote (see the note)."
        )
    elif result.withheld:
        print("NO ANSWER: the model replied, but no quote could be verified.")
    else:
        print("UNEXPECTED: the model replied, but not with the quote this check expects.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
