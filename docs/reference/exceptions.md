# Exceptions

Invalid arguments raise `TypeError` or `ValueError`. Unsuccessful HTTP responses
raise `MermaidAPIError` subclasses. An unusable response (including invalid JSON,
a malformed list/detail/choices response, or a malformed zonal result) raises
`MermaidConnectionError`, with the parsing exception retained as `__cause__`.
Transport failures also use `MermaidConnectionError`.

Paginated responses must contain a `results` array. Optional `count` must be a
non-negative integer or null; `next` must be a non-empty URL string or null.
Empty arrays remain valid, and unknown model fields are still preserved.

Worker and retry counts must be integers (booleans are rejected). Coordinates,
radii, and retry backoff must be finite; radii and backoff must also be non-negative.
Invalid STAC input shapes raise `TypeError` or `ValueError` during preparation.

Project and detail IDs share normalization: surrounding whitespace and slashes
are trimmed, empty IDs and standalone dot path segments are rejected, and
internal slashes remain percent-encoded as part of the ID.

::: datamermaid.exceptions
