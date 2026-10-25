"""Interpretation of GHC compilation errors.

Thanks to the LINE pragmas set by codegen, each GHC error tells its origin:

  solution.hs:L:C   error in the student code, line L of their code
  tests:I:C         error compiling test I (usually, the student solution does
                    not have the type the test expects)
  anything else     internal error: teacher definitions, harness, linking...

Compatible with Python 3.8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Collection, List, Optional

from .codegen import STUDENT_FILE, TESTS_FILE

MAX_MESSAGE = 4000

HIDDEN_TEST_CODE = "hidden_test_type_mismatch"
HIDDEN_TEST_MESSAGE = (
    "The solution is not compatible with a hidden test: check that the types of "
    "your functions match the ones in the problem statement."
)

_LOCATED = re.compile(
    r"^(?P<file>[^\s:<][^:\n]*):"
    r"(?:(?P<l1>\d+):\d+(?:-\d+)?|\((?P<l2>\d+),\d+\)-\(\d+,\d+\)): "
    r"(?P<severity>error|warning)"
)
_UNLOCATED = re.compile(r"^<no location info>: (?P<severity>error|warning)")
_PROGRESS = re.compile(r"^(\[\s*\d+ of \d+\] Compiling |Linking )")


@dataclass
class Diagnostic:
    file: Optional[str]
    line: Optional[int]
    severity: str  # "error" | "warning"
    text: str


@dataclass
class CompileFailure:
    source: str  # "student" | "tests" | "internal"
    message: str
    test: Optional[int] = None  # test index, if source == "tests"
    code: Optional[str] = None  # set when the message is ours and not GHC's


def parse(output: str) -> List[Diagnostic]:
    """Splits the GHC output into diagnostics (errors and warnings)."""
    diags: List[Diagnostic] = []
    block: List[str] = []

    def close() -> None:
        if block:
            diags[-1].text = "\n".join(block).rstrip()
            block.clear()

    for raw in output.splitlines():
        if _PROGRESS.match(raw):
            continue
        m = _LOCATED.match(raw)
        u = None if m else _UNLOCATED.match(raw)
        if m or u:
            close()
            if m:
                line = int(m.group("l1") or m.group("l2"))
                diags.append(Diagnostic(m.group("file"), line, m.group("severity"), ""))
            else:
                diags.append(Diagnostic(None, None, u.group("severity"), ""))
        if diags:
            block.append(raw)
    close()
    return diags


def summarize(output: str, hidden: Collection[int] = ()) -> CompileFailure:
    """Summarizes the output of a failed compilation.

    Priority: student errors, then the first test that does not compile and,
    if there is neither, an internal error with the full output.
    """
    errors = [d for d in parse(output) if d.severity == "error"]

    student = [d for d in errors if d.file == STUDENT_FILE]
    if student:
        return CompileFailure("student", _join(student))

    tests = [d for d in errors if d.file == TESTS_FILE and d.line is not None]
    if tests:
        index = min(d.line for d in tests)
        if index in hidden:
            return CompileFailure("tests", HIDDEN_TEST_MESSAGE, index, HIDDEN_TEST_CODE)
        return CompileFailure("tests", _join([d for d in tests if d.line == index]), index)

    return CompileFailure("internal", _truncate(output.strip()))


def _join(diags: List[Diagnostic]) -> str:
    return _truncate("\n\n".join(d.text for d in diags))


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE] + "\n..."
