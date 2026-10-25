{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

-- Infinite loop that allocates memory without retaining it: the allocation limit stops it.
total :: [Int] -> Int
total _ = length (filter (< 0) [(1 :: Int) ..])

reversed :: [a] -> [a]
reversed = foldl (flip (:)) []

inOrder :: Tree a -> [a]
inOrder Leaf = []
inOrder (Node l x r) = inOrder l ++ [x] ++ inOrder r

depth :: Tree a -> Int
depth Leaf = 0
depth (Node l _ r) = 1 + max (depth l) (depth r)
