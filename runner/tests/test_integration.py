"""Pruebas de integración: generan, compilan con GHC y ejecutan (sin sandbox)."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from runner.codegen import Submission, TestCase, generate
from runner.diagnostics import HIDDEN_TEST_MESSAGE, summarize

ROOT = Path(__file__).resolve().parents[2]
JUDGE = ROOT / "harness" / "Judge.hs"
GHC = shutil.which("ghc")
GHC_FLAGS = [
    "-XGHC2021", "-O0", "-dynamic", "-hide-all-packages", "-package", "base",
    "-fno-omit-yields", "-rtsopts=ignoreAll", "-with-rtsopts=-M200m -K8m",
    "-fdiagnostics-color=never", "-v0",
]

ARBOL = "data Arbol a = Hoja | Nodo (Arbol a) a (Arbol a)\n  deriving (Show, Eq)\n"


def run(code, tests, **kw):
    """Devuelve ('compile', CompileFailure) o ('run', última línea de result.json)."""
    files = generate(Submission(student_code=code, tests=tests, **kw))
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name, content in files.items():
            (d / name).write_text(content, encoding="utf-8")
        shutil.copy(JUDGE, d)
        c = subprocess.run([GHC, *GHC_FLAGS, "-o", "prog", "Tests.hs"], cwd=d,
                           capture_output=True, text=True)
        if c.returncode != 0:
            hidden = {i for i, t in enumerate(tests, start=1) if t.hidden}
            return "compile", summarize(c.stdout + c.stderr, hidden)
        subprocess.run(["./prog", "timeout_ms=2000"], cwd=d, capture_output=True, timeout=30)
        lines = (d / "result.json").read_text().splitlines()
        return "run", json.loads(lines[-1])


@unittest.skipUnless(GHC, "ghc no está instalado")
class IntegrationTest(unittest.TestCase):
    def assertRun(self, outcome, status, test=None):
        kind, res = outcome
        self.assertEqual(kind, "run", res)
        self.assertEqual(res["status"], status, res)
        if test is not None:
            self.assertEqual(res["test"], test, res)
        return res

    def assertCompile(self, outcome, source, test=None):
        kind, res = outcome
        self.assertEqual(kind, "compile", res)
        self.assertEqual((res.source, res.test), (source, test), res.message)
        return res

    def test_ok_with_conflicting_names(self):
        # El alumno define main y un nombre igual al de un test generado.
        code = "suma :: [Int] -> Int\nsuma = sum\n\nmain = print 1\njt__1 = 5\n"
        self.assertRun(run(code, [TestCase("suma []", "0"), TestCase("suma [1,2,3]", "6")]), "OK")

    def test_student_type_error_reports_original_line(self):
        code = "suma :: [Int] -> Int\nsuma [] = 0\nsuma (x:xs) = x + suma xs + True\n"
        res = self.assertCompile(run(code, [TestCase("suma []", "0")]), "student")
        self.assertTrue(res.message.startswith("solucion.hs:3:"), res.message)
        self.assertIn("3 | suma (x:xs) = x + suma xs + True", res.message)

    def test_unsafe_import_is_student_error(self):
        code = "import System.IO.Unsafe (unsafePerformIO)\nsuma :: [Int] -> Int\nsuma = sum\n"
        res = self.assertCompile(run(code, [TestCase("suma []", "0")]), "student")
        self.assertIn("solucion.hs:1:", res.message)
        self.assertIn("Can't be safely imported", res.message)

    def test_wrong_type_is_reported_on_first_failing_test(self):
        code = "suma :: String -> Int\nsuma = length\n"
        tests = [TestCase("suma []", "0"), TestCase("suma [1,2,3]", "6"),
                 TestCase("suma [4,5,6]", "15", hidden=True)]
        res = self.assertCompile(run(code, tests), "tests", 2)
        self.assertIn("suma [1,2,3]", res.message)
        self.assertNotIn("4,5,6", res.message)

    def test_wrong_type_on_hidden_test_is_redacted(self):
        code = "suma :: String -> Int\nsuma = length\n"
        tests = [TestCase("suma []", "0"), TestCase("suma [4,5,6]", "15", hidden=True)]
        res = self.assertCompile(run(code, tests), "tests", 2)
        self.assertEqual(res.message, HIDDEN_TEST_MESSAGE)

    def test_runtime_error_reports_original_line(self):
        code = "suma :: [Int] -> Int\nsuma (x:xs) = x + suma xs\n"
        res = self.assertRun(run(code, [TestCase("suma []", "0")]), "RUNTIME_ERROR", 1)
        self.assertIn("solucion.hs:2:1", res["exception"])

    def test_definitions_and_wrong_answer(self):
        code = "enOrden :: Arbol a -> [a]\nenOrden Hoja = []\nenOrden (Nodo i x d) = x : enOrden i ++ enOrden d\n"
        tests = [TestCase("enOrden (Hoja :: Arbol Int)", "[]"),
                 TestCase("enOrden (Nodo (Nodo Hoja 1 Hoja) 2 Hoja)", "[1, 2]")]
        res = self.assertRun(run(code, tests, definitions=ARBOL), "WRONG_ANSWER", 2)
        self.assertEqual((res["expected"], res["obtained"]), ("[1,2]", "[2,1]"))

    def test_extensions(self):
        code = "signo :: Int -> Int\nsigno = \\case\n  0 -> 0\n  n -> if n > 0 then 1 else -1\n"
        tests = [TestCase("signo (-5)", "-1")]
        self.assertCompile(run(code, tests), "student")
        self.assertRun(run(code, tests, extensions=["LambdaCase"]), "OK")

    def test_prelude_name_clash_needs_qualification(self):
        code = "import Prelude hiding (sum)\nsum :: [Int] -> Int\nsum = foldr (+) 0\n"
        self.assertRun(run(code, [TestCase("Student.sum [1,2]", "3")]), "OK")
        self.assertCompile(run(code, [TestCase("sum [1,2]", "3")]), "tests", 1)

    def test_teacher_pragmas_in_definitions(self):
        defs = "{-# LANGUAGE LambdaCase #-}\nesCero :: Int -> Bool\nesCero = \\case { 0 -> True; _ -> False }\n"
        code = "f :: Int -> Bool\nf = not . esCero\n"
        self.assertRun(run(code, [TestCase("f 0", "False")], definitions=defs), "OK")


if __name__ == "__main__":
    unittest.main()
