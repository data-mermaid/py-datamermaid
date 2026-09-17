# /// script
# requires-python = ">=3.10"
# dependencies = ["datamermaid[pandas]"]
#
# [tool.uv.sources]
# datamermaid = { path = "..", editable = true }
# ///
"""Load the fish and benthic taxonomies into DataFrames and join them up.

    uv run examples/reference_data.py

Reference data is public, so this example needs no credentials at all.  It
also shows the two shapes the API answers in: the paginated list routes, which
come back as models and go straight to a DataFrame with ``.to_df()``, and
``/choices/``, the controlled vocabularies, which come back as plain dicts.
"""

from __future__ import annotations

import sys

import pandas as pd

from datamermaid import AuthenticationError, AuthFlowError, MermaidClient

#: Page size for the reference routes.  They are small enough to pull whole.
PAGE_SIZE = 1000

CREDENTIALS_HELP = """\
Reference data is normally public.  If this deployment requires credentials,
export an API key:

    export MERMAID_API_KEY='mmd_<key_id>.<secret>'

or log in once with `uv run examples/oauth_login.py`.
"""


def show(title: str, frame: pd.DataFrame) -> None:
    """Print a frame's shape and first rows under a heading."""

    print(f"\n{title}  ({len(frame)} rows x {len(frame.columns)} columns)")
    print(frame.head().to_string(index=False))


def fish_taxonomy(client: MermaidClient) -> pd.DataFrame:
    """Species joined to their genus, family and trophic vocabulary names."""

    species = client.fish_species.list(limit=PAGE_SIZE).to_df()
    genera = client.fish_genera.list(limit=PAGE_SIZE).to_df()
    families = client.fish_families.list(limit=PAGE_SIZE).to_df()

    # The list routes hand back ids, not labels, so the labels are a join away.
    genera = genera[["id", "name", "family"]].rename(columns={"name": "genus_name"})
    families = families[["id", "name"]].rename(columns={"name": "family_name"})
    taxonomy = species.merge(
        genera, left_on="genus", right_on="id", how="left", suffixes=("", "_genus")
    )
    taxonomy = taxonomy.merge(
        families, left_on="family", right_on="id", how="left", suffixes=("", "_family")
    )

    # `/choices/` holds the vocabularies the taxonomy's foreign keys point at.
    for column, choice_set in (
        ("trophic_group", "fishgrouptrophics"),
        ("functional_group", "fishgroupfunctions"),
    ):
        labels = pd.DataFrame(client.choices(choice_set))[["id", "name"]]
        labels = labels.rename(columns={"name": f"{column}_name"})
        taxonomy = taxonomy.merge(
            labels, left_on=column, right_on="id", how="left", suffixes=("", f"_{column}")
        )

    return taxonomy


def benthic_taxonomy(client: MermaidClient) -> pd.DataFrame:
    """Benthic attributes joined to their own top level category by name."""

    attributes = client.benthic_attributes.list(limit=PAGE_SIZE).to_df()
    categories = attributes[["id", "name"]].rename(columns={"name": "category_name"})
    return attributes.merge(
        categories, left_on="top_level_category", right_on="id", how="left", suffixes=("", "_cat")
    )


def main() -> int:
    with MermaidClient() as client:  # no credentials: this data is public
        print(f"API root: {client.base_url}")
        try:
            fish = fish_taxonomy(client)
            benthic = benthic_taxonomy(client)
            choice_sets = client.choices()
        except (AuthenticationError, AuthFlowError) as error:
            # These routes are public, but a stale cached OAuth token still
            # fails before the request is sent.
            print(f"\nThe request could not be made: {error}\n", file=sys.stderr)
            print(CREDENTIALS_HELP, file=sys.stderr)
            return 0

    show(
        "Fish species",
        fish[
            [
                "display_name",
                "family_name",
                "trophic_group_name",
                "functional_group_name",
                "max_length",
                "trophic_level",
            ]
        ],
    )

    print("\nSpecies per family (top 10):")
    print(fish["family_name"].value_counts().head(10).to_string())

    print("\nMean trophic level per trophic group:")
    print(fish.groupby("trophic_group_name")["trophic_level"].mean().round(2).to_string())

    show("Benthic attributes", benthic[["name", "category_name", "status"]])

    print("\nAttributes per top level category:")
    print(benthic["category_name"].value_counts().to_string())

    print(f"\n{len(choice_sets)} choice sets: {', '.join(sorted(choice_sets))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
