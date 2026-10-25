{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

suma :: [Int] -> Int
suma = go 0
  where
    go acc [] = acc
    go acc (x : xs) = let acc' = acc + x in acc' `seq` go acc' xs

inversa :: [a] -> [a]
inversa = foldl (flip (:)) []

enOrden :: Arbol a -> [a]
enOrden Hoja = []
enOrden (Nodo i x d) = enOrden i ++ [x] ++ enOrden d

profundidad :: Arbol a -> Int
profundidad Hoja = 0
profundidad (Nodo i _ d) = 1 + max (profundidad i) (profundidad d)
