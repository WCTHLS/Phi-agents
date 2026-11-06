"""Tests for evals/answer_check.py: the scoring and the pacing, with no database and no model.

The numbers it reports are only as good as this arithmetic, so each definition is pinned here with
a small set of asks whose right answers can be counted by hand.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[1] / "evals"
sys.path.insert(0, str(EVALS))
_spec = importlib.util.spec_from_file_location("answer_check", EVALS / "answer_check.py")
ac = importlib.util.module_from_spec(_spec)
sys.modules["answer_check"] = ac
_spec.loader.exec_module(ac)


def quote(source: str, start: int, end: int, text: str) -> dict:
    return {"source": source, "page_start": start, "page_end": end, "text": text}


# --- the interval -------------------------------------------------------------------------------


def test_the_interval_is_empty_for_nothing_and_brackets_the_proportion() -> None:
    assert ac.wilson(0, 0) == (0.0, 0.0)
    low, high = ac.wilson(5, 10)
    assert low < 0.5 < high and (round(low, 2), round(high, 2)) == (0.24, 0.76)
    low, high = ac.wilson(10, 10)
    assert high == 1.0 and 0.65 < low < 0.75  # all of ten is still not certainty
    low, high = ac.wilson(0, 10)
    assert low == 0.0 and 0.25 < high < 0.35


def test_a_rate_is_written_with_its_counts_and_interval() -> None:
    assert ac.rate(3, 4).startswith("3/4 = 75% (95% CI ")
    assert ac.rate(0, 0) == "n/a"


# --- what counts as the right evidence ---------------------------------------------------------


def test_a_quote_is_on_evidence_only_if_it_is_the_right_document_on_the_right_page() -> None:
    evidence = {"DOC-A": {5, 6}}
    quotes = [
        quote("DOC-A", 5, 5, "on the page"),
        quote("DOC-A", 6, 7, "a passage that runs from the evidence page onward"),
        quote("DOC-A", 8, 8, "right document, wrong page"),
        quote("DOC-B", 5, 5, "wrong document, same page number"),
    ]
    assert ac.quotes_on_evidence(quotes, evidence) == (2, True)
    assert ac.quotes_on_evidence(quotes[2:], evidence) == (0, False)
    assert ac.quotes_on_evidence([], evidence) == (0, False)


def test_the_quotes_contain_the_answer_only_if_every_pattern_is_in_that_documents_quotes() -> None:
    spec = {"DOC-A": [r"Q ?= ?80 ?%", r"30 ?min"]}
    both = [quote("DOC-A", 8, 8, "Q = 80% of the drug"), quote("DOC-A", 8, 8, "in 30 minutes")]
    assert ac.quotes_contain(both, spec)
    assert not ac.quotes_contain(both[:1], spec)  # one pattern missing
    assert not ac.quotes_contain(
        [quote("DOC-B", 8, 8, "Q = 80% in 30 min")], spec
    )  # wrong document
    assert not ac.quotes_contain(both, {})  # no spec, nothing to check
    two_docs = {"DOC-A": [r"alpha"], "DOC-B": [r"beta"]}
    assert not ac.quotes_contain([quote("DOC-A", 1, 1, "alpha")], two_docs)  # needs both documents
    assert ac.quotes_contain(
        [quote("DOC-A", 1, 1, "alpha"), quote("DOC-B", 2, 2, "BETA")], two_docs
    )  # case does not matter


# --- the summary, counted by hand --------------------------------------------------------------


def make(role: str, **fields) -> object:
    ask = ac.Ask("GQ-X", fields.pop("kind", "definition"), role, "PRJ-001", "a question?")
    for name, value in fields.items():
        setattr(ask, name, value)
    return ask


def sample_asks() -> list:
    right = [quote("DOC-A", 5, 5, "evidence")]
    return [
        # answerable: correct, with the evidence in view; two quotes, one on evidence
        make(
            "answerable",
            answered=True,
            withheld=False,
            on_evidence=True,
            contains=True,
            retrieval_hit=True,
            quotes=right + [quote("DOC-A", 9, 9, "other")],
            relevant_quotes=1,
            offered=3,
            removed=1,
            seconds=2.0,
            kind="table value",
        ),
        # answerable: answered, but not on the evidence page
        make(
            "answerable",
            answered=True,
            withheld=False,
            retrieval_hit=True,
            quotes=[quote("DOC-A", 9, 9, "x")],
            offered=1,
            seconds=3.0,
        ),
        # answerable: withheld although the evidence was in view; asked twice and the retry failed
        make("answerable", retrieval_hit=True, retried=True, seconds=4.0),
        # answerable: answered although the evidence was not in view (a lucky quote)
        make(
            "answerable",
            answered=True,
            withheld=False,
            on_evidence=True,
            retrieval_hit=False,
            quotes=right,
            relevant_quotes=1,
            offered=1,
            seconds=1.0,
            kind="table value",
        ),
        # negatives: one withheld, one wrongly answered, one gap withheld
        make("cross", withheld=True),
        make("cross", answered=True, withheld=False, quotes=right, offered=1, retried=True),
        make("cross", answered=True, withheld=False, quotes=right, offered=1),
        make("gap", withheld=True),
        # left out: the model could not be reached
        make("answerable", model_failed=True),
    ]


def test_the_summary_counts_each_definition() -> None:
    s = ac.summarise(sample_asks())
    assert (s["asks"], s["model_failed"], s["answerable"], s["negative"]) == (9, 1, 4, 4)
    assert (s["answered"], s["on_evidence"], s["contains"], s["correct"]) == (3, 2, 1, 2)
    assert (s["retrieval_hit"], s["correct_given_retrieval"]) == (3, 1)
    assert (s["no_retrieval"], s["answered_given_no_retrieval"]) == (1, 1)
    assert (s["cross"], s["cross_withheld"], s["gap"], s["gap_withheld"]) == (3, 1, 1, 1)
    assert s["negatives_withheld"] == 2


def test_answer_precision_recall_and_f1_follow_their_definitions() -> None:
    s = ac.summarise(sample_asks())
    # answers given: 3 answerable + 2 wrongly answered negatives = 5; correct = 2; answerable = 4
    assert s["answers_given"] == 5 and s["correct"] == 2
    assert s["answer_precision"] == pytest.approx(2 / 5)
    assert s["answer_recall"] == pytest.approx(2 / 4)
    assert s["answer_f1"] == pytest.approx(2 * 0.4 * 0.5 / (0.4 + 0.5))


def test_quote_counts_cover_offered_removed_and_relevant_quotes() -> None:
    s = ac.summarise(sample_asks())
    assert s["quotes_verified"] == 2 + 1 + 1 + 1 + 1  # the failed ask is left out
    assert s["quotes_offered"] == 3 + 1 + 1 + 1 + 1
    assert (s["quotes_relevant"], s["quotes_on_answerable"]) == (2, 4)  # negatives are not counted


def test_retries_and_latency_are_counted_without_the_asks_that_failed() -> None:
    s = ac.summarise(sample_asks())
    assert s["retried"] == 2 and s["retry_recovered"] == 1
    assert s["median_seconds"] == pytest.approx(0.5)  # the zero-second asks count too
    assert s["by_kind"] == {"definition": (0, 2), "table value": (2, 2)}


def test_an_empty_run_gives_zeros_not_a_crash() -> None:
    s = ac.summarise([])
    assert s["answer_f1"] == 0.0 and s["median_seconds"] == 0.0 and s["answerable"] == 0
    assert "n/a" in ac.report(s)


def test_the_report_names_every_measure() -> None:
    text = ac.report(ac.summarise(sample_asks()))
    for heading in (
        "answer precision",
        "answer recall",
        "answer F1",
        "verbatim rate",
        "quote precision",
        "withheld, same question in the other project",
        "LATENCY",
        "RETRY",
        "BY QUESTION TYPE",
    ):
        assert heading in text, heading
    assert "left out because the model could not be reached" in text


# --- pacing ------------------------------------------------------------------------------------


def test_a_failed_model_call_is_asked_again_after_a_longer_pause_and_then_left_out(
    monkeypatch,
) -> None:
    pauses: list[float] = []
    calls: list[int] = []

    def always_fails(ask, *_args):
        calls.append(1)
        ask.model_failed = True
        return ask

    monkeypatch.setattr(ac, "run_ask", always_fails)
    ask = make("answerable")
    done = ac.execute(
        [(ask, {}, {})],
        None,
        None,
        None,
        None,
        delay=5,
        sleep=pauses.append,
        progress=lambda _m: None,
    )
    assert len(calls) == 3 and pauses == [5, 60, 60]
    assert done[0].model_failed  # counted as left out, not as a withheld answer


def test_a_call_that_works_on_the_second_try_is_kept(monkeypatch) -> None:
    states = iter([True, False])

    def flaky(ask, *_args):
        ask.model_failed = next(states)
        ask.answered, ask.withheld = (not ask.model_failed), ask.model_failed
        return ask

    monkeypatch.setattr(ac, "run_ask", flaky)
    pauses: list[float] = []
    (done,) = ac.execute(
        [(make("answerable"), {}, {})],
        None,
        None,
        None,
        None,
        delay=2,
        sleep=pauses.append,
        progress=lambda _m: None,
    )
    assert not done.model_failed and done.answered and pauses == [2, 60]
