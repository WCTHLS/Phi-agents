# ADR-009: Hybrid retrieval (search by meaning plus keywords)

- **Status:** Proposed (S2-07, built 3 Nov 2026). Awaiting review by the platform owner.
- **Plain-language terms:** *Search by meaning* compares the 384 numbers of a question with the numbers of every passage (ADR-008). *Keyword search* finds passages that contain the question's words. *Hybrid* runs both and merges the two rankings. A *tsvector* is PostgreSQL's prepared list of a passage's words (reduced to stems) for keyword search; a *GIN index* makes searching it fast. *Reciprocal rank fusion* merges rankings by place in the list, not by score.
- **Decision:**
  - **One set of visibility rules for both searches.** `ChunkRepository._visible_chunks` builds the SQL both searches share: the caller's projects, an eligible source, a passage embedded by the named model, and (by default) the newest version of the document. The rules are part of the query, so unauthorised text never leaves the database. Both searches require the model name. With no allowed projects the result is empty.
  - **Keyword search uses PostgreSQL full-text search** (`ChunkRepository.search_keywords`). Migration 0004 adds `chunks.search_vector`, a *generated* column built by the database from the section heading and the text (`to_tsvector('english', ...)`), and a GIN index. The database keeps it current, so it cannot disagree with the text and no re-indexing is needed; passages already in a database get it when the migration runs.
  - **The question is reduced to plain words first** (`keyword_terms`): letters and digits only, lower case, no repeats, no single characters, at most 32. The words are joined with "or" and given to `websearch_to_tsquery`, so a passage matches if it has *any* of them and ranks higher with more. Nothing a user types can act as search syntax.
  - **Merging** (`features/research/search.py`): each search returns its top 20; a passage earns `1 / (60 + rank)` from each list it is in; passages are ordered by the total, ties going to the one that ranked higher in either list. Every result records its vector rank and keyword rank, so it can be explained and audited. The constants (20, 60) are the usual defaults, not tuned.
  - **Interfaces.** `hybrid_search(...)` merges for an already-embedded question; `PassageSearch.search(question, project_ids)` embeds the question with the model and returns citable passages. Neither embeds when there is nothing to search for or nowhere the caller may look.
  - **Measured the same way as before.** `evals/retrieval_check.py --retriever dense|keyword|hybrid` scores each against the golden questions, and the results are saved in `evals/results/`.
- **Measured on 3 Nov 2026** (13-document corpus; 9 scoreable golden questions; right passage = a returned passage from the expected document whose pages include an evidence page):

  | Search | Passage, top 1 | Top 3 | Top 5 | Document, top 3 |
  |---|---|---|---|---|
  | Meaning only | 5 of 9 | 6 | 6 | 8 |
  | Keywords only | 3 of 9 | 7 | 7 | 8 |
  | **Hybrid** | **6 of 9** | 7 | 7 | **9** |

  Hybrid lost no question that meaning-only got right, gained GQ-09 (a question that needs two documents) at the top 5 and GQ-10 at rank 1. **That is two questions out of nine: a direction, not proof.** It says nothing about whether answers are scientifically right (decision D11).
- **Limits, not hidden:**
  - **PostgreSQL has no true BM25.** `ts_rank_cd` rewards more matching words close together, but not rare words over common ones, so "oral" counts as much as "M13A". If the numbers show it matters, the next step is to weight words by how rare they are across the corpus.
  - **A question that names a document is not handled.** Chunking removes running headers (ADR-008), so a passage no longer contains its own document's name, and GQ-07 ("under ICH M13A") still finds FDA's 2022 guidance first. The plan is to match document codes and titles in the question against the source register and boost or filter to those documents.
  - **Stemming is English only**, and tables are still flattened text (ADR-008).
  - **Two golden questions remain misses:** GQ-01 (a valid passage on a different page from the one the check accepts) and GQ-07 (above).
  - **The access list is an argument.** The search enforces the project IDs it is given. The endpoint that calls it must take them from the signed-in user's token, never from the request, and no endpoint does yet; `/research/query` still answers from built-in samples (S2-09 connects them).
- **Not done here:** a reranker (deferred in the sprint scope); the named-document step; an audited search endpoint and its HTTP-level permission tests (S2-12); an "indexed" flag on the document list for the library UI.
- **Revisit when:** the golden set grows enough to separate close options; a reranker or named-document step is added; the chunking or the embedding model changes (run `scripts/index_chunks.py --force`; keyword search needs no re-indexing); or a second language needs different stemming.
