# ADR-009: Hybrid retrieval (search by meaning plus keywords)

- **Status:** Proposed (S2-07, built 3 Nov 2026). Awaiting review by the platform owner.
- **Plain-language terms:** *Search by meaning* compares the 384 numbers of a question with the numbers of every passage (ADR-008). *Keyword search* finds passages that contain the question's words. *Hybrid* runs both and merges the two rankings. A *tsvector* is PostgreSQL's prepared list of a passage's words (reduced to stems) for keyword search; a *GIN index* makes searching it fast. *Reciprocal rank fusion* merges rankings by place in the list, not by score.
- **Decision:**
  - **One set of visibility rules for both searches.** `ChunkRepository._visible_chunks` builds the SQL both searches share: the caller's projects, an eligible source, a passage embedded by the named model, and (by default) the newest version of the document. The rules are part of the query, so unauthorised text never leaves the database. Both searches require the model name. With no allowed projects the result is empty.
  - **Keyword search uses PostgreSQL full-text search** (`ChunkRepository.search_keywords`). Migration 0004 adds `chunks.search_vector`, a *generated* column built by the database from the section heading and the text (`to_tsvector('english', ...)`), and a GIN index. The database keeps it current, so it cannot disagree with the text and no re-indexing is needed; passages already in a database get it when the migration runs.
  - **The question is reduced to plain words first** (`keyword_terms`): letters and digits only, lower case, no repeats, no single characters, at most 32. The words are joined with "or" and given to `websearch_to_tsquery`, so a passage matches if it has *any* of them and ranks higher with more. Nothing a user types can act as search syntax.
  - **Common words are left out of the keyword search** (`_informative_terms`). A word that occurs in more than **15%** of the passages the caller can see is ignored ("drug", "products"), but the 3 rarest words are always kept so a question of only common words still searches. How common a word is is counted over the *visible* passages (the same rules as the search), never over the whole corpus, so it cannot reveal anything about documents the caller may not read. With fewer than 100 visible passages every word is kept, because "common" means little there. The 15% was chosen on the first 17 golden questions and then tested on 16 it had not seen (below); 10% and 20% were slightly worse.
  - **Merging** (`features/research/search.py`): each search returns its top 20; a passage earns `1 / (60 + rank)` from each list it is in; passages are ordered by the total, ties going to the one that ranked higher in either list. Every result records its vector rank and keyword rank, so it can be explained and audited. The constants (20, 60) are the usual defaults, not tuned.
  - **Interfaces.** `hybrid_search(...)` merges for an already-embedded question; `PassageSearch.search(question, project_ids)` embeds the question with the model and returns citable passages. Neither embeds when there is nothing to search for or nowhere the caller may look.
  - **Measured the same way as before.** `evals/retrieval_check.py --retriever dense|keyword|hybrid` scores each against the golden questions, and the results are saved in `evals/results/`.
