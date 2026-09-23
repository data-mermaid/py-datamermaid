# /// script
# requires-python = ">=3.10"
# dependencies = ["datamermaid[pandas]"]
#
# [tool.uv.sources]
# datamermaid = { path = "..", editable = true }
# ///
"""Raster statistics around every site of one project, in parallel.

    export MERMAID_API_KEY='mmd_<key_id>.<secret>'
    uv run examples/zonal_stats.py --url https://example.test/depth.tif

``--url`` is a Cloud Optimized GeoTIFF the Zonal Stats service can read; there
is no public sample raster, so bring your own.  Each site is a point, so the
example buffers it by ``--radius`` metres and asks for one set of statistics per
site.  The requests go to the Zonal Stats service, which is public: only the
project's sites need credentials.
"""

from __future__ import annotations

import argparse
import os
import sys

from datamermaid import (
    AuthenticationError,
    AuthFlowError,
    MermaidClient,
    MermaidConnectionError,
    NotFoundError,
    Project,
)

#: Buffer around each site, in metres.  A site is a point, so without one the
#: service reads a single pixel.
DEFAULT_RADIUS = 500.0

#: Statistics to ask for.  ``aoi_area`` and ``data_area`` always come back too.
DEFAULT_STATS = ["mean", "min", "max", "count"]

#: Requests in flight at once.  The workers share the client's rate-limit
#: backoff, so this is politeness rather than throughput.
DEFAULT_MAX_WORKERS = 4

#: Sites to cover.  A batch is one request per site, so a large project is
#: worth capping while you are experimenting.
DEFAULT_LIMIT = 25

CREDENTIALS_HELP = """\
This example needs credentials with access to a project.  Either export an
API key:

    export MERMAID_API_KEY='mmd_<key_id>.<secret>'

or log in through the browser once:

    uv run examples/oauth_login.py
"""


def positive_int(text: str) -> int:
    """An argparse type for a whole number of at least 1."""

    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        # A negative slice stop would make `sites.list()[:limit]` fetch every
        # page, the opposite of a cap, and 0 would measure nothing.
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url",
        required=True,
        help="Cloud Optimized GeoTIFF to read, as an https:// or s3:// URL",
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("MERMAID_PROJECT_ID"),
        help="project whose sites to cover; defaults to $MERMAID_PROJECT_ID, "
        "then to the first visible project",
    )
    parser.add_argument(
        "--stats",
        nargs="+",
        default=DEFAULT_STATS,
        help=f"statistics to compute (default: {' '.join(DEFAULT_STATS)})",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=DEFAULT_RADIUS,
        help=f"buffer around each site, in metres (default: {DEFAULT_RADIUS:g})",
    )
    parser.add_argument(
        "--max-workers",
        type=positive_int,
        default=DEFAULT_MAX_WORKERS,
        help=f"requests in flight at once (default: {DEFAULT_MAX_WORKERS})",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=DEFAULT_LIMIT,
        help=f"how many sites to cover (default: {DEFAULT_LIMIT})",
    )
    return parser.parse_args()


def pick_project(client: MermaidClient, project_id: str | None) -> Project | None:
    """Resolve the project to read, announcing an auto-selected one."""

    if project_id:
        return client.projects.get(project_id)

    first_page = client.projects.list(limit=1)[:1]
    if not first_page:
        return None
    project = first_page[0]
    print(f"No --project-id given; using the first visible project: {project.name}")
    return project


def main() -> int:
    args = parse_args()

    with MermaidClient() as client:
        print(f"API root: {client.base_url}")
        print(f"Zonal Stats service: {client.zonal_stats_url}  (no credentials are sent to it)")

        try:
            record = pick_project(client, args.project_id)
        except (AuthenticationError, AuthFlowError) as error:
            # AuthFlowError covers a cached OAuth token that can no longer be
            # refreshed, which never reaches the API at all.
            print(f"\nCould not authenticate: {error}\n", file=sys.stderr)
            print(CREDENTIALS_HELP, file=sys.stderr)
            return 0
        except NotFoundError:
            # A project the credentials cannot see is a 404, not a 403.
            print(
                f"\nProject {args.project_id} does not exist, or is invisible to "
                "these credentials.\n",
                file=sys.stderr,
            )
            print(CREDENTIALS_HELP, file=sys.stderr)
            return 0

        if record is None:
            print("\nNo projects are visible with these credentials.\n", file=sys.stderr)
            print(CREDENTIALS_HELP, file=sys.stderr)
            return 0

        print(f"\nProject: {record.name}  ({record.id})")

        try:
            # Slicing the lazy list stops fetching as soon as it has enough.
            sites = client.projects(record).sites.list()[: args.limit]
        except (AuthenticationError, AuthFlowError) as error:
            print(f"\nThe project's sites are not readable: {error}\n", file=sys.stderr)
            print(CREDENTIALS_HELP, file=sys.stderr)
            return 0

        located = [site for site in sites if site.location]
        print(f"Sites: {len(sites)} fetched, {len(located)} with a location")
        if not located:
            print("\nNothing to measure: none of these sites has a location.", file=sys.stderr)
            return 0

        # Nothing is requested here.  The options are checked at the call, so a
        # misspelled statistic fails now rather than on a worker thread.
        try:
            batch = client.zonal_stats.raster.batch(
                located,
                url=args.url,
                stats=args.stats,
                radius=args.radius,
                max_workers=args.max_workers,
                errors="return",
            )
        except (TypeError, ValueError) as error:
            print(f"\n{error}", file=sys.stderr)
            return 2
        print(
            f"\nReading {args.url}\n"
            f"  {len(batch)} requests, {batch.max_workers} at a time, "
            f"{args.radius:g} m around each site"
        )

        # With `errors="return"` nothing here raises: each failed site comes
        # back as a row whose `error` column holds the exception.
        frame = batch.to_df()

    errors = frame.get("error")
    if (
        errors is not None
        and errors.map(lambda error: isinstance(error, MermaidConnectionError)).all()
    ):
        # Every request failed to connect, so the service itself is down or
        # the URL is wrong.  One message reads better than a table of failures.
        print(
            f"\nThe Zonal Stats service could not be reached: {errors.iloc[0]}",
            file=sys.stderr,
        )
        return 0

    # Rows come back in input order, so row i belongs to site i whichever
    # worker finished first.  `label` is the site id; the name reads better.
    frame.insert(0, "site", [site.name for site in located])
    frame = frame.drop(columns=["label", "source"], errors="ignore")

    failed = 0
    if "error" in frame:
        reasons = frame["error"].map(
            lambda error: type(error).__name__ if isinstance(error, Exception) else None
        )
        frame["error"] = reasons
        failed = int(reasons.notna().sum())
    print(f"\nStatistics  ({len(frame)} rows x {len(frame.columns)} columns, {failed} failed)")
    print(frame.head(20).to_string(index=False))

    mean_column = next((column for column in frame.columns if column.endswith("_mean")), None)
    if mean_column and frame[mean_column].notna().any():
        print(f"\n{mean_column} across the sites:")
        print(frame[mean_column].describe().round(3).to_string())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
