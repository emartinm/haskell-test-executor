"""Cache of precompiled modules: the harness (Judge) and the definitions of each problem.

With the cache, each submission only compiles the student code and the tests,
and only the linking step remains as a significant fixed cost.

Security: the cached .hi/.o files are reused by the submissions of every
student of a problem. That is why they are compiled in the box *before*
anything from the student is written into it: a submission cannot influence
what gets cached.

The cache lives in memory (LRU) and is thread-safe.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Dict, Optional

from .judge import Config, Limits, compile_env, compile_limits, ghc_flags
from .sandbox import Box

MAX_ARTIFACT_BYTES = 64 << 20


class ModuleCache:
    def __init__(self, config: Config, max_entries: int = 256):
        self.config = config
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0
        self._entries: "OrderedDict[str, Dict[str, bytes]]" = OrderedDict()
        self._building: Dict[str, threading.Lock] = {}
        self._mutex = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def provide(self, box: Box, module: str, source: str, limits: Limits) -> bool:
        """Leaves the compiled module (.hs, .hi, .o) in the box.

        If it is not cached, it is compiled in the box and stored. Returns False
        if it does not compile; the compilation of the submission will then try
        again and report the corresponding error.
        """
        key = self._key(module, source)
        with self._mutex:
            lock = self._building.setdefault(key, threading.Lock())
        with lock:  # only one thread compiles each module
            with self._mutex:
                entry = self._entries.get(key)
                if entry is not None:
                    self._entries.move_to_end(key)
                    self.hits += 1
                else:
                    self.misses += 1
            if entry is not None:
                for name, data in entry.items():
                    box.write_bytes(name, data)
                return True
            entry = self._build(box, module, source, limits)
            if entry is None:
                return False
            with self._mutex:
                self._entries[key] = entry
                while len(self._entries) > self.max_entries:
                    old, _ = self._entries.popitem(last=False)
                    self._building.pop(old, None)
            return True

    def _key(self, module: str, source: str) -> str:
        h = hashlib.sha256()
        for part in (module, source, self.config.ghc, " ".join(ghc_flags(self.config))):
            h.update(part.encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()

    def _build(self, box: Box, module: str, source: str, limits: Limits) -> Optional[Dict[str, bytes]]:
        hs = f"{module}.hs"
        box.write(hs, source)
        meta = box.run(
            [self.config.ghc, *ghc_flags(self.config), "-c", hs],
            compile_limits(limits),
            dirs=[self.config.ghc_root],
            env=compile_env(self.config),
        )
        if not meta.ok:
            return None
        hi = box.read_bytes(f"{module}.hi", MAX_ARTIFACT_BYTES)
        obj = box.read_bytes(f"{module}.o", MAX_ARTIFACT_BYTES)
        if not hi or not obj:
            return None
        # Order matters: the .o must end up newer than the .hi and the .hs.
        return {hs: source.encode("utf-8"), f"{module}.hi": hi, f"{module}.o": obj}
