"""Generation of the Haskell files for a submission.

From the student code, the teacher definitions and the tests, it generates the
modules that are compiled together with the harness (harness/Judge.hs):

  Defs.hs      teacher definitions, marked Trustworthy
  Student.hs   student code, in Safe mode and preceded by a header
  Tests.hs     Main module that passes the tests to Judge.runTests
  solucion.hs  verbatim copy of the student code
  tests        one test per line (jt__i = Judge.Test ...), as in Tests.hs

The {-# LINE #-} pragmas make GHC report errors with the original line numbers
of the student code (file "solucion.hs") and, in the tests, with the test index
as the line number (file "tests"). The copies "solucion.hs" and "tests" exist
only so that GHC shows the right code snippet below each error.

In test expressions, if a student name clashes with one from the Prelude (e.g.
the student defines their own `sum`), it must be qualified: `Student.sum [1,2,3]`.

Compatible with Python 3.8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

# File name shown to students in error messages.
STUDENT_FILE = "solucion.hs"
TESTS_FILE = "tests"

# Extensions a problem may enable for the student code. The GHC2021 ones are
# already enabled; none of these affects security (TemplateHaskell, CPP and
# GeneralizedNewtypeDeriving are not here, and Safe Haskell would block them anyway).
ALLOWED_EXTENSIONS = frozenset(
    {
        "BlockArguments",
        "DataKinds",
        "FunctionalDependencies",
        "GADTs",
        "LambdaCase",
        "MonadComprehensions",
        "MultiWayIf",
        "NoImplicitPrelude",
        "OverloadedStrings",
        "ParallelListComp",
        "RecordWildCards",
        "TypeFamilies",
        "ViewPatterns",
    }
)


class InvalidRequest(ValueError):
    """The request is invalid (responsibility of the judge or the teacher)."""


class Rejected(Exception):
    """The student code is rejected before compiling it (message for the student)."""


@dataclass(frozen=True)
class TestCase:
    expr: str
    expected: str
    hidden: bool = False


@dataclass(frozen=True)
class Submission:
    student_code: str
    tests: Sequence[TestCase]
    definitions: str = ""
    extensions: Sequence[str] = field(default_factory=tuple)


_PRAGMA = re.compile(r"\{-#")
_MODULE_DECL = re.compile(r"^[ \t]*module\b", re.MULTILINE)
_HEADER_PRAGMA = re.compile(r"\{-#\s*(?:LANGUAGE|OPTIONS_GHC)\b.*?#-\}", re.IGNORECASE | re.DOTALL)


def generate(sub: Submission) -> Dict[str, str]:
    """Returns {file name: content}.

    Raises Rejected if the student code is not admissible and InvalidRequest if
    the definitions, the tests or the extensions are invalid.
    """
    for ext in sub.extensions:
        if ext not in ALLOWED_EXTENSIONS:
            raise InvalidRequest(f"extension not allowed: {ext}")
    code = _normalize(sub.student_code)
    _check_student_code(code)
    test_lines = _test_lines(sub.tests)
    return {
        "Defs.hs": _defs_module(_normalize(sub.definitions)),
        "Student.hs": _student_module(code, sub.extensions),
        "Tests.hs": _tests_module(test_lines),
        STUDENT_FILE: code,
        TESTS_FILE: "".join(line + "\n" for line in test_lines),
    }


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text if text.endswith("\n") or not text else text + "\n"


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _check_student_code(code: str) -> None:
    m = _PRAGMA.search(code)
    if m:
        raise Rejected(
            f"{STUDENT_FILE}:{_line_of(code, m.start())}: no se permiten pragmas ({{-# ... #-}}) en la solución"
        )
    m = _MODULE_DECL.search(code)
    if m:
        raise Rejected(
            f"{STUDENT_FILE}:{_line_of(code, m.start())}: no incluyas la declaración 'module'; "
            "escribe solo las importaciones y las definiciones"
        )


def _student_module(code: str, extensions: Sequence[str]) -> str:
    header = ["{-# LANGUAGE Safe, NoGeneralizedNewtypeDeriving #-}"]
    if extensions:
        header.append("{-# LANGUAGE " + ", ".join(extensions) + " #-}")
    header += ["module Student where", "import Defs", f'{{-# LINE 1 "{STUDENT_FILE}" #-}}']
    return "\n".join(header) + "\n" + code


def _defs_module(definitions: str) -> str:
    if _MODULE_DECL.search(definitions):
        raise InvalidRequest("definitions must not include a 'module' declaration")
    # The teacher's header pragmas must go before 'module'.
    pragmas = _HEADER_PRAGMA.findall(definitions)
    body = _HEADER_PRAGMA.sub("", definitions)
    return "\n".join(["{-# LANGUAGE Trustworthy #-}", *pragmas, "module Defs where", ""]) + body


def _test_lines(tests: Sequence[TestCase]) -> List[str]:
    if not tests:
        raise InvalidRequest("at least one test is required")
    lines = []
    for i, t in enumerate(tests, start=1):
        for name, value in (("expr", t.expr), ("expected", t.expected)):
            if not value.strip():
                raise InvalidRequest(f"test {i}: '{name}' is empty")
            if "\n" in value or "\r" in value:
                raise InvalidRequest(f"test {i}: '{name}' must be a single line")
        lines.append(f"{_test_name(i)} = Judge.Test ({t.expr}) ({t.expected})")
    return lines


def _test_name(i: int) -> str:
    return f"jt__{i}"


def _tests_module(test_lines: List[str]) -> str:
    # Each test is a separate definition so that the context of a type error
    # never quotes other tests (they might be hidden). The list refers to them
    # qualified with Main in case the student defines a name with the same name.
    names = ", ".join("Main." + _test_name(i) for i in range(1, len(test_lines) + 1))
    out = [
        "module Main (main) where",
        "",
        "import Defs",
        "import qualified Judge",
        "import Student hiding (main)",
        "",
        "main :: IO ()",
        f"main = Judge.runTests [{names}]",
    ]
    for i, line in enumerate(test_lines, start=1):
        out.append(f'{{-# LINE {i} "{TESTS_FILE}" #-}}')
        out.append(line)
    return "\n".join(out) + "\n"
