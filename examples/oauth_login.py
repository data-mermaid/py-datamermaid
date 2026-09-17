# /// script
# requires-python = ">=3.10"
# dependencies = ["datamermaid"]
#
# [tool.uv.sources]
# datamermaid = { path = "..", editable = true }
# ///
"""Log in to MERMAID interactively, with no API key involved.

    uv run examples/oauth_login.py
    uv run examples/oauth_login.py --logout

``datamermaid.login()`` opens a browser, completes an authorization code flow
with PKCE and writes the tokens to a 0600 file under the user's config
directory.  Every later ``MermaidClient()`` in any process picks those tokens
up on its own, refreshing them when they expire, so this is a once-per-machine
step rather than something a data script has to repeat.
"""

from __future__ import annotations

import argparse

import datamermaid
from datamermaid import AuthFlowError, AuthTimeoutError, MermaidClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--logout",
        action="store_true",
        help="discard the cached tokens instead of logging in",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="log in again even if a usable token is already cached",
    )
    parser.add_argument(
        "--flow",
        default="auto",
        choices=["auto", "pkce", "implicit", "device", "manual"],
        help=(
            "which grant to use.  'auto' (the default) runs the browser flow "
            "locally and falls back to the device flow over SSH"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.logout:
        # Returns whether there was anything to forget.
        found = datamermaid.logout()
        print("Cached MERMAID tokens discarded." if found else "No cached tokens to discard.")
        return 0

    print(f"Logging in to MERMAID (flow={args.flow}) ...")
    try:
        # Over SSH or on a headless box `flow="auto"` already picks the device
        # flow: it prints a short code to enter on another machine's browser.
        # Pass `flow="device"` to insist on it, or `flow="manual"` for tenants
        # with the device grant switched off, which prints a URL to open and
        # asks for the redirect URL pasted back.
        auth = datamermaid.login(force=args.force, flow=args.flow)
    except AuthTimeoutError as error:
        print(f"Login timed out: {error}")
        return 1
    except AuthFlowError as error:
        print(f"Login failed: {error}")
        return 1

    # The returned credential can be handed straight to a client; a plain
    # `MermaidClient()` would find the same tokens in the cache.
    with MermaidClient(auth=auth) as client:
        me = client.me()
        print(f"\nSigned in as {me.full_name} <{me.email}>")
        for membership in me.projects:
            print(f"  {membership.name}")

    print("\nTokens are cached; `MermaidClient()` will now authenticate on its own.")
    print("Run this example with --logout to forget them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
