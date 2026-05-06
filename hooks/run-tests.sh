#!/usr/bin/env bash
# Run all hook tests. Exit 0 only if ALL pass.
# Stays under 30s on a healthy box.

set -u

cd "$(dirname "$0")"

PASS=0
FAIL=0
FAILED_FILES=()

START=$(date +%s)

for t in test_*.py; do
    # Skip the helpers module if it ever matched; it doesn't, but be safe.
    [ "$t" = "test_helpers.py" ] && continue
    out=$(python "$t" 2>&1)
    rc=$?
    if [ $rc -eq 0 ]; then
        PASS=$((PASS + 1))
        printf "PASS %s\n" "$t"
    else
        FAIL=$((FAIL + 1))
        FAILED_FILES+=("$t")
        printf "FAIL %s\n" "$t"
        printf "%s\n" "$out" | sed 's/^/    /'
    fi
done

END=$(date +%s)
ELAPSED=$((END - START))

echo
echo "----------------------------------------"
echo "Hook tests: $PASS passed, $FAIL failed (${ELAPSED}s)"
if [ $FAIL -gt 0 ]; then
    echo "Failed files:"
    for f in "${FAILED_FILES[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
exit 0
