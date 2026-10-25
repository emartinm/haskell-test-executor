{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

-- Infinite loop that does not allocate: only the timeout stops it, and only
-- if compiled with -fno-omit-yields.
total :: [Int] -> Int
total xs = total xs

reversed :: [a] -> [a]
reversed = foldl (flip (:)) []

inOrder :: Tree a -> [a]
inOrder Leaf = []
inOrder (Node l x r) = inOrder l ++ [x] ++ inOrder r

depth :: Tree a -> Int
depth Leaf = 0
depth (Node l _ r) = 1 + max (depth l) (depth r)
