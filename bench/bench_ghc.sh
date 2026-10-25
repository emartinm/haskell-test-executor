#!/usr/bin/env bash
# GHC benchmark to size the Haskell test executor.
#
# Measures time (median of N runs) and peak RAM of:
#   - interpreting with GHCi (ghc -e)     with our own harness and, if installed, with Hspec
#   - compiling -O0 (static linking)      and running the binary
#   - compiling -O0 -dynamic              and running the binary
#   - a "heavy" case to compare interpreted vs compiled speed
#   - concurrent bursts of 30 submissions with 3 and 4 workers
#
# Usage:  bash bench_ghc.sh
#         N=20 GHC=ghc-9.8.4 bash bench_ghc.sh
# Requires GNU time (/usr/bin/time; on Debian/Ubuntu: apt install time).
set -u

# awk and sort must parse "0.22" with a decimal point regardless of the user's locale.
export LC_ALL=C

N=${N:-10}
GHC=${GHC:-ghc}
TIME=/usr/bin/time

command -v "$GHC" >/dev/null || { echo "Cannot find $GHC"; exit 1; }
[ -x "$TIME" ] || { echo "GNU time is missing ($TIME): sudo apt install time"; exit 1; }

W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
cd "$W" || exit 1

# ---------------------------------------------------------------- Haskell files

