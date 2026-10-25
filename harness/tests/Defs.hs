{-# LANGUAGE Trustworthy #-}
module Defs where

data Tree a = Leaf | Node (Tree a) a (Tree a)
  deriving (Show, Eq)
