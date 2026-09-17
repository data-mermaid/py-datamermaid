# /// script
# requires-python = ">=3.10"
# dependencies = ["altair>=5.0", "datamermaid[pandas]", "marimo>=0.24", "pyarrow>=14"]
#
# [tool.uv.sources]
# datamermaid = { path = "../..", editable = true }
# ///
#
# Reef fish biomass, from the public summaries and from your own projects.
#
#     uv run examples/marimo/fish_observations.py        # serves this notebook
#     uvx marimo edit examples/marimo/fish_observations.py
#
# Both commands install marimo themselves from the inline metadata above, so
# nothing has to be installed globally first.  `uvx marimo edit` offers to run
# the notebook in a sandbox built from that metadata; pass `--sandbox` to skip
# the prompt.  Set `MARIMO_SCRIPT_EDIT=` (empty) to execute the cells once and
# exit instead of serving.
#
# The first half reads public data and needs no credentials.  The second half
# reads one project's observations, which needs an API key in MERMAID_API_KEY
# or a token from `datamermaid.login()`.

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md(
        """
        # Reef fish biomass

        MERMAID publishes each sample event's fish belt summary broken down by
        family, whenever the project's data policy allows it. That part is
        public. Observation-level rows need credentials, and live further down.
        """
    )
    return


@app.cell
def _(client, mo, pd):
    # `data_policy_beltfish="public"` is the API's own filter, passed straight
    # through; slicing the lazy list bounds how many pages are fetched.
    summaries = client.summary_sample_events.list(limit=500, data_policy_beltfish="public")[:1000]

    family_rows = [
        {
            "project_name": summary.project_name,
            "country_name": summary.country_name,
            "site_name": summary.site_name,
            "sample_date": summary.sample_date,
            "fish_family": family,
            "biomass_kgha": biomass,
        }
        for summary in summaries
        for family, biomass in (
            ((summary.protocols or {}).get("beltfish") or {}).get("biomass_kgha_fish_family_avg")
            or {}
        ).items()
    ]
    public_biomass = pd.DataFrame(family_rows)
    mo.md(
        f"Loaded **{len(public_biomass)}** family biomass rows from "
        f"**{public_biomass['site_name'].nunique()}** sites."
    )
    return (public_biomass,)


@app.cell
def _(mo, public_biomass):
    country = mo.ui.dropdown(
        options=["(all)", *sorted(public_biomass["country_name"].dropna().unique())],
        value="(all)",
        label="country",
    )
    top_n = mo.ui.slider(5, 30, step=5, value=15, label="families to chart", show_value=True)
    mo.hstack([country, top_n], justify="start", gap=1)
    return country, top_n


@app.cell
def _(alt, country, mo, public_biomass, top_n):
    scoped = public_biomass
    if country.value != "(all)":
        scoped = scoped[scoped["country_name"] == country.value]

    ranked = (
        scoped.groupby("fish_family", as_index=False)["biomass_kgha"]
        .mean()
        .sort_values("biomass_kgha", ascending=False)
        .head(top_n.value)
    )
    mo.ui.altair_chart(
        alt.Chart(ranked, height=24 * len(ranked))
        .mark_bar()
        .encode(
            x=alt.X("biomass_kgha:Q", title="mean biomass (kg/ha)"),
            y=alt.Y("fish_family:N", title="family", sort="-x"),
            tooltip=["fish_family:N", alt.Tooltip("biomass_kgha:Q", format=".1f")],
        )
        .properties(title=f"Mean fish biomass per family - {country.value}")
    )
    return


@app.cell
def _(mo):
    mo.md(
        """
        ## Observation rows from one of your projects

        `project.beltfishes.observations()` is the denormalized view: one row
        per fish counted, with the site, management regime and transect already
        joined in.
        """
    )
    return


