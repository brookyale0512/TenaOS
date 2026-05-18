#!/usr/bin/env bash
# TenaOS repository hygiene guard.
#
# Fails when forbidden files (SQL dumps, runtime artifacts, secrets) are
# tracked by git. Runs in CI as the last line of defense behind .gitignore.

set -euo pipefail

cd "$(dirname "$0")/.."

violations=0

check_pattern() {
  local pattern="$1" description="$2"
  local matches
  matches=$(git ls-files | grep -E "$pattern" || true)
  if [ -n "$matches" ]; then
    echo "ERROR: ${description} should not be committed:" >&2
    echo "$matches" >&2
    violations=$((violations + 1))
  fi
}

check_pattern '\.sql(\.gz)?$' "SQL dumps"
check_pattern '\.dump$' "Database dumps"
check_pattern '^runtime-artifacts/' "Runtime artifacts"
check_pattern '\.pem$|\.key$|\.p12$' "Private keys / certificates"
check_pattern '^\.env$|^.+/\.env$' ".env files (use .env.example)"

if [ "$violations" -gt 0 ]; then
  echo "" >&2
  echo "$violations forbidden path(s) committed. Add to .gitignore and remove from history." >&2
  exit 1
fi

echo "ci-guard ok"
