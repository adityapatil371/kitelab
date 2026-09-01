"""Log in to Kite and cache today's access token.

    python -m scripts.login

You log in yourself in your own browser. Nothing here asks for your Zerodha
password, PIN, or TOTP -- only the request_token from the redirect URL afterwards.
"""
from kitelab import auth, config


def main() -> None:
    cfg = config.require_secrets(config.load())
    auth.interactive_login(cfg)


if __name__ == "__main__":
    main()
