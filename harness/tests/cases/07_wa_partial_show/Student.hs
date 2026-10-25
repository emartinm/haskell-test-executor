{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

total :: [Int] -> Int
total = go 0
  where
    go acc [] = acc
    go acc (x : xs) = let acc' = acc + x in acc' `seq` go acc' xs

reversed :: [a] -> [a]
reversed = foldl (flip (:)) []

-- Bug: the first element is already different (WRONG_ANSWER) and the rest is
-- undefined, so "<error>" must appear when showing it.
inOrder :: Tree a -> [a]
inOrder Leaf = []
inOrder (Node _ x _) = x : undefined

depth :: Tree a -> Int
depth Leaf = 0
depth (Node l _ r) = 1 + max (depth l) (depth r)
