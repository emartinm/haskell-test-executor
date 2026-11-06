import unittest

from runner.diagnostics import HIDDEN_TEST_CODE, HIDDEN_TEST_MESSAGE, MISSING_SIGNATURE_CODE, parse, summarize

# Real GHC 9.10.3 output (trimmed).
STUDENT_ERROR = """\
[3 of 5] Compiling Student          ( Student.hs, Student.o )
solution.hs:3:31: error: [GHC-83865]
    • Couldn't match expected type ‘Int’ with actual type ‘Bool’
    • In the second argument of ‘(+)’, namely ‘True’
  |
3 | total (x:xs) = x + total xs + True
  |                               ^^^^
solution.hs:(5,1)-(6,10): warning: [GHC-62161] [-Wincomplete-patterns]
    Pattern match(es) are non-exhaustive

solution.hs:7:1-4: error: [GHC-88464]
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

MISSING_SIGNATURES = """\
solution.hs:1:1: error: [GHC-38417] [-Wmissing-signatures, Werror=missing-signatures]
    Top-level binding with no type signature: doble :: Num a => a -> a
  |
1 | doble x = x + x
  | ^^^^^

solution.hs:3:1: error: [GHC-38417] [-Wmissing-signatures, Werror=missing-signatures]
    Top-level binding with no type signature:
      (<+>) :: [a] -> [a] -> [a]
  |
3 | (<+>) a b = a ++ b
  | ^^^^^

solution.hs:4:7: error: [GHC-38417] [-Wmissing-signatures, Werror=missing-signatures]
    Top-level binding with no type signature: dos :: Char
  |
4 | (uno, dos) = (1 :: Int, 'c')
  |       ^^^
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
            ("solution.hs", 3, "error"),
            ("solution.hs", 5, "warning"),
            ("solution.hs", 7, "error"),
        ])
        self.assertTrue(diags[0].text.startswith("solution.hs:3:31: error:"))
        self.assertTrue(diags[0].text.endswith("^^^^"))
        self.assertNotIn("Compiling", diags[0].text)

    def test_unlocated(self):
        diags = parse(INTERNAL_ERROR)
        self.assertEqual([(d.file, d.line) for d in diags], [("Defs.hs", 3), (None, None)])


class SummarizeTest(unittest.TestCase):
    def test_student_errors_only(self):
        f = summarize(STUDENT_ERROR)
        self.assertEqual((f.source, f.test), ("student", None))
        self.assertIn("solution.hs:3:31", f.message)
        self.assertIn("solution.hs:7:1-4", f.message)
        self.assertNotIn("warning", f.message)

    def test_first_failing_test(self):
        f = summarize(TESTS_ERROR)
        self.assertEqual((f.source, f.test, f.code), ("tests", 2, None))
        self.assertIn('"abc"', f.message)
        self.assertNotIn('"xyz"', f.message)

    def test_hidden_test_is_redacted(self):
        f = summarize(TESTS_ERROR, hidden={2})
        self.assertEqual((f.source, f.test, f.message, f.code), ("tests", 2, HIDDEN_TEST_MESSAGE, HIDDEN_TEST_CODE))

    def test_missing_signatures_without_types(self):
        f = summarize(MISSING_SIGNATURES)
        self.assertEqual((f.source, f.code, f.test), ("student", MISSING_SIGNATURE_CODE, None))
        self.assertEqual(f.signatures, [
            {"name": "doble", "line": 1, "column": 1},
            {"name": "(<+>)", "line": 3, "column": 1},
            {"name": "dos", "line": 4, "column": 7},
        ])
        self.assertEqual(f.message, (
            "solution.hs:1:1: error: missing type signature for doble\n"
            "solution.hs:3:1: error: missing type signature for (<+>)\n"
            "solution.hs:4:7: error: missing type signature for dos"
        ))
        for leak in ("::", "Num a", "[a]", "Char"):
            self.assertNotIn(leak, f.message)

    def test_missing_signature_unexpected_format(self):
        # Neither the name nor the type are given away if GHC changes the text.
        output = "solution.hs:(2,1)-(3,4): error: [GHC-38417]\n    Some new text: f :: Int\n"
        f = summarize(output)
        self.assertEqual((f.code, f.signatures), (MISSING_SIGNATURE_CODE, [{"name": None, "line": 2, "column": 1}]))
        self.assertEqual(f.message, "solution.hs:2:1: error: missing type signature")

    def test_other_student_errors_go_first(self):
        f = summarize(STUDENT_ERROR + MISSING_SIGNATURES)
        self.assertEqual((f.source, f.code, f.signatures), ("student", None, None))
        self.assertIn("solution.hs:3:31", f.message)
        self.assertNotIn("38417", f.message)
        self.assertNotIn("doble", f.message)

    def test_internal(self):
        f = summarize(INTERNAL_ERROR)
        self.assertEqual(f.source, "internal")
        self.assertIn("Tre", f.message)


if __name__ == "__main__":
    unittest.main()
