# Benchmarks

A number in the docs must come from a file in `results/`. Each file records the date, the commit, the model (or
"none"), the seed, the scale, the number of cases and the runs per case, next to the results.

| Benchmark | Run | What it measures |
|---|---|---|
| Guard latency | `python bench/guard_latency/run.py --scale N` | The same queries with and without the data guard. Calls no model and costs nothing. |
| Answers | `python -m agent_workcell.eval --cases reference_erp/eval_cases.toml --runtime claude --max-usd N --out bench/results` | Answers graded against gold SQL, and permission probes. Calls a real model and costs money: `--max-usd` is required. `--runtime fake` checks the cases at no cost. |

The guard latency benchmark needs the reference ERP loaded at scale N
(`python -m reference_erp.seed --reset --scale N`) and the guard installed (`python -m agent_workcell.guard apply`)
in the database that `ERP_DATABASE_URL` points to.

The answers benchmark needs `ERP_DATABASE_URL` (to find each case's person, as the host application would) and the
answer server's settings (see `.env.example`). It starts the answer server itself and deletes its data folder at the
end. `--only` runs some cases, `--repeats` runs each case more than once, `--answer-max-usd` caps each answer.

Times depend on the machine. Compare runs from the same machine, and read small differences as noise.
