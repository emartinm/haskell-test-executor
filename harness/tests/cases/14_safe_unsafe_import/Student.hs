{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}
module Student where

import Defs
import System.IO.Unsafe (unsafePerformIO)

suma :: [Int] -> Int
suma xs = unsafePerformIO (pure (sum xs))

inversa :: [a] -> [a]
inversa = foldl (flip (:)) []

enOrden :: Arbol a -> [a]
enOrden Hoja = []
enOrden (Nodo i x d) = enOrden i ++ [x] ++ enOrden d

profundidad :: Arbol a -> Int
profundidad Hoja = 0
profundidad (Nodo i _ d) = 1 + max (profundidad i) (profundidad d)
