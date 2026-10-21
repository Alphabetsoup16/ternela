# Wait for GitHub Status readiness without an account

Choose **Wait for GitHub Actions readiness** in the workbench, then **Read public API
now**. The recipe requires the exact Actions component to be reported operational,
with no unresolved incident or active maintenance attached. It returns **ready** or
**blocked** because it checks a precondition. Missing or ambiguous evidence is **unknown**.

The [public GitHub Status API](https://www.githubstatus.com/api) exposes this information
in one summary. No GitHub login, API key or model call is required.

From the source checkout, with `python3 -m ternela` running:

```python
from ternela import Client

client = Client()
watch = client.recipe("github_status", {"component_id": "br0l2tvcx85d"}, ttl=900)
client.refresh(watch["id"])
print(client.status(watch["id"])["explanation"])
```

`python3 -m examples.status_wait` runs this flow. The Actions ID above and API Requests
ID `brv1bkgrwx7q` are examples from the public page, not identifiers inferred from names.
A retired or unknown ID produces unknown. Other IDs can be inspected in the API summary
and receipt's retained `source_body`.

For an unattended wait, opt into the existing monitor explicitly:

```python
client.monitor(watch["id"], interval=300, duration=1800)
try:
    current = client.wait(watch["id"], timeout=120)
    print(current["receipt"]["result"])
finally:
    client.pause(watch["id"])
```

Read [monitoring](monitoring.md) for clocks, restart, backoff and pause semantics.
The local service must remain running. A timeout does not stop a monitor.

## Exact meaning

Identity comes from the fixed GitHub Status page ID and an exact leaf-component ID.
A matching name or an overall green banner cannot substitute for that resource.
An unrelated component outage does not block this recipe. Group readiness is unsupported.

Investigating, identified and monitoring incidents count as unresolved. In-progress
and verifying maintenance count as active. Scheduled maintenance does not predict
future readiness. An active record with missing, empty or unknown component associations
makes the summary unknown. Duplicate IDs, unrecognized states and malformed metadata
also remain unknown. This first strict adapter treats malformed unrelated rows as unknown;
it does not silently discard them.

The source endpoint is a complete, non-paginated summary. The existing Link traversal
policy captures the nested component collection with a one-page budget. Complete headers
with no next link establish the expected single response; an unexpected next link makes
it incomplete. No cursor or has-more flag is invented by the adapter.

`single_or_unknown` selects one observed component. Zero or multiple matches are unknown.
The older `single` mode retains its previous empty-selection behavior. `page.updated_at`
is not used as a heartbeat; freshness is the existing capture-time TTL. Provider reporting
and caches may lag reality. Ready means these reported preconditions pass; it does not
prove service health, authorize a deployment or predict its success.

## What the experiment found

The [frozen contract](experiments/github-status-v1.md) specified 55 cases before the
adapter existed. The first adapter matched 52; three missing-resource cases were
incorrectly classified as blocked instead of unknown. An explicit selector policy fixed
those cases without changing any expected label. The package-specific public creation
route also needed a generic reviewed-recipe entry point.

`python3 -m examples.status_probe` reruns the frozen cases and performs three live
summary reads: Actions, API Requests and an absent ID control. Full receipts and logs
are retained as verified gzip artifacts under `data/audits/github-status/`.
See the [audit](audits/2026-10-19-github-status.md) for results, code boundaries and limits.
