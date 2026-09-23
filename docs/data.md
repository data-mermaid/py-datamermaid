# Working with data

Every list endpoint in the SDK behaves the same way: `.list(**filters)` returns
a lazy [`PaginatedList`][datamermaid.pagination.PaginatedList] of typed models,
`.get(id)` returns one record, and `.to_df()` flattens the whole collection into
a `pandas` DataFrame.

The examples below use the fish taxonomy, which is public, so you can paste any
of them into a REPL without credentials.

## Lazy loading

`.list()` issues no request at all. The first page is fetched when you first
iterate, index, or measure the list, and each later page only when you read past
the end of the last one. Items already fetched are cached, so re-iterating or
indexing backwards never costs another request.

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    species = client.fish_species.list(limit=100)
    print(repr(species))  # <PaginatedList fetched=0 count=None lazy> - nothing requested yet

    print(len(species))  # one request: the API reports the total on page 1
    print(species[0].display_name)  # already cached, no request
    print(species[150].display_name)  # fetches page 2
    print(repr(species))  # <PaginatedList fetched=200 count=... lazy>
```

Three properties let you see what has happened without forcing more work:

| Expression | Meaning |
| --- | --- |
| `species.count` | total matching records as the API reports it (fetches page 1 if nothing has been fetched yet) |
| `species.fetched` | items cached so far, never triggering a request |
| `len(species)` | the same total, falling back to exhausting the list if the API omits a count |

Slicing fetches only as far as it has to. An open-ended or negative slice cannot
know where to stop, so it materialises everything:

```python
species[:25]  # stops after the first page that covers 25 items
species[::2]  # no stop, so every page is fetched
species[-1]  # likewise: the last item needs the last page
```

Iteration is the memory-friendly option on a large collection, because you can
stop early:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    for species in client.fish_species.list(ordering="name"):
        if species.trophic_level and species.trophic_level > 4.0:
            print(species.display_name, species.trophic_level)
            break  # only the pages consumed so far were ever requested
```

!!! warning "`to_df()` is not lazy"

    `.to_df()` materialises every page, because a DataFrame has to know how many
    rows it has. Narrow the query with filters or `limit` before calling it on a
    large collection.

## Filtering

Keyword arguments to `.list()` become query parameters, so anything the endpoint
filters on passes straight through. Arguments whose value is `None` are left out,
which makes optional filters easy to build up. Every list route also understands
`limit`, `search`, `ordering` and `fields`.

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    # `search` is a free-text match the API applies across the record.
    acanthuridae = client.fish_families.list(search="Acanthuridae")[0]

    # `limit` is the page size, not a cap on the result: the list keeps paging.
    genera = client.fish_genera.list(family=acanthuridae.id, ordering="name", limit=100)
    for genus in genera:
        print(genus.name, len(client.fish_species.list(genus=genus.id)))
```

`fields` asks the API for a subset of columns, which is worth doing when you are
about to load thousands of rows into a DataFrame:

```python
client.fish_species.list(fields="id,display_name,trophic_level", limit=1000).to_df()
```

## Models

Records come back as frozen dataclasses. Every field is optional, so a field the
API omits is `None` rather than an error:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    species = client.fish_species.list(search="Acanthurus")[0]
    print(species.display_name, species.max_length, species.trophic_group)
```

Models never drop data. Anything the SDK does not declare is kept in `.extra`,
and `.to_dict()` merges the two back into one flat row, which is exactly what
becomes a DataFrame row:

```python
species.extra  # {"some_new_api_field": ...}
species.to_dict()  # declared fields plus extras
species.to_dict(include_extra=False)  # declared fields only
```

That is why a server-side addition can never break parsing: it lands in `extra`.
A value that fails conversion (an unparseable date, say) is also left in `extra`
under its original key, and the declared field keeps its default, so nothing is
silently lost.

## DataFrames

`to_df()` needs the optional `pandas` extra (`uv add 'datamermaid[pandas]'`).

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    species = client.fish_species.list(limit=1000).to_df()
    print(species[["display_name", "max_length", "trophic_level"]].head())
```

Keyword arguments are forwarded to `pandas.DataFrame.from_records`, so you can
pick columns and set an index in one step:

```python
client.fish_families.list().to_df(index="id", columns=["id", "name", "biomass_constant_a"])
```

To build a frame out of records you already have in hand rather than a whole
collection, use [`to_dataframe()`][datamermaid.pagination.to_dataframe]:

```python
from datamermaid.pagination import to_dataframe

