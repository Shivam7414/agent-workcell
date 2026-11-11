# Security

This project sits between an AI agent and business data, so we welcome reports of any way to bypass its controls.

## Reporting

Please do not open a public issue for a security problem. Use the repository's private "Report a vulnerability" form
(Security tab → Advisories). Describe what you did, what you expected and what happened. A failing test is the most
useful form of report.

## Scope

- Reading a row, column or table the policy does not allow, by any SQL or any tool call.
- Writing to the business database through the assistant.
- Making a tool act as another person.
- Getting data out through an answer, a file or a chart (a link, an image, a spreadsheet formula).
- Making the agent runtime read files, run commands or reach the network.
- A check (policy, knowledge graph, start-up) that passes when it should fail.

## Known limits

Known limits are listed in [docs/security-model.md](docs/security-model.md). A misleading sentence caused by hostile
text in a record is a known limit of language models; reading or sending data because of it is a bug.

## Status

The project is at an early stage and has not had an independent security review. Do not use it with production data.
