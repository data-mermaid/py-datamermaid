# /// script
# requires-python = ">=3.10"
# dependencies = ["datamermaid[covariates,pandas]"]
#
# [tool.uv.sources]
# datamermaid = { path = "..", editable = true }
# ///
"""List the covariate datasets, describe one, and summarise it at two points.

    uv run examples/covariates.py

Reads the MERMAID covariates catalog, prints every dataset with its kind, then
describes NOAA CoralTemp (``daily_sst``) and computes the daily mean and
maximum sea surface temperature around two points for March 2026. The catalog
and the Zonal Stats service are public, so no credentials are needed.
"""

from __future__ import annotations

import sys

from datamermaid import MermaidClient, MermaidError

POINTS = [(-130.0, 30.0), (-132.0, 15.0)]


def main() -> int:
    with MermaidClient() as client:
        print("## Covariate collections\n")
        for collection in client.covariates.collections():
            print(collection.id, collection.kind, collection.title)

        sst = client.covariates.collection("daily_sst")
        print("\n## The SST collection\n")
        print(sst.describe())

        frame = sst.zonal_stats(
            POINTS,
            labels=["north", "south"],
            radius=5,
            stats=["mean", "max"],
            datetime=("2026-03-01", "2026-03-30"),
        ).to_df()

    print("\n## Warmest day at each point, in degrees Celsius\n")
    print(frame.groupby("label")["band_1_max"].max())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MermaidError as exc:
        print(f"The catalog or the Zonal Stats service failed: {exc}", file=sys.stderr)
        sys.exit(1)
