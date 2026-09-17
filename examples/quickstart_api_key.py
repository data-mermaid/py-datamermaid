# /// script
# requires-python = ">=3.10"
# dependencies = ["datamermaid"]
#
# [tool.uv.sources]
# datamermaid = { path = "..", editable = true }
# ///
"""Connect with an API key, then read the profile and the projects it can see.

    export MERMAID_API_KEY='mmd_<key_id>.<secret>'
    uv run examples/quickstart_api_key.py

``MermaidClient()`` picks the key up from ``MERMAID_API_KEY``; pass
``api_key=...`` to hand it one explicitly.
"""

from __future__ import annotations

import sys

from datamermaid import AnonymousAuth, AuthenticationError, MermaidClient

#: How many projects to print; the list itself is lazy and unbounded.
PROJECT_LIMIT = 10

CREDENTIALS_HELP = """\
This example needs MERMAID credentials.  Either create an API key in MERMAID
and export it:

    export MERMAID_API_KEY='mmd_<key_id>.<secret>'

or log in through the browser once, which caches a token the client reuses:

    uv run examples/oauth_login.py
"""


def main() -> int:
    with MermaidClient() as client:
        print(f"API root:    {client.base_url}")
        # `repr` of a credential never includes the secret half of the key.
        print(f"Credentials: {client.auth!r}")

        if isinstance(client.auth, AnonymousAuth):
            print("\nNo credentials found.\n", file=sys.stderr)
            print(CREDENTIALS_HELP, file=sys.stderr)
            return 0

        try:
            me = client.me()
            # Fetch the first page eagerly so an expired token surfaces here
            # rather than halfway through the loop below.
            projects = client.projects.list(limit=PROJECT_LIMIT)
            first_page = projects[:PROJECT_LIMIT]
        except AuthenticationError as error:
            print(f"\nThe API rejected the credentials: {error}\n", file=sys.stderr)
            print(CREDENTIALS_HELP, file=sys.stderr)
            return 0

        print(f"\nSigned in as {me.full_name} <{me.email}>")
        print(f"Member of {len(me.projects)} project(s).")

        print(f"\nProjects ({projects.count} total, showing up to {PROJECT_LIMIT}):")
        for project in first_page:
            countries = ", ".join(project.countries) or "-"
            print(f"  {project.name}  [{countries}]  {project.num_sites} sites")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
