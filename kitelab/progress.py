"""A progress bar for the long rebuilds. Standard library only.

    from kitelab.progress import Bar

    bar = Bar(len(items), "grid")
    for item in items:
        ...
        bar.step()
    bar.close()

Why this exists: scripts.dashboard_data runs for ~20 minutes and used to print a
line only when a whole stage finished, so for minutes at a time there was no way
to tell a slow stage from a hung one. A count and an ETA are the difference
between waiting and wondering.

It writes to stderr with a carriage return, so the bar redraws in place and never
mixes into piped stdout. When stderr is not a terminal -- a log file, a
background task -- it prints a plain line every few percent instead, because
thousands of \\r updates in a log file are unreadable.
"""
from __future__ import annotations

import shutil
import sys
import time

_MIN_REDRAW = 0.1          # seconds; a terminal cannot show more than this
_FILE_STEP = 5.0           # percent; how often to print when not a terminal


def _clock(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


class Bar:
    """A counter with a bar, an elapsed time and an estimate of what is left."""

    def __init__(self, total: int, label: str = "", stream=None) -> None:
        self.total = max(int(total), 1)
        self.label = label
        self.count = 0
        self.started = time.monotonic()
        self._last_draw = 0.0
        self._last_pct = -_FILE_STEP
        self.stream = stream or sys.stderr
        self.tty = hasattr(self.stream, "isatty") and self.stream.isatty()
        self._draw(force=True)

    def step(self, n: int = 1) -> None:
        self.count = min(self.count + n, self.total)
        self._draw()

    def _draw(self, force: bool = False) -> None:
        now = time.monotonic()
        pct = 100.0 * self.count / self.total
        if not force:
            if self.tty:
                if now - self._last_draw < _MIN_REDRAW and self.count < self.total:
                    return
            elif pct - self._last_pct < _FILE_STEP and self.count < self.total:
                return
        self._last_draw, self._last_pct = now, pct

        elapsed = now - self.started
        # Rate over the whole run, not the last step: these loops are lumpy, and
        # an instantaneous rate makes the estimate jump around uselessly.
        left = (elapsed / self.count * (self.total - self.count)) if self.count else 0.0
        tail = (f"{self.count:,}/{self.total:,}  {_clock(elapsed)} elapsed"
                + (f"  ~{_clock(left)} left" if self.count else ""))

        if self.tty:
            width = max(shutil.get_terminal_size((100, 20)).columns, 60)
            room = width - len(self.label) - len(tail) - 14
            room = max(min(room, 40), 10)
            filled = int(room * self.count / self.total)
            bar = "█" * filled + "░" * (room - filled)
            self.stream.write(f"\r  {self.label:<14}[{bar}] {pct:3.0f}%  {tail}   ")
        else:
            self.stream.write(f"  {self.label:<14}{pct:3.0f}%  {tail}\n")
        self.stream.flush()

    def close(self) -> None:
        self.count = self.total
        self._draw(force=True)
        self.stream.write("\n" if self.tty else "")
        self.stream.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
