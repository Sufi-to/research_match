#!/bin/sh
# Scan commits that are about to be pushed for personal data, workspace details and secrets.
# usage: check_push.sh [base=origin/main] [head=HEAD]
BASE=${1:-origin/main}; HEAD=${2:-HEAD}
git fetch -q origin 2>/dev/null || true
PATTERN='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|dbc-[0-9a-f]{6,}|cloud\.databricks\.com|databricksapps\.com|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|dapi[0-9a-f]{20,}|ghp_[A-Za-z0-9]{20,}|OPENALEX_API_KEY=[^ ]+'
# Placeholders and long-standing public IDs that are fine to publish.
ALLOW=':PATTERN=|dbc-…cloud|@example\.com|pass@doi\.org|@anthropic\.com|<you>|9c1571ed-a5fb-45e3-8f5f-b066801e7cd5|3230bb0f-28c7-4830-90b5-5a21034794cb|a5f7a124-|5fb6b85e-|b239f898-|6fcc7472-|d1875e1e-|12345678-1234-1234-1234-123456789012'
HITS=$(git diff "$BASE" "$HEAD" | grep '^+' | sed 's/^+//' | grep -n -E "$PATTERN" | grep -v -E "$ALLOW")
MSGS=$(git log --format='%B' "$BASE..$HEAD" | grep -n -E "$PATTERN" | grep -v -E "$ALLOW")
echo "Commits: $(git rev-list --count "$BASE..$HEAD")  Authors/committers:"
git log --format='  %ae / %ce' "$BASE..$HEAD" | sort -u
if [ -n "$HITS$MSGS" ]; then
  echo "Possible leaks in added lines or commit messages:"
  [ -n "$HITS" ] && echo "$HITS" | cut -c1-200
  [ -n "$MSGS" ] && echo "$MSGS" | cut -c1-200
  exit 1
fi
echo "No personal data, workspace details or secrets found."
