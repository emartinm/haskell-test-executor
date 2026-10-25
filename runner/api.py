"""Servicio HTTP del ejecutor seguro de Haskell.

    uvicorn runner.api:app --uds /run/haskell-judge/judge.sock

    POST /run      evalúa un envío (petición: RunRequest; respuesta: ver runner.judge)
    GET  /health   estado del servicio

Los envíos se evalúan en paralelo en un conjunto fijo de cajas de isolate; el
resto espera en una cola acotada (503 si está llena).

Variables de entorno:
    HASKELL_JUDGE_WORKERS    número de cajas en paralelo (por defecto 3)
    HASKELL_JUDGE_MAX_QUEUE  envíos que pueden esperar (por defecto 100)
    HASKELL_JUDGE_FIRST_BOX  identificador de la primera caja (por defecto 0)
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .cache import ModuleCache
from .codegen import InvalidRequest, Rejected, Submission, TestCase, generate
from .judge import Config, Limits, judge

log = logging.getLogger("runner")

MAX_CODE = 64 * 1024


class Busy(Exception):
    pass


class Pool:
    """Reparte las cajas entre los envíos; los que no caben esperan en cola."""

    def __init__(self, first_box: int, workers: int, max_queue: int):
        self.workers = workers
        self.max_queue = max_queue
        self.waiting = 0
        self._boxes: asyncio.Queue[int] = asyncio.Queue()
        for box_id in range(first_box, first_box + workers):
            self._boxes.put_nowait(box_id)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="box")

    @property
    def busy(self) -> int:
        return self.workers - self._boxes.qsize()

    async def run(self, fn: Callable, *args):
        """Ejecuta fn(box_id, *args) en un hilo cuando haya una caja libre."""
        if self._boxes.empty() and self.waiting >= self.max_queue:
            raise Busy
        self.waiting += 1
        try:
            box_id = await self._boxes.get()
        finally:
            self.waiting -= 1
        fut = asyncio.get_running_loop().run_in_executor(self._executor, fn, box_id, *args)
        try:
            return await asyncio.shield(fut)
        finally:
            # Si el cliente se desconecta, la caja no se libera hasta que acabe.
            fut.add_done_callback(lambda _: self._boxes.put_nowait(box_id))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)


# --- Modelo de la petición --------------------------------------------------


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TestIn(Strict):
    expr: str = Field(min_length=1, max_length=4000)
    expected: str = Field(min_length=1, max_length=20000)
    hidden: bool = False


class LimitsIn(Strict):
    time_per_test_ms: int = Field(2000, ge=100, le=10_000)
    alloc_per_test_mb: int = Field(4000, ge=10, le=40_000)
    heap_mb: int = Field(200, ge=16, le=1024)
    stack_mb: int = Field(64, ge=1, le=1024)
    run_time_s: float = Field(10.0, gt=0, le=60)


class RunRequest(Strict):
    student_code: str = Field(max_length=MAX_CODE)
    definitions: str = Field("", max_length=MAX_CODE)
    tests: list[TestIn] = Field(min_length=1, max_length=500)
    extensions: list[str] = Field(default_factory=list, max_length=20)
    limits: LimitsIn = LimitsIn()

    def submission(self) -> Submission:
        return Submission(
            student_code=self.student_code,
            definitions=self.definitions,
            tests=[TestCase(t.expr, t.expected, t.hidden) for t in self.tests],
            extensions=list(self.extensions),
        )


# --- Aplicación -------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    app.state.config = config
    app.state.cache = ModuleCache(config)
    app.state.pool = Pool(
        first_box=int(os.environ.get("HASKELL_JUDGE_FIRST_BOX", "0")),
        workers=int(os.environ.get("HASKELL_JUDGE_WORKERS", "3")),
        max_queue=int(os.environ.get("HASKELL_JUDGE_MAX_QUEUE", "100")),
    )
    app.state.ghc_version = subprocess.run(
        [config.ghc, "--numeric-version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    log.info("GHC %s, %d cajas, cola de %d", app.state.ghc_version, app.state.pool.workers, app.state.pool.max_queue)
    yield
    app.state.pool.shutdown()


app = FastAPI(title="Ejecutor seguro de Haskell", lifespan=lifespan)


@app.post("/run")
async def run(req: RunRequest) -> dict:
    sub = req.submission()
    try:
        generate(sub)  # valida la petición antes de ocupar una caja
    except InvalidRequest as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Rejected:
        pass  # es un veredicto: lo devuelve judge
    limits = Limits(**req.limits.model_dump())
    state = app.state
    start = time.monotonic()
    try:
        result = await state.pool.run(_judge, sub, limits, state.config, state.cache)
    except Busy:
        raise HTTPException(status_code=503, detail="cola llena, inténtalo más tarde")
    log.info("%s en %.2f s (cola: %d)", result["status"], time.monotonic() - start, state.pool.waiting)
    return result


def _judge(box_id: int, sub: Submission, limits: Limits, config: Config, cache: ModuleCache) -> dict:
    return judge(sub, limits, box_id, config, cache)


@app.get("/health")
async def health() -> dict:
    state = app.state
    return {
        "status": "ok",
        "ghc": state.ghc_version,
        "workers": state.pool.workers,
        "busy": state.pool.busy,
        "queued": state.pool.waiting,
        "cache": {"entries": len(state.cache), "hits": state.cache.hits, "misses": state.cache.misses},
    }
