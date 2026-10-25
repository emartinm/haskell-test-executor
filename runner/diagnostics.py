"""Interpretación de los errores de compilación de GHC.

Gracias a los pragmas LINE que pone codegen, cada error de GHC indica su origen:

  solucion.hs:L:C   error en el código del alumno, línea L de su código
  tests:I:C         error al compilar el test I (normalmente, la solución del
                    alumno no tiene el tipo que el test espera)
  cualquier otro    error interno: definiciones del profesor, harness, enlazado...

Compatible con Python 3.8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Collection, List, Optional

from .codegen import STUDENT_FILE, TESTS_FILE

MAX_MESSAGE = 4000

HIDDEN_TEST_MESSAGE = (
    "La solución no es compatible con un test oculto: revisa que los tipos de tus "
    "funciones coinciden con los del enunciado."
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
    test: Optional[int] = None  # índice del test, si source == "tests"


def parse(output: str) -> List[Diagnostic]:
    """Separa la salida de GHC en diagnósticos (errores y warnings)."""
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
    """Resume la salida de una compilación fallida.

    Prioridad: errores del alumno, después el primer test que no compila y, si
    no hay ninguno de los dos, error interno con la salida completa.
    """
    errors = [d for d in parse(output) if d.severity == "error"]

    student = [d for d in errors if d.file == STUDENT_FILE]
    if student:
        return CompileFailure("student", _join(student))

    tests = [d for d in errors if d.file == TESTS_FILE and d.line is not None]
    if tests:
        index = min(d.line for d in tests)
        if index in hidden:
            return CompileFailure("tests", HIDDEN_TEST_MESSAGE, index)
        return CompileFailure("tests", _join([d for d in tests if d.line == index]), index)

    return CompileFailure("internal", _truncate(output.strip()))


def _join(diags: List[Diagnostic]) -> str:
    return _truncate("\n\n".join(d.text for d in diags))


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE] + "\n..."
