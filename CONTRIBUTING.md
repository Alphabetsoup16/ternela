# Contributing

Start with the [architecture](docs/architecture.md) and [event contract](docs/api.md).
This release runs from a source checkout and uses Python's standard library.

```sh
python3 -m ternela.setup_demo
python3 -m ternela.check
python3 -m ternela.regression
```

Setup downloads pinned public specifications and separately attributed guidance.
Tests then run without provider credentials, model inference or paid services.

Keep the plan data-only. Add a meaningful regression case when a change affects
identity matching, phase assignment, pagination completeness, preservation,
freshness or event delivery. Keep missing evidence distinct from a known unmet
condition. Do not make a failing result disappear by changing its expectation.

For connector proposals, open an issue with one concrete task, the provider's
canonical read operation, event coverage, reconciliation behavior and a fixture
that demonstrates a failure mode. GitHub and generic event ingestion are the
initial focus; Linear follows.

Runtime databases, downloaded documentation, tokens, model logs and user-created
task records belong in ignored local data. Include only synthetic or explicitly
public fixtures. Provider material retains its upstream attribution.

Contributions to original project code are licensed under Apache-2.0.

For Python lint and formatting checks, install the optional development tool:

```sh
python3 -m pip install -e ".[dev]"
python3 -m ruff check .
python3 -m ruff format --check .
node --check workbench/static/app.js
```

Interface source uses Prettier 3.9.10. Keep formatting-only edits separate from
behavior changes where practical. `python3 -m ternela.benchmark` measures local
operations with temporary storage; it makes no network or model calls.
