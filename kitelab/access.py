"""The door. One shared passphrase, for when the dashboard leaves this machine.

Ported from `livedesk/access.py` on 2026-09-19, deliberately as a SIBLING
rather than a shared module: the two projects have separate git histories and
nothing imports across them (`/work/CLAUDE.md`). Fixes travel by hand, and the
list of differences below is what to re-read when one does.

WHY THIS IS SIMPLER THAN LIVEDESK'S
-----------------------------------
livedesk asks for a NAME as well as a passphrase, because it has a shared book:
anyone who gets in can clear a stop somebody else is relying on, so every change
is stamped with who made it. **This dashboard has nothing to write.** Every
control on the page selects among results computed before the server started,
each viewer's selections are their own, and no request can change a byte on
disk. So there is no change to stamp and no name to ask for -- adding one would
be ceremony, and ceremony that looks like a login is worse than none.

WHAT THE PASSPHRASE IS ACTUALLY PROTECTING, THEN
------------------------------------------------
Not the data's integrity -- disclosure. `dashboard.json` is a complete account
of which rules were tried over which stocks and what each one did, including
1,000 symbol names and five years of equity curves. That is the owner's
research. A `trycloudflare.com` URL is unguessable but not private: it is a
public DNS name on a public certificate log, and hosts on it get scanned. So the
bar is "the nine people who were sent the passphrase", not "whoever finds the
link".

Sessions live in memory only. Restarting the server logs everybody out, which is
the right default for something started for an afternoon and then stopped.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time

COOKIE = "kitelab"

# A passphrase shorter than this is refused outright. On a public URL the
# passphrase is the entire defence, and short ones are guessed by machines that
# do nothing else all day.
MIN_PASSPHRASE = 10

# Brute-force limits. The per-client counter is the useful one in ordinary use;
# the global counter is the one that holds when a client address cannot be
# trusted, which is the case behind any proxy that lets a caller set its own
# forwarding headers. Both are deliberately crude.
FAIL_WINDOW = 300.0        # seconds a failure is remembered
MAX_FAILS_PER_CLIENT = 8
MAX_FAILS_GLOBAL = 30


def suggest() -> str:
    """A passphrase worth using, for the error message that rejects a weak one."""
    words = ("anchor", "basket", "cinder", "damson", "ember", "fathom", "girder",
             "harbour", "ingot", "jetty", "kernel", "lantern", "marrow", "nutmeg")
    return "-".join(secrets.choice(words) for _ in range(4))


class Gate:
    """Who may see the dashboard. Nothing here decides what they may do to it."""

    def __init__(self, passphrase: str | None) -> None:
        self.passphrase = (passphrase or "").strip() or None
        self._sessions: set[str] = set()
        self._fails: dict[str, list[float]] = {}  # client key -> failure times
        self._global: list[float] = []
        self._lock = threading.Lock()

    @property
    def guarded(self) -> bool:
        return self.passphrase is not None

    def problem(self) -> str | None:
        """Why this passphrase is not fit to expose, or None if it is."""
        if not self.guarded:
            return "no passphrase is set"
        if len(self.passphrase) < MIN_PASSPHRASE:
            return (f"the passphrase is {len(self.passphrase)} characters; "
                    f"{MIN_PASSPHRASE} is the minimum. Try: {suggest()}")
        return None

    # -- sessions ------------------------------------------------------------

    def admits(self, cookie_header: str | None) -> bool:
        """May this request see the page?

        When no passphrase is set the dashboard is unguarded -- which `serve()`
        only permits on loopback, where the operating system is the door -- and
        every request is admitted.
        """
        if not self.guarded:
            return True
        token = _cookie_value(cookie_header, COOKIE)
        if not token:
            return False
        with self._lock:
            return token in self._sessions

    def login(self, client: str, passphrase: str) -> str | None:
        """Check the passphrase; on success return a fresh session token."""
        if not self.guarded:
            return None
        if self.locked_out(client):
            return None
        if not hmac.compare_digest(passphrase.strip(), self.passphrase):
            self._record_failure(client)
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions.add(token)
            self._fails.pop(client, None)
        return token

    def logout(self, cookie_header: str | None) -> None:
        token = _cookie_value(cookie_header, COOKIE)
        if token:
            with self._lock:
                self._sessions.discard(token)

    def watching(self) -> int:
        """How many sessions are open. Browser tabs, not people."""
        with self._lock:
            return len(self._sessions)

    # -- throttling ----------------------------------------------------------

    def locked_out(self, client: str) -> bool:
        now = time.time()
        with self._lock:
            self._global = [t for t in self._global if now - t < FAIL_WINDOW]
            if len(self._global) >= MAX_FAILS_GLOBAL:
                return True
            recent = [t for t in self._fails.get(client, []) if now - t < FAIL_WINDOW]
            self._fails[client] = recent
            return len(recent) >= MAX_FAILS_PER_CLIENT

    def _record_failure(self, client: str) -> None:
        now = time.time()
        with self._lock:
            self._fails.setdefault(client, []).append(now)
            self._global.append(now)


def _cookie_value(header: str | None, key: str) -> str | None:
    """One cookie out of a Cookie: header, without importing a cookie parser."""
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == key and value:
            return value
    return None
