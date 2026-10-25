"""Generación de los ficheros Haskell de un envío.

A partir del código del alumno, las definiciones del profesor y los tests se
generan los módulos que se compilan junto al harness (harness/Judge.hs):

  Defs.hs      definiciones del profesor, marcadas Trustworthy
  Student.hs   código del alumno, en modo Safe y precedido de una cabecera
  Tests.hs     módulo Main que pasa los tests a Judge.runTests
  solucion.hs  copia literal del código del alumno
  tests        un test por línea (jt__i = Judge.Test ...), tal como en Tests.hs

Los pragmas {-# LINE #-} hacen que GHC informe de los errores con las líneas
originales del alumno (fichero "solucion.hs") y, en los tests, con el índice
del test como número de línea (fichero "tests"). Las copias "solucion.hs" y
"tests" existen solo para que GHC muestre el fragmento de código correcto
debajo de cada error.

En las expresiones de los tests, si un nombre del alumno coincide con uno del
Prelude (p.ej. el alumno define su propio `sum`), hay que cualificarlo:
`Student.sum [1,2,3]`.

Compatible con Python 3.8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

STUDENT_FILE = "solucion.hs"
TESTS_FILE = "tests"

# Extensiones que un problema puede activar para el código del alumno. Las de
# GHC2021 ya están activas; ninguna de estas afecta a la seguridad (TemplateHaskell,
# CPP o GeneralizedNewtypeDeriving no están, y Safe Haskell las bloquearía igualmente).
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
    """La petición es incorrecta (responsabilidad del juez o del profesor)."""


class Rejected(Exception):
    """El código del alumno se rechaza antes de compilarlo."""


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
    """Devuelve {nombre de fichero: contenido}.

    Lanza Rejected si el código del alumno no es admisible e InvalidRequest si
    lo incorrecto son las definiciones, los tests o las extensiones.
    """
    for ext in sub.extensions:
        if ext not in ALLOWED_EXTENSIONS:
            raise InvalidRequest(f"extensión no permitida: {ext}")
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
        raise InvalidRequest("las definiciones no deben incluir la declaración 'module'")
    # Los pragmas de cabecera del profesor tienen que ir antes de 'module'.
    pragmas = _HEADER_PRAGMA.findall(definitions)
    body = _HEADER_PRAGMA.sub("", definitions)
    return "\n".join(["{-# LANGUAGE Trustworthy #-}", *pragmas, "module Defs where", ""]) + body


def _test_lines(tests: Sequence[TestCase]) -> List[str]:
    if not tests:
        raise InvalidRequest("se necesita al menos un test")
    lines = []
    for i, t in enumerate(tests, start=1):
        for name, value in (("expr", t.expr), ("expected", t.expected)):
            if not value.strip():
                raise InvalidRequest(f"test {i}: '{name}' está vacío")
            if "\n" in value or "\r" in value:
                raise InvalidRequest(f"test {i}: '{name}' debe ocupar una sola línea")
        lines.append(f"{_test_name(i)} = Judge.Test ({t.expr}) ({t.expected})")
    return lines


def _test_name(i: int) -> str:
    return f"jt__{i}"


def _tests_module(test_lines: List[str]) -> str:
    # Cada test es una definición aparte para que el contexto de un error de
    # tipos nunca cite otros tests (podrían ser ocultos). La lista los nombra
    # cualificados con Main por si el alumno define un nombre igual.
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
