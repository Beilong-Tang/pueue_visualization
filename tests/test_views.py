"""Pagination, filters, wait reasons, rendering and the HTTP server (fixture data, no daemon needed)."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pueue_viz import render as r
from pueue_viz import server, views
from pueue_viz.pueue import PueueClient, Task

FIX = Path(__file__).parent / "fixtures"


def fake_runner(args):
    data = json.loads((FIX / "status.json").read_text())
    # one extra task with HTML in its command and label to check escaping
    evil = dict(data["tasks"]["0"], id=50, command="<script>alert(1)</script>", label="<b>x</b>")
    data["tasks"]["50"] = evil
    return json.dumps(data)


def make_app(page_size=20):
    return views.App(client=PueueClient(runner=fake_runner), page_size=page_size, host="testhost")


class TestPaginate(unittest.TestCase):
    def test_pages(self):
        items = list(range(45))
        self.assertEqual(views.paginate(items, 1, 20), (list(range(20)), 1, 3))
        self.assertEqual(views.paginate(items, 3, 20), (list(range(40, 45)), 3, 3))
        self.assertEqual(views.paginate(items, 99, 20)[1:], (3, 3))
        self.assertEqual(views.paginate([], 1, 20), ([], 1, 1))
        self.assertEqual(views.parse_page("abc"), 1)

    def test_pager_html(self):
        html = r.pagination("/", {"q": "x"}, 6, 20, 400, 20)
        self.assertIn("101–120 of 400", html)
        self.assertIn('href="/?q=x&amp;page=7"', html)


class TestFilterSort(unittest.TestCase):
    def setUp(self):
        self.snap = make_app().client.snapshot().value
        self.tasks = self.snap.tasks

    def ids(self, tasks):
        return sorted(t.task_id for t in tasks)

    def test_search(self):
        self.assertEqual(self.ids(views.apply_filters(self.tasks, {"q": "depfail"})), [9])
        self.assertEqual(self.ids(views.apply_filters(self.tasks, {"q": "MY LABEL"})), [0])
        self.assertEqual(self.ids(views.apply_filters(self.tasks, {"q": "#7"})), [7])
        self.assertEqual(self.ids(views.apply_filters(self.tasks, {"q": "/tmp"})), [0, 50])

    def test_state_and_group(self):
        self.assertEqual(self.ids(views.apply_filters(self.tasks, {"state": "failed"})), [2, 7, 9])
        self.assertEqual(self.ids(views.apply_filters(self.tasks, {"state": "Killed"})), [1])
        self.assertEqual(self.ids(views.apply_filters(self.tasks, {"group": "gpu"})), [1, 2, 7, 8, 10, 11])

    def test_dates(self):
        self.assertEqual(len(views.apply_filters(self.tasks, {"from": "2026-11-07", "to": "2026-11-07"})), 13)
        self.assertEqual(len(views.apply_filters(self.tasks, {"from": "2026-11-08"})), 0)

    def test_sort_missing_last(self):
        params = {"sort": "end", "order": "asc"}
        out = views.sort_tasks(self.tasks, params, "id")
        self.assertIsNone(out[-1].end)
        self.assertIsNotNone(out[0].end)
        params = {"sort": "bogus"}
        out = views.sort_tasks(self.tasks, params, "id")
        self.assertEqual(params["sort"], "id")
        self.assertEqual(out[0].task_id, 50)

    def test_wait_reasons(self):
        by_id = {t.task_id: t for t in self.tasks}
        self.assertEqual(views.wait_reason(by_id[5], self.snap)[0], "Stashed")
        self.assertEqual(views.wait_reason(by_id[8], self.snap)[0], "Scheduled")
        self.assertEqual(views.wait_reason(by_id[4], self.snap)[0], "No free slot")
        dep = Task(99, state="Queued", dependencies=[10])  # 10 is still running
        self.assertIn("#10", views.wait_reason(dep, self.snap)[1])
        self.snap.groups["default"]["status"] = "Paused"
        self.assertEqual(views.wait_reason(by_id[4], self.snap)[0], "Group paused")


class TestTimezone(unittest.TestCase):
    def test_viewer_zone(self):
        t = datetime(2026, 11, 3, 2, 58, tzinfo=timezone.utc)
        token = r.DISPLAY_TZ.set(r.parse_tz("America/New_York"))
        try:
            self.assertIn(">11-02 21:58</time>", r.fmt_dt(t, now=t))
        finally:
            r.DISPLAY_TZ.reset(token)
        self.assertIsNone(r.parse_tz("../../etc/passwd"))


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = type("H", (server.Handler,), {"app": make_app(page_size=5), "token": "tok", "cookie_name": "pv"})
        handler.log_message = lambda *a: None
        cls.httpd = server.bind("127.0.0.1", 18900, handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path, cookie="pv=tok"):
        req = urllib.request.Request(self.base + path, headers={"Cookie": cookie} if cookie else {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_auth(self):
        self.assertEqual(self.get("/", cookie=None)[0], 403)
        self.assertEqual(self.get("/", cookie="pv=wrong")[0], 403)
        self.assertEqual(self.get("/?token=wrong", cookie=None)[0], 403)
        self.assertEqual(self.get("/static/style.css", cookie=None)[0], 200)

    def test_pages(self):
        for path in ("/", "/running", "/queue", "/finished", "/?page=3&sort=elapsed",
                     "/finished?state=failed", "/task/0", "/task/4", "/task/9"):
            status, html = self.get(path)
            self.assertEqual(status, 200, path)
        self.assertEqual(self.get("/task/999")[0], 404)
        self.assertEqual(self.get("/task/abc")[0], 404)
        self.assertEqual(self.get("/static/../server.py")[0], 404)

    def test_content(self):
        _, html = self.get("/")
        self.assertIn("1–5 of 13", html)
        self.assertIn("<h2>Groups</h2>", html)
        _, html = self.get("/queue")
        self.assertIn("No free slot", html)
        _, html = self.get("/task/9")
        self.assertIn("Dependency failed", html)
        self.assertIn('href="/task/2"', html)

    def test_escaping_and_no_envs(self):
        _, html = self.get("/?q=script")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        _, html = self.get("/task/50")
        self.assertNotIn("<b>x</b>", html)
        for path in ("/", "/task/0"):
            self.assertNotIn("SECRET_TOKEN", self.get(path)[1])


if __name__ == "__main__":
    unittest.main()