- **Measured on 3 Nov 2026** (13-document corpus, on the developer's own database; the 28 scoreable of the 33 golden questions; right passage = a returned passage from the expected document whose pages include an evidence page):

  | Search | Passage, top 1 | Top 3 | Top 5 | Document, top 3 |
  |---|---|---|---|---|
  | Meaning only | 19 of 28 | 24 | 24 | 27 |
  | Keywords only | 13 of 28 | 20 | 23 | 24 |
  | **Hybrid** | **19 of 28** | 23 | **27** | **28** |

  Hybrid matches meaning-only search at rank 1 and is better at the top 5 and for the right document. It finds GQ-01, GQ-07 and GQ-09, which meaning alone misses entirely; it ranks GQ-06 lower (top 5, where meaning alone has it first). Only GQ-32 is missed by both.

  **How this was found, and what it corrected.** The first version of hybrid (all question words, no rarity rule) looked better than meaning alone on the first 12 scoreable questions: 8 against 7 at rank 1. On 16 questions written afterwards, which no setting had been tuned on, it was **worse** at rank 1: 9 of 16 against 12. Long natural-language questions give many weak keyword matches, and a passage that appears in both lists outranks one that tops only one. Giving the keyword list a smaller weight (1.0 down to 0.25) changed nothing. Leaving out words that appear in many passages fixed it: on the 16 unseen questions hybrid then matched meaning-only search (12 of 16 at rank 1, 15 of 16 at the top 5). A hand-written list of generic question words ("describe", "containing") added nothing over the rarity rule alone, so there is none. Other settings that were tried and rejected: keeping only the rarest 4 to 6 words, a stricter 10% or looser 20% cut-off, other rank normalisations, and requiring all the rarest words to match.

  **How much to trust it.** The gain over meaning-only search comes from the first 12 questions, which were picked partly because they were hard; on the 16 unseen questions the two are level. The questions are drafts that no domain expert has reviewed (decision D11), and 28 is still a small set: a difference of one or two questions is noise. Nothing here says whether answers are scientifically right.

- **Reranking was tried and is deferred** (decision D14). A cross-encoder re-reads the top 20 passages against the question. On the same 28 questions on the developer laptop (CPU only):

  | Reranker | Candidates | Rank 1 | Top 5 | Time per question |
  |---|---|---|---|---|
  | none | hybrid | 19 of 28 | 27 | none |
  | `ms-marco-MiniLM-L-6-v2` (Apache 2.0, 91 MB) | hybrid | 19 | 27 | 2.3 s |
  | `ms-marco-MiniLM-L-6-v2` | meaning only | 21 | 28 | 2.2 s |
  | `bge-reranker-base` (MIT, 1.1 GB; peak memory 2.3 GB) | hybrid | 21 | 27 | 14.9 s |
  | `bge-reranker-base` | meaning only | 22 | 27 | 14.3 s |

  It moved the right passage to rank 1 for two or three more questions, but the result depends on which candidates are reranked (the small model gains nothing on hybrid candidates), the models are not tuned for pharma text, and the larger one is slow and heavy for an interactive answer on this hardware. Licences are from the model hub's metadata and have not been independently checked. Worth revisiting with a larger question set and the real language model's latency known.
- **Limits, not hidden:**
  - **PostgreSQL has no true BM25.** `ts_rank_cd` rewards more matching words close together, but not rare words over common ones. The rarity rule above works around this by dropping common words before searching; it does not weight the remaining words by how rare they are. The surviving words are also only compared with the passages the caller can see, which is the right scope but means the cut-off moves as the visible corpus changes.
  - **A question that names a document is only partly handled.** Chunking removes running headers (ADR-008), so a passage no longer contains its own document's name. GQ-07 ("under ICH M13A") is now found in the top 5 but not at rank 1. The plan is to match document codes and titles in the question against the source register and boost or filter to those documents.
  - **Stemming is English only**, and tables are still flattened text (ADR-008).
  - **Miss at the top 5 for hybrid:** GQ-32 only. At rank 1 hybrid misses 9 questions, among them GQ-01 (a valid passage on a different page from the one the check accepts), GQ-07 and GQ-09.
  - **The access list is an argument.** The search enforces the project IDs it is given. The endpoint that calls it must take them from the signed-in user's token, never from the request, and no endpoint does yet; `/research/query` still answers from built-in samples (S2-09 connects them).
- **Not done here:** a reranker (tried, deferred: D14); the named-document step; an audited search endpoint and its HTTP-level permission tests (S2-12); an "indexed" flag on the document list for the library UI.
- **Revisit when:** the golden set grows enough to separate close options (28 scoreable questions cannot); a reranker or named-document step is added; the visible corpus grows well past a few hundred passages (the common-word counts scan every visible passage on each search); the chunking or the embedding model changes (run `scripts/index_chunks.py --force`; keyword search needs no re-indexing); or a second language needs different stemming.
