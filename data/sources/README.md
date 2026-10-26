# Approved source manifest

Do not commit downloaded documents here. Keep source metadata in a reviewed manifest once the manager/domain contact confirms permission and scope. Store local sample documents outside Git or in an approved access-controlled location.

Suggested fields: `source_id`, `title`, `publisher`, `url_or_identifier`, `source_type`, `publication_date`, `version`, `access_basis`, `permission_status`, `retrieved_at`, `content_hash`, `owner`, `notes`.

The reviewed manifest is now `corpus_manifest.csv`; see `CORPUS.md`. Its columns differ from the suggestion above (for example `doc_id`, `licence_status`, `reuse_basis`, `attribution`, `eligible`). Hashes and retrieval times are generated into `data/corpus/corpus_register.csv`, which is not committed.
