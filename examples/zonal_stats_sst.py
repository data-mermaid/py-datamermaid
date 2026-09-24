# /// script
# requires-python = ">=3.10"
# dependencies = ["datamermaid"]
#
# [tool.uv.sources]
# datamermaid = { path = "..", editable = true }
# ///
"""Stream daily sea surface temperature means around MERMAID project sites.

    export MERMAID_API_KEY='mmd_<key_id>.<secret>'
    uv run examples/zonal_stats_sst.py --project-id <uuid>

Reads NOAA CoralTemp from the MERMAID covariates catalog, then writes one JSONL row
per site and daily item. Defaults to five sites and three items from May
2026. Only reading the project's sites needs MERMAID credentials. A cached
login from ``uv run examples/oauth_login.py`` also works.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from datamermaid import (
    AuthenticationError,
    AuthFlowError,
    BatchFailure,
    MermaidClient,
    MermaidError,
)

# Verified public MERMAID catalog. Override if the catalog moves.
STAC_URL = "https://mermaid.prescient.earth/stac"
COLLECTION = "daily_sst"


def positive_int(text: str) -> int:
    """Keep input caps and worker counts positive."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project-id",
        default=os.environ.get("MERMAID_PROJECT_ID"),
        help="project UUID; defaults to $MERMAID_PROJECT_ID or the first visible project",
    )
    parser.add_argument("--stac-url", default=STAC_URL, help="STAC API root")
    parser.add_argument(
        "--datetime",
        default="2026-05-01/2026-05-30",
        help="STAC date or interval (default: 2026-05-01/2026-05-30)",
    )
    parser.add_argument("--limit", type=positive_int, default=5, help="maximum sites (default: 5)")
    parser.add_argument(
        "--max-items", type=positive_int, default=3, help="maximum daily STAC items (default: 3)"
    )
    parser.add_argument(
        "--radius", type=positive_int, default=500, help="site buffer in metres (default: 500)"
    )
    parser.add_argument(
        "--max-workers",
        type=positive_int,
        default=4,
        help="maximum concurrent requests (default: 4)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sst-results.jsonl"),
        help="JSONL output, overwritten if present (default: sst-results.jsonl)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        # Keep the HTTP client open until the stream has been fully consumed.
        with MermaidClient(covariates_url=args.stac_url) as client:
            if args.project_id:
                project = client.projects.get(args.project_id)
            else:
                projects = client.projects.list(limit=1)[:1]
                if not projects:
                    print(
                        "No visible projects. Supply credentials and --project-id.", file=sys.stderr
                    )
                    return 0
                project = projects[0]
            sites = client.projects(project).sites.list()[: args.limit]
            located = [site for site in sites if site.location]
            if not located:
                print("No located sites in this selection.", file=sys.stderr)
                return 0
            site_names = {site.id: site.name for site in located}
            print(f"Project: {project.name}; {len(located)} located sites")

            # Each SST item covers the globe. Pair each selected day with each
            # site; no per-site STAC search is needed. The collection picks the
            # raster route and the item's `data` asset.
            sst = client.covariates.collection(COLLECTION)
            job = sst.prepare_zonal_stats(
                located,
                datetime=args.datetime,
                max_items=args.max_items,  # Total cap, not merely the page size.
                bands=[1],
                stats=["mean"],
                radius=args.radius,
            )
            print(f"{len(job.sources)} daily items; {job.request_count} site-day calculations")

            failed = 0
            with (
                args.output.open("w", encoding="utf-8") as output,
                job.run(stream=True, max_workers=args.max_workers, errors="return") as results,
            ):
                for result in results:
                    if isinstance(result, BatchFailure):
                        failed += 1
                        row = {
                            "label": result.item.label,
                            "source": result.item.source.url,
                            "stac": result.item.source.stac,
                            "error": str(result.error),
                        }
                    else:
                        row = result.to_dict()
                        # The service already applies CoralTemp's 0.01 scale
                        # factor. Do not multiply the returned mean again.
                        row["sst_mean_celsius"] = result["band_1"].get("mean")
                    row["site_name"] = site_names[row["label"]]
                    output.write(json.dumps(row) + "\n")
                    output.flush()  # Completed rows survive interruption.
            print(f"Wrote {job.request_count} rows to {args.output} ({failed} failed)")
            return 1 if failed else 0
    except (AuthenticationError, AuthFlowError) as error:
        print(f"Could not authenticate: {error}", file=sys.stderr)
        print("Export MERMAID_API_KEY or run: uv run examples/oauth_login.py", file=sys.stderr)
        return 1
    except (MermaidError, OSError, ValueError) as error:
        print(f"Could not complete the SST example: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
