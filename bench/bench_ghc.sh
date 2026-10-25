#!/usr/bin/env bash
# Benchmark de GHC para dimensionar el ejecutor de Haskell.
#
# Mide tiempo (mediana de N ejecuciones) y RAM pico de:
#   - interpretar con GHCi (ghc -e)       con harness propio y, si está instalado, con Hspec
#   - compilar -O0 (enlace estático)      y ejecutar el binario
#   - compilar -O0 -dynamic               y ejecutar el binario
#   - un caso "pesado" para comparar la velocidad interpretado vs compilado
#   - ráfagas concurrentes de 30 envíos con 3 y 4 workers
#
# Uso:  bash bench_ghc.sh
#       N=20 GHC=ghc-9.8.4 bash bench_ghc.sh
# Requiere GNU time (/usr/bin/time; en Debian/Ubuntu: apt install time).
set -u

N=${N:-10}
GHC=${GHC:-ghc}
TIME=/usr/bin/time

command -v "$GHC" >/dev/null || { echo "No encuentro $GHC"; exit 1; }
[ -x "$TIME" ] || { echo "Falta GNU time ($TIME): sudo apt install time"; exit 1; }

W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
cd "$W" || exit 1

# ---------------------------------------------------------------- ficheros Haskell

cat > Defs.hs <<'EOF'
{-# LANGUAGE Trustworthy #-}
module Defs where

import Control.DeepSeq (NFData (..))

data Arbol a = Hoja | Nodo (Arbol a) a (Arbol a)
  deriving (Show, Eq)

instance NFData a => NFData (Arbol a) where
  rnf Hoja         = ()
  rnf (Nodo i x d) = rnf i `seq` rnf x `seq` rnf d
EOF

cat > Student.hs <<'EOF'
{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

suma :: [Int] -> Int
suma []     = 0
suma (x:xs) = x + suma xs

inversa :: [a] -> [a]
inversa = foldl (flip (:)) []

esPrimo :: Int -> Bool
esPrimo n = n > 1 && all (\d -> n `mod` d /= 0) (takeWhile (\d -> d * d <= n) [2 ..])

primos :: Int -> [Int]
primos n = filter esPrimo [2 .. n]

insertar :: Ord a => a -> Arbol a -> Arbol a
insertar x Hoja = Nodo Hoja x Hoja
insertar x t@(Nodo i y d)
  | x < y     = Nodo (insertar x i) y d
  | x > y     = Nodo i y (insertar x d)
  | otherwise = t

enOrden :: Arbol a -> [a]
enOrden Hoja         = []
enOrden (Nodo i x d) = enOrden i ++ [x] ++ enOrden d

fromList :: Ord a => [a] -> Arbol a
fromList = foldr insertar Hoja
EOF

cat > Judge.hs <<'EOF'
{-# LANGUAGE ExistentialQuantification #-}
module Judge (Test (..), runTests) where

import Control.DeepSeq (NFData, force)
import Control.Exception (SomeException, evaluate, try)
import System.Mem (disableAllocationLimit, enableAllocationLimit, setAllocationCounter)
import System.Timeout (timeout)

data Test = forall a. (Eq a, Show a, NFData a) => Test String a a

tryAny :: IO a -> IO (Either SomeException a)
tryAny = try

runOne :: Test -> IO Bool
runOne (Test _ got expected) = do
  setAllocationCounter 10000000000
  enableAllocationLimit
  r <- timeout 10000000 (tryAny (evaluate (force got)))
  disableAllocationLimit
  pure $ case r of
    Just (Right v) -> v == expected
    _              -> False

runTests :: [Test] -> IO ()
runTests ts = do
  ok <- mapM runOne ts
  putStrLn (show (length (filter id ok)) ++ "/" ++ show (length ts) ++ " tests OK")
EOF

cat > Light.hs <<'EOF'
import Defs
import Judge
import Student

main :: IO ()
main = runTests
  [ Test "suma []" (suma []) 0
  , Test "suma [1,2,3]" (suma [1, 2, 3]) 6
  , Test "suma [1..100]" (suma [1 .. 100]) 5050
  , Test "inversa \"\"" (inversa "") ""
  , Test "inversa \"hola\"" (inversa "hola") "aloh"
  , Test "inversa [1..5]" (inversa [1 .. 5 :: Int]) [5, 4, 3, 2, 1]
  , Test "esPrimo 1" (esPrimo 1) False
  , Test "esPrimo 2" (esPrimo 2) True
  , Test "esPrimo 97" (esPrimo 97) True
  , Test "esPrimo 91" (esPrimo 91) False
  , Test "primos 30" (primos 30) [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
  , Test "enOrden Hoja" (enOrden (Hoja :: Arbol Int)) []
  , Test "enOrden (fromList [3,1,2])" (enOrden (fromList [3, 1, 2 :: Int])) [1, 2, 3]
  , Test "insertar 1 Hoja" (insertar 1 Hoja) (Nodo Hoja (1 :: Int) Hoja)
  , Test "enOrden (fromList \"haskell\")" (enOrden (fromList "haskell")) "aehkls"
  ]
EOF

cat > Heavy.hs <<'EOF'
import Judge
import Student

main :: IO ()
main = runTests
  [ Test "length (primos 200000)" (length (primos 200000)) 17984
  , Test "suma [1..1000000]" (suma [1 .. 1000000]) 500000500000
  , Test "length (enOrden (fromList [1..2000]))" (length (enOrden (fromList [1 .. 2000 :: Int]))) 2000
  ]
EOF

cat > SpecLight.hs <<'EOF'
import Defs
import Student
import Test.Hspec

main :: IO ()
main = hspec $ do
  describe "suma" $ do
    it "suma []" $ suma [] `shouldBe` 0
    it "suma [1,2,3]" $ suma [1, 2, 3] `shouldBe` 6
    it "suma [1..100]" $ suma [1 .. 100] `shouldBe` 5050
  describe "inversa" $ do
    it "inversa \"\"" $ inversa "" `shouldBe` ""
    it "inversa \"hola\"" $ inversa "hola" `shouldBe` "aloh"
    it "inversa [1..5]" $ inversa [1 .. 5 :: Int] `shouldBe` [5, 4, 3, 2, 1]
  describe "primos" $ do
    it "esPrimo 1" $ esPrimo 1 `shouldBe` False
    it "esPrimo 2" $ esPrimo 2 `shouldBe` True
    it "esPrimo 97" $ esPrimo 97 `shouldBe` True
    it "esPrimo 91" $ esPrimo 91 `shouldBe` False
    it "primos 30" $ primos 30 `shouldBe` [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
  describe "arbol" $ do
    it "enOrden Hoja" $ enOrden (Hoja :: Arbol Int) `shouldBe` []
    it "enOrden (fromList [3,1,2])" $ enOrden (fromList [3, 1, 2 :: Int]) `shouldBe` [1, 2, 3]
    it "insertar 1 Hoja" $ insertar 1 Hoja `shouldBe` Nodo Hoja (1 :: Int) Hoja
    it "enOrden (fromList \"haskell\")" $ enOrden (fromList "haskell") `shouldBe` "aehkls"
EOF

# ---------------------------------------------------------------- utilidades

median() {
  sort -n | awk '{a[NR] = $1} END {if (NR % 2) print a[(NR + 1) / 2]; else print (a[NR / 2] + a[NR / 2 + 1]) / 2}'
}

# measure NOMBRE COMANDO : 1 ejecución de calentamiento + N medidas; mediana de tiempo y RAM pico
measure() {
  local name=$1 cmd=$2
  if ! bash -c "$cmd" >/dev/null 2>err.txt; then
    printf '%-42s FALLO\n' "$name"
    sed 's/^/    /' err.txt | head -20
    return 1
  fi
  : > times.txt
  for _ in $(seq "$N"); do
    $TIME -f '%e %M' -o t.txt bash -c "$cmd" >/dev/null 2>&1
    tail -n 1 t.txt >> times.txt
  done
  local t m
  t=$(cut -d' ' -f1 times.txt | median)
  m=$(cut -d' ' -f2 times.txt | median)
  awk -v n="$name" -v t="$t" -v m="$m" 'BEGIN {printf "%-42s %6.2f s  %6d MB\n", n, t, m / 1024}'
}

# burst P TOTAL ETIQUETA COMANDO : lanza TOTAL envíos con P en paralelo, cada uno en su directorio
burst() {
  local p=$1 total=$2 label=$3 cmd=$4
  rm -rf burst && mkdir burst
  for i in $(seq "$total"); do
    mkdir "burst/$i" && cp Defs.hs Student.hs Judge.hs Light.hs "burst/$i/"
  done
  local t0 t1
  t0=$(date +%s.%N)
  seq "$total" | xargs -P "$p" -I{} bash -c "cd burst/{} && { { $cmd; } >/dev/null 2>&1 || touch FAIL; }"
  t1=$(date +%s.%N)
  local fails
  fails=$(find burst -name FAIL | wc -l)
  awk -v a="$t0" -v b="$t1" -v n="$total" -v p="$p" -v l="$label" -v f="$fails" \
    'BEGIN {printf "%-22s %2d envíos, %d workers: %6.2f s total  (%.1f envíos/s)%s\n", l, n, p, b - a, n / (b - a), (f > 0 ? "  ¡" f " FALLIDOS!" : "")}'
}

# ---------------------------------------------------------------- informe

echo "== Entorno"
"$GHC" --version
echo "CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)  ($(nproc) hilos)"
echo "N = $N repeticiones por medida"
echo

echo "== Verificación (debe salir 15/15 y 3/3)"
"$GHC" -e main Light.hs 2>&1 | tail -n 3
"$GHC" -e main Heavy.hs 2>&1 | tail -n 3
echo

echo "== Envío típico (15 tests ligeros)               tiempo     RAM pico"
measure "GHCi: ghc -e main (harness propio)"   "$GHC -e main Light.hs"
if "$GHC" -package hspec -e 'return ()' >/dev/null 2>&1; then
  measure "GHCi: ghc -e main (Hspec)"          "$GHC -package hspec -e main SpecLight.hs"
else
  printf '%-42s no instalado (opcional: cabal install --lib hspec)\n' "GHCi: ghc -e main (Hspec)"
fi
measure "Compilar -O0 (estático)"              "rm -rf b1 light; $GHC -O0 -outputdir b1 -o light Light.hs"
measure "  ejecutar binario"                   "./light"
measure "Compilar -O0 -dynamic"                "rm -rf b2 light_dyn; $GHC -O0 -dynamic -outputdir b2 -o light_dyn Light.hs"
measure "  ejecutar binario"                   "./light_dyn"
echo

echo "== Caso pesado (velocidad de ejecución)"
measure "GHCi: ghc -e main"                    "$GHC -e main Heavy.hs"
measure "Compilar -O0 -dynamic"                "rm -rf b3 heavy_dyn; $GHC -O0 -dynamic -outputdir b3 -o heavy_dyn Heavy.hs"
measure "  ejecutar binario"                   "./heavy_dyn"
echo

echo "== Ráfagas concurrentes (30 envíos)"
for p in 3 4; do
  burst "$p" 30 "GHCi"          "$GHC -e main Light.hs"
  burst "$p" 30 "Compilar+run dyn" "$GHC -O0 -dynamic -o prog Light.hs && ./prog"
done
