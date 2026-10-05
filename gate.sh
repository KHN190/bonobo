#!/usr/bin/env bash
# The gates: every gate runs to the end (no failfast), then one summary of each gate
# and every failure; the exit code is non-zero when any gate failed. CPU ≤ 6.
#   1 static_check.py ∥ 2 pyright → 3 runtests.py (all files) → 4 check.run (known violations only fall) → 5 jar tests
# check.run's known violations: check/known.txt, one "INVARIANT COUNT" per line (counts may only fall).
set -u
cd "$(dirname "$0")"
CPUS=6
FUZZ_S=60          # the fuzzer's budget inside the gate (seconds)
JAR="${ANAKA:-$HOME/Desktop/code/minecraft-claude-bridge/anaka}"
OUT="$(mktemp -d)"
declare -a NAMES=() RESULTS=()

note() { NAMES+=("$1"); RESULTS+=("$2"); }

# the main checkout's venv (requirements.txt), found from any worktree; missing is a failed gate, not a fallback
VENV="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.venv"
PY="$VENV/bin/python"
if [ -x "$PY" ]; then
    note venv ok
else
    note venv "FAIL (missing: $VENV; python3 -m venv .venv && PIP_USER=0 .venv/bin/pip install -r requirements.txt)"
    PY=python3
fi

# 1 ∥ 2: the fast ones together
if [ -f static_check.py ]; then
    "$PY" static_check.py >"$OUT/static.txt" 2>&1 &
    STATIC=$!
else
    STATIC=""
fi
npx -y pyright@1.1.414 --pythonpath "$PY" >"$OUT/pyright.txt" 2>&1 &
PYRIGHT=$!

if [ -n "$STATIC" ]; then
    if wait "$STATIC"; then note static_check ok; else note static_check FAIL; fi
else
    echo "static_check.py not found" >"$OUT/static.txt"
    note static_check "FAIL (missing)"
fi
if wait "$PYRIGHT"; then note pyright ok; else note pyright FAIL; fi

# 3: every test file, ≤ CPUS processes (runtests.py caps its pool)
if "$PY" runtests.py >"$OUT/tests.txt" 2>&1; then note runtests ok; else note runtests FAIL; fi

# 4: the checker, after the tests (they would fight for the cores)
if [ -f check/run.py ]; then
    if "$PY" -m check.run 0 "$OUT/check-run.md" "$CPUS" >"$OUT/check.txt" 2>&1; then
        if [ ! -f check/known.txt ]; then
            echo "check/known.txt missing: no baseline to hold the violations to" >>"$OUT/check.txt"
            note check.run "FAIL (no baseline)"
        else
            "$PY" - "$OUT/check-run.md" check/known.txt >>"$OUT/check.txt" 2>&1 <<'PY'
import re, sys
got = {m.group(1): int(m.group(2)) for m in re.finditer(r"^\| (\w+) \| (\d+) \|$", open(sys.argv[1]).read(), re.M)}
known = {k: int(v) for k, v in (ln.split() for ln in open(sys.argv[2]) if ln.strip() and not ln.startswith("#"))}
grew = [f"{k}: {n} > known {known.get(k, 0)}" for k, n in sorted(got.items()) if n > known.get(k, 0)]
print("\n".join(grew) or "no invariant over its known count")
sys.exit(1 if grew else 0)
PY
            if [ $? -eq 0 ]; then note check.run ok; else note check.run "FAIL (new violations)"; fi
        fi
    else
        note check.run FAIL
    fi
else
    echo "check/run.py not found" >"$OUT/check.txt"
    note check.run "FAIL (missing)"
fi

# 4b: the coverage-guided fuzzer, bounded (check/fuzz.py: offline it runs longer and keeps its corpus with --save)
if [ -f check/fuzz.py ]; then
    if "$PY" -m check.fuzz "$FUZZ_S" "$OUT/fuzz.md" >"$OUT/fuzz.txt" 2>&1; then note fuzz ok; else note fuzz "FAIL (new violations)"; fi
else
    note fuzz "FAIL (missing)"
fi

# 5: the jar, when its tree differs from the integration branch
if [ -d "$JAR" ] && { [ -n "$(git -C "$JAR" status --porcelain --untracked-files=no)" ] || \
        ! git -C "$JAR" diff --quiet integrate/inv HEAD -- 2>/dev/null; }; then
    if (cd "$JAR" && JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21}" ./gradlew test -q) >"$OUT/jar.txt" 2>&1; then
        note "jar test" ok
    else
        note "jar test" FAIL
    fi
else
    note "jar test" "skipped (unchanged)"
fi

echo "== gates =="
failed=0
for i in "${!NAMES[@]}"; do
    printf '%-12s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}"
    case "${RESULTS[$i]}" in FAIL*) failed=1 ;; esac
done
if [ "$failed" -ne 0 ]; then
    echo; echo "== failures =="
    for i in "${!NAMES[@]}"; do
        case "${RESULTS[$i]}" in
        FAIL*)
            echo "-- ${NAMES[$i]}"
            case "${NAMES[$i]}" in
            static_check) grep -E "^R[0-9]+: [1-9]|^    |not found" "$OUT/static.txt" ;;
            pyright) grep -E "error|errors" "$OUT/pyright.txt" ;;
            runtests) grep -E "^FAIL|^ERROR|Error:|FAILED" "$OUT/tests.txt" ;;
            check.run) tail -20 "$OUT/check.txt" ;;
            fuzz) tail -5 "$OUT/fuzz.txt"; cat "$OUT/fuzz.md" ;;
            "jar test") grep -vE "^\s+at " "$OUT/jar.txt" | head -40 ;;
            esac
            ;;
        esac
    done
fi
echo; echo "outputs: $OUT"
exit "$failed"
