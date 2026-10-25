"""Evaluación completa de un envío: generar, compilar y ejecutar en el sandbox.

    result = judge(Submission(...), Limits(...), box_id=0)

devuelve el veredicto como un diccionario listo para serializar a JSON:

    {"status": "OK", "passed": 6, "total": 6}
    {"status": "WRONG_ANSWER", "passed": 1, "total": 6,
     "test": {"index": 2, "expr": "suma [1,2,3]", "expected": "6", "obtained": "1"}}
    {"status": "COMPILATION_ERROR", "source": "student", "message": "solucion.hs:3:29: error: ..."}
    {"status": "COMPILATION_ERROR", "source": "tests", "message": "...", "test": {"index": 2, "expr": "..."}}
    {"status": "RUNTIME_ERROR" | "TIME_LIMIT" | "MEMORY_LIMIT", "passed": ..., "total": ..., "test": {...}}
    {"status": "INTERNAL_ERROR", "message": "..."}

Si el test que falla es oculto, "test" solo contiene {"index": i, "hidden": true}
(más "reason" en TIME_LIMIT).

Compatible con Python 3.8.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Sequence

from .codegen import InvalidRequest, Rejected, Submission, generate
from .diagnostics import summarize
from .sandbox import Box, Meta, RunLimits, SandboxError

if TYPE_CHECKING:
    from .cache import ModuleCache

log = logging.getLogger(__name__)

JUDGE_HS = Path(__file__).resolve().parents[1] / "harness" / "Judge.hs"

# Margen de memoria del cgroup sobre el heap del RTS (-M): el harness debe poder
# capturar HeapOverflow antes de que el cgroup mate el proceso.
MEMORY_MARGIN_MB = 100

# Código de salida del RTS de GHC cuando se agota el heap sin capturarlo.
RTS_HEAP_EXHAUSTED = 251

COMPILE_LIMITS_MESSAGE = "La compilación ha excedido los límites de tiempo o memoria."
CRASH_MESSAGE = "El programa terminó de forma inesperada."
INTERNAL_MESSAGE = "Error interno del ejecutor."

__all__ = ["Config", "InvalidRequest", "Limits", "judge"]


@dataclass(frozen=True)
class Limits:
    time_per_test_ms: int = 2000
    alloc_per_test_mb: int = 4000
    heap_mb: int = 200  # -M del RTS
    stack_mb: int = 64  # -K del RTS
    run_time_s: float = 10.0  # CPU total de la ejecución de los tests
    compile_time_s: float = 15.0
    compile_memory_mb: int = 1024
    run_memory_mb: Optional[int] = None  # por defecto heap_mb + MEMORY_MARGIN_MB


@dataclass(frozen=True)
class Config:
    ghc: str  # ejecutable de GHC
    ghc_root: str  # instalación de GHC; se monta en solo lectura en la caja
    packages: Sequence[str] = ("base", "containers")
    isolate: str = "isolate"
    judge_hs: str = str(JUDGE_HS)
    use_lld: bool = False  # enlazar con lld: ~0,15 s menos por envío que ld.bfd

    @classmethod
    def from_env(cls) -> "Config":
        """GHC de HASKELL_EXECUTOR_GHC o del PATH; su raíz es el padre de bin/.

        Usa lld si está instalado (ld.lld en el PATH)."""
        ghc = os.environ.get("HASKELL_EXECUTOR_GHC") or shutil.which("ghc")
        if not ghc:
            raise RuntimeError("no se encuentra ghc (define HASKELL_EXECUTOR_GHC)")
        ghc = os.path.realpath(ghc)
        root = os.environ.get("HASKELL_EXECUTOR_GHC_ROOT") or str(Path(ghc).parents[1])
        return cls(ghc=ghc, ghc_root=root, use_lld=shutil.which("ld.lld") is not None)


def judge(
    sub: Submission,
    limits: Limits = Limits(),
    box_id: int = 0,
    config: Optional[Config] = None,
    cache: Optional["ModuleCache"] = None,
) -> dict:
    """Evalúa un envío. Lanza InvalidRequest si la petición es incorrecta.

    Con una caché, Judge y las definiciones del problema se reutilizan ya
    compilados y solo se compilan el código del alumno y los tests.
    """
    config = config or Config.from_env()
    try:
        files = generate(sub)
    except Rejected as e:
        return {"status": "COMPILATION_ERROR", "source": "student", "message": str(e)}
    try:
        with Box(box_id, config.isolate) as box:
            files["Judge.hs"] = Path(config.judge_hs).read_text(encoding="utf-8")
            if cache is not None:
                # Antes de escribir nada del alumno en la caja (ver ModuleCache).
                for module in ("Judge", "Defs"):
                    if cache.provide(box, module, files[f"{module}.hs"], limits):
                        del files[f"{module}.hs"]
            for name, content in files.items():
                box.write(name, content)
            failure = _compile(box, sub, limits, config)
            if failure is not None:
                return failure
            return _run(box, sub, limits, config)
    except SandboxError:
        log.exception("fallo del sandbox")
        return {"status": "INTERNAL_ERROR", "message": INTERNAL_MESSAGE}


def ghc_flags(config: Config) -> List[str]:
    """Opciones comunes a toda compilación (los módulos precompilados deben usar las mismas)."""
    flags = ["-XGHC2021", "-O0", "-dynamic", "-hide-all-packages"]
    for p in config.packages:
        flags += ["-package", p]
    return flags + ["-fno-omit-yields", "-fdiagnostics-color=never", "-fmax-errors=10", "-v0"]


def compile_env(config: Config) -> dict:
    return {"PATH": f"{config.ghc_root}/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"}


def compile_limits(limits: Limits) -> RunLimits:
    return RunLimits(
        time_s=limits.compile_time_s,
        wall_time_s=limits.compile_time_s * 2 + 5,
        memory_mb=limits.compile_memory_mb,
        processes=64,
        fsize_kb=64 * 1024,
    )


def _compile(box: Box, sub: Submission, limits: Limits, config: Config) -> Optional[dict]:
    flags = ghc_flags(config) + [
        *(["-optl-fuse-ld=lld"] if config.use_lld else []),
        "-rtsopts=ignoreAll",
        f"-with-rtsopts=-M{limits.heap_mb}m -K{limits.stack_mb}m",
        "-o",
        "prog",
        "Tests.hs",
    ]
    meta = box.run(
        [config.ghc, *flags],
        compile_limits(limits),
        dirs=[config.ghc_root],
        env=compile_env(config),
        stderr="compile.err",
    )
    if meta.ok:
        return None
    if meta.status == "TO" or meta.oom_killed:
        return {"status": "COMPILATION_ERROR", "source": "student", "message": COMPILE_LIMITS_MESSAGE}
    output = box.read("compile.err")
    f = summarize(output, _hidden(sub))
    if f.source == "internal":
        log.error("error interno de compilación: %s\n%s", meta.fields, output)
        return {"status": "INTERNAL_ERROR", "message": INTERNAL_MESSAGE}
    result = {"status": "COMPILATION_ERROR", "source": f.source, "message": f.message}
    if f.test is not None:
        result["test"] = _test_info(sub, f.test)
    return result


def _run(box: Box, sub: Submission, limits: Limits, config: Config) -> dict:
    run_limits = RunLimits(
        time_s=limits.run_time_s,
        wall_time_s=limits.run_time_s * 2 + 2,
        memory_mb=limits.run_memory_mb or limits.heap_mb + MEMORY_MARGIN_MB,
        processes=4,
        fsize_kb=1024,
    )
    args = [
        "./prog",
        f"timeout_ms={limits.time_per_test_ms}",
        f"alloc_mb={limits.alloc_per_test_mb}",
        "out=result.json",
    ]
    meta = box.run(args, run_limits, dirs=[config.ghc_root], stderr="run.err")
    return _verdict(_events(box.read("result.json")), meta, sub)


def _events(text: str) -> List[dict]:
    events = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue  # línea incompleta si el proceso murió escribiéndola
        if isinstance(e, dict):
            events.append(e)
    return events


def _verdict(events: List[dict], meta: Meta, sub: Submission) -> dict:
    total = len(sub.tests)
    end = next((e for e in reversed(events) if e.get("event") == "end"), None)
    if end is not None:
        result = {"status": end["status"], "passed": end["passed"], "total": end["total"]}
        if end["status"] != "OK":
            info = _test_info(sub, end["test"])
            keys = ("reason",) if info.get("hidden") else ("expected", "obtained", "exception", "reason")
            info.update((k, end[k]) for k in keys if k in end)
            result["test"] = info
        return result

    # Sin línea final: el proceso murió. El test en curso es el último iniciado.
    started = [e["test"] for e in events if e.get("event") == "start" and isinstance(e.get("test"), int)]
    index = started[-1] if started else None
    if meta.oom_killed or meta.exitcode == RTS_HEAP_EXHAUSTED:
        result = {"status": "MEMORY_LIMIT"}
    elif meta.status == "TO":
        result = {"status": "TIME_LIMIT"}
    elif index is not None:
        result = {"status": "RUNTIME_ERROR"}
    else:
        log.error("el programa terminó sin ejecutar ningún test: %s", meta.fields)
        return {"status": "INTERNAL_ERROR", "message": INTERNAL_MESSAGE}
    result.update(passed=(index - 1) if index else 0, total=total)
    if index is not None:
        info = _test_info(sub, index)
        if result["status"] == "TIME_LIMIT":
            info["reason"] = "wall" if meta.wall_timeout else "cpu"
        elif result["status"] == "RUNTIME_ERROR" and not info.get("hidden"):
            info["exception"] = CRASH_MESSAGE
        result["test"] = info
    return result


def _hidden(sub: Submission) -> set:
    return {i for i, t in enumerate(sub.tests, start=1) if t.hidden}


def _test_info(sub: Submission, index: int) -> dict:
    t = sub.tests[index - 1]
    if t.hidden:
        return {"index": index, "hidden": True}
    return {"index": index, "expr": t.expr}
