"""Unit tests of the verdict when the program dies without a harness report.

These paths (OOM kill, CPU limit, crash) are hard to trigger deterministically
in the sandbox, so they are tested here with fabricated isolate reports.
"""

import unittest

from runner.codegen import Submission, TestCase
from runner.judge import CRASH_MESSAGE, INTERNAL_ERROR, _verdict
from runner.sandbox import Meta

SUB = Submission(
    student_code="",
    tests=[TestCase("f 1", "1"), TestCase("f 2", "2"), TestCase("f 3", "3", hidden=True)],
)


def meta(**fields):
    return Meta({k.replace("_", "-"): str(v) for k, v in fields.items()})


def started(*indexes):
    return [{"event": "start", "test": i} for i in indexes]


class VerdictWithoutReportTest(unittest.TestCase):
    def test_crash_on_visible_test(self):
        r = _verdict(started(1, 2), meta(status="SG", exitsig=25), SUB)
        self.assertEqual(r, {
            "status": "RUNTIME_ERROR", "code": "program_crashed", "passed": 1, "total": 3,
            "test": {"index": 2, "expr": "f 2", "exception": CRASH_MESSAGE},
        })

    def test_crash_on_hidden_test(self):
        r = _verdict(started(1, 2, 3), meta(status="RE", exitcode=1), SUB)
        self.assertEqual(r, {
            "status": "RUNTIME_ERROR", "code": "program_crashed", "passed": 2, "total": 3,
            "test": {"index": 3, "hidden": True},
        })

    def test_oom_kill(self):
        r = _verdict(started(1), meta(status="SG", cg_oom_killed=1), SUB)
        self.assertEqual(r, {"status": "MEMORY_LIMIT", "passed": 0, "total": 3, "test": {"index": 1, "expr": "f 1"}})

    def test_cpu_and_wall_time_limits(self):
        r = _verdict(started(1), meta(status="TO", message="Time limit exceeded"), SUB)
        self.assertEqual(r["test"], {"index": 1, "expr": "f 1", "reason": "cpu"})
        r = _verdict(started(1), meta(status="TO", message="Time limit exceeded (wall clock)"), SUB)
        self.assertEqual(r["test"]["reason"], "wall")

    def test_no_test_started_is_internal_error(self):
        self.assertEqual(_verdict([], meta(status="RE", exitcode=1), SUB), INTERNAL_ERROR)


if __name__ == "__main__":
    unittest.main()