fishes = client.fish_species.list(limit=50)
fishes[0]  # force the first page; `.list()` on its own sends no request
to_dataframe(fishes.fetched)  # only what has been fetched so far
```

## Reference data

Beyond `/projects/`, every top-level route is a client attribute returning the
same lazy list of typed models.

| Attribute | Route | Model | Auth |
| --- | --- | --- | --- |
| `client.sites` | `/sites/` | [`Site`][datamermaid.models.Site] | required |
| `client.managements` | `/managements/` | [`Management`][datamermaid.models.Management] | required |
| `client.project_tags` | `/projecttags/` | [`ProjectTag`][datamermaid.models.ProjectTag] | public |
| `client.fish_sizes` | `/fishsizes/` | [`FishSize`][datamermaid.models.FishSize] | public |
| `client.fish_families` | `/fishfamilies/` | [`FishFamily`][datamermaid.models.FishFamily] | public |
| `client.fish_genera` | `/fishgenera/` | [`FishGenus`][datamermaid.models.FishGenus] | public |
| `client.fish_species` | `/fishspecies/` | [`FishSpecies`][datamermaid.models.FishSpecies] | public |
| `client.benthic_attributes` | `/benthicattributes/` | [`BenthicAttribute`][datamermaid.models.BenthicAttribute] | public |
| `client.invert_attributes` | `/invertattributes/` | [`InvertAttribute`][datamermaid.models.InvertAttribute] | public |
| `client.invert_species` | `/invertspecies/` | [`InvertSpecies`][datamermaid.models.InvertSpecies] | public |
| `client.summary_sample_events` | `/summarysampleevents/` | [`SummarySampleEvent`][datamermaid.models.SummarySampleEvent] | public |
| `client.label_mappings` | `/classification/labelmappings/` | [`LabelMapping`][datamermaid.models.LabelMapping] | public |

`client.sites` and `client.managements` return the sites and management regimes
of every project the credentials can see, which is why they need an API key or a
login. Their project-scoped equivalents are on the
[project handle](projects.md).

## Public summaries

`/summarysampleevents/` is the whole public catalogue of reef surveys: one row
per site visit, with the per-protocol aggregates kept as a nested mapping under
`.protocols`. It needs no credentials, which makes it the best place to start
exploring.

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    summaries = client.summary_sample_events.list(
        project_name="Fiji",
        sample_date_after="2018-01-01",
        limit=200,
    )
    for summary in summaries:
        print(summary.site_name, summary.sample_date, sorted(summary.protocols))
```

Each protocol's own statistics sit inside that mapping, keyed by protocol name:

```python
summary = summaries[0]
beltfish = summary.protocols.get("beltfish", {})
print(beltfish.get("biomass_kgha_avg"))
```

## Choices

`/choices/` holds the controlled vocabularies the API validates submissions
against, and is the one list route that is not paginated. It comes back as plain
dictionaries rather than models:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    choices = client.choices()  # every set, keyed by name
    print(sorted(choices))  # 'countries', 'reeftypes', 'managementparties', ...
    print([reef_type["name"] for reef_type in choices["reeftypes"]])

    reef_types = client.choices("reeftypes")  # just one set, from /choices/reeftypes/
```

Those vocabularies are how you turn an id on a record into a human-readable
name. Many models carry the resolved name alongside the id already
(`site.reef_type` and `site.reef_type_name`), but where they do not, a lookup
table built from `/choices/` does the job:

```python
reef_types = {row["id"]: row["name"] for row in client.choices("reeftypes")}
print(reef_types.get(site.reef_type))
```

## Errors

Every exception derives from [`MermaidError`][datamermaid.exceptions.MermaidError],
so one `except` clause is always enough.

| Status | Exception |
| --- | --- |
| 401, 403 | [`AuthenticationError`][datamermaid.exceptions.AuthenticationError] |
| 404 | [`NotFoundError`][datamermaid.exceptions.NotFoundError] |
| 429 | [`RateLimitError`][datamermaid.exceptions.RateLimitError] (with `.retry_after`) |
| 5xx | [`ServerError`][datamermaid.exceptions.ServerError] |
| other 4xx | [`MermaidAPIError`][datamermaid.exceptions.MermaidAPIError] |
| network failure | [`MermaidConnectionError`][datamermaid.exceptions.MermaidConnectionError] |

```python
from datamermaid import MermaidClient, MermaidError, NotFoundError

with MermaidClient() as client:
    try:
        client.fish_species.get("00000000-0000-0000-0000-000000000000")
    except NotFoundError:
        print("no such species")
    except MermaidError as exc:
        print(exc)
```

Because the list is lazy, an error surfaces where the page is fetched, not where
`.list()` was called:

```python
species = client.fish_species.list()  # cannot raise: nothing requested yet
for record in species:  # this is where a NotFoundError or ServerError appears
    ...
```

Rate limits and server errors are retried automatically, three times by default,
with exponential backoff that honours a `Retry-After` header up to 30 seconds.
Tune or disable it per client:

```python
from datamermaid import MermaidClient

with MermaidClient(max_retries=0) as client:  # fail fast
    ...

with MermaidClient(max_retries=5, backoff_factor=1.0, timeout=60.0) as client:
    ...
```
