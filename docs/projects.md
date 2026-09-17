# Project data

A MERMAID project is one team's body of work: its reef sites, its management
regimes, its members, and every survey they have recorded. All of it needs
credentials with access to that project, so start from
[Authentication](authentication.md) if you have not already.

## Finding a project

`client.projects` is an ordinary [list endpoint](data.md): `.list(**filters)`
and `.get(id)`, over [`Project`][datamermaid.models.Project] records.

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    for project in client.projects.list():
        print(project.id, project.name, project.countries, project.num_sites)
```

By default you see the projects your credentials are a member of. Pass
`showall=True` for the public catalogue, and filter on `name`, `country`, `tags`
or `status`:

```python
client.projects.list(showall=True, country="Fiji", limit=100)
```

A project record carries its own metadata, including the per-protocol data
sharing policies:

```python
project = client.projects.get("d5491b25-4a5f-401b-a50f-bb80fd1df78f")
print(project.name, project.suggested_citation)
print(project.data_policies)  # {'beltfish': 50, 'benthiclit': 50, ...}
```

## The project handle

Calling `client.projects` with an id (or a `Project` you already fetched) returns
a [`ProjectContext`][datamermaid.resources.project_context.ProjectContext], the
handle everything recorded under that project hangs off. Note the difference:

- `client.projects.get(id)` fetches **the project record**,
- `client.projects(id)` opens **what is recorded under it**, issuing no request.

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    project = client.projects("d5491b25-4a5f-401b-a50f-bb80fd1df78f")

    for site in project.sites.list():
        print(site.name, site.reef_type_name, site.reef_zone_name, site.location)

    for member in project.project_profiles.list():
        print(member.profile_name, member.role, member.is_admin)
```

Each collection below the handle is the same lazy
[`PaginatedList`][datamermaid.pagination.PaginatedList] as everywhere else, with
`.list(**filters)`, `.get(id)` and `.to_df()`.

| Attribute | Route under `/projects/{id}/` | Model |
| --- | --- | --- |
| `project.sites` | `sites/` | [`Site`][datamermaid.models.Site] |
| `project.managements` | `managements/` | [`Management`][datamermaid.models.Management] |
| `project.observers` | `observers/` | [`Observer`][datamermaid.models.Observer] |
| `project.project_profiles` | `project_profiles/` | [`ProjectProfile`][datamermaid.models.ProjectProfile] |
| `project.sample_events` | `sampleevents/` | [`SampleEvent`][datamermaid.models.SampleEvent] |
| `project.fishbelt_transects` | `fishbelttransects/` | [`FishBeltTransect`][datamermaid.models.FishBeltTransect] |
| `project.benthic_transects` | `benthictransects/` | [`BenthicTransect`][datamermaid.models.BenthicTransect] |
| `project.beltfish_methods` | `beltfishtransectmethods/` | [`BeltFishMethod`][datamermaid.models.BeltFishMethod] |
| `project.benthiclit_methods` | `benthiclittransectmethods/` | [`BenthicLITMethod`][datamermaid.models.BenthicLITMethod] |
| `project.benthicpit_methods` | `benthicpittransectmethods/` | [`BenthicPITMethod`][datamermaid.models.BenthicPITMethod] |
| `project.benthicpqt_methods` | `benthicphotoquadrattransectmethods/` | [`BenthicPhotoQuadratTransectMethod`][datamermaid.models.BenthicPhotoQuadratTransectMethod] |
| `project.habitatcomplexity_methods` | `habitatcomplexitytransectmethods/` | [`HabitatComplexityMethod`][datamermaid.models.HabitatComplexityMethod] |
| `project.bleachingqc_methods` | `bleachingquadratcollectionmethods/` | [`BleachingQuadratCollectionMethod`][datamermaid.models.BleachingQuadratCollectionMethod] |
| `project.beltinvert_methods` | `beltinverttransectmethods/` | [`BeltInvertMethod`][datamermaid.models.BeltInvertMethod] |

The handle is cached, so the same id always yields the same object and the
collections below it keep whatever they have already fetched.

## Sample units and their observations

