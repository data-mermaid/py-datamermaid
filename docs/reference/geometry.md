# Geometry

The Zonal Stats service takes one GeoJSON `Point` or `Polygon` per request.
[`to_aoi`][datamermaid.geometry.to_aoi] gets there from whatever describes the
place: a GeoJSON mapping, anything with a `__geo_interface__` (a shapely
geometry, a GeoDataFrame row), a `(lon, lat)` tuple or a
[`Site`][datamermaid.models.Site].  The endpoints call it for you; call it
yourself to check an area of interest before sending it.

::: datamermaid.geometry
