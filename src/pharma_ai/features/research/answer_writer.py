"""Writing an answer from retrieved passages, and checking that it is grounded (S2-09).

Plain language: the system never answers from memory. It is given the passages the search found
and may only say things those passages say. Two parts keep it honest:

- An **answer writer** turns the passages into an answer made of *quotes*. ``QuotingAnswerWriter``
  needs no language model: it picks the sentences that share the question's words and quotes them
  word for word. A language model can be plugged in later by writing another ``AnswerWriter``;
  nothing else changes.
- **Verification** (``verify``) runs on whatever any writer returns. A quote counts only if it
  appears, word for word, in a passage that was actually retrieved. Anything else is dropped, and
  an answer with no verified quote is withheld rather than guessed.

"Verified" here means *the quoted words are really in the cited passage*. It does not mean the
passage answers the question, or that the answer is scientifically correct; that is what the human
review is for.
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pharma_ai.features.documents.chunking import split_sentences
from pharma_ai.infrastructure.db.repositories import ChunkHit

# Words that say nothing about what a question is about. Kept short and plain on purpose.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "more",
        "most",
        "must",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "shall",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "under",
        "up",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "whose",
        "why",
        "will",
        "with",
        "within",
        "without",
        "would",
        "you",
        "your",
    ]
)

MAX_SENTENCES_PER_PASSAGE = 2
MAX_QUOTES = 6
# The longest quote a model may offer: a sentence, or an excerpt such as the headings and a few
# rows of a table. Longer than this is a passage, not a quote, and is not used.
MAX_QUOTE_CHARS = 1200
QUOTING_MODE = "quotes-only"
_WORD = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    """A very light plural fold so "limits" matches "limit". Applied to both sides."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _content_words(text: str) -> dict[str, str]:
    """The distinctive words of a text: folded form -> the word as written, in order, no repeats.

    Filler words are left out. Numbers count however short ("96"); other words need three letters.
    """
    found: dict[str, str] = {}
    for word in _WORD.findall(text.lower()):
        if word in STOPWORDS or (len(word) < 3 and not word.isdigit()):
            continue
        found.setdefault(_stem(word), word)
    return found


def content_terms(text: str) -> list[str]:
    """The folded distinctive words of a question (see ``_content_words``)."""
    return list(_content_words(text))


def _words(text: str) -> set[str]:
    return {_stem(word) for word in _WORD.findall(text.lower())}


def normalise(text: str) -> str:
    """Collapse every run of whitespace to one space, so line breaks do not affect a comparison."""
    return " ".join(text.split())


@dataclass(frozen=True)
class Quote:
    """Words copied from one retrieved passage."""

    chunk_id: uuid.UUID
    text: str


@dataclass(frozen=True)
class DraftAnswer:
    """What a writer offers: quotes only. The answer text is built later from the verified ones,
    so nothing a writer says outside a verified quote can reach a user."""

    quotes: tuple[Quote, ...]
    mode: str  # which writer produced it: "quotes-only", or a model's name
    # Shown to the reviewer with the evidence gaps, for example "the model was unavailable".
    notes: tuple[str, ...] = ()
    # The provider that was sent the question and passages (even if its reply was not usable), or
    # None if nothing left this machine. Written to the audit trail.
    sent_to: str | None = None


class AnswerWriter(ABC):
    """Turns a question and the passages found for it into a draft answer made of quotes."""

    @property
    @abstractmethod
    def mode(self) -> str: ...

    @abstractmethod
    def write(self, question: str, passages: Sequence[ChunkHit]) -> DraftAnswer: ...


def passage_label(hit: ChunkHit) -> str:
    where = (
        f"p. {hit.page_start}"
        if hit.page_start == hit.page_end
        else f"pp. {hit.page_start}-{hit.page_end}"
    )
    section = f"{hit.section}, " if hit.section else ""
    return f"{hit.title or hit.source_id} ({section}{where})"


def render_quotes(quotes: Iterable[Quote], passages: Sequence[ChunkHit]) -> str:
    """The answer text for a set of quotes: each passage's quotes under a line naming the document.

    Used for every answer that reaches a user, so the text is made only of verified quotes and
    the names of the documents they come from.
    """
    by_chunk = {hit.chunk_id: hit for hit in passages}
    order: list[uuid.UUID] = []
    grouped: dict[uuid.UUID, list[str]] = {}
    for quote in quotes:
        if quote.chunk_id not in grouped:
            order.append(quote.chunk_id)
            grouped[quote.chunk_id] = []
        grouped[quote.chunk_id].append(quote.text)
    return "\n\n".join(
        f"According to {passage_label(by_chunk[chunk_id])}:\n"
        + "\n".join(f'"{text}"' for text in grouped[chunk_id])
        for chunk_id in order
        if chunk_id in by_chunk
    )


class QuotingAnswerWriter(AnswerWriter):
    """No language model: quote the sentences that share the question's distinctive words."""

    mode = QUOTING_MODE

    def write(self, question: str, passages: Sequence[ChunkHit]) -> DraftAnswer:
        wanted = set(content_terms(question))
        quotes: list[Quote] = []
        for hit in passages:
            if len(quotes) >= MAX_QUOTES or not wanted:
                break
            chosen = self._best_sentences(hit.text, wanted)  # none: the passage shares no word
            quotes.extend(Quote(hit.chunk_id, sentence) for sentence in chosen)
        quotes = quotes[:MAX_QUOTES]
        return DraftAnswer(quotes=tuple(quotes), mode=self.mode)

    @staticmethod
    def _best_sentences(text: str, wanted: set[str]) -> list[str]:
        scored = []
        for index, (start, end) in enumerate(split_sentences(text)):
            sentence = text[start:end].strip()
            score = len(wanted & _words(sentence))
            if score:
                scored.append((-score, index, sentence))
        best = sorted(scored)[:MAX_SENTENCES_PER_PASSAGE]
        return [sentence for _, _, sentence in sorted(best, key=lambda item: item[1])]


@dataclass(frozen=True)
class Verification:
    verified: tuple[Quote, ...]
    rejected: tuple[Quote, ...]


def verify(quotes: Iterable[Quote], passages: Sequence[ChunkHit]) -> Verification:
    """Keep the quotes that appear word for word in the passage they name.

    A quote that names a passage that was not retrieved, or whose words are not in it, is rejected.
    Whitespace differences (line breaks) are ignored; nothing else is.
    """
    text_by_chunk = {hit.chunk_id: normalise(hit.text) for hit in passages}
    verified, rejected = [], []
    for quote in quotes:
        wanted = normalise(quote.text)
        passage = text_by_chunk.get(quote.chunk_id)
        if wanted and passage is not None and wanted in passage:
            verified.append(quote)
        else:
            rejected.append(quote)
    return Verification(tuple(verified), tuple(rejected))


def coverage(question: str, quotes: Iterable[Quote]) -> float:
    """The share of the question's distinctive words found in the quoted text, from 0 to 1.

    This is a coverage figure, not a probability that the answer is right.
    """
    wanted = set(content_terms(question))
    if not wanted:
        return 0.0
    found: set[str] = set()
    for quote in quotes:
        found |= wanted & _words(quote.text)
    return round(len(found) / len(wanted), 2)


def evidence_gaps(question: str, passages: Sequence[ChunkHit]) -> list[str]:
    """Distinctive words of the question (longer than three letters) that no retrieved passage uses."""
    mentioned: set[str] = set()
    for hit in passages:
        mentioned |= _words(hit.text)
    return [
        f"None of the retrieved passages mentions '{word}'."
        for folded, word in _content_words(question).items()
        if folded not in mentioned and len(word) > 3
    ]
