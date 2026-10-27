"""Run one read-only SQL statement on the ResearchMatch warehouse and print tab-separated rows.

usage: python3 -I sql_readonly.py <file.sql> [schema]
Env: RM_PROFILE (default tom), RM_WAREHOUSE_ID (default: first warehouse in the workspace).
"""
import json
import os
import re
import subprocess
import sys

PROFILE = os.environ.get("RM_PROFILE", "tom")


def cli(*args):
    out = subprocess.run(["databricks", *args, "-p", PROFILE, "-o", "json"], capture_output=True, text=True)
    return json.loads(out.stdout)


sql = re.sub(r"--[^\n]*", "", open(sys.argv[1]).read()).strip().rstrip(";")
if sql.split(None, 1)[0].upper() not in ("SELECT", "WITH", "SHOW", "DESCRIBE"):
    sys.exit("Refused: this helper only runs SELECT/WITH/SHOW/DESCRIBE")
if ";" in re.sub(r"'[^']*'", "", sql):
    sys.exit("Refused: one statement at a time")
warehouse = os.environ.get("RM_WAREHOUSE_ID") or cli("warehouses", "list")[0]["id"]
body = {"warehouse_id": warehouse, "statement": sql, "wait_timeout": "50s", "format": "JSON_ARRAY",
        "catalog": "workspace", "schema": sys.argv[2] if len(sys.argv) > 2 else "researchmatch_silver"}
out = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements", "--json", json.dumps(body),
                      "-p", PROFILE], capture_output=True, text=True).stdout
response = json.loads(out)
status = response["status"]
if status["state"] != "SUCCEEDED":
    sys.exit(f"{status['state']}: {status.get('error')}")
print("\t".join(c["name"] for c in response["manifest"]["schema"]["columns"]))
for row in response.get("result", {}).get("data_array") or []:
    print("\t".join("" if v is None else str(v) for v in row))
