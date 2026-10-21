# Connector contract audit — 2026-10-19

Provider rules now live in small modules under `ternela/providers/`. A frozen registry
selects them through the existing connector facade. The shared boundary owns approved
origins, GET-only policy, parameter encoding, continuation checks, raw evidence
retention and typed normalized-view validation. Existing engine, scheduling, expiry,
receipts and outbox behavior remain shared.

## Evidence

- **174 unit/API tests pass:** 83 general, 9 safety and 82 workbench on Python 3.15.0.
- **41/41 known simulated regression scenarios pass**, with zero structural issues
  and zero false ready/complete outcomes in that set.
- **Six live registry probes pass:** four published releases, including scoped npm,
  complete; two missing releases stay unknown. An additional **live GitHub release
  read returns HTTP 200 and complete** through the refactored boundary.
- Shared fixtures exercise **176 metadata vectors**: 2 successes, 18 provider-specific
  negatives, 28 HTTP-error/malformed-root cases and 128 wrong identity/version cases.
  These are finite known cases, not independent samples or a correctness probability.
- A test-only connector with a different path, nested response shape and published
  condition runs through create, pending, completion, outage, restart, recovery,
  expiry and acknowledgement without modifying the engine or monitor.
- The negative oracle detects a deliberately broken adapter that invents usable
  files for an empty release. Shared schema validation rejects normalized type drift
  and retains the original metadata/status as an unknown observation.
- Provider/registry immutability, duplicate registration, destination/resource
  restrictions and denied header/body overrides are tested. Empty header/body
  objects remain compatible with existing admitted plans.
- Ruff lint/format passes. No UI source was changed in this pass.

## Extension boundary

Another exact-release connector requires a provider module, a registration entry and
fixtures. The existing SDK release route, capability discovery, watch/monitor runtime
and receipt flow dispatch through that registry. A new release connector without
fixtures fails the coverage test. Optional UI presets are separate.

Read [adding a connector](../adding-connectors.md) for the precise interface. Local
normalized evidence uses a strict inline schema subset, not a general OpenAPI/JSON
Schema validator. Raw provider APIs still require their pinned source. Trusted Python
adapters are reviewed code, not sandboxed third-party plugins.

## Jev: prepared, not measured

The optional evaluator loads **14 frozen task/plan cases** and scores only explicitly
labeled dimensions. Cases cover matching semantics, wrong package/version, omitted
distribution checks, wrong phase, unsupported artifact-safety claims and ambiguity.
The 35,059-byte corpus is stored in a 1,838-byte deterministic gzip file with pinned
raw/compressed hashes.

A live attempt reported **not_run / missing_credentials**. No remote judgments,
probabilities, accuracy or model-quality claims were produced. Evaluator unit tests
use explicit mock answers to verify scoring, failure recording and compressed report
storage; they are not Jev accuracy results. See [the corpus](../../evals/README.md).

The labels are implementation-authored development expectations, not independent
annotations or held-out evaluation. A successful future run remains advisory and
cannot authorize a watch or override deterministic admission. It reports false
matches, unclear answers, service failures and raw judgments separately.

## Performance and reproduction

A 30-iteration synthetic local benchmark measured median create **1.896 ms**, submit
**1.499 ms**, get **0.813 ms** and outbox **1.084 ms**. These samples do not establish
an improvement, throughput bound or latency SLA. Runtime/provider concurrency remains
one serial polling worker; no connection pool, distributed scheduler or read sharing
was added.

```sh
python3 -m ternela.check
python3 -m ternela.regression
python3 -m examples.public_release_probe
python3 -m ternela.semantic_eval
# Optional paid inference after configuring TYPESAFE_API_KEY:
python3 -m ternela.semantic_eval --live
```

[Machine-readable audit](2026-10-19-connector-contracts.json) records hashes for
122 source files, excluding this audit pair. Full evidence and compressed logs
are under ignored `data/audits/connector-contracts/`; registry receipts are in its
`registry/blobs/` store, while the GitHub receipt is in its root `blobs/` store.
Original public-connector audit data is retained. All changes remain local.
