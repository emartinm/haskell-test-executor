{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

-- Bug: only returns the first element.
total :: [Int] -> Int
total [] = 0
total (x : _) = x

reversed :: [a] -> [a]
reversed = foldl (flip (:)) []

inOrder :: Tree a -> [a]
inOrder Leaf = []
inOrder (Node l x r) = inOrder l ++ [x] ++ inOrder r

depth :: Tree a -> Int
depth Leaf = 0
depth (Node l _ r) = 1 + max (depth l) (depth r)
