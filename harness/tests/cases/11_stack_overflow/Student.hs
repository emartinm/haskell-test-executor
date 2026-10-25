{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

-- Non-tail recursion: passes the small tests and overflows the stack (-K8m)
-- in test 6, with one million elements.
total :: [Int] -> Int
total [] = 0
total (x : xs) = x + total xs

reversed :: [a] -> [a]
reversed = foldl (flip (:)) []

inOrder :: Tree a -> [a]
inOrder Leaf = []
inOrder (Node l x r) = inOrder l ++ [x] ++ inOrder r

depth :: Tree a -> Int
depth Leaf = 0
depth (Node l _ r) = 1 + max (depth l) (depth r)
