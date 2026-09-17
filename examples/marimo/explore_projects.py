# /// script
# requires-python = ">=3.10"
# dependencies = ["altair>=5.0", "datamermaid[pandas]", "marimo>=0.24", "pyarrow>=14"]
#
# [tool.uv.sources]
# datamermaid = { path = "../..", editable = true }
# ///
#
# Explore MERMAID's public sample event summaries.
#
#     uv run examples/marimo/explore_projects.py        # serves this notebook
#     uvx marimo edit examples/marimo/explore_projects.py
#
# Both commands install marimo themselves from the inline metadata above, so
# nothing has to be installed globally first.  `uvx marimo edit` offers to run
# the notebook in a sandbox built from that metadata; pass `--sandbox` to skip
# the prompt.  Set `MARIMO_SCRIPT_EDIT=` (empty) to execute the cells once and
# exit instead of serving.
#
# `/summarysampleevents/` is public, so this notebook needs no credentials.

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md(
        """
        # MERMAID sample events

        Every sample event MERMAID publishes a summary for, straight from
        `/summarysampleevents/`. No credentials needed: this route is public.
        """
    )
    return


@app.cell
def _(mo):
    # Summaries run to five figures, so the notebook loads a bounded slice.
    row_limit = mo.ui.slider(
        500, 5000, step=500, value=1500, label="sample events to load", show_value=True
    )
    row_limit
    return (row_limit,)


@app.cell
def _(MermaidClient, pd, row_limit):
    with MermaidClient() as client:
        summaries = client.summary_sample_events.list(limit=500)
        # Slicing a PaginatedList fetches only the pages it needs; `.to_df()`
        # would instead pull every page the endpoint has.
        rows = summaries[: row_limit.value]

    events = pd.DataFrame([row.to_dict() for row in rows])
    events["year"] = pd.to_datetime(events["sample_date"]).dt.year
    events["protocol_names"] = events["protocols"].map(lambda p: ", ".join(sorted(p or {})))
    return (events,)


@app.cell
def _(events, mo):
    country = mo.ui.dropdown(
        options=["(all)", *sorted(events["country_name"].dropna().unique())],
        value="(all)",
        label="country",
    )
    project_search = mo.ui.text(label="project name contains", placeholder="e.g. Fiji")
    mo.hstack([country, project_search], justify="start", gap=1)
    return country, project_search


@app.cell
def _(country, events, project_search):
    selected = events
    if country.value != "(all)":
        selected = selected[selected["country_name"] == country.value]
    if project_search.value:
        selected = selected[
            selected["project_name"].str.contains(project_search.value, case=False, na=False)
        ]
    return (selected,)


@app.cell
def _(mo, selected):
    mo.md(
        f"**{len(selected)} sample events** across "
        f"**{selected['project_name'].nunique()} projects** and "
        f"**{selected['country_name'].nunique()} countries**."
    )
    return


@app.cell
def _(alt, mo, selected):
    by_year = selected.groupby(["year", "reef_type"], as_index=False).size()
    chart = mo.ui.altair_chart(
        alt.Chart(by_year, height=320)
        .mark_bar()
        .encode(
            x=alt.X("year:O", title="sample year"),
            y=alt.Y("size:Q", title="sample events"),
            color=alt.Color("reef_type:N", title="reef type"),
            tooltip=["year:O", "reef_type:N", "size:Q"],
        )
        .properties(title="Sample events per year")
    )
    chart
    return (chart,)


@app.cell
def _(chart, mo, selected):
    # Selecting bars in the chart above narrows the table below.
    picked = chart.value
    years = set(picked["year"]) if len(picked) else set(selected["year"])
    columns = [
        "project_name",
        "site_name",
        "country_name",
        "sample_date",
        "depth_avg",
        "protocol_names",
    ]
    mo.ui.table(
        selected[selected["year"].isin(years)][columns].sort_values("sample_date", ascending=False),
        page_size=15,
        selection=None,
    )
    return


@app.cell
def _(mo):
    mo.md("## Busiest projects in the slice")
    return


@app.cell
def _(selected):
    (
        selected.groupby("project_name")
        .agg(sample_events=("sample_event_id", "count"), sites=("site_id", "nunique"))
        .sort_values("sample_events", ascending=False)
        .head(15)
    )
    return


@app.cell
def _():
    import altair as alt
    import pandas as pd

    from datamermaid import MermaidClient

    return MermaidClient, alt, pd


@app.cell
def _():
    import marimo as mo

    return (mo,)


if __name__ == "__main__":
    # marimo's own switch: with MARIMO_SCRIPT_EDIT set, `app.run()` serves this
    # notebook in the editor rather than executing it once and exiting, so
    # `uv run examples/marimo/explore_projects.py` hands back a URL to open.
    import os

    os.environ.setdefault("MARIMO_SCRIPT_EDIT", "1")
    app.run()
