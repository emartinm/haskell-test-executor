-- En producción este módulo lo genera el runner; aquí está escrito a mano.
module Main (main) where

import Defs
import qualified Judge
import Student

main :: IO ()
main =
  Judge.runTests
    [ Judge.Test (suma []) (0)
    , Judge.Test (suma [1, 2, 3]) (6)
    , Judge.Test (inversa "hola") ("aloh")
    , Judge.Test (enOrden (Nodo (Nodo Hoja 1 Hoja) 2 (Nodo Hoja 3 Hoja))) ([1, 2, 3])
    , Judge.Test (profundidad (Nodo Hoja 'a' (Nodo Hoja 'b' Hoja))) (2)
    , Judge.Test (suma [1 .. 1000000]) (500000500000)
    ]
