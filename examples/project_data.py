# /// script
# requires-python = ">=3.10"
# dependencies = ["datamermaid[pandas]"]
#
# [tool.uv.sources]
# datamermaid = { path = "..", editable = true }
# ///
"""Pull one project's sites, sample events and fish observations into pandas.

    export MERMAID_API_KEY='mmd_<key_id>.<secret>'
    uv run examples/project_data.py --project-id <uuid>

With no ``--project-id`` the example takes the first project the credentials
can see (or ``MERMAID_PROJECT_ID``, if that is exported).  Sites and sample
events need access to the project; the aggregated observation view additionally
needs the project's fish belt data policy to allow it.
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

from datamermaid import (
    AuthenticationError,
    AuthFlowError,
    MermaidClient,
    NotFoundError,
    Project,
)
from datamermaid.pagination import to_dataframe

#: Rows to pull from the observation view.  It is one row per fish counted, so
#: a whole project easily runs to six figures.
DEFAULT_LIMIT = 500

#: Rows per request, kept separate from the row cap above; see the comment on
#: the observation call in `main`.
PAGE_SIZE = 500

CREDENTIALS_HELP = """\
This example needs credentials with access to a project.  Either export an
API key:

    export MERMAID_API_KEY='mmd_<key_id>.<secret>'

or log in through the browser once:

    uv run examples/oauth_login.py
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project-id",
        default=os.environ.get("MERMAID_PROJECT_ID"),
        help="project to read; defaults to $MERMAID_PROJECT_ID, then to the first visible project",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"how many observation rows to fetch (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--sample-date-after",
        help="only surveys on or after this date, as YYYY-MM-DD",
    )
    return parser.parse_args()


def show(title: str, frame: pd.DataFrame, columns: list[str]) -> None:
    """Print a frame's shape and the first rows of the columns that exist.

    The aggregated views are wide and their columns move between API releases,
    so anything missing is simply left out rather than raising.
    """

    present = [column for column in columns if column in frame.columns]
    print(f"\n{title}  ({len(frame)} rows x {len(frame.columns)} columns)")
    if frame.empty:
        print("  (no rows)")
        return
    print(frame[present].head().to_string(index=False))


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
        print(f"  countries: {', '.join(record.countries) or '-'}")
        print(f"  sites: {record.num_sites}   sample units: {record.num_sample_units}")

        # Building the handle issues no request; each collection below is the
        # same lazy PaginatedList as every other list route.
        project = client.projects(record)

        try:
            sites = project.sites.list().to_df()
            events = project.sample_events.list(
                sample_date_after=args.sample_date_after,
            ).to_df()
        except (AuthenticationError, AuthFlowError) as error:
            print(f"\nThe project's records are not readable: {error}\n", file=sys.stderr)
            print(CREDENTIALS_HELP, file=sys.stderr)
            return 0

        # The denormalized view: one row per fish counted, with the site,
        # management regime and transect already joined in.  The survey-shaped
        # view of the same data is `project.beltfish_methods.list()`.
        try:
            # `limit` is the API's page size, not a row cap, and `.to_df()`
            # materialises every page -- so the cap has to come from slicing
            # the lazy list, which stops fetching as soon as it has enough.
            rows = project.beltfishes.observations(
                limit=min(args.limit, PAGE_SIZE),
                sample_date_after=args.sample_date_after,
            )[: args.limit]
            observations = to_dataframe(rows)
        except (AuthenticationError, AuthFlowError) as error:
            print(f"\nFish belt observations are not readable here: {error}", file=sys.stderr)
            observations = pd.DataFrame()

    show("Sites", sites, ["name", "country_name", "reef_type_name", "reef_zone_name", "exposure"])
    if not sites.empty and "reef_type_name" in sites:
        print("\nSites per reef type:")
        print(sites["reef_type_name"].value_counts().to_string())

    show("Sample events", events, ["id", "site", "management", "sample_date"])
    if not events.empty and "sample_date" in events:
        years = pd.to_datetime(events["sample_date"]).dt.year
        print("\nSample events per year:")
        print(years.value_counts().sort_index().to_string())

    show(
        "Fish belt observations",
        observations,
        ["site_name", "sample_date", "fish_family", "fish_taxon", "size", "count", "biomass_kgha"],
    )
    if not observations.empty and {"fish_family", "biomass_kgha"} <= set(observations.columns):
        print("\nBiomass (kg/ha) per fish family, top 10 of the rows fetched:")
        biomass = observations.groupby("fish_family")["biomass_kgha"].sum()
        print(biomass.sort_values(ascending=False).head(10).round(1).to_string())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
