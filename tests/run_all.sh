#!/usr/bin/env bash
# Run every unit test (tests/test_*.py, offline, no model calls) and print one line each; exit 1 if any fails.
#   tests/run_all.sh
cd "$(dirname "$0")/.." || exit 1
fail=0
for t in tests/test_*.py; do
  out=$(timeout 300 uv run python "$t" 2>&1); rc=$?
  if [ $rc -eq 0 ]; then echo "ok    $t"; else echo "FAIL  $t: $(echo "$out" | tail -1)"; fail=1; fi
done
exit $fail
