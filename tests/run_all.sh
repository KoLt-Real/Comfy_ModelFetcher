#!/usr/bin/env bash
# The whole suite. The Python and JS tests need nothing beyond ComfyUI's own deps;
# the E2E tests need playwright, and are skipped when it is missing.
#
# A skipped test is NOT a passing test. Pass --strict (what CI should do) to make
# any skip fail the run, so a missing dependency can never read as a green suite.
set -u
strict=0
# An unrecognised argument must be an error, not a silent non-strict run: a typo like
# --stict exiting 0 with skips is exactly the "quietly never ran" outcome --strict exists
# to prevent.
if [ $# -gt 0 ]; then
  if [ "$1" = "--strict" ] && [ $# -eq 1 ]; then
    strict=1
  else
    echo "usage: $0 [--strict]" >&2
    exit 2
  fi
fi
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
# Probed through sync_api, not the bare package: `import playwright` succeeds even when
# greenlet (its real dependency) is unusable, which turned the promised clean skip into
# three FAILs. The browser binary itself is still only checked at launch.
if python3 -c "import playwright.sync_api" 2>/dev/null; then
  for t in tests/e2e/test_*.py; do run "$(basename "$t")" python3 "$t"; done
else
  for t in tests/e2e/test_*.py; do
    echo "  – $(basename "$t") : skipped, playwright missing (pip install playwright && playwright install chromium)"
    skip=$((skip+1))
  done
fi

echo
echo "$pass passed, $fail failed, $skip skipped"
if [ "$skip" -gt 0 ]; then
  echo "  ! $skip test(s) never ran — this suite did NOT cover them."
  [ "$strict" -eq 1 ] && exit 1
fi
[ "$fail" -eq 0 ]
