"""Parsing `pueue status --json` (fixture captured from pueue 4.0.4; env vars replaced)."""

import json
import unittest
from datetime import timedelta
from pathlib import Path

from pueue_viz.pueue import PueueClient, PueueError, Task, parse_status, parse_time

FIX = Path(__file__).parent / "fixtures"


def load():
    return parse_status((FIX / "status.json").read_text())


class TestParseTime(unittest.TestCase):
    def test_nanoseconds_and_offset(self):
        dt = parse_time("2026-11-07T22:29:09.975630231-05:00")
        self.assertEqual((dt.year, dt.hour, dt.microsecond), (2026, 22, 975630))
        self.assertEqual(dt.utcoffset(), timedelta(hours=-5))

    def test_bad_values(self):
        self.assertIsNone(parse_time(None))
        self.assertIsNone(parse_time(""))
        self.assertIsNone(parse_time("yesterday"))
        self.assertIsNotNone(parse_time("2026-11-07T22:29:09Z").tzinfo)
        self.assertIsNotNone(parse_time("2026-11-07T22:29:09").tzinfo)  # naive -> server local


class TestParseStatus(unittest.TestCase):
    def setUp(self):
        tasks, self.groups = load()
        self.by_id = {t.task_id: t for t in tasks}
        self.tasks = tasks

    def test_groups(self):
        self.assertEqual(self.groups, {"default": {"status": "Running", "parallel": 1},
                                       "gpu": {"status": "Running", "parallel": 2}})

    def test_order_newest_first(self):
        self.assertEqual([t.task_id for t in self.tasks], sorted(self.by_id, reverse=True))

    def test_states(self):
        got = {i: (t.state, t.category, t.state_group, t.state_label) for i, t in self.by_id.items()}
        self.assertEqual(got[0], ("Success", "finished", "completed", "Success"))
        self.assertEqual(got[1], ("Killed", "finished", "cancelled", "Killed"))
        self.assertEqual(got[2], ("Failed", "finished", "failed", "Failed (3)"))
        self.assertEqual(got[3], ("Paused", "running", "paused", "Paused"))
        self.assertEqual(got[4], ("Queued", "pending", "pending", "Queued"))
        self.assertEqual(got[5], ("Stashed", "pending", "pending", "Stashed"))
        self.assertEqual(got[7][3], "Failed (127)")
        self.assertEqual(got[9], ("DependencyFailed", "finished", "failed", "Dependency failed"))
        self.assertEqual(got[10][:2], ("Running", "running"))

    def test_fields(self):
        t = self.by_id[0]
        self.assertEqual((t.label, t.path, t.group, t.command), ("my label", "/tmp", "default", "echo hi; sleep 1"))
        self.assertTrue(t.created and t.enqueued and t.start and t.end)
        self.assertEqual(self.by_id[9].dependencies, [2])
        self.assertIsNotNone(self.by_id[8].enqueue_at)
        self.assertIsNone(self.by_id[5].enqueue_at)
        self.assertEqual(self.by_id[1].label, "")  # null label

    def test_durations(self):
        t = self.by_id[0]
        self.assertEqual(t.elapsed, t.end - t.start)
        self.assertGreater(self.by_id[10].elapsed, timedelta(0))  # running: up to now
        self.assertIsNone(self.by_id[4].elapsed)  # never started
        self.assertIsNotNone(self.by_id[4].waited)  # queued: waiting until now
        self.assertIsNone(self.by_id[5].waited)  # stashed: not in the queue

    def test_envs_are_dropped(self):
        self.assertFalse(any(hasattr(t, "envs") for t in self.tasks))
        self.assertNotIn("SECRET_TOKEN", repr(self.tasks))


class TestOtherShapes(unittest.TestCase):
    def one(self, task_extra: dict) -> Task:
        raw = {"id": 1, "command": "x", "path": "/", "group": "default", "label": None,
               "dependencies": [], "created_at": "2026-11-01T10:00:00-05:00", **task_extra}
        tasks, _ = parse_status(json.dumps({"tasks": {"1": raw}, "groups": {}}))
        return tasks[0]

    def test_failed_to_spawn(self):
        t = self.one({"status": {"Done": {"start": None, "end": None, "result": {"FailedToSpawn": "no such file"}}}})
        self.assertEqual((t.state, t.spawn_error, t.state_group), ("FailedToSpawn", "no such file", "failed"))

    def test_locked_keeps_previous_times(self):
        t = self.one({"status": {"Locked": {"previous_status": {"Queued": {"enqueued_at": "2026-11-01T10:01:00-05:00"}}}}})
        self.assertEqual((t.state, t.category), ("Locked", "pending"))
        self.assertIsNotNone(t.enqueued)

    def test_pueue3_shape(self):
        t = self.one({"status": {"Done": "Success"}, "start": "2026-11-01T10:00:00-05:00",
                      "end": "2026-11-01T11:30:00-05:00", "enqueued_at": None})
        self.assertEqual((t.state, t.elapsed), ("Success", timedelta(hours=1, minutes=30)))
        t = self.one({"status": "Running", "start": "2026-11-01T10:00:00-05:00"})
        self.assertEqual(t.state, "Running")
        t = self.one({"status": {"Done": {"Failed": 2}}})
        self.assertEqual(t.state_label, "Failed (2)")

    def test_missing_group_is_added(self):
        _, groups = parse_status(json.dumps({"tasks": {"1": {"id": 1, "group": "x", "status": "Queued"}},
                                             "groups": {}}))
        self.assertIn("x", groups)

    def test_garbage(self):
        with self.assertRaises(PueueError):
            parse_status("not json")
        with self.assertRaises(PueueError):
            parse_status(json.dumps({"tasks": {"1": {"no_id": True}}}))


class TestClient(unittest.TestCase):
    def test_args_and_cache(self):
        calls = []

        def runner(args):
            calls.append(args)
            return (FIX / "status.json").read_text()

        c = PueueClient(config="/x/pueue.yml", runner=runner, ttl=60)
        self.assertEqual(c.status_args(), ["pueue", "-c", "/x/pueue.yml", "status", "--json"])
        self.assertEqual(c.task(2).state, "Failed")
        self.assertIsNone(c.task(999))
        self.assertEqual(len(calls), 1)  # cached

    def test_keeps_last_good_value(self):
        state = {"fail": False}

        def runner(args):
            if state["fail"]:
                raise PueueError("daemon down")
            return (FIX / "status.json").read_text()

        c = PueueClient(runner=runner, ttl=0)
        c.snapshot()
        state["fail"] = True
        res = c.snapshot()
        self.assertEqual(res.error, "daemon down")
        self.assertEqual(len(res.value.tasks), 12)


if __name__ == "__main__":
    unittest.main()
