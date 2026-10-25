{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

total :: [Int] -> Int
total = go 0
  where
    go acc [] = acc
    go acc (x : xs) = let acc' = acc + x in acc' `seq` go acc' xs

-- Bug: infinite result. It must give WRONG_ANSWER with truncated output.
reversed :: [a] -> [a]
reversed = cycle

inOrder :: Tree a -> [a]
inOrder Leaf = []
inOrder (Node l x r) = inOrder l ++ [x] ++ inOrder r

depth :: Tree a -> Int
depth Leaf = 0
depth (Node l _ r) = 1 + max (depth l) (depth r)
