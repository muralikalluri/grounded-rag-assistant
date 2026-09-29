# API Guidelines

## Versioning

APIs are versioned in the URL path, for example /v2/shipments. Breaking changes require a new major version and a 6-month deprecation period.

## Pagination

List endpoints use cursor pagination with a default page size of 50 and a maximum of 200.

## Errors

Errors return a JSON body with a machine-readable code and a human-readable message. Internal stack traces are never returned.