The `*_methods` collections are the sample units with their observations: one
record per survey, carrying the [`SampleEvent`][datamermaid.models.SampleEvent],
the transect or quadrat collection it was recorded on, the
[`Observer`][datamermaid.models.Observer]s who recorded it, and the observation
rows.

```python
survey = project.beltfish_methods.get("ffffffff-0000-0000-0000-000000000001")

print(survey.sample_event.sample_date)
print(survey.fishbelt_transect.len_surveyed, survey.fishbelt_transect.depth)
print(survey.observers[0].profile_name)

for observation in survey.observations["obs_belt_fishes"]:
    print(observation["size"], observation["count"])
```

Two properties reach the same things without knowing which protocol is in hand,
which is what you want when walking several collections at once:

```python
survey.sample_unit  # the transect or quadrat collection, whatever the protocol
survey.observations  # {"obs_belt_fishes": ({...}, {...}), ...}
```

Observation rows stay as plain dictionaries: their columns differ per protocol
and they run to thousands of rows per survey.

## Aggregated observation views

The `*_methods` collections are shaped for editing one survey at a time. For
analysis the API also publishes each protocol denormalized, as flat rows with
the site, management regime and project already joined in.

Every family answers the same three methods, each a lazy list of
[`AggregatedRecord`][datamermaid.models.AggregatedRecord] rows:

- `.observations()` - one row per observation,
- `.sample_units()` - one row per transect or quadrat collection, with that
  unit's aggregates,
- `.sample_events()` - one row per site visit, averaged over its sample units.

| Attribute | Observations | Sample units | Sample events |
| --- | --- | --- | --- |
| `project.beltfishes` | `beltfishes/obstransectbeltfishes/` | `beltfishes/sampleunits/` | `beltfishes/sampleevents/` |
| `project.benthiclits` | `benthiclits/obstransectbenthiclits/` | `benthiclits/sampleunits/` | `benthiclits/sampleevents/` |
| `project.benthicpits` | `benthicpits/obstransectbenthicpits/` | `benthicpits/sampleunits/` | `benthicpits/sampleevents/` |
| `project.benthicpqts` | `benthicpqts/obstransectbenthicpqts/` | `benthicpqts/sampleunits/` | `benthicpqts/sampleevents/` |
| `project.habitatcomplexities` | `habitatcomplexities/obshabitatcomplexities/` | `habitatcomplexities/sampleunits/` | `habitatcomplexities/sampleevents/` |
| `project.bleachingqcs` | `bleachingqcs/obscoloniesbleacheds/` and `bleachingqcs/obsquadratbenthicpercents/` | `bleachingqcs/sampleunits/` | `bleachingqcs/sampleevents/` |
| `project.beltinverts` | `beltinverts/obstransectbeltinverts/` | `beltinverts/sampleunits/` | `beltinverts/sampleevents/` |

Bleaching records two kinds of observation, so
[`BleachingQCFamilyResource`][datamermaid.resources.aggregated.BleachingQCFamilyResource]
has two observation views and `.observations()` is an alias of the first:

```python
project.bleachingqcs.colonies_bleached().to_df()
project.bleachingqcs.quadrat_benthic_percent().to_df()
```

Keyword arguments are the API's own query parameters: the shared filters
(`sample_date_after`, `sample_date_before`, `site_id`, `site_name`,
`management_id`, `depth_min`, `depth_max`, `label`, `observers`, ...), each
protocol's own (`fish_family`, `benthic_category`, `biomass_kgha_min`, ...), and
the parameters every list route understands (`limit`, `ordering`, `fields`):

```python
project.beltfishes.sample_events(
    sample_date_after="2018-01-01",
    site_name="Namena South",
    ordering="sample_date",
    limit=200,
)
```

### Wide rows and `extra`

These rows are wide, and their columns differ per protocol, per view and per API
release. `AggregatedRecord` therefore declares only what every view shares
(`id`, `project_id`, `project_name`, `site_id`, `site_name`, `sample_date`,
`management_id`, `sample_event_id`) and keeps everything else in `.extra`.
`to_dict()` and `to_df()` flatten both, so each field the API sent becomes one
DataFrame column:

```python
row = project.benthicpits.observations(limit=1)[0]
print(row.site_name, row.sample_date)  # declared
print(row.extra["benthic_category"])  # protocol column, as the API sent it
```