@app.cell
def _(AuthFlowError, AuthenticationError, client, mo, signed_in):
    projects = []
    auth_error = None
    if signed_in:
        try:
            projects = client.projects.list(limit=50)[:50]
        except (AuthenticationError, AuthFlowError) as error:
            # A cached OAuth token that can no longer be refreshed raises
            # AuthFlowError before any request goes out; the API rejecting the
            # credentials it did send raises AuthenticationError.
            auth_error = error

    project_picker = mo.ui.dropdown(
        options={project.name: project.id for project in projects},
        value=projects[0].name if projects else None,
        label="project",
    )
    obs_limit = mo.ui.slider(
        100, 2000, step=100, value=500, label="observation rows", show_value=True
    )

    if not signed_in or auth_error is not None:
        reason = f"These credentials were rejected: {auth_error}" if auth_error else ""
        picker_view = mo.md(
            f"""
            /// warning | No usable credentials

            {reason}

            Export an API key and restart the notebook:

            ```
            export MERMAID_API_KEY='mmd_<key_id>.<secret>'
            ```

            or log in once through the browser with
            `uv run examples/oauth_login.py`, which caches a token this
            notebook will pick up on its own.
            ///
            """
        )
    elif not projects:
        picker_view = mo.md("/// warning | These credentials cannot see any project. ///")
    else:
        picker_view = mo.hstack([project_picker, obs_limit], justify="start", gap=1)
    picker_view
    return obs_limit, project_picker


@app.cell
def _(AuthFlowError, AuthenticationError, client, mo, obs_limit, pd, project_picker):
    observations = pd.DataFrame()
    note = mo.md("")

    if project_picker.value:
        project = client.projects(project_picker.value)
        try:
            rows = project.beltfishes.observations(limit=500)[: obs_limit.value]
            observations = pd.DataFrame([row.to_dict() for row in rows])
        except (AuthenticationError, AuthFlowError) as error:
            # Access to a project does not imply access to its raw
            # observations: the project's fish belt data policy decides.
            note = mo.md(f"/// warning | Observations are not readable: {error} ///")
    note
    return (observations,)


@app.cell
def _(mo, observations):
    columns = [
        "site_name",
        "sample_date",
        "transect_number",
        "fish_family",
        "fish_taxon",
        "size",
        "count",
        "biomass_kgha",
    ]
    mo.ui.table(
        observations[[column for column in columns if column in observations.columns]],
        page_size=12,
        selection=None,
    )
    return


@app.cell
def _(alt, mo, observations):
    if {"fish_family", "biomass_kgha"} <= set(observations.columns):
        per_family = (
            observations.groupby("fish_family", as_index=False)["biomass_kgha"]
            .sum()
            .sort_values("biomass_kgha", ascending=False)
            .head(15)
        )
        observed_chart = mo.ui.altair_chart(
            alt.Chart(per_family, height=24 * len(per_family))
            .mark_bar()
            .encode(
                x=alt.X("biomass_kgha:Q", title="biomass (kg/ha), summed over the rows fetched"),
                y=alt.Y("fish_family:N", title="family", sort="-x"),
                tooltip=["fish_family:N", alt.Tooltip("biomass_kgha:Q", format=".1f")],
            )
            .properties(title="Observed biomass per family")
        )
    else:
        observed_chart = mo.md("No observation rows loaded.")
    observed_chart
    return


@app.cell
def _(AnonymousAuth, MermaidClient):
    # One client for the whole notebook; the marimo kernel owns its lifetime.
    client = MermaidClient()
    signed_in = not isinstance(client.auth, AnonymousAuth)
    return client, signed_in


@app.cell
def _():
    import altair as alt
    import pandas as pd

    from datamermaid import (
        AnonymousAuth,
        AuthenticationError,
        AuthFlowError,
        MermaidClient,
    )

    return AnonymousAuth, AuthFlowError, AuthenticationError, MermaidClient, alt, pd


@app.cell
def _():
    import marimo as mo

    return (mo,)


if __name__ == "__main__":
    # marimo's own switch: with MARIMO_SCRIPT_EDIT set, `app.run()` serves this
    # notebook in the editor rather than executing it once and exiting, so
    # `uv run examples/marimo/fish_observations.py` hands back a URL to open.
    import os

    os.environ.setdefault("MARIMO_SCRIPT_EDIT", "1")
    app.run()
