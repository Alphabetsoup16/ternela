# Public connectors without accounts

Start `python3 -m ternela` after the README setup. Select **Read pypi: httpx 0.28.1**
or **Read npm: lodash 4.17.21**, then **Read public API now**. Inspect the checks and
open the receipt. These are real anonymous reads; no model, login or API key is used.
**Start automatic checks** opts into a persisted schedule while the service runs.

For your own package and version, use the SDK from this source checkout:

```python
from ternela import Client

client = Client()
watch = client.release("npm", "@types/node", "22.10.0", ttl=900)
client.refresh(watch["id"])
print(client.status(watch["id"])["explanation"])
```

`python3 -m examples.public_releases` exercises both public presets with a running
local service. Change the package/version arguments to wait for a future release;
use `client.monitor` and `client.wait` as described in [monitoring](monitoring.md).
Names and versions in examples are fixed examples, not current-version recommendations.

## What the recipes establish

| Provider | Anonymous endpoint | Completion checks |
|---|---|---|
| PyPI | `GET https://pypi.org/pypi/{package}/{version}/json` | Canonicalized name and exact version match; at least one non-yanked file has a filename and a reported 64-hex SHA-256 |
| npm | `GET https://registry.npmjs.org/{package}/{version}` | Exact name/version match; an HTTPS tarball URL without credentials; a syntactically valid SHA-256/384/512 SRI token with the expected digest length |
| GitHub | Admitted repository GETs at `api.github.com` | Existing plan-specific checks; not all repository endpoints establish the same claims |

PyPI names normalize case and runs of `-`, `_`, and `.`. Scoped npm names are encoded
as one path segment. npm versions must be exact SemVer; tags such as `latest` and
ranges are rejected before acquisition. Registry reads do not traverse package-wide
version catalogs. Response bodies and decompressed bodies have separate 2 MiB limits.

A missing release (HTTP 404), permission/rate-limit error, malformed metadata or
failed read is **unknown**, not evidence of nonexistence. Known empty or entirely
yanked PyPI file sets are **pending**. An npm release with an invalid integrity token
or insecure/missing-host tarball URL is **pending**; missing required metadata is
**unknown**. New successful observations can replace those decisions.

These are metadata claims. Ternela does not download package files, verify their
bytes against hashes, establish safety, install them or publish anything. Upstream
metadata can be wrong. The [PyPI JSON documentation](https://docs.pypi.org/api/json/)
and [npm registry API](https://github.com/npm/registry/blob/main/docs/REGISTRY-API.md)
describe the provider responses; [npm metadata documentation](https://github.com/npm/registry/blob/main/docs/responses/package-metadata.md)
describes distribution fields.

## Reviewed adapters and retained evidence

These two providers use explicit deterministic recipes and a reviewed local evidence
view. They are not arbitrary natural-language task compilation or automatically
discovered official OpenAPI specifications. The compiler provider selector remains
GitHub, Stripe and Slack.

Each successful read retains the upstream JSON in `source_body`, the normalized
checkable `body`, original URL/headers, wire size and a `view_contract` identifying
the documentation URL and hashes of the local contract and adapter. Malformed views
retain `source_body` and `source_http_status`, set the executable status to zero and
remain unknown. Admission records identify `reviewed_local_evidence_view` explicitly.

Live collection and supplied-snapshot replay share phase selection, dependency
binding and preservation guards. Receipts are still replayed and validated before
transactional admission. Provider origins are fixed; reads have a 60-second total
budget, at most 20 requests, no redirects and no credentials. Each provider has its
own persisted retry pause. One serial background worker means a slow provider can
still delay other queued watches.

Receipts are content-addressed, SHA-256 verified and gzip-compressed on disk. Full
metadata stays local in ignored `data/`; compact UI polling changes transfer size,
not the retained evidence. HTTP gzip uses level 3 for low compression cost; immutable
blobs retain level 9. Retention and garbage collection remain future work. No archive
is made of active SQLite databases, and no evidence is deleted by this change.

## Reproduce the checks

```sh
python3 -m ternela.check
python3 -m ternela.regression
python3 -m examples.public_release_probe
```

The probe performs six anonymous reads (four published versions, including a scoped
npm name, and two missing versions). It writes a report and verified gzip receipts
under `data/audits/public-connectors/`. Its compression comparison uses the same
watch and transition count; the gzip calculation is a serialization experiment,
not an HTTP load test. See the [recorded audit](audits/2026-10-19-public-connectors.md)
for actual HTTP measurements and limits.

See [adding a connector](adding-connectors.md) for the provider interface and shared conformance checks.

The separate [GitHub Status readiness recipe](status-readiness.md) uses `client.recipe`
and checks a provider-reported precondition rather than an exact package release.
