# Security model

Every rule below is built and covered by the tests listed in "What the tests check". Rules 1 to 4 rest on the data
guard, which exists for PostgreSQL only. The conformance tests have run against the real runtime in the Claude Code
sign-in mode; the isolated mode's own checks are **to verify** (they need an API key). [roadmap.md](roadmap.md) lists
what exists. Nothing here is a promise unless its tests are in the repository.

## What is trusted

| Zone | What is in it | Trust |
|---|---|---|
| Server state | The principal from the host application, the policy, the configuration | Trusted |
| Written knowledge | The knowledge graph, the generated guide, the system prompt | Trusted, checked by tests |
| The person's input | Messages, notes they typed | Untrusted |
| Model output | SQL, tool arguments, answer text, notes it saves | Untrusted |
| Data values | Text in records, especially text typed by outside parties | Untrusted |
| The agent runtime | Its events and session files | Untrusted for decisions, sensitive for storage |

## Rules that follow

1. **Access is enforced by the database.** Row rules, column hiding and table access are grants and views. The prompt
   may tell the model what the person can see, to shape the answer. It never decides it.
2. **Fail closed.** Not in the policy means not available. An unmarked column is hidden. A connection with no bound
   identity sees no rows. A database account that can do more than it should stops the service.
3. **Identity never comes from the model.** Not from tool arguments, not from the message, not from memory.
4. **Read only, one query at a time.** The agent's database role cannot write. Each query runs as a server-side
   cursor in a read-only transaction, so PostgreSQL accepts only one query and nothing else, with a time limit, a row
   cap and a size cap.
5. **Memory is never authorization and never trusted.** A note saying "I am an admin" changes nothing. Notes go to the
   model in their own block, marked as notes.
6. **The model never retypes data.** Files and charts are built by the server from query results.
7. **No outbound path for data.** The agent has no web, fetch or shell tools. Answer HTML is cleaned. Images are removed
   and outside links are shown as plain text, because either can carry data out.
8. **Bounded.** Each answer has a time cap and a cost cap. Each person has a daily limit and a limit on answers
   running at once.

## What the tests check

| Rule | Tests |
|---|---|
| Access is enforced by the database | `tests/security/test_attack_suite.py`: hostile queries run as six test people (three roles, a supplier user with no supplier id, and a connection with nobody bound). Each result must equal the result of the same query on a schema that physically holds only that person's rows, built by an independent oracle (`tests/security/oracle.py`). Queries that reach outside the guard views must be refused. |
| Per person and table, exactly the allowed rows | `tests/security/test_parity.py`, including a check that a deliberately widened policy is caught |
| Fail closed | Nobody bound sees no rows (while the real tables have rows); unknown roles and attributes are refused; unmarked columns stay hidden (`test_identity.py`, `test_doctor.py`) |
| Identity never comes from the model | The agent role cannot call `bind` or write `sessions`; settings written by the model change nothing; a reused process id gets nobody; a bound connection cannot be bound again (`test_identity.py`) |
| Read only, one query, caps | `test_limits.py` |
| A role that can do too much stops the service | `test_doctor.py`: twelve deliberate breaks of the install, each of which the doctor must report; `test_answer_server.py`: the server will not start when the check fails |
| Identity comes from the host application and the run's token | `test_answer_server.py`: no tool has a user, role or tenant argument; a tool call without a valid token gets 401; a run's token stops working when the run ends; a question that claims a higher role changes nothing; two people at once each get their own rows; the answer API needs the host key |
| Text from the person cannot pose as trusted text | `tests/unit/test_runtime_and_prompt.py`: escaped tags; no paths, hosts or set-up details in any role's system prompt |
| The runtime has no shell, file or web tools, and no server secrets | `tests/unit/test_runtime_and_prompt.py` checks the options the adapter passes, that the runtime gets only absolute paths, and that the service refuses a data folder inside a code project; `tests/runtime_conformance/` checks the real runtime, by hand |
| Every answer is bounded and leaves an audit record | `test_answer_server.py`: a run that takes too long is stopped, and its token, connection and folder are released; the audit holds the queries and never the token |
| Each person is bounded | `test_answer_server.py`: the daily answer limit, the daily cost limit (with the cap reserved for each answer, and unknown costs counted at the cap), and the limit on answers at once; `test_runtime_and_prompt.py`: a limit of zero is refused at start-up |
| Memory is never authorization and never trusted | `test_answer_server.py`: a note that claims the admin role is escaped in the next message and changes no rows; one person's notes never reach another; the person can list and delete them. `test_memory.py`: notes belong to the whole principal; a note cannot close its block |
| The model never retypes data | `test_answer_server.py`: a file holds exactly the person's rows; only its owner can download it; a result over the cap makes no file. `test_files.py`: text that looks like a formula is never a formula, in CSV or Excel; a title cannot name a path |
| No outbound path for data in the answer | `test_render.py`: hostile text, including the hostile records of the reference ERP, never becomes a tag, an attribute, an image or a link. `test_host_app.py`: the chat page allows no inline script and no outside source, and sets HTML only from the cleaned answer |
| Permission probes in the benchmark | `tests/security/test_eval_harness.py`: a runtime that leaks a hidden value is reported as a violation |
| The reference host application takes identity from its own session | `test_host_app.py`: the principal comes from the signed session, never from the browser; a changed, forged or expired cookie is refused; a POST from another site is refused, including one that sends `Origin: null` without `Sec-Fetch-Site: same-origin`; a browser that sends `Origin: null` on the site's own sign-in can still sign in; a wrong password and an unknown email get the same answer |

