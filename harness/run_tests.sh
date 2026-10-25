#!/usr/bin/env bash
# Pruebas del harness (fase 1): compila cada caso de tests/cases con Judge.hs,
# lo ejecuta (sin sandbox) y comprueba la línea final de result.json contra el
# fichero "expect" del caso.
#
# Formato de expect (una clave por línea):
#   status=WRONG_ANSWER   veredicto esperado (COMPILE_ERROR: la compilación debe fallar)
#   test=2                índice del test que falla (opcional)
#   match=<texto>         texto que debe aparecer en el resultado o en el error (repetible)
#   nomatch=<texto>       texto que no debe aparecer (repetible)
#   args=<k=v ...>        argumentos extra para el binario (opcional)
#
# Uso: bash run_tests.sh [filtro]          p.ej. bash run_tests.sh tle
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
pass=0
fail=0

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
      errors+=("compiló, pero se esperaba un error de compilación")
    else
      # shellcheck disable=SC2046
      (cd "$d" && timeout 60 ./prog $DEFAULT_ARGS $(get args) out=result.json) >"$d/run.log" 2>&1
      got=$(grep '"event":"end"' "$d/result.json" 2>/dev/null | tail -n 1)
      [ -n "$got" ] || errors+=("no hay línea \"end\" en result.json")
      [[ $got == *"\"status\":\"$want\""* ]] || errors+=("se esperaba status $want")
      t=$(get test)
      if [ -n "$t" ]; then
        [[ $got == *"\"test\":$t,"* ]] || errors+=("se esperaba test $t")
      fi
    fi
  else
    got=$(cat "$d/compile.log")
    [ "$want" = COMPILE_ERROR ] || errors+=("no compila")
  fi

  while IFS= read -r m; do
    [ -n "$m" ] || continue
    [[ $got == *"$m"* ]] || errors+=("no aparece: $m")
  done < <(get match)

  while IFS= read -r m; do
    [ -n "$m" ] || continue
    [[ $got != *"$m"* ]] || errors+=("no debería aparecer: $m")
  done < <(get nomatch)

  if [ ${#errors[@]} -eq 0 ]; then
    pass=$((pass + 1))
    printf 'OK     %s\n' "$name"
  else
    fail=$((fail + 1))
    printf 'FALLO  %s\n' "$name"
    for e in "${errors[@]}"; do printf '         - %s\n' "$e"; done
    printf '         resultado: %s\n' "${got:0:800}"
    [ -s "$d/run.log" ] && sed 's/^/         stderr: /' "$d/run.log" | head -n 5
  fi
done

echo
echo "$pass correctos, $fail fallidos"
[ "$fail" -eq 0 ]
