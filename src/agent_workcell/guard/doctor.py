"""Check that the installed guard matches the policy, and that the agent role can do nothing more than read it.

Run it at start-up. Any problem with level "error" means the service must not start. The checks read the system
catalogs, so any login role can run them; they do not trust what the guard itself reports.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_workcell.guard.plan import DEFAULT_NAMES, Names, installed_marker
from agent_workcell.policy import Policy

SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")
TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"
WRITE_PRIVILEGES = "INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"


@dataclass(frozen=True)
class Problem:
    level: str  # "error" stops the service; "warning" is reported
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


def check(conn, policy: Policy, names: Names = DEFAULT_NAMES) -> list[Problem]:
    problems: list[Problem] = []
    problems += _roles(conn, names)
    if any(problem.message.startswith("role ") and "does not exist" in problem.message for problem in problems):
        return problems
    problems += _installed(conn, policy, names)
    problems += _columns(conn, policy)
    problems += _agent_privileges(conn, names)
    problems += _binder_privileges(conn, names)
    return problems


def errors(problems: list[Problem]) -> list[Problem]:
    return [problem for problem in problems if problem.level == "error"]


def _rows(conn, query: str, params: dict | None = None) -> list[tuple]:
    return conn.execute(query, params or {}).fetchall()


def _roles(conn, names: Names) -> list[Problem]:
    problems = []
    expected_login = {names.guard_role: False, names.agent_role: True, names.binder_role: True}
    found = {row[0]: row[1:] for row in _rows(conn, """
        SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
        FROM pg_catalog.pg_roles WHERE rolname = ANY(%(names)s)
    """, {"names": list(expected_login)})}

    for role, login in expected_login.items():
        if role not in found:
            problems.append(Problem("error", f"role {role} does not exist. Install the guard (guard plan)."))
            continue
        can_login, *powers = found[role]
        if can_login != login:
            problems.append(Problem("error", f"role {role} must {'' if login else 'not '}be able to log in"))
        for power, name in zip(powers, ("SUPERUSER", "CREATEDB", "CREATEROLE", "REPLICATION", "BYPASSRLS"),
                               strict=True):
            if power:
                problems.append(Problem("error", f"role {role} has {name}"))

    for member, granted in _rows(conn, """
        SELECT m.rolname, r.rolname
        FROM pg_catalog.pg_auth_members a
        JOIN pg_catalog.pg_roles m ON m.oid = a.member
        JOIN pg_catalog.pg_roles r ON r.oid = a.roleid
        WHERE m.rolname = ANY(%(names)s)
    """, {"names": [names.agent_role, names.binder_role]}):
        problems.append(Problem("error", f"role {member} is a member of {granted}, and so has its rights"))
    return problems


def _installed(conn, policy: Policy, names: Names) -> list[Problem]:
    marker = _rows(conn, """
        SELECT pg_catalog.obj_description(n.oid, 'pg_namespace'), r.rolname
        FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid = n.nspowner
        WHERE n.nspname = %(schema)s
    """, {"schema": names.views})
    if not marker:
        return [Problem("error", f"schema {names.views} does not exist. Install the guard (guard plan).")]

    problems = []
    if marker[0][0] != installed_marker(policy):
        problems.append(Problem("error", "the installed guard was made from a different policy. Run guard plan again."))

    relations = {row[0]: row[1:] for row in _rows(conn, """
        SELECT c.relname, c.relkind, pg_catalog.pg_get_userbyid(c.relowner), c.reloptions
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %(schema)s
    """, {"schema": names.views})}

    for name in sorted(set(relations) - set(policy.tables)):
        problems.append(Problem("error", f"{names.views}.{name} is not in the policy"))
    for name in sorted(set(policy.tables) - set(relations)):
        problems.append(Problem("error", f"{names.views}.{name} is missing"))

    for name in sorted(set(relations) & set(policy.tables)):
        kind, owner, options = relations[name]
        if kind != "v":
            problems.append(Problem("error", f"{names.views}.{name} is not a view"))
        if owner != names.guard_role:
            problems.append(Problem("error", f"{names.views}.{name} is owned by {owner}, not {names.guard_role}"))
        if "security_barrier=true" not in (options or []):
            problems.append(Problem("error", f"{names.views}.{name} is not a security_barrier view"))
        # Catalog joins, not to_regclass(): that needs USAGE on the schema, and the doctor runs as any login role.
        columns = [row[0] for row in _rows(conn, """
            SELECT a.attname FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %(schema)s AND c.relname = %(view)s AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """, {"schema": names.views, "view": name})]
        if columns != list(policy.tables[name].visible):
            problems.append(Problem("error", f"{names.views}.{name} has columns {columns}, but the policy makes "
                                             f"visible {list(policy.tables[name].visible)}"))
    return problems


def _columns(conn, policy: Policy) -> list[Problem]:
    """Every column of a policy table must be marked visible or hidden. Unmarked columns stay hidden."""
    live: dict[str, list[str]] = {}
    for table, column in _rows(conn, """
        SELECT c.relname, a.attname
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname = %(schema)s AND c.relname = ANY(%(tables)s)
        ORDER BY c.relname, a.attnum
    """, {"schema": policy.schema, "tables": list(policy.tables)}):
        live.setdefault(table, []).append(column)

    problems = []
    for name, table in policy.tables.items():
        if name not in live:
            problems.append(Problem("error", f"table {policy.schema}.{name} is in the policy but not in the database"))
            continue
        marked = set(table.visible) | set(table.hidden)
        for column in sorted(marked - set(live[name])):
            problems.append(Problem("error", f"{name}.{column} is in the policy but not in the database"))
        for column in live[name]:
            if column not in marked:
                problems.append(Problem("warning", f"{name}.{column} is not marked visible or hidden, so it is hidden. "
                                                   "Mark it in the policy."))
    return problems


def _agent_privileges(conn, names: Names) -> list[Problem]:
    problems = []
    params = {"role": names.agent_role, "views": names.views, "system": list(SYSTEM_SCHEMAS)}

    for schema, name, kind in _rows(conn, f"""
        SELECT n.nspname, c.relname, c.relkind
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname <> ALL(%(system)s) AND n.nspname NOT LIKE 'pg\\_temp\\_%%'
          AND n.nspname NOT LIKE 'pg\\_toast\\_temp\\_%%'
          AND (
            (c.relkind IN ('r', 'p', 'v', 'm', 'f')
             AND (CASE WHEN n.nspname = %(views)s
                       THEN pg_catalog.has_table_privilege(%(role)s, c.oid, '{WRITE_PRIVILEGES}')
                       ELSE pg_catalog.has_table_privilege(%(role)s, c.oid, '{TABLE_PRIVILEGES}')
                         OR pg_catalog.has_any_column_privilege(%(role)s, c.oid, 'SELECT, INSERT, UPDATE, REFERENCES')
                  END))
            OR (c.relkind = 'S' AND pg_catalog.has_sequence_privilege(%(role)s, c.oid, 'USAGE, SELECT, UPDATE'))
          )
        ORDER BY 1, 2
    """, params):  # noqa: S608 - only fixed privilege lists are formatted in
        problems.append(Problem("error", f"{names.agent_role} has rights on {schema}.{name} (kind {kind})"))

    for (schema,) in _rows(conn, """
        SELECT n.nspname FROM pg_catalog.pg_namespace n
        WHERE pg_catalog.has_schema_privilege(%(role)s, n.oid, 'CREATE')
    """, params):
        problems.append(Problem("error", f"{names.agent_role} can create objects in schema {schema}"))

    if _rows(conn, """
        SELECT pg_catalog.has_schema_privilege(%(role)s, n.oid, 'USAGE')
        FROM pg_catalog.pg_namespace n WHERE n.nspname = %(internal)s
    """, {**params, "internal": names.internal}) == [(True,)]:
        problems.append(Problem("error", f"{names.agent_role} can use schema {names.internal}"))

    if _rows(conn, "SELECT pg_catalog.has_database_privilege(%(role)s, current_database(), 'CREATE')", params)[0][0]:
        problems.append(Problem("error", f"{names.agent_role} can create schemas in this database"))

    for (function,) in _rows(conn, """
        SELECT p.oid::regprocedure::text FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = %(internal)s AND pg_catalog.has_function_privilege(%(role)s, p.oid, 'EXECUTE')
    """, {**params, "internal": names.internal}):
        problems.append(Problem("error", f"{names.agent_role} can run {function}"))

    for (kind, name) in _rows(conn, """
        SELECT 'relation', c.relname FROM pg_catalog.pg_class c WHERE c.relowner = %(role)s::regrole
        UNION ALL SELECT 'schema', n.nspname FROM pg_catalog.pg_namespace n WHERE n.nspowner = %(role)s::regrole
        UNION ALL SELECT 'function', p.proname FROM pg_catalog.pg_proc p WHERE p.proowner = %(role)s::regrole
    """, params):
        problems.append(Problem("error", f"{names.agent_role} owns {kind} {name}"))
    return problems


def _binder_privileges(conn, names: Names) -> list[Problem]:
    problems = []
    params = {"role": names.binder_role, "system": list(SYSTEM_SCHEMAS), "internal": names.internal}

    for schema, name in _rows(conn, f"""
        SELECT n.nspname, c.relname
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname <> ALL(%(system)s) AND n.nspname NOT LIKE 'pg\\_temp\\_%%'
          AND n.nspname NOT LIKE 'pg\\_toast\\_temp\\_%%'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
          AND (pg_catalog.has_table_privilege(%(role)s, c.oid, '{TABLE_PRIVILEGES}')
               OR pg_catalog.has_any_column_privilege(%(role)s, c.oid, 'SELECT, INSERT, UPDATE, REFERENCES'))
    """, params):  # noqa: S608 - only a fixed privilege list is formatted in
        problems.append(Problem("error", f"{names.binder_role} has rights on {schema}.{name}"))

    for (function,) in _rows(conn, """
        SELECT p.oid::regprocedure::text FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = %(internal)s AND p.proname NOT IN ('bind', 'unbind')
          AND pg_catalog.has_function_privilege(%(role)s, p.oid, 'EXECUTE')
    """, params):
        problems.append(Problem("error", f"{names.binder_role} can run {function}"))
    return problems
