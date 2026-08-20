#!/usr/bin/env bash
# The whole suite. The Python and JS tests need nothing beyond ComfyUI's own deps;
# the E2E tests need playwright (cleanly skipped when it is missing).
set -u
cd "$(dirname "$0")/.."
pass=0; fail=0; skip=0

run() {  # run <label> <command...> ; exit code 77 = the test skips itself
  out=$("${@:2}" 2>&1); rc=$?
  if [ $rc -eq 0 ]; then
    echo "  ✓ $1"; pass=$((pass+1))
  elif [ $rc -eq 77 ]; then
    echo "  – $1 : $(echo "$out" | tail -1)"; skip=$((skip+1))
  else
    echo "  ✗ $1"; echo "$out" | tail -15 | sed 's/^/      /'; fail=$((fail+1))
  fi
}

echo "Python"
for t in tests/test_*.py; do run "$(basename "$t")" python3 "$t"; done

echo "JavaScript"
run "relink.test.js" node tests/js/relink.test.js

echo "Browser (Playwright)"
if python3 -c "import playwright" 2>/dev/null; then
  for t in tests/e2e/test_*.py; do run "$(basename "$t")" python3 "$t"; done
else
  echo "  – playwright missing, E2E skipped (pip install playwright && playwright install chromium)"
  skip=$((skip+1))
fi

echo
echo "$pass passed, $fail failed, $skip skipped"
[ "$fail" -eq 0 ]
