# API reference

Everything below is generated from the source, so it is always in step with the
installed version.

The public surface is small. Almost everything is reachable from one object:

```python
from datamermaid import MermaidClient

with MermaidClient() as client:
    ...
```

| Page | What is in it |
| --- | --- |
| [Client](client.md) | [`MermaidClient`][datamermaid.client.MermaidClient], the base URL constants and the transport helpers |
| [Authentication](auth.md) | The [`Auth`][datamermaid.auth.base.Auth] seam, API keys, the OAuth grants, the token cache |
| [Resources](resources.md) | One wrapper per endpoint group, including the project handle and the aggregated views |
| [Models](models.md) | The frozen dataclasses records are parsed into |
| [Pagination](pagination.md) | [`PaginatedList`][datamermaid.pagination.PaginatedList] and the DataFrame export |
| [Batches](batch.md) | [`LazyBatch`][datamermaid.batch.LazyBatch], one request per area of interest on a thread pool |
| [Geometry](geometry.md) | [`to_aoi`][datamermaid.geometry.to_aoi], which turns a Site, a shapely geometry or a tuple into the GeoJSON the Zonal Stats service accepts |
| [Exceptions](exceptions.md) | The [`MermaidError`][datamermaid.exceptions.MermaidError] hierarchy |

## Package

::: datamermaid
    options:
      members: false
      show_root_heading: false
      show_root_toc_entry: false

Everything named in `datamermaid.__all__` is importable straight from the
package, whatever module it is defined in:

```python
from datamermaid import APIKeyAuth, MermaidClient, NotFoundError, Project, login
```

The version is available as `datamermaid.__version__`.
