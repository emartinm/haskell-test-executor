import unittest

from runner.diagnostics import HIDDEN_TEST_MESSAGE, parse, summarize

# Real GHC 9.10.3 output (trimmed).
STUDENT_ERROR = """\
[3 of 5] Compiling Student          ( Student.hs, Student.o )
solucion.hs:3:31: error: [GHC-83865]
    • Couldn't match expected type ‘Int’ with actual type ‘Bool’
    • In the second argument of ‘(+)’, namely ‘True’
  |
3 | total (x:xs) = x + total xs + True
  |                               ^^^^
solucion.hs:(5,1)-(6,10): warning: [GHC-62161] [-Wincomplete-patterns]
    Pattern match(es) are non-exhaustive

solucion.hs:7:1-4: error: [GHC-88464]
    Variable not in scope: foo
"""

TESTS_ERROR = """\
tests:3:25: error: [GHC-83865]
    • Couldn't match type ‘Char’ with ‘Int’
    • In the first argument of ‘total’, namely ‘"xyz"’
  |
3 | jt__3 = Judge.Test (total "xyz") (6)
  |                         ^^^^^
tests:2:25: error: [GHC-83865]
    • Couldn't match type ‘Char’ with ‘Int’
    • In the first argument of ‘total’, namely ‘"abc"’
  |
2 | jt__2 = Judge.Test (total "abc") (6)
  |                         ^^^^^
"""

INTERNAL_ERROR = """\
Defs.hs:3:10: error: [GHC-76037]
    Not in scope: type constructor or class ‘Tre’
<no location info>: error:
    linking failed
"""


class ParseTest(unittest.TestCase):
    def test_blocks(self):
        diags = parse(STUDENT_ERROR)
        self.assertEqual([(d.file, d.line, d.severity) for d in diags], [
            ("solucion.hs", 3, "error"),
            ("solucion.hs", 5, "warning"),
            ("solucion.hs", 7, "error"),
        ])
        self.assertTrue(diags[0].text.startswith("solucion.hs:3:31: error:"))
        self.assertTrue(diags[0].text.endswith("^^^^"))
        self.assertNotIn("Compiling", diags[0].text)

    def test_unlocated(self):
        diags = parse(INTERNAL_ERROR)
        self.assertEqual([(d.file, d.line) for d in diags], [("Defs.hs", 3), (None, None)])


class SummarizeTest(unittest.TestCase):
    def test_student_errors_only(self):
        f = summarize(STUDENT_ERROR)
        self.assertEqual((f.source, f.test), ("student", None))
        self.assertIn("solucion.hs:3:31", f.message)
        self.assertIn("solucion.hs:7:1-4", f.message)
        self.assertNotIn("warning", f.message)

    def test_first_failing_test(self):
        f = summarize(TESTS_ERROR)
        self.assertEqual((f.source, f.test), ("tests", 2))
        self.assertIn('"abc"', f.message)
        self.assertNotIn('"xyz"', f.message)

    def test_hidden_test_is_redacted(self):
        f = summarize(TESTS_ERROR, hidden={2})
        self.assertEqual((f.source, f.test, f.message), ("tests", 2, HIDDEN_TEST_MESSAGE))

    def test_internal(self):
        f = summarize(INTERNAL_ERROR)
        self.assertEqual(f.source, "internal")
        self.assertIn("Tre", f.message)


if __name__ == "__main__":
    unittest.main()
