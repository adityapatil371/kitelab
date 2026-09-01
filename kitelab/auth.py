"""Kite session handling.

Kite access tokens expire every morning (Zerodha invalidates them around 06:00 IST),
so this caches the token for the day and prompts for a fresh login when it goes stale.

You perform the login yourself in your own browser. This module never sees, asks for,
or stores your Zerodha password, PIN, or TOTP -- only the short-lived request_token
that Kite hands back in the redirect URL after you have already logged in.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kiteconnect import KiteConnect

from .config import TOKEN_PATH, Config


def _load_cached(api_key: str) -> str | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        blob = json.loads(TOKEN_PATH.read_text())
    except json.JSONDecodeError:
        return None
    if blob.get("api_key") != api_key:
        return None
    # Tokens die at ~06:00 IST. Anything not issued today is assumed dead.
    if blob.get("issued_on") != date.today().isoformat():
        return None
    return blob.get("access_token")


def _save(api_key: str, access_token: str) -> None:
    TOKEN_PATH.write_text(
        json.dumps(
            {
                "api_key": api_key,
                "access_token": access_token,
                "issued_on": date.today().isoformat(),
                "issued_at": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        )
    )
    TOKEN_PATH.chmod(0o600)


def extract_request_token(pasted: str) -> str:
    """Accept either a bare request_token or the whole redirect URL."""
    pasted = pasted.strip()
    if "request_token" in pasted:
        qs = parse_qs(urlparse(pasted).query)
        if qs.get("request_token"):
            return qs["request_token"][0]
        found = re.search(r"request_token=([A-Za-z0-9]+)", pasted)
        if found:
            return found.group(1)
    return pasted


def interactive_login(cfg: Config) -> str:
    """Print the login URL, wait for the user to paste back the redirect."""
    kite = KiteConnect(api_key=cfg.api_key)
    print("\n1. Open this URL in your browser and log in to Zerodha:\n")
    print(f"   {kite.login_url()}\n")
    print("2. After login you land on your app's redirect URL. Copy the whole")
    print("   address bar contents (or just the request_token= value) and paste it here.\n")
    pasted = input("   Paste redirect URL or request_token: ")
    request_token = extract_request_token(pasted)
    if not request_token:
        raise SystemExit("No request_token found in what you pasted.")
    session = kite.generate_session(request_token, api_secret=cfg.api_secret)
    access_token = session["access_token"]
    _save(cfg.api_key, access_token)
    print(f"\n   Logged in as {session.get('user_name')} ({session.get('user_id')}).")
    print(f"   Token cached in {TOKEN_PATH.relative_to(Path.cwd()) if TOKEN_PATH.is_relative_to(Path.cwd()) else TOKEN_PATH}, valid until tomorrow ~06:00 IST.\n")
    return access_token


def client(cfg: Config, allow_login: bool = True) -> KiteConnect:
    """Return an authenticated KiteConnect, logging in if the cached token is stale."""
    kite = KiteConnect(api_key=cfg.api_key)
    token = _load_cached(cfg.api_key)
    if token:
        kite.set_access_token(token)
        try:
            kite.profile()  # cheap liveness check
            return kite
        except Exception:
            print("Cached token was rejected; a fresh login is needed.")
    if not allow_login:
        raise SystemExit("No valid access token. Run: python -m scripts.login")
    kite.set_access_token(interactive_login(cfg))
    return kite
