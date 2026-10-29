"""Normalization shared by saved and generated grading artifacts."""
import json
import math
import sqlite3
from contextlib import closing


def normalize_language(value, default="python"):
    """Only an absent language uses the legacy default."""
    if value is None or value == "":
        value = default
    aliases = {"python": "python", "py": "python", "javascript": "javascript", "js": "javascript",
               "node": "javascript", "nodejs": "javascript", "node.js": "javascript",
               "sql": "sql", "sqlite": "sql", "sqlite3": "sql"}
    normalized = aliases.get(value.strip().lower()) if isinstance(value, str) else None
    if normalized is None:
        raise ValueError(f"Unsupported coding language: {value!r}")
    return normalized


def normalize_sql_assertions(value):
    """Accept saved list[str], JSON single object/array; validate with one contract."""
    try:
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list) or not value:
            raise ValueError("no SQL assertions were generated")
        assertions = []
        for index, item in enumerate(value, 1):
            if isinstance(item, str):
                item = json.loads(item)
            if not isinstance(item, dict):
                raise ValueError(f"assertion {index} is not a JSON object")
            query, expected = item.get("query"), item.get("expected")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"assertion {index} requires a nonempty query")
            if not isinstance(expected, list) or any(not isinstance(row, list) for row in expected):
                raise ValueError(f"assertion {index} expected must be a list of rows")
            if any(type(cell) not in (str, int, float, bool, type(None))
                   or (isinstance(cell, float) and not math.isfinite(cell))
                   for row in expected for cell in row):
                raise ValueError(f"assertion {index} row cells must be finite JSON scalar values")
            if "label" in item and not isinstance(item["label"], str):
                raise ValueError(f"assertion {index} label must be text")
            # Compile without running. Only verification SELECTs may execute after learner SQL;
            # an assertion must not create the data it claims the learner supplied. The SQLite
            # authorizer sees actual operations through comments and CTEs, without regex parsing.
            read_query, denied = False, False
            def authorize(action, first, second, database, trigger):
                nonlocal read_query, denied
                if action == sqlite3.SQLITE_SELECT:
                    read_query = True
                allowed = action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                                     sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE)
                if action == sqlite3.SQLITE_FUNCTION and str(second).lower() in ("load_extension", "writefile"):
                    allowed = False
                if not allowed:
                    denied = True
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.set_authorizer(authorize)
                try:
                    connection.execute("EXPLAIN " + query)
                except sqlite3.Error as exc:
                    if denied or not read_query or not any(token in str(exc).lower() for token in ("no such table", "no such column")):
                        raise ValueError(f"assertion {index} query is invalid SQL: {exc}") from exc
                if denied or not read_query:
                    raise ValueError(f"assertion {index} query must be a read-only SELECT")
            assertions.append({"label": item.get("label", f"test_{index}"), "query": query, "expected": expected})
        return assertions
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"SQL assertions are malformed: {exc}") from exc
