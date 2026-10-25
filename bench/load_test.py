"""Prueba de carga: N envíos simultáneos contra el servicio por su socket Unix.

    python3 load_test.py [N] [socket]        (por defecto 30 y /run/haskell-test-executor/executor.sock)

Cada envío es distinto (el alumno no se cachea) y comparte las definiciones,
como en una clase resolviendo el mismo problema. Necesita httpx.
"""

import sys
import threading
import time
from collections import Counter

import httpx

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
SOCKET = sys.argv[2] if len(sys.argv) > 2 else "/run/haskell-test-executor/executor.sock"

DEFINITIONS = "data Arbol a = Hoja | Nodo (Arbol a) a (Arbol a)\n  deriving (Show, Eq)\n"
TESTS = [
    {"expr": "suma []", "expected": "0"},
    {"expr": "suma [1,2,3]", "expected": "6"},
    {"expr": "enOrden (Nodo (Nodo Hoja 1 Hoja) 2 (Nodo Hoja 3 Hoja))", "expected": "[1, 2, 3]"},
    {"expr": "profundidad (Nodo Hoja 'a' (Nodo Hoja 'b' Hoja))", "expected": "2"},
    {"expr": "suma [1 .. 100000]", "expected": "5000050000", "hidden": True},
]


def submission(i: int) -> dict:
    code = f"""\
-- envío {i}
suma :: [Int] -> Int
suma = foldr (+) 0

enOrden :: Arbol a -> [a]
enOrden Hoja = []
enOrden (Nodo l x r) = enOrden l ++ [x] ++ enOrden r

profundidad :: Arbol a -> Int
profundidad Hoja = 0
profundidad (Nodo l _ r) = 1 + max (profundidad l) (profundidad r)
"""
    return {"student_code": code, "definitions": DEFINITIONS, "tests": TESTS}


def main() -> None:
    client = httpx.Client(transport=httpx.HTTPTransport(uds=SOCKET), base_url="http://executor", timeout=120)
    # Un envío previo para que la caché ya tenga Judge y las definiciones.
    client.post("/run", json=submission(-1)).raise_for_status()

    latencies = [0.0] * N
    statuses = [""] * N
    barrier = threading.Barrier(N)

    def worker(i: int) -> None:
        barrier.wait()
        t = time.perf_counter()
        r = client.post("/run", json=submission(i))
        latencies[i] = time.perf_counter() - t
        statuses[i] = r.json().get("status", str(r.status_code)) if r.status_code == 200 else str(r.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.perf_counter() - start

    lat = sorted(latencies)
    print(f"{N} envíos simultáneos en {total:.2f} s ({N / total:.1f} envíos/s)")
    print(f"latencia: mín {lat[0]:.2f} s, mediana {lat[N // 2]:.2f} s, p90 {lat[int(N * 0.9)]:.2f} s, máx {lat[-1]:.2f} s")
    print("veredictos:", dict(Counter(statuses)))
    print("health:", client.get("/health").json())


if __name__ == "__main__":
    main()
