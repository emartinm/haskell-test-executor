"""Load test: N simultaneous submissions against the service through its Unix socket.

    python3 load_test.py [N] [socket]        (default 30 and /run/haskell-test-executor/executor.sock)

Each submission is different (student code is not cached) and they share the
definitions, like a class solving the same problem. Requires httpx.
"""

import sys
import threading
import time
from collections import Counter

import httpx

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
SOCKET = sys.argv[2] if len(sys.argv) > 2 else "/run/haskell-test-executor/executor.sock"

DEFINITIONS = "data Tree a = Leaf | Node (Tree a) a (Tree a)\n  deriving (Show, Eq)\n"
TESTS = [
    {"expr": "total []", "expected": "0"},
    {"expr": "total [1,2,3]", "expected": "6"},
    {"expr": "inOrder (Node (Node Leaf 1 Leaf) 2 (Node Leaf 3 Leaf))", "expected": "[1, 2, 3]"},
    {"expr": "depth (Node Leaf 'a' (Node Leaf 'b' Leaf))", "expected": "2"},
    {"expr": "total [1 .. 100000]", "expected": "5000050000", "hidden": True},
]


def submission(i: int) -> dict:
    code = f"""\
-- submission {i}
total :: [Int] -> Int
total = foldr (+) 0

inOrder :: Tree a -> [a]
inOrder Leaf = []
inOrder (Node l x r) = inOrder l ++ [x] ++ inOrder r

depth :: Tree a -> Int
depth Leaf = 0
depth (Node l _ r) = 1 + max (depth l) (depth r)
"""
    return {"student_code": code, "definitions": DEFINITIONS, "tests": TESTS}


def main() -> None:
    client = httpx.Client(transport=httpx.HTTPTransport(uds=SOCKET), base_url="http://executor", timeout=120)
    # A warm-up submission so that the cache already holds Judge and the definitions.
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
    print(f"{N} simultaneous submissions in {total:.2f} s ({N / total:.1f} submissions/s)")
    print(f"latency: min {lat[0]:.2f} s, median {lat[N // 2]:.2f} s, p90 {lat[int(N * 0.9)]:.2f} s, max {lat[-1]:.2f} s")
    print("verdicts:", dict(Counter(statuses)))
    print("health:", client.get("/health").json())


if __name__ == "__main__":
    main()
