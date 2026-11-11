"""The data guard: the database decides what the agent may read.

- plan.py turns a policy into the SQL a database administrator installs once.
- connection.py binds a person to an agent connection and runs one read-only query with caps.
- doctor.py checks that the installed guard still matches the policy, and that the agent role can do nothing more.
"""

from agent_workcell.guard.connection import GuardedConnection, GuardError, Limits, Principal, QueryError, QueryResult
from agent_workcell.guard.doctor import Problem, check
from agent_workcell.guard.plan import Names, plan_sql

__all__ = [
    "GuardError",
    "GuardedConnection",
    "Limits",
    "Names",
    "Principal",
    "Problem",
    "QueryError",
    "QueryResult",
    "check",
    "plan_sql",
]
