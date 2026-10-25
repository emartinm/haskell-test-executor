{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs

-- Bucle infinito que reserva memoria sin retenerla: lo detiene el límite de reserva.
suma :: [Int] -> Int
suma _ = length (filter (< 0) [(1 :: Int) ..])

inversa :: [a] -> [a]
inversa = foldl (flip (:)) []

enOrden :: Arbol a -> [a]
enOrden Hoja = []
enOrden (Nodo i x d) = enOrden i ++ [x] ++ enOrden d

profundidad :: Arbol a -> Int
profundidad Hoja = 0
profundidad (Nodo i _ d) = 1 + max (profundidad i) (profundidad d)
