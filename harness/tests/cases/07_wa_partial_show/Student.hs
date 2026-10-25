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

-- Error: el primer elemento ya es distinto (WRONG_ANSWER) y el resto es
-- indefinido, así que al mostrarlo debe aparecer "<error>".
enOrden :: Arbol a -> [a]
enOrden Hoja = []
enOrden (Nodo _ x _) = x : undefined

profundidad :: Arbol a -> Int
profundidad Hoja = 0
profundidad (Nodo i _ d) = 1 + max (profundidad i) (profundidad d)
