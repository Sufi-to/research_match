"""Bounded SQL Statement API adapter for the notebook's read-only Spark-shaped calls.

No Spark session, user SQL, external result URLs, polling loop or write statements.
Only the exact SELECT/DESCRIBE grammar emitted by the mirrored helpers is allowed.
"""
import json
import math
import re

MAX_ROWS = 25001  # Largest EVIDENCE_LIMITS value + 1; the byte limit still applies.


class WarehouseError(RuntimeError):
    # category is a fixed label for user-facing guidance; raw SQL errors are never kept.
    def __init__(self, message, category="failed"):
        super().__init__(message)
        self.category = category


def failure_category(status):
    if status.get("state") in ("PENDING", "RUNNING", "CANCELED", "CLOSED"):
        return "timeout"
    error = status.get("error") or {}
    text = (str(error.get("error_code", "")) + " " + str(error.get("message", ""))).upper()
    if "PERMISSION_DENIED" in text or "INSUFFICIENT_PERMISSIONS" in text:
        return "permission"
    if "NOT_FOUND" in text or "DOES_NOT_EXIST" in text:
        return "not_found"
    return "failed"


class Row(dict):
    def asDict(self, recursive=True):
        return dict(self)


def decode(value, column):
    if value is None:
        return None
    kind = column["type_name"]
    if kind in ("ARRAY", "MAP", "STRUCT"):
        result = json.loads(value) if isinstance(value, str) else value
        wanted = list if kind == "ARRAY" else dict
        if not isinstance(result, wanted):
            raise WarehouseError("Invalid complex SQL value")
        return result
    if kind == "BOOLEAN":
        if value not in ("true", "false", True, False):
            raise WarehouseError("Invalid SQL boolean")
        return value is True or value == "true"
    if kind in ("BYTE", "SHORT", "INT", "LONG"):
        return int(value)
    if kind in ("FLOAT", "DOUBLE", "DECIMAL"):
        result = float(value)
        if not math.isfinite(result):
            raise WarehouseError("Nonfinite SQL number")
        return result
    if kind in ("STRING", "CHAR", "DATE", "TIMESTAMP"):
        return value
    raise WarehouseError("Unsupported SQL result type")


class Frame:
    def __init__(self, warehouse, statement, limit=None):
        self.warehouse, self.statement, self.bound = warehouse, statement, limit

    def limit(self, count):
        if type(count) is not int or not 1 <= count <= MAX_ROWS:
            raise WarehouseError("Invalid pilot row bound")
        return Frame(self.warehouse, self.statement, count)

    def collect(self):
        return self.warehouse.read(self.statement, self.bound)

    def first(self):
        rows = self.warehouse.read(self.statement, 1)
        if not rows:
            raise WarehouseError("Expected a SQL metadata row")
        return rows[0]


class Warehouse:
    def __init__(self, workspace, warehouse_id, cfg):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", warehouse_id or ""):
            raise WarehouseError("Configure the SQL warehouse resource first")
        self.workspace, self.warehouse_id = workspace, warehouse_id
        cat, gold, silver = (cfg[k] for k in ("catalog", "gold", "silver"))
        self.tables = {f"`{cat}`.`{gold}`.`{t}`" for t in (
            "current_build", "researcher_search_documents", "researcher_profile_publications",
            "researcher_profile_topics", "researcher_topic_evidence")}
        self.tables |= {f"`{cat}`.`{silver}`.`{t}`" for t in ("current_build", "identity_decisions")}

    def sql(self, statement):
        return Frame(self, statement)

    def bounded_statement(self, statement, limit):
        target = r"(`\w+`\.`\w+`\.`\w+`)"
        select = re.fullmatch(r"SELECT (\*|[a-z_][a-z0-9_]*(?:, [a-z_][a-z0-9_]*)*) FROM " + target
                             + r"(?: VERSION AS OF ([0-9]+))?(?: WHERE build_id = '([a-f0-9-]{36})')?", statement)
        meta = re.fullmatch(r"DESCRIBE (HISTORY|DETAIL) " + target + r"( LIMIT 1)?", statement)
        table = select.group(2) if select else meta.group(2) if meta else None
        if table not in self.tables:
            raise WarehouseError("SQL is outside the read-only app contract")
        if select:
            if limit is None or not 1 <= limit <= MAX_ROWS:
                raise WarehouseError("SELECT requires a bounded row count")
            return statement + f" LIMIT {limit}", limit
        if meta.group(1) == "HISTORY" and not meta.group(3):
            raise WarehouseError("History must be bounded")
        return statement, 1

    def read(self, statement, limit):
        statement, limit = self.bounded_statement(statement, limit)
        response = self.workspace.api_client.do(method="POST", path="/api/2.0/sql/statements", body={
            "warehouse_id": self.warehouse_id, "statement": statement,
            "format": "JSON_ARRAY", "disposition": "INLINE", "wait_timeout": "50s",
            "on_wait_timeout": "CANCEL", "byte_limit": 16 * 1024 * 1024})
        if response.get("status", {}).get("state") != "SUCCEEDED":
            # Do not expose raw SQL errors, credentials, query contents or response URLs.
            raise WarehouseError("SQL did not complete. Check warehouse status, permissions or quota; retry manually.",
                                 failure_category(response.get("status", {})))
        manifest = response.get("manifest", {})
        columns = manifest.get("schema", {}).get("columns", [])
        names = [c["name"] for c in columns]
        total = manifest.get("total_row_count")
        chunks = manifest.get("total_chunk_count")
        if (manifest.get("format") != "JSON_ARRAY" or manifest.get("truncated")
                or not columns or len(names) != len(set(names))
                or type(total) is not int or not 0 <= total <= limit
                or type(chunks) is not int or not 0 <= chunks <= 16):
            raise WarehouseError("Incomplete or oversized SQL result")
        statement_id = response.get("statement_id", "")
        if not re.fullmatch(r"[a-fA-F0-9-]{36}", statement_id):
            raise WarehouseError("Invalid SQL statement ID")
        rows, byte_count = [], 0
        chunk = response.get("result", {})
        for idx in range(max(1, chunks)):
            if idx:
                # Construct a fixed same-workspace path. Never follow returned URLs.
                chunk = self.workspace.api_client.do(method="GET", path=
                    f"/api/2.0/sql/statements/{statement_id}/result/chunks/{idx}")
            byte_count += len(json.dumps(chunk).encode())
            values = chunk.get("data_array", [])
            if (chunk.get("external_links") or chunk.get("row_offset", 0) != len(rows)
                    or chunk.get("chunk_index", idx) != idx or len(rows) + len(values) > limit
                    or byte_count > 16 * 1024 * 1024
                    or any(len(r) != len(columns) for r in values)):
                raise WarehouseError("Invalid SQL result chunk")
            rows.extend(Row({c["name"]: decode(v, c) for c, v in zip(columns, r)}) for r in values)
        if len(rows) != total:
            raise WarehouseError("SQL result count mismatch")
        return rows
