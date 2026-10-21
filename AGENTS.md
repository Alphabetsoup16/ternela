# Working in this repository

Preserve unrelated working-tree changes. Keep the observation plan data-only and
missing evidence distinct from an unmet condition. Provider code owns interpretation;
scheduling, acquisition policy, evaluation, persistence and delivery remain shared.
See `docs/adding-connectors.md` and `docs/development.md`.

Use the existing development command while making changes:

```sh
python3 -m ternela.check --changed
# Or keep one foreground terminal running:
python3 -m ternela.check --watch
```

The first run and uncertain change types select the full suite automatically. Use
the full `python3 -m ternela.check` for release work and shared runtime refactors.
Do not add a second checklist/approval workflow around an ordinary passing check.
Inspect a failure's `data/checks/last-run.json`; repair the behavior or justify a
changed expectation. Do not weaken expectations or discard failure evidence to
obtain a pass. Receipts are historical comparison material, not ground truth.

Keep generated reports compressed in ignored `data/audits/`, documentation under
`docs/`, and original/local receipt data out of public fixtures and release bundles.
Do not introduce a dependency or parallel implementation when an existing primitive
fits. Add tests for meaningful changed behavior and failure modes, not source shape.
A passing finite test set is not permission to remove unexercised recovery logic.


Stable runtime code lives under `ternela/`; interface and experiment packages depend
on it. Keep SQL in the database/migration/watch/schedule repositories. Preparation
runs outside write transactions; commit rechecks plan/context identity, revision,
lease/deadline and freshness. Add ordered migrations and explicit format versions
for incompatible state changes. Preserve legacy aliases and historical blobs.
