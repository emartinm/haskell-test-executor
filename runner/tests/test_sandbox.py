"""Pruebas de la fase 3: veredictos dentro de isolate y robustez del sandbox."""

import shutil
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path

from runner.codegen import Submission, TestCase
from runner.judge import COMPILE_LIMITS_MESSAGE, Config, Limits, judge

BOX_ID = 9  # caja reservada para las pruebas


def _isolate_available():
    try:
        return subprocess.run(["isolate", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


AVAILABLE = shutil.which("ghc") is not None and _isolate_available()

SUMA_OK = "suma :: [Int] -> Int\nsuma = sum\n"
TESTS = [TestCase("suma []", "0"), TestCase("suma [1,2,3]", "6")]


def run(code=SUMA_OK, tests=TESTS, limits=Limits(), config=None, **kw):
    return judge(Submission(student_code=code, tests=tests, **kw), limits, BOX_ID, config)


@unittest.skipUnless(AVAILABLE, "se necesitan ghc e isolate (y pertenecer al grupo isolate)")
class VerdictTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(run(), {"status": "OK", "passed": 2, "total": 2})

    def test_wrong_answer(self):
        r = run("suma :: [Int] -> Int\nsuma [] = 0\nsuma (x:_) = x\n")
        self.assertEqual(r, {
            "status": "WRONG_ANSWER", "passed": 1, "total": 2,
            "test": {"index": 2, "expr": "suma [1,2,3]", "expected": "6", "obtained": "1"},
        })

    def test_wrong_answer_on_hidden_test(self):
        tests = [TestCase("suma []", "0"), TestCase("suma [1,2,3]", "6", hidden=True)]
        r = run("suma :: [Int] -> Int\nsuma [] = 0\nsuma (x:_) = x\n", tests)
        self.assertEqual(r["test"], {"index": 2, "hidden": True})

    def test_rejected_pragma(self):
        r = run("{-# LANGUAGE TemplateHaskell #-}\n" + SUMA_OK)
        self.assertEqual((r["status"], r["source"]), ("COMPILATION_ERROR", "student"))

    def test_student_compile_error(self):
        r = run("suma :: [Int] -> Int\nsuma = sum\nf = suma True\n")
        self.assertEqual((r["status"], r["source"]), ("COMPILATION_ERROR", "student"))
        self.assertTrue(r["message"].startswith("solucion.hs:3:"), r["message"])
        self.assertIn("‘Bool’", r["message"])  # UTF-8 correcto dentro de la caja

    def test_tests_compile_error(self):
        r = run("suma :: String -> Int\nsuma = length\n")
        self.assertEqual((r["status"], r["source"]), ("COMPILATION_ERROR", "tests"))
        self.assertEqual(r["test"], {"index": 2, "expr": "suma [1,2,3]"})

    def test_runtime_error(self):
        r = run("suma :: [Int] -> Int\nsuma (x:xs) = x + suma xs\n")
        self.assertEqual((r["status"], r["test"]["index"]), ("RUNTIME_ERROR", 1))
        self.assertIn("solucion.hs:2:1", r["test"]["exception"])

    def test_time_limit_per_test(self):
        r = run("suma :: [Int] -> Int\nsuma xs = suma xs\n", limits=Limits(time_per_test_ms=300))
        self.assertEqual(r["status"], "TIME_LIMIT")
        self.assertEqual(r["test"], {"index": 1, "expr": "suma []", "reason": "timeout"})

    def test_time_limit_allocation(self):
        code = "suma :: [Int] -> Int\nsuma _ = length (filter (< 0) [(1 :: Int) ..])\n"
        r = run(code, limits=Limits(alloc_per_test_mb=100, time_per_test_ms=20000))
        self.assertEqual((r["status"], r["test"]["reason"]), ("TIME_LIMIT", "allocation"))

    def test_time_limit_total_cpu(self):
        # El timeout por test es enorme: tiene que actuar el límite de CPU de isolate.
        r = run("suma :: [Int] -> Int\nsuma xs = suma xs\n",
                limits=Limits(time_per_test_ms=600000, run_time_s=1))
        self.assertEqual(r, {"status": "TIME_LIMIT", "passed": 0, "total": 2,
                             "test": {"index": 1, "expr": "suma []", "reason": "cpu"}})

    def test_memory_limit_heap(self):
        code = "suma :: [Int] -> Int\nsuma _ = let ys = [(1 :: Int) ..] in length ys + sum ys\n"
        r = run(code, limits=Limits(heap_mb=100, alloc_per_test_mb=100000, time_per_test_ms=20000))
        self.assertEqual((r["status"], r["test"]["index"]), ("MEMORY_LIMIT", 1))

    def test_memory_limit_cgroup(self):
        # Heap del RTS mayor que el cgroup: el kernel mata el proceso.
        code = "suma :: [Int] -> Int\nsuma _ = let ys = [(1 :: Int) ..] in length ys + sum ys\n"
        limits = Limits(heap_mb=4000, run_memory_mb=150, alloc_per_test_mb=100000, time_per_test_ms=20000)
        r = run(code, limits=limits)
        self.assertEqual((r["status"], r["test"]["index"], r["passed"]), ("MEMORY_LIMIT", 1, 0))

    def test_module_cache(self):
        from runner.cache import ModuleCache

        cache = ModuleCache(Config.from_env())
        defs = "data Color = Rojo | Azul deriving (Show, Eq)\n"
        code = "f :: Color -> Color\nf Rojo = Azul\nf Azul = Rojo\n"
        tests = [TestCase("f Rojo", "Azul")]
        for _ in range(2):
            sub = Submission(student_code=code, tests=tests, definitions=defs)
            self.assertEqual(judge(sub, Limits(), BOX_ID, cache=cache)["status"], "OK")
        self.assertEqual((cache.misses, cache.hits, len(cache)), (2, 2, 2))
        # Unas definiciones que no compilan no se cachean y dan error interno.
        sub = Submission(student_code="x = 1\n", tests=[TestCase("x", "1")], definitions="data = 1\n")
        self.assertEqual(judge(sub, Limits(), BOX_ID, cache=cache)["status"], "INTERNAL_ERROR")
        self.assertEqual(len(cache), 2)

    def test_compile_limits(self):
        r = run(limits=Limits(compile_time_s=0.05))
        self.assertEqual(r, {"status": "COMPILATION_ERROR", "source": "student",
                             "message": COMPILE_LIMITS_MESSAGE})


# Código del "profesor" (no está en modo Safe) que intenta salir del sandbox.
# blocked act devuelve "blocked" si la acción falla y "LEAK: ..." si funciona.
ATTACKS = """\
import Control.Exception (SomeException, evaluate, try)
import System.IO.Unsafe (unsafePerformIO)
import System.Process (readProcess)

blocked :: IO String -> String
blocked act = unsafePerformIO $ do
  r <- try (act >>= \\s -> evaluate (length s) >> pure s)
  pure $ case r of
    Left e -> const "blocked" (e :: SomeException)
    Right s -> "LEAK: " ++ take 200 s
{-# NOINLINE blocked #-}

shell :: String -> IO String
shell cmd = readProcess "/bin/sh" ["-c", cmd] ""

interfaces :: [String]
interfaces = unsafePerformIO $ do
  s <- readFile "/proc/net/dev"
  pure [takeWhile (/= ':') (dropWhile (== ' ') l) | l <- drop 2 (lines s)]
{-# NOINLINE interfaces #-}
"""


@unittest.skipUnless(AVAILABLE, "se necesitan ghc e isolate (y pertenecer al grupo isolate)")
class SandboxSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = Config.from_env()
        cls.config = replace(base, packages=("base", "process"))

    def attack(self, *tests, limits=Limits()):
        return run("x = 1\n", list(tests), limits=limits, config=self.config, definitions=ATTACKS)

    def assertBlocked(self, expr):
        r = self.attack(TestCase(expr, '"blocked"'))
        self.assertEqual(r["status"], "OK", r)

    def test_no_network(self):
        r = self.attack(TestCase("interfaces", '["lo"]'))
        self.assertEqual(r["status"], "OK", r)

    def test_cannot_read_system_files(self):
        self.assertBlocked('blocked (readFile "/etc/passwd")')
        self.assertBlocked('blocked (readFile "/etc/shadow")')

    def test_cannot_read_home(self):
        home = Path.home()
        self.assertBlocked(f'blocked (readFile "{home}/.bashrc")')
        ghc_root = Path(self.config.ghc_root)
        if home in ghc_root.parents:
            # Montar GHC crea los directorios intermedios: solo deben verse esos.
            next_dir = ghc_root.relative_to(home).parts[0]
            r = self.attack(TestCase(f'blocked (shell "ls -A {home}")', f'"LEAK: {next_dir}\\n"'))
            self.assertEqual(r["status"], "OK", r)
        else:
            self.assertBlocked(f'blocked (shell "ls {home}")')

    def test_cannot_write_outside_box(self):
        self.assertBlocked('blocked (writeFile "/tmp/../usr/pwned" "x" >> pure "")')
        self.assertBlocked(f'blocked (writeFile "{Path(__file__).parent}/pwned" "x" >> pure "")')

    def test_process_limit(self):
        self.assertBlocked('blocked (shell "for i in 1 2 3 4 5 6 7 8; do sleep 1 & done; wait")')

    def test_unprivileged_user(self):
        r = self.attack(TestCase('take 4 (blocked (shell "id -u"))', '"LEAK"'))
        self.assertEqual(r["status"], "OK", r)
        r = self.attack(TestCase('blocked (shell "id -u")', '"LEAK: 0\\n"'))
        self.assertEqual(r["status"], "WRONG_ANSWER", r)  # no es root

    def test_file_size_limit(self):
        # Escribir más de fsize: o falla la escritura o el kernel mata el proceso.
        r = self.attack(TestCase('blocked (writeFile "grande" (replicate (5 * 1024 * 1024) \'x\') >> pure "")',
                                 '"blocked"'))
        self.assertIn(r["status"], ("OK", "RUNTIME_ERROR"), r)


if __name__ == "__main__":
    unittest.main()
