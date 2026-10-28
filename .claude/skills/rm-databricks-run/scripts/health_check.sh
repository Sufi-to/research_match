#!/bin/sh
# Read-only health check for the things Free Edition breaks silently:
# deleted/offline endpoint, stale or unready index, stopped app, shortened retention.
# usage: health_check.sh   (exit 1 if any check fails)
PROFILE=${RM_PROFILE:-tom}
APP=${RM_APP:-researchmatch-pilot}
ENDPOINT=vs_endpoint_1
INDEX=workspace.researchmatch_gold.researcher_profiles_index
SOURCE=workspace.researchmatch_gold.researcher_search_documents
DIR=$(dirname "$0")
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
FAIL=0
report() { if [ "$2" = ok ]; then echo "PASS  $1"; else echo "FAIL  $1: $2"; FAIL=1; fi; }
field() { python3 -c "import json,sys
try: d=json.load(sys.stdin)
except Exception: print('unreadable'); sys.exit()
for k in sys.argv[1].split('.'): d=(d or {}).get(k) if isinstance(d,dict) else None
print(d)" "$1"; }
sql() { echo "$1" > "$TMP/q.sql"; python3 -I "$DIR/sql_readonly.py" "$TMP/q.sql" researchmatch_gold 2>&1 | tail -n +2; }

STATE=$(databricks vector-search-endpoints get-endpoint "$ENDPOINT" -p "$PROFILE" -o json 2>/dev/null | field endpoint_status.state)
[ "$STATE" = ONLINE ] && report "endpoint $ENDPOINT online" ok || report "endpoint $ENDPOINT" "state $STATE (missing endpoints read as None; see references/rebuild.md)"

INDEX_JSON=$(databricks vector-search-indexes get-index "$INDEX" -p "$PROFILE" -o json 2>/dev/null)
READY=$(echo "$INDEX_JSON" | field status.ready)
ROWS=$(echo "$INDEX_JSON" | field status.indexed_row_count)
MODEL=$(echo "$INDEX_JSON" | python3 -c "import json,sys
try: print(json.load(sys.stdin)['delta_sync_index_spec']['embedding_source_columns'][0]['embedding_model_endpoint_name'])
except Exception: print(None)")
[ "$READY" = True ] && report "index ready" ok || report "index ready" "ready=$READY"
[ "$MODEL" = databricks-gte-large-en ] && report "index model $MODEL" ok || report "index model" "$MODEL"

DOCS=$(sql "SELECT document_count FROM workspace.researchmatch_gold.current_build")
[ -n "$DOCS" ] && [ "$ROWS" = "$DOCS" ] && report "index rows $ROWS = Gold document_count" ok \
  || report "index rows vs Gold document_count" "index $ROWS, Gold $DOCS (sync the index with notebook 12)"

RETENTION=$(sql "SHOW TBLPROPERTIES $SOURCE" | grep -c "Duration	interval 365 days")
[ "$RETENTION" = 2 ] && report "source keeps 365 days of history" ok \
  || report "source retention" "expected log and deleted-file retention of 365 days (sql/09)"

APP_JSON=$(databricks apps get "$APP" -p "$PROFILE" -o json 2>/dev/null)
COMPUTE=$(echo "$APP_JSON" | field compute_status.state)
RUNNING=$(echo "$APP_JSON" | field app_status.state)
[ "$COMPUTE" = ACTIVE ] && [ "$RUNNING" = RUNNING ] && report "app $APP running" ok \
  || report "app $APP" "compute $COMPUTE, app $RUNNING (Free Edition stops apps after ~24 h; start it)"

exit $FAIL
