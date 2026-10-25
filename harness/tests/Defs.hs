{-# LANGUAGE Trustworthy #-}
module Defs where

data Arbol a = Hoja | Nodo (Arbol a) a (Arbol a)
  deriving (Show, Eq)
