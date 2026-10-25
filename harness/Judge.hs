{-# LANGUAGE ExistentialQuantification #-}

-- | Harness del ejecutor: ejecuta los tests en orden y se detiene en el primer fallo.
--
-- Protocolo: escribe en el fichero de resultados una línea JSON por evento.
--
-- > {"event":"start","test":3}
-- > {"event":"end","status":"OK","passed":6,"total":6}
-- > {"event":"end","status":"WRONG_ANSWER","expected":"6","obtained":"1","test":2,"passed":1,"total":6}
-- > {"event":"end","status":"RUNTIME_ERROR","exception":"...","test":1,"passed":0,"total":6}
-- > {"event":"end","status":"TIME_LIMIT","reason":"timeout","test":1,"passed":0,"total":6}
-- > {"event":"end","status":"MEMORY_LIMIT","test":1,"passed":0,"total":6}
--
-- Los tests se numeran desde 1. Si el proceso muere (OOM-kill, límite de CPU del
-- sandbox) no habrá línea "end": el runner usa el último "start" para saber en
-- qué test ocurrió.
--
-- Argumentos (key=value, el último gana):
--
--   * @out@        fichero de resultados (por defecto @result.json@)
--   * @timeout_ms@ tiempo de reloj máximo por test (por defecto 2000)
--   * @alloc_mb@   memoria reservada máxima por test, TLE determinista (por defecto 4000)
--   * @max_show@   caracteres máximos al mostrar valores y mensajes (por defecto 1000)
--
-- Solo depende de @base@.
module Judge (Test (..), runTests) where

import Control.Exception
  ( AllocationLimitExceeded (..)
  , AsyncException (..)
  , SomeException (..)
  , displayException
  , evaluate
  , finally
  , fromException
  , try
  )
import Data.Char (ord)
import Data.Int (Int64)
import Data.IORef (modifyIORef', newIORef, readIORef)
import Data.List (intercalate)
import Data.Maybe (isJust)
import Numeric (showHex)
import System.Environment (getArgs)
import System.IO (BufferMode (..), IOMode (..), hPutStrLn, hSetBuffering, withFile)
import System.Mem (disableAllocationLimit, enableAllocationLimit, setAllocationCounter)
import System.Timeout (Timeout, timeout)

-- | Un test: valor obtenido (código del alumno) y valor esperado.
--
-- Se comparan con '==' sin forzarlos antes, así que un resultado infinito o
-- parcialmente indefinido da WRONG_ANSWER en cuanto difiere del esperado.
data Test = forall a. (Eq a, Show a) => Test a a

-- ---------------------------------------------------------------------------
-- Configuración

data Config = Config
  { cfgOut :: FilePath
  , cfgTimeoutUs :: Int
  , cfgAllocBytes :: Int64
  , cfgMaxShow :: Int
  }

defaultConfig :: Config
defaultConfig =
  Config
    { cfgOut = "result.json"
    , cfgTimeoutUs = 2000 * 1000
    , cfgAllocBytes = 4000 * 1024 * 1024
    , cfgMaxShow = 1000
    }

parseArgs :: [String] -> Config
parseArgs = foldl set defaultConfig
  where
    set cfg arg = case break (== '=') arg of
      ("out", '=' : v) -> cfg {cfgOut = v}
      ("timeout_ms", '=' : v) | Just n <- readInt v -> cfg {cfgTimeoutUs = n * 1000}
      ("alloc_mb", '=' : v) | Just n <- readInt v -> cfg {cfgAllocBytes = fromIntegral n * 1024 * 1024}
      ("max_show", '=' : v) | Just n <- readInt v -> cfg {cfgMaxShow = n}
      _ -> cfg

readInt :: String -> Maybe Int
readInt v = case reads v of
  [(n, "")] | n > 0 -> Just n
  _ -> Nothing

-- ---------------------------------------------------------------------------
-- Ejecución de los tests

data Failure
  = WrongAnswer String String -- esperado y obtenido, ya renderizados
  | RuntimeError String
  | TimeLimit String -- "timeout" o "allocation"
  | MemoryLimit

runTests :: [Test] -> IO ()
runTests tests = do
  cfg <- parseArgs <$> getArgs
  withFile (cfgOut cfg) WriteMode $ \h -> do
    hSetBuffering h LineBuffering
    let emit = hPutStrLn h . jsonObject
        total = length tests
        go passed [] =
          emit [("event", JStr "end"), ("status", JStr "OK"), ("passed", JInt passed), ("total", JInt total)]
        go passed ((i, t) : rest) = do
          emit [("event", JStr "start"), ("test", JInt i)]
          r <- runOne cfg t
          case r of
            Nothing -> go (passed + 1) rest
            Just f ->
              emit $
                [("event", JStr "end")]
                  ++ failureFields f
                  ++ [("test", JInt i), ("passed", JInt passed), ("total", JInt total)]
    go 0 (zip [1 ..] tests)

runOne :: Config -> Test -> IO (Maybe Failure)
runOne cfg (Test actual expected) = do
  r <- guarded cfg (evaluate (actual == expected))
  case r of
    Right True -> pure Nothing
    Right False -> do
      e <- render cfg (show expected)
      o <- render cfg (show actual)
      pure (Just (WrongAnswer e o))
    Left (RuntimeError msg) -> Just . RuntimeError <$> render cfg msg
    Left f -> pure (Just f)

-- | Ejecuta una acción con timeout de reloj y límite de reserva de memoria,
-- capturando cualquier excepción. Requiere compilar el código del alumno con
-- -fno-omit-yields para que el timeout interrumpa bucles que no reservan memoria.
guarded :: Config -> IO a -> IO (Either Failure a)
guarded cfg act = do
  r <- timeout (cfgTimeoutUs cfg) (try limited)
  pure $ case r of
    Nothing -> Left (TimeLimit "timeout")
    Just (Left e) -> Left (classify e)
    Just (Right x) -> Right x
  where
    limited = do
      setAllocationCounter (cfgAllocBytes cfg)
      enableAllocationLimit
      act `finally` disableAllocationLimit

classify :: SomeException -> Failure
classify e
  | isJust (fromException e :: Maybe Timeout) = TimeLimit "timeout"
  | Just AllocationLimitExceeded <- fromException e = TimeLimit "allocation"
  | Just HeapOverflow <- fromException e = MemoryLimit
  | Just StackOverflow <- fromException e = RuntimeError "stack overflow"
  -- Se muestra la excepción interna: desde GHC 9.10, displayException sobre
  -- SomeException añade una traza interna del runtime que no aporta al alumno.
  | SomeException inner <- e = RuntimeError (displayException inner)

-- | Convierte una cadena perezosa (el show de un valor del alumno o el mensaje
-- de una excepción) en texto seguro: como mucho cfgMaxShow caracteres, y
-- "<error>" en el punto donde su evaluación falla o tarda demasiado.
render :: Config -> String -> IO String
render cfg s = do
  acc <- newIORef []
  let go _ [] = pure ""
      go 0 _ = pure "..."
      go k (c : cs) = do
        _ <- evaluate c
        modifyIORef' acc (c :)
        go (k - 1 :: Int) cs
  r <- guarded (cfg {cfgTimeoutUs = 1000 * 1000}) (go (cfgMaxShow cfg) s)
  prefix <- reverse <$> readIORef acc
  pure (prefix ++ either (const "<error>") id r)

failureFields :: Failure -> [(String, JValue)]
failureFields f = case f of
  WrongAnswer e o -> [("status", JStr "WRONG_ANSWER"), ("expected", JStr e), ("obtained", JStr o)]
  RuntimeError m -> [("status", JStr "RUNTIME_ERROR"), ("exception", JStr m)]
  TimeLimit why -> [("status", JStr "TIME_LIMIT"), ("reason", JStr why)]
  MemoryLimit -> [("status", JStr "MEMORY_LIMIT")]

-- ---------------------------------------------------------------------------
-- JSON mínimo

data JValue = JStr String | JInt Int

jsonObject :: [(String, JValue)] -> String
jsonObject kvs = "{" ++ intercalate "," [jsonString k ++ ":" ++ value v | (k, v) <- kvs] ++ "}"
  where
    value (JStr s) = jsonString s
    value (JInt n) = show n

-- | Cadena JSON con todo lo que no sea ASCII imprimible escapado como \uXXXX,
-- para que la salida no dependa de la codificación del entorno.
jsonString :: String -> String
jsonString s = "\"" ++ concatMap escape s ++ "\""
  where
    escape '"' = "\\\""
    escape '\\' = "\\\\"
    escape '\n' = "\\n"
    escape '\t' = "\\t"
    escape c
      | c >= ' ' && c <= '~' = [c]
      | n < 0x10000 = hex4 n
      | otherwise = hex4 (0xD800 + m `div` 0x400) ++ hex4 (0xDC00 + m `mod` 0x400)
      where
        n = ord c
        m = n - 0x10000
    hex4 x = let h = showHex x "" in "\\u" ++ replicate (4 - length h) '0' ++ h
