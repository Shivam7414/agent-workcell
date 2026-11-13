---
paths:
  - "src/agent_workcell/documents/**"
  - "reference_erp/documents.sql"
  - "tests/unit/test_documents_read.py"
  - "tests/security/test_documents.py"
  - "tests/security/test_document_processing.py"
  - "tests/security/test_document_search.py"
  - "tests/unit/test_documents_embed.py"
  - "tests/embedding_conformance/**"
---

# Documents

Status: tables, access rules, upload, the job queue, reading text, Markdown and CSV, embeddings, hybrid search and
the document tools (`search_documents`, `read_document`) are **built**. The design is in `docs/architecture.md`
("Documents").

## Rules

- Documents live in the business database, behind the data guard. No separate vector database, and no search path
  that skips the guard: a document tool runs fixed, parameterised SQL through the person's guarded connection.
- Who may read a document is decided by the policy's row rules, never by the upload, a tool argument or the model.
  The host application decides what a document is attached to.
- The upload and the worker write as the documents role only. Never with the owner's account. When the role needs a
  new right, change `plan_sql` and `check_role` together, and add a break to `BREAKS` in
  `test_document_processing.py`.
- A file is untrusted. Its name is stored as data and never becomes part of a path; the storage key is made of
  numbers and the file's hash. Its content only ever becomes passage text.
- Reading is plain code, no model call. Read in parts: never load a whole file into memory.
- A message in `document_versions.error` is read by people and the model. It never names a path, a host or a role.
  Details go to the worker's log and the job table.
- A file that cannot be read fails at once. Only faults of the environment are retried.
- A worker writes a version's passages only while it holds the job's lock and the job is still its attempt.
- Embeddings come from a provider chosen in settings. Tests never call a real provider; they use `FakeEmbedder` or a
  mock transport. `tests/embedding_conformance/` calls a real one, by hand only.
- A search compares only vectors of one model and its dimensions. Never mix models.
- A document tool answers a document the person may not read exactly as one that does not exist.
- Text from a document (content, title, section) reaches the model escaped, inside `<passage>`. Feed every new
  output the hostile passage from the seed.
- Tests that add versions remove them again, so the parity test and the attack suite see the seeded rows only.