The sample unit views carry no `id` (they report `sample_unit_ids` instead), so
`row.id` is `None` on those. The `/csv/` and `/geojson/` variants of these routes
are not wrapped; the SDK reads the JSON views only.

## A worked example: fish biomass by family and management regime

A real question: in one project, has biomass inside no-take areas diverged from
biomass outside them, and which fish families drive the difference?

The fish belt sample unit view is the right grain. It gives one row per transect
with the per-transect biomass already computed, so no per-observation arithmetic
is needed.

```python
import pandas as pd

from datamermaid import MermaidClient

PROJECT_ID = "d5491b25-4a5f-401b-a50f-bb80fd1df78f"

with MermaidClient() as client:  # reads MERMAID_API_KEY
    project = client.projects(PROJECT_ID)

    # One row per fish belt transect, with its biomass and management regime.
    units = project.beltfishes.sample_units(
        sample_date_after="2018-01-01",
        ordering="sample_date",
        limit=500,
    ).to_df()

    # Which of the project's management regimes are no-take?
    regimes = project.managements.list().to_df()
```

`units` is one row per transect. `biomass_kgha` is the transect total;
`biomass_kgha_fish_family` is a nested mapping of family name to that family's
share of it, e.g. `{"Acanthuridae": 402.11, "Serranidae": 128.9}`. Join the
management flags on and aggregate:

!!! note "The printed output below is illustrative"

    The numbers are made up, to show the shape of the result. Yours depend on
    the project.

```python
no_take = regimes.set_index("id")["no_take"]
units["no_take"] = units["management_id"].map(no_take).fillna(False)

# Mean transect biomass, inside no-take areas and outside them.
by_regime = (
    units.groupby("no_take")["biomass_kgha"]
    .agg(transects="count", mean_biomass_kgha="mean", sd="std")
    .round(1)
)
print(by_regime)
```

```text
         transects  mean_biomass_kgha     sd
no_take
False           84              412.7  238.4
True            37              889.1  401.6
```

Now the per-family breakdown. Each row's `biomass_kgha_fish_family` is a dict, so
expand it into long form first:

```python
families = (
    pd.json_normalize(units["biomass_kgha_fish_family"])
    .set_index(units.index)
    .join(units[["site_name", "sample_date", "no_take"]])
    .melt(
        id_vars=["site_name", "sample_date", "no_take"],
        var_name="fish_family",
        value_name="biomass_kgha",
    )
    .dropna(subset=["biomass_kgha"])
)

top = (
    families.groupby(["fish_family", "no_take"])["biomass_kgha"]
    .mean()
    .unstack("no_take")
    .rename(columns={False: "open", True: "no_take"})
    .assign(difference=lambda frame: frame["no_take"] - frame["open"])
    .sort_values("difference", ascending=False)
    .head(10)
    .round(1)
)
print(top)
```

```text
no_take          open  no_take  difference
fish_family
Scaridae        108.4    301.7       193.3
Acanthuridae     94.2    212.8       118.6
Lutjanidae       21.7     96.4        74.7
Serranidae       12.9     61.3        48.4
...
```

!!! note "Columns vary"

    `biomass_kgha_fish_family` is a column of the fish belt sample unit view, so
    it arrives in `.extra` and `to_df()` turns it into a DataFrame column. Other
    protocols expose different aggregates (`percent_cover_benthic_category` on
    the benthic views, `percent_hard` on the bleaching quadrat view). Print
    `frame.columns` on a one-row query before writing the analysis:

    ```python
    print(project.benthicpits.sample_units(limit=1).to_df().columns.tolist())
    ```

## Which grain should I use?

| You want | Use |
| --- | --- |
| to edit or audit one survey, with its observers and raw observation rows | `project.beltfish_methods` and its siblings |
| every observation as a flat table (per fish, per point, per colony) | `project.beltfishes.observations()` |
| one row per transect, with that transect's aggregates | `project.beltfishes.sample_units()` |
| one row per site visit, averaged over its transects | `project.beltfishes.sample_events()` |
| the same, across every public project rather than one | `client.summary_sample_events` |
