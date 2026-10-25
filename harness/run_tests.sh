#!/usr/bin/env bash
# Harness tests (phase 1): compiles each case in tests/cases with Judge.hs,
# runs it (without sandbox) and checks the last line of result.json against
# the "expect" file of the case.
#
# expect format (one key per line):
#   status=WRONG_ANSWER   expected verdict (COMPILE_ERROR: compilation must fail)
#   test=2                index of the failing test (optional)
#   match=<text>          text that must appear in the result or in the error (repeatable)
#   nomatch=<text>        text that must not appear (repeatable)
#   args=<k=v ...>        extra arguments for the binary (optional)
#
# Usage: bash run_tests.sh [filter]          e.g. bash run_tests.sh tle
set -u

GHC=${GHC:-ghc}
HERE=$(cd "$(dirname "$0")" && pwd)
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT

GHC_FLAGS=(-XGHC2021 -O0 -dynamic -hide-all-packages -package base
  -fno-omit-yields -rtsopts=ignoreAll "-with-rtsopts=-M200m -K8m"
  -fdiagnostics-color=never -v0)
DEFAULT_ARGS="timeout_ms=2000 alloc_mb=1000"

"$GHC" --version
passed=0
failed=0

for dir in "$HERE"/tests/cases/*${1:-}*/; do
  name=$(basename "$dir")
  expect="$dir/expect"
  get() { sed -n "s/^$1=//p" "$expect"; }

  d="$W/$name"
  mkdir -p "$d"
  cp "$HERE/Judge.hs" "$HERE/tests/Defs.hs" "$HERE/tests/Tests.hs" "$dir/Student.hs" "$d/"

  want=$(get status | head -n 1)
  errors=()
  got=""

  if (cd "$d" && "$GHC" "${GHC_FLAGS[@]}" -o prog Tests.hs) >"$d/compile.log" 2>&1; then
    if [ "$want" = COMPILE_ERROR ]; then
      errors+=("compiled, but a compilation error was expected")
    else
      # shellcheck disable=SC2046
      (cd "$d" && timeout 60 ./prog $DEFAULT_ARGS $(get args) out=result.json) >"$d/run.log" 2>&1
      got=$(grep '"event":"end"' "$d/result.json" 2>/dev/null | tail -n 1)
      [ -n "$got" ] || errors+=("no \"end\" line in result.json")
      [[ $got == *"\"status\":\"$want\""* ]] || errors+=("expected status $want")
      t=$(get test)
      if [ -n "$t" ]; then
        [[ $got == *"\"test\":$t,"* ]] || errors+=("expected test $t")
      fi
    fi
  else
    got=$(cat "$d/compile.log")
    [ "$want" = COMPILE_ERROR ] || errors+=("does not compile")
  fi

  while IFS= read -r m; do
    [ -n "$m" ] || continue
    [[ $got == *"$m"* ]] || errors+=("missing: $m")
  done < <(get match)

  while IFS= read -r m; do
    [ -n "$m" ] || continue
    [[ $got != *"$m"* ]] || errors+=("should not appear: $m")
  done < <(get nomatch)

  if [ ${#errors[@]} -eq 0 ]; then
    passed=$((passed + 1))
    printf 'OK     %s\n' "$name"
  else
    failed=$((failed + 1))
    printf 'FAIL   %s\n' "$name"
    for e in "${errors[@]}"; do printf '         - %s\n' "$e"; done
    printf '         result: %s\n' "${got:0:800}"
    [ -s "$d/run.log" ] && sed 's/^/         stderr: /' "$d/run.log" | head -n 5
  fi
done

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
