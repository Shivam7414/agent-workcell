---
paths:
  - "src/agent_workcell/knowledge/**"
  - "reference_erp/knowledge/**"
---

# Knowledge graph

Status: **built.** Code: `src/agent_workcell/knowledge/`. Reference knowledge: `reference_erp/knowledge/`.
The design is in `docs/architecture.md` ("The knowledge graph").

## Rules

- The business side is written by hand as data files. Tables and columns are **read from the live database**, never
  copied into the files, so they cannot go stale.
- The guide sent with every request is **generated** from the graph. Never edit a generated file by hand. A check
  fails when it is stale.
- Screen words come from what people see in the application, not from developer notes.
- Every calculation carries SQL, and the check runs it. A calculation that does not run is an error.
- Every rule points to the code that implements it. The check proves the file and the name exist. It cannot prove the
  wording is still true, so when you change code a rule points to, read that rule again and say so in your reply.
- Retrieval describes only tables and columns the person may read. It asks the guard's policy; it has no visibility
  rules of its own.
- No embeddings and no vector database unless a benchmark shows word matching is the problem.
- After changing a knowledge file, the policy or the schema, regenerate the guides
  (`python -m agent_workcell.knowledge guide`) and run the check. The test suite fails on a stale guide.

## Checks must be able to fail

Each kind of check has a test that feeds it a broken graph and asserts the error is reported.
