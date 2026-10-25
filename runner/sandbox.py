"""Ejecución de comandos en una caja de isolate (https://github.com/ioi/isolate).

Cada caja es un directorio de trabajo (/box dentro del sandbox) con su propio
uid sin privilegios, sin red, con el resto del sistema de ficheros en solo
lectura y con límites de memoria, CPU y procesos aplicados por cgroups v2.

Compatible con Python 3.8.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence


class SandboxError(RuntimeError):
    """Fallo del propio sandbox (no del código evaluado)."""


@dataclass(frozen=True)
class RunLimits:
    time_s: float  # tiempo de CPU (todos los procesos de la caja)
    wall_time_s: float
    memory_mb: int  # cgroup: memoria total de la caja
    processes: int  # procesos e hilos
    fsize_kb: int  # tamaño máximo de cada fichero escrito
    extra_time_s: float = 0.5


@dataclass
class Meta:
    """Informe de isolate sobre una ejecución (fichero --meta)."""

    fields: Dict[str, str]

    @classmethod
    def parse(cls, text: str) -> "Meta":
        fields = {}
        for line in text.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields[key] = value
        return cls(fields)

    @property
    def status(self) -> str:
        """'' si terminó con código 0; si no RE, SG, TO o XX."""
        return self.fields.get("status", "")

    @property
    def exitcode(self) -> Optional[int]:
        v = self.fields.get("exitcode")
        return int(v) if v is not None else None

    @property
    def oom_killed(self) -> bool:
        return self.fields.get("cg-oom-killed") == "1"

    @property
    def wall_timeout(self) -> bool:
        return self.status == "TO" and "wall" in self.fields.get("message", "")

    @property
    def ok(self) -> bool:
        return self.status == "" and self.exitcode == 0


class Box:
    """Caja de isolate; se usa como context manager (init / cleanup)."""

    def __init__(self, box_id: int, isolate: str = "isolate"):
        self.box_id = box_id
        self.isolate = isolate
        self.path: Optional[Path] = None

    def _cmd(self, *args: str) -> list:
        return [self.isolate, "--cg", f"--box-id={self.box_id}", *args]

    def __enter__(self) -> "Box":
        # Por si quedaron restos de una ejecución interrumpida.
        subprocess.run(self._cmd("--cleanup"), capture_output=True)
        r = subprocess.run(self._cmd("--init"), capture_output=True, text=True)
        if r.returncode != 0:
            raise SandboxError(f"isolate --init: {r.stderr.strip()}")
        self.path = Path(r.stdout.strip()) / "box"
        return self

    def __exit__(self, *exc) -> None:
        subprocess.run(self._cmd("--cleanup"), capture_output=True)

    def write(self, name: str, content: str) -> None:
        (self.path / name).write_text(content, encoding="utf-8")

    def write_bytes(self, name: str, content: bytes) -> None:
        (self.path / name).write_bytes(content)

    def read(self, name: str, limit: int = 1 << 20) -> str:
        return self.read_bytes(name, limit).decode("utf-8", errors="replace")

    def read_bytes(self, name: str, limit: int = 1 << 20) -> bytes:
        """Lee un fichero escrito desde dentro de la caja (b"" si no existe).

        No sigue enlaces simbólicos ni lee nada que no sea un fichero regular:
        el contenido de la caja lo controla código no confiable.
        """
        try:
            fd = os.open(self.path / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return b""
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return b""
            return f.read(limit)

    def run(
        self,
        argv: Sequence[str],
        limits: RunLimits,
        *,
        dirs: Sequence[str] = (),
        env: Optional[Mapping[str, str]] = None,
        stdout: str = "/dev/null",
        stderr: str = "/dev/null",
    ) -> Meta:
        """Ejecuta argv en la caja (cwd /box). stdout/stderr son rutas dentro de la caja."""
        fd, meta_path = tempfile.mkstemp(prefix="isolate-meta-")
        os.close(fd)
        try:
            cmd = self._cmd(
                f"--meta={meta_path}",
                f"--time={limits.time_s}",
                f"--wall-time={limits.wall_time_s}",
                f"--extra-time={limits.extra_time_s}",
                f"--cg-mem={limits.memory_mb * 1024}",
                f"--processes={limits.processes}",
                f"--fsize={limits.fsize_kb}",
                f"--stdout={stdout}",
                f"--stderr={stderr}",
                *[f"--dir={d}" for d in dirs],
                *[f"--env={k}={v}" for k, v in (env or {}).items()],
                "--run",
                "--",
                *argv,
            )
            r = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=limits.wall_time_s + 30,
            )
            meta = Meta.parse(Path(meta_path).read_text())
        except subprocess.TimeoutExpired as e:
            raise SandboxError("isolate no terminó a tiempo") from e
        finally:
            os.unlink(meta_path)
        if r.returncode not in (0, 1) or meta.status == "XX":
            raise SandboxError(f"isolate --run ({r.returncode}): {r.stderr.strip()} {meta.fields}")
        return meta
