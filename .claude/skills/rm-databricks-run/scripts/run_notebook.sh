#!/bin/sh
# Run a ResearchMatch workspace notebook as a one-time serverless job and print its output.
# usage: run_notebook.sh <label> <notebook name> '<json base_parameters>'
set -e
LABEL=$1; NB=$2; PARAMS=${3:-'{}'}
PROFILE=${RM_PROFILE:-tom}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${TMPDIR:-/tmp}/rm-runs; mkdir -p "$WORK"
ME=$(databricks current-user me -p "$PROFILE" -o json | python3 -c "import json,sys;print(json.load(sys.stdin)['userName'])")
cat > "$WORK/submit_$LABEL.json" <<J
{"run_name": "researchmatch-$LABEL",
 "tasks": [{"task_key": "nb", "notebook_task": {"notebook_path": "/Users/$ME/research_match/notebooks/$NB", "base_parameters": $PARAMS}}]}
J
# submit blocks until the run ends; it exits non-zero on failure, which we report below.
databricks jobs submit --json @"$WORK/submit_$LABEL.json" -p "$PROFILE" -o json --timeout 60m > "$WORK/run_$LABEL.json" 2>&1 || true
RID=$(databricks jobs list-runs -p "$PROFILE" -o json --limit 10 | python3 -c "
import json,sys
print(next(r['run_id'] for r in json.load(sys.stdin) if r['run_name']=='researchmatch-$LABEL'))")
TASK=$(databricks jobs get-run "$RID" -p "$PROFILE" -o json | python3 -c "
import json,sys
d=json.load(sys.stdin); s=d['state']
print('RUN', d['run_id'], s.get('result_state'), s.get('state_message', ''), file=sys.stderr)
print(d['tasks'][0]['run_id'])")
databricks jobs export-run "$TASK" --views-to-export CODE -p "$PROFILE" -o json > "$WORK/export_$LABEL.json"
python3 -I "$HERE/notebook_output.py" "$WORK/export_$LABEL.json"
