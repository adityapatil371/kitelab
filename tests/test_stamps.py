"""Every module that can change a number on the page is in the dashboard stamp.

Added 2026-09-07. The audit that day touched portfolio.py, curves.py,
validation.py, contracts.py, config.py and scripts/dashboard_data.py one at a
time on an isolated copy and the dashboard digest did not move for any of
them, so an edit to the account engine left `refresh` reporting "already
current" over superseded numbers -- the 2026-09-02 holygrail.py failure one
layer up. This test walks the import graph of the build script and fails when
a kitelab module it reaches is not covered by signals.stamp(account=True),
so the next omission is caught the day it is made rather than a day of
published numbers later.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

from kitelab import signals

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "kitelab"

# Modules that cannot change a number: they move bytes, print progress, or
# ARE the stamp. Anything else reachable from the build must be stamped.
CANNOT_CHANGE_A_NUMBER = {"signals", "progress", "dashboard_server", "auth",
                          "fetch", "__init__"}


def _imports(path: pathlib.Path) -> set[str]:
    """kitelab.<name> modules imported by one file, including relative imports."""
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("kitelab."):
                    out.add(a.name.split(".")[1])
        elif isinstance(node, ast.ImportFrom):
            if node.module == "kitelab":
                out |= {a.name for a in node.names}
            elif node.module and node.module.startswith("kitelab."):
                out.add(node.module.split(".")[1])
            elif node.level and node.module:            # from .x import y
                out.add(node.module.split(".")[0])
            elif node.level and not node.module:        # from . import x, y
                out |= {a.name for a in node.names}
    return {m for m in out if (PKG / f"{m}.py").exists()}


def reachable_from(script: pathlib.Path) -> set[str]:
    seen: set[str] = set()
    todo = list(_imports(script))
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo.extend(_imports(PKG / f"{m}.py") - seen)
    return seen


class DashboardStampCoversTheNumbersPath(unittest.TestCase):
    def test_every_reachable_module_is_stamped(self):
        reached = reachable_from(ROOT / "scripts" / "dashboard_data.py")
        stamped = {pathlib.Path(f).name[:-3] for f in signals.stamped_files(account=True)}
        missing = sorted(reached - stamped - CANNOT_CHANGE_A_NUMBER)
        self.assertEqual(missing, [], f"reachable from the build but not stamped: {missing}")

    def test_the_build_script_itself_is_stamped(self):
        files = signals.stamped_files(account=True)
        self.assertIn("../scripts/dashboard_data.py", files)

    def test_signal_caches_do_not_depend_on_the_account_modules(self):
        narrow = set(signals.stamped_files(account=False))
        for f in ("portfolio.py", "curves.py", "validation.py"):
            self.assertNotIn(f, narrow)

    def test_account_stamp_differs_from_cache_stamp(self):
        # Same universe, same files: the two stamps must still differ, because
        # they cover different code, or the wider one is not doing anything.
        a = signals.stamp([], account=False)
        b = signals.stamp([], account=True)
        self.assertNotEqual(a["code"], b["code"])
        self.assertTrue(b.get("account"))


if __name__ == "__main__":
    unittest.main()
