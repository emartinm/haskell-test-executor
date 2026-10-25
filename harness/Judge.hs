{-# LANGUAGE ExistentialQuantification #-}

-- | Executor harness: runs the tests in order and stops at the first failure.
--
-- Protocol: writes one JSON line per event to the results file.
--
-- > {"event":"start","test":3}
-- > {"event":"end","status":"OK","passed":6,"total":6}
-- > {"event":"end","status":"WRONG_ANSWER","expected":"6","obtained":"1","test":2,"passed":1,"total":6}
-- > {"event":"end","status":"RUNTIME_ERROR","exception":"...","test":1,"passed":0,"total":6}
-- > {"event":"end","status":"TIME_LIMIT","reason":"timeout","test":1,"passed":0,"total":6}
-- > {"event":"end","status":"MEMORY_LIMIT","test":1,"passed":0,"total":6}
--
-- Tests are numbered from 1. If the process dies (OOM kill, sandbox CPU limit)
-- there will be no "end" line: the runner uses the last "start" to know which
-- test it happened in.
--
-- Arguments (key=value, the last one wins):
--
--   * @out@        results file (default @result.json@)
--   * @timeout_ms@ maximum wall-clock time per test (default 2000)
--   * @alloc_mb@   maximum memory allocated per test, deterministic TLE (default 4000)
--   * @max_show@   maximum characters when showing values and messages (default 1000)
--
-- It only depends on @base@.
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

-- | A test: obtained value (student code) and expected value.
--
-- They are compared with '==' without forcing them first, so an infinite or
-- partially undefined result gives WRONG_ANSWER as soon as it differs from the
-- expected one.
data Test = forall a. (Eq a, Show a) => Test a a

-- ---------------------------------------------------------------------------
-- Configuration

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
-- Running the tests

data Failure
  = WrongAnswer String String -- expected and obtained, already rendered
  | RuntimeError String
  | TimeLimit String -- "timeout" or "allocation"
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

-- | Runs an action with a wall-clock timeout and a memory allocation limit,
-- catching any exception. The student code must be compiled with
-- -fno-omit-yields so that the timeout can interrupt loops that do not allocate.
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
  -- Show the inner exception: since GHC 9.10, displayException on SomeException
  -- adds an internal runtime backtrace that is useless to the student.
  | SomeException inner <- e = RuntimeError (displayException inner)

-- | Turns a lazy string (the show of a student value or the message of an
-- exception) into safe text: at most cfgMaxShow characters, and "<error>" at
-- the point where its evaluation fails or takes too long.
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
-- Minimal JSON

data JValue = JStr String | JInt Int

jsonObject :: [(String, JValue)] -> String
jsonObject kvs = "{" ++ intercalate "," [jsonString k ++ ":" ++ value v | (k, v) <- kvs] ++ "}"
  where
    value (JStr s) = jsonString s
    value (JInt n) = show n

-- | JSON string with everything that is not printable ASCII escaped as \uXXXX,
-- so that the output does not depend on the encoding of the environment.
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
