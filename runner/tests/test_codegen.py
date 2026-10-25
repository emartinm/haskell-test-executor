import unittest

from runner.codegen import (
    STUDENT_FILE,
    TESTS_FILE,
    InvalidRequest,
    Rejected,
    Submission,
    TestCase,
    generate,
)

TESTS = [TestCase("suma []", "0"), TestCase("suma [1,2,3]", "6", hidden=True)]


def gen(code="suma = sum\n", tests=TESTS, **kw):
    return generate(Submission(student_code=code, tests=tests, **kw))


class StudentCodeTest(unittest.TestCase):
    def test_header_and_line_pragma(self):
        files = gen()
        self.assertEqual(
            files["Student.hs"],
            "{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}\n"
            "module Student where\n"
            "import Defs\n"
            f'{{-# LINE 1 "{STUDENT_FILE}" #-}}\n'
            "suma = sum\n",
        )
        self.assertEqual(files[STUDENT_FILE], "suma = sum\n")

    def test_extensions(self):
        files = gen(extensions=["LambdaCase", "MultiWayIf"])
        self.assertIn("{-# LANGUAGE LambdaCase, MultiWayIf #-}\nmodule Student", files["Student.hs"])

    def test_extension_not_allowed(self):
        for ext in ["TemplateHaskell", "CPP", "Unsafe", "GeneralizedNewtypeDeriving"]:
            with self.assertRaises(InvalidRequest):
                gen(extensions=[ext])

    def test_rejects_pragmas_anywhere(self):
        for code, line in [
            ("{-# LANGUAGE CPP #-}\nf = 1\n", 1),
            ("f = 1\n\n{-# options_ghc -F -pgmF=/bin/sh #-}\n", 3),
            ("f = 1\n{-#INLINE f#-}\n", 2),
        ]:
            with self.assertRaises(Rejected) as cm:
                gen(code)
            self.assertIn(f"{STUDENT_FILE}:{line}:", str(cm.exception))

    def test_rejects_module_declaration(self):
        with self.assertRaises(Rejected) as cm:
            gen("-- mi solución\nmodule Main where\nf = 1\n")
        self.assertIn(f"{STUDENT_FILE}:2:", str(cm.exception))

    def test_module_as_part_of_identifier_is_fine(self):
        gen("modules = 3\nf = modules\n")

    def test_normalizes_newlines(self):
        files = gen("f = 1\r\ng = 2")
        self.assertEqual(files[STUDENT_FILE], "f = 1\ng = 2\n")


class DefinitionsTest(unittest.TestCase):
    def test_trustworthy_and_moved_pragmas(self):
        files = gen(definitions="{-# LANGUAGE LambdaCase #-}\ndata Color = Rojo | Azul\n")
        defs = files["Defs.hs"]
        self.assertTrue(defs.startswith("{-# LANGUAGE Trustworthy #-}\n{-# LANGUAGE LambdaCase #-}\nmodule Defs where\n"))
        self.assertIn("data Color = Rojo | Azul", defs)
        self.assertEqual(defs.count("LambdaCase"), 1)

    def test_empty_definitions(self):
        self.assertEqual(gen()["Defs.hs"], "{-# LANGUAGE Trustworthy #-}\nmodule Defs where\n")

    def test_module_declaration_not_allowed(self):
        with self.assertRaises(InvalidRequest):
            gen(definitions="module Defs where\nx = 1\n")


class TestsModuleTest(unittest.TestCase):
    def test_one_definition_per_test_with_line_pragma(self):
        files = gen()
        tests = files["Tests.hs"]
        self.assertIn("main = Judge.runTests [Main.jt__1, Main.jt__2]", tests)
        self.assertIn(f'{{-# LINE 1 "{TESTS_FILE}" #-}}\njt__1 = Judge.Test (suma []) (0)\n', tests)
        self.assertIn(f'{{-# LINE 2 "{TESTS_FILE}" #-}}\njt__2 = Judge.Test (suma [1,2,3]) (6)\n', tests)
        self.assertIn("import Student hiding (main)", tests)
        self.assertEqual(
            files[TESTS_FILE],
            "jt__1 = Judge.Test (suma []) (0)\njt__2 = Judge.Test (suma [1,2,3]) (6)\n",
        )

    def test_invalid_tests(self):
        for tests in [
            [],
            [TestCase("", "0")],
            [TestCase("f 1", "  ")],
            [TestCase("f\n1", "0")],
            [TestCase("f 1", "0\r")],
        ]:
            with self.assertRaises(InvalidRequest):
                gen(tests=tests)


if __name__ == "__main__":
    unittest.main()
