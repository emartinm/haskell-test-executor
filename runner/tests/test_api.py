"""Pruebas de la API (fase 4). Necesitan fastapi y httpx, además de ghc e isolate."""

import asyncio
import os
import threading
import unittest

from runner.tests.test_sandbox import AVAILABLE

try:
    from fastapi.testclient import TestClient
except ImportError:  # fuera del contenedor puede no estar instalado
    TestClient = None

SUMA = "suma :: [Int] -> Int\nsuma = sum\n"
TESTS = [{"expr": "suma []", "expected": "0"}, {"expr": "suma [1,2,3]", "expected": "6"}]


@unittest.skipUnless(TestClient is not None and AVAILABLE, "se necesitan fastapi, httpx, ghc e isolate")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(HASKELL_JUDGE_FIRST_BOX="20", HASKELL_JUDGE_WORKERS="2")
        from runner.api import app

        cls.ctx = TestClient(app)
        cls.client = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def post(self, **body):
        body.setdefault("student_code", SUMA)
        body.setdefault("tests", TESTS)
        return self.client.post("/run", json=body)

    def test_ok(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "OK", "passed": 2, "total": 2})

    def test_wrong_answer(self):
        r = self.post(student_code="suma :: [Int] -> Int\nsuma = length\n")
        self.assertEqual(r.json()["test"], {"index": 2, "expr": "suma [1,2,3]", "expected": "6", "obtained": "3"})

    def test_compilation_error_is_a_verdict(self):
        r = self.post(student_code="{-# LANGUAGE CPP #-}\n" + SUMA)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "COMPILATION_ERROR")

    def test_limits_and_definitions(self):
        r = self.post(
            student_code="f :: Color -> Color\nf Rojo = Azul\nf c = f c\n",
            definitions="data Color = Rojo | Azul deriving (Show, Eq)\n",
            tests=[{"expr": "f Rojo", "expected": "Azul"}, {"expr": "f Azul", "expected": "Rojo", "hidden": True}],
            limits={"time_per_test_ms": 300},
        )
        self.assertEqual(r.json()["test"], {"index": 2, "hidden": True, "reason": "timeout"})

    def test_invalid_requests(self):
        cases = [
            ({"tests": []}, 422),
            ({"tests": [{"expr": "f", "expected": "1", "extra": 1}]}, 422),
            ({"student_code": "x" * (64 * 1024 + 1)}, 422),
            ({"limits": {"heap_mb": 100000}}, 422),
            ({"limits": {"inventado": 1}}, 422),
            ({"extensions": ["TemplateHaskell"]}, 400),
            ({"tests": [{"expr": "f\n1", "expected": "1"}]}, 400),
        ]
        for body, code in cases:
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.post(**body).status_code, code)

    def test_cache_is_used(self):
        defs = "data Dia = Lunes | Martes deriving (Show, Eq)\n"
        tests = [{"expr": "siguiente Lunes", "expected": "Martes"}]
        code = "siguiente :: Dia -> Dia\nsiguiente Lunes = Martes\nsiguiente Martes = Lunes\n"
        before = self.client.get("/health").json()["cache"]
        for _ in range(2):
            self.assertEqual(self.post(student_code=code, definitions=defs, tests=tests).json()["status"], "OK")
        after = self.client.get("/health").json()["cache"]
        self.assertGreaterEqual(after["hits"] - before["hits"], 2)  # Judge y Defs en el 2º envío

    def test_health(self):
        h = self.client.get("/health").json()
        self.assertEqual((h["status"], h["workers"]), ("ok", 2))
        self.assertTrue(h["ghc"].startswith("9."))


@unittest.skipUnless(TestClient is not None, "se necesita fastapi")
class PoolTest(unittest.TestCase):
    def test_queue_limit_and_box_reuse(self):
        from runner.api import Busy, Pool

        async def scenario():
            pool = Pool(first_box=0, workers=1, max_queue=1)
            release = threading.Event()
            used = []

            def work(box_id):
                used.append(box_id)
                release.wait(5)
                return box_id

            first = asyncio.ensure_future(pool.run(work))
            await asyncio.sleep(0.05)
            second = asyncio.ensure_future(pool.run(work))  # espera en la cola
            await asyncio.sleep(0.05)
            with self.assertRaises(Busy):  # la cola (1) está llena
                await pool.run(work)
            release.set()
            results = await asyncio.gather(first, second)
            pool.shutdown()
            return results, used

        results, used = asyncio.run(scenario())
        self.assertEqual(results, [0, 0])
        self.assertEqual(used, [0, 0])


if __name__ == "__main__":
    unittest.main()
