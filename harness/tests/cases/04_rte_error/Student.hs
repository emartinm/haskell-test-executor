{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

total :: [Int] -> Int
total _ = error "not implemented"

reversed :: [a] -> [a]
reversed = foldl (flip (:)) []

inOrder :: Tree a -> [a]
inOrder Leaf = []
inOrder (Node l x r) = inOrder l ++ [x] ++ inOrder r

depth :: Tree a -> Int
depth Leaf = 0
depth (Node l _ r) = 1 + max (depth l) (depth r)
