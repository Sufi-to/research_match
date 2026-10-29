#!/bin/sh
# Upload app/ to the workspace source folder, verify it byte-for-byte, then redeploy.
# usage: deploy_app.sh [--check]   (--check: verify only, no upload/deploy)
set -e
PROFILE=${RM_PROFILE:-tom}
APP=${RM_APP:-researchmatch-pilot}
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT/app"
ME=$(databricks current-user me -p "$PROFILE" -o json | python3 -c "import json,sys;print(json.load(sys.stdin)['userName'])")
DEST=/Users/$ME/researchmatch-app
FILES=$(git ls-files --cached --others --exclude-standard | grep -v -e '^\.gitignore$' -e '__pycache__')
if [ "$1" != "--check" ]; then
  for f in $FILES; do
    databricks workspace mkdirs "$DEST/$(dirname "$f")" -p "$PROFILE" >/dev/null 2>&1 || true
    databricks workspace import "$DEST/$f" --file "$f" --format AUTO --overwrite -p "$PROFILE" >/dev/null
  done
fi
TMP=$(mktemp -d); BAD=0
for f in $FILES; do
  mkdir -p "$TMP/$(dirname "$f")"
  KIND=$(databricks workspace get-status "$DEST/$f" -p "$PROFILE" -o json 2>/dev/null \
         | python3 -c "import json,sys;print(json.load(sys.stdin)['object_type'])" 2>/dev/null || echo MISSING)
  databricks workspace export "$DEST/$f" --format AUTO -p "$PROFILE" --file "$TMP/$f" >/dev/null 2>&1 || true
  if [ "$KIND" = FILE ] && cmp -s "$f" "$TMP/$f"; then echo "ok    $f"; else echo "BAD   $f ($KIND)"; BAD=1; fi
done
rm -rf "$TMP"
[ $BAD = 0 ] || { echo "Workspace copy does not match the repo; not deploying."; exit 1; }
[ "$1" = "--check" ] && exit 0
# Free Edition stops apps after ~24 hours; a stopped app cannot be deployed to.
STATE=$(databricks apps get "$APP" -p "$PROFILE" -o json | python3 -c "import json,sys;print(json.load(sys.stdin).get('compute_status',{}).get('state'))")
if [ "$STATE" != ACTIVE ]; then
  echo "App compute is $STATE; starting it first."
  databricks apps start "$APP" -p "$PROFILE" -o json --timeout 20m >/dev/null
fi
databricks apps deploy "$APP" --source-code-path "/Workspace$DEST" -p "$PROFILE" -o json --timeout 20m >/dev/null || true
# The deploy command's stdout has come back empty even when the deployment succeeded,
# so report the app's active deployment instead of parsing it.
databricks apps get "$APP" -p "$PROFILE" -o json | python3 -c "
import json,sys
d=json.load(sys.stdin); a=d.get('active_deployment') or {}; s=a.get('status') or {}
print('deployment', a.get('deployment_id'), a.get('create_time'), s.get('state'), '| app', (d.get('app_status') or {}).get('state'))
sys.exit(0 if s.get('state') == 'SUCCEEDED' else 1)"