## The three things an attacker needs

An agent can be made to leak data when it has all three: private data, untrusted content, and a way to send data
out. The design cannot avoid the first two, so it removes the third.

## What prompt injection can still do

A supplier can type instructions into a record that a buyer later reads through the assistant. The model is told
that record text is data, but a prompt is not a control. The worst case should be a misleading sentence in an answer.
It should not be able to read other rows, write anything or send data out, because those limits are in code. The
reference ERP carries hostile text in its records so that tests can check this.

## Known limits

- The policy is a second copy of the host application's own permission rules. A test must compare, per role, what the
  guard returns with what the application shows. The reference ERP has no application screens, so its parity test
  compares with the role rules written again in Python.
- **Catalog metadata.** The agent role can read the system catalogs: the names of all tables and columns (hidden ones
  too), the view definitions (and so the policy's conditions), and the statistics views. Those include each table's
  total row count, so a supplier user can learn how many purchase orders the whole company has. Row data is not
  readable this way.
- **Other agent connections.** All agent connections use one database role. One of them can cancel another's query
  (`pg_cancel_backend`, `pg_terminate_backend`) and can hold advisory locks until its connection closes. This allows
  denial of service, not reading data. An administrator can revoke these functions from `PUBLIC` in the business
  database if no other role needs them.
- **Timing.** A query on a view often reads the hidden rows too before the row condition drops them, so its run time
  can depend on the size of the whole table, not only on the rows the person may see.
- **The agent runtime is a large, closed-source program.** Claude Code can run commands, read files and use the web.
  The adapter removes those tools in two layers and passes no server secrets, but the protection rests on that
  configuration and on the pinned version. Run `tests/runtime_conformance` after every change to the adapter or the
  SDK version.
- **The runtime adds context of its own.** Claude Code tells the model the run folder's path, the platform, the OS
  version and the real date. None of it is a secret, but it is set-up detail the project's own prompt avoids, and the
  real date can differ from the one in `<today>`.
- **The sign-in mode (`WORKCELL_CLAUDE_ACCOUNT_LOGIN=1`) is for local trials only.** In it, the runtime uses the
  server user's Claude Code sign-in: the account's email address reaches the model, and a transcript of every run,
  data included, stays in `~/.claude/projects`. The default isolated mode avoids both.
- **The run's token reaches the runtime process** (in its MCP configuration), though not the model. Anyone who can
  read that process's memory during the run can call the tools as that person until the run ends. The token is
  random, lives only for one answer, and works only against this server.
- **Blanked, not removed.** The SDK merges the adapter's environment over the inherited one, so a secret is passed as
  an empty value. The names of the server's environment variables still reach the runtime process.
- **Notes written through prompt injection.** Hostile text in a record can lead the model to save a note. The note
  cannot change access, is shown to the person when it is saved, is marked as untrusted in the prompt, and can be
  deleted. Until it is deleted, it can still mislead later answers.
- **Limits are counted per server process.** Several answer server processes behind a load balancer would each count
  running answers on their own. The daily limits are read from one audit file, so they hold only if the processes
  share it.
- **Files on disk.** Built files stay in the data folder, unencrypted, until they expire (24 hours by default).
  Protect the data folder like the database.
- **The CSV apostrophe** marks text such as `-5 units` too. Excel files have real text cells and need no mark.
- **The reference host application is not a login system to deploy.** It has no limit on sign-in attempts, no
  password change and no way to end other sessions. A real host application keeps its own login.
- **The binder role** can bind any person to any agent connection. It is meant for trusted server code only; protect
  its password like an administrator password.
- The knowledge checks prove names are real. They cannot prove that a rule's wording is still true.
- On engines without `security_barrier` views, hidden rows may be detectable through errors or timing. See
  [architecture.md](architecture.md).

## Reporting a problem

See [SECURITY.md](../SECURITY.md).