cat > Defs.hs <<'EOF'
{-# LANGUAGE Trustworthy #-}
module Defs where

import Control.DeepSeq (NFData (..))

data Tree a = Leaf | Node (Tree a) a (Tree a)
  deriving (Show, Eq)

instance NFData a => NFData (Tree a) where
  rnf Leaf         = ()
  rnf (Node l x r) = rnf l `seq` rnf x `seq` rnf r
EOF

cat > Student.hs <<'EOF'
{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

total :: [Int] -> Int
total []     = 0
total (x:xs) = x + total xs

reversed :: [a] -> [a]
reversed = foldl (flip (:)) []

isPrime :: Int -> Bool
isPrime n = n > 1 && all (\d -> n `mod` d /= 0) (takeWhile (\d -> d * d <= n) [2 ..])

primes :: Int -> [Int]
primes n = filter isPrime [2 .. n]

insert :: Ord a => a -> Tree a -> Tree a
insert x Leaf = Node Leaf x Leaf
insert x t@(Node l y r)
  | x < y     = Node (insert x l) y r
  | x > y     = Node l y (insert x r)
  | otherwise = t

inOrder :: Tree a -> [a]
inOrder Leaf         = []
inOrder (Node l x r) = inOrder l ++ [x] ++ inOrder r

fromList :: Ord a => [a] -> Tree a
fromList = foldr insert Leaf
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
  [ Test "total []" (total []) 0
  , Test "total [1,2,3]" (total [1, 2, 3]) 6
  , Test "total [1..100]" (total [1 .. 100]) 5050
  , Test "reversed \"\"" (reversed "") ""
  , Test "reversed \"hello\"" (reversed "hello") "olleh"
  , Test "reversed [1..5]" (reversed [1 .. 5 :: Int]) [5, 4, 3, 2, 1]
  , Test "isPrime 1" (isPrime 1) False
  , Test "isPrime 2" (isPrime 2) True
  , Test "isPrime 97" (isPrime 97) True
  , Test "isPrime 91" (isPrime 91) False
  , Test "primes 30" (primes 30) [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
  , Test "inOrder Leaf" (inOrder (Leaf :: Tree Int)) []
  , Test "inOrder (fromList [3,1,2])" (inOrder (fromList [3, 1, 2 :: Int])) [1, 2, 3]
  , Test "insert 1 Leaf" (insert 1 Leaf) (Node Leaf (1 :: Int) Leaf)
  , Test "inOrder (fromList \"haskell\")" (inOrder (fromList "haskell")) "aehkls"
  ]
EOF

cat > Heavy.hs <<'EOF'
import Judge
import Student

main :: IO ()
main = runTests
  [ Test "length (primes 200000)" (length (primes 200000)) 17984
  , Test "total [1..1000000]" (total [1 .. 1000000]) 500000500000
  , Test "length (inOrder (fromList [1..2000]))" (length (inOrder (fromList [1 .. 2000 :: Int]))) 2000
  ]
EOF

cat > SpecLight.hs <<'EOF'
import Defs
import Student
import Test.Hspec

main :: IO ()
main = hspec $ do
  describe "total" $ do
    it "total []" $ total [] `shouldBe` 0
    it "total [1,2,3]" $ total [1, 2, 3] `shouldBe` 6
    it "total [1..100]" $ total [1 .. 100] `shouldBe` 5050
  describe "reversed" $ do
    it "reversed \"\"" $ reversed "" `shouldBe` ""
    it "reversed \"hello\"" $ reversed "hello" `shouldBe` "olleh"
    it "reversed [1..5]" $ reversed [1 .. 5 :: Int] `shouldBe` [5, 4, 3, 2, 1]
  describe "primes" $ do
    it "isPrime 1" $ isPrime 1 `shouldBe` False
    it "isPrime 2" $ isPrime 2 `shouldBe` True
    it "isPrime 97" $ isPrime 97 `shouldBe` True
    it "isPrime 91" $ isPrime 91 `shouldBe` False
    it "primes 30" $ primes 30 `shouldBe` [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
  describe "tree" $ do
    it "inOrder Leaf" $ inOrder (Leaf :: Tree Int) `shouldBe` []
    it "inOrder (fromList [3,1,2])" $ inOrder (fromList [3, 1, 2 :: Int]) `shouldBe` [1, 2, 3]
    it "insert 1 Leaf" $ insert 1 Leaf `shouldBe` Node Leaf (1 :: Int) Leaf
    it "inOrder (fromList \"haskell\")" $ inOrder (fromList "haskell") `shouldBe` "aehkls"
EOF

# ---------------------------------------------------------------- utilities

median() {
  sort -n | awk '{a[NR] = $1} END {if (NR % 2) print a[(NR + 1) / 2]; else print (a[NR / 2] + a[NR / 2 + 1]) / 2}'
}

# measure NAME COMMAND : 1 warm-up run + N measurements; median of time and peak RAM
measure() {
  local name=$1 cmd=$2
  if ! bash -c "$cmd" >/dev/null 2>err.txt; then
    printf '%-42s FAILED\n' "$name"
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

# burst P TOTAL LABEL COMMAND : launches TOTAL submissions with P in parallel, each in its own directory
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
  local failures
  failures=$(find burst -name FAIL | wc -l)
  awk -v a="$t0" -v b="$t1" -v n="$total" -v p="$p" -v l="$label" -v f="$failures" \
    'BEGIN {printf "%-22s %2d submissions, %d workers: %6.2f s total  (%.1f submissions/s)%s\n", l, n, p, b - a, n / (b - a), (f > 0 ? "  " f " FAILED!" : "")}'
}

# ---------------------------------------------------------------- report

echo "== Environment"
"$GHC" --version
echo "CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)  ($(nproc) threads)"
echo "N = $N repetitions per measurement"
echo

echo "== Check (must print 15/15 and 3/3)"
"$GHC" -e main Light.hs 2>&1 | tail -n 3
"$GHC" -e main Heavy.hs 2>&1 | tail -n 3
echo

echo "== Typical submission (15 light tests)            time       peak RAM"
measure "GHCi: ghc -e main (own harness)"      "$GHC -e main Light.hs"
if "$GHC" -package hspec -e 'return ()' >/dev/null 2>&1; then
  measure "GHCi: ghc -e main (Hspec)"          "$GHC -package hspec -e main SpecLight.hs"
else
  printf '%-42s not installed (optional: cabal install --lib hspec)\n' "GHCi: ghc -e main (Hspec)"
fi
measure "Compile -O0 (static)"                 "rm -rf b1 light; $GHC -O0 -outputdir b1 -o light Light.hs"
measure "  run binary"                         "./light"
measure "Compile -O0 -dynamic"                 "rm -rf b2 light_dyn; $GHC -O0 -dynamic -outputdir b2 -o light_dyn Light.hs"
measure "  run binary"                         "./light_dyn"
echo

echo "== Heavy case (execution speed)"
measure "GHCi: ghc -e main"                    "$GHC -e main Heavy.hs"
measure "Compile -O0 -dynamic"                 "rm -rf b3 heavy_dyn; $GHC -O0 -dynamic -outputdir b3 -o heavy_dyn Heavy.hs"
measure "  run binary"                         "./heavy_dyn"
echo

echo "== Concurrent bursts (30 submissions)"
for p in 3 4; do
  burst "$p" 30 "GHCi"             "$GHC -e main Light.hs"
  burst "$p" 30 "Compile+run dyn"  "$GHC -O0 -dynamic -o prog Light.hs && ./prog"
done
