# Semantic review development corpus

`semantic_cases.json.gz` contains 14 frozen task/plan pairs for PyPI and npm:
matching plans, wrong package/version bindings, omitted distribution conditions,
wrong phase, unsupported artifact-safety claims and ambiguous requests. The manifest
pins both compressed and raw bytes. The corpus is about Jev's task-to-plan judgments,
not the compiler's ability to generate a plan or the runtime's provider acquisition.

Labels are implementation-authored development expectations. They were specified
without Jev responses, but are not independent annotations or held-out test data.
Only explicitly labeled dimensions are scored. Other answers are retained without
being treated as correct or incorrect. Do not tune on this corpus and advertise it
as generalization evidence.

```sh
python3 -m ternela.semantic_eval         # Validate corpus; no inference
python3 -m ternela.semantic_eval --live  # Requires TYPESAFE_API_KEY; paid API usage
```

A live run submits only these synthetic public fixtures to TypeSafe. It records the
requested model, each returned model, probabilities, confidence, input hash, elapsed
time, all service failures, a confusion matrix, false matches and unclear answers.
It never admits a plan, starts a monitor or changes a watch. There are no automatic
paid retries. Use `--model` to select a specific available model for a comparison.
No acceptance threshold or quality gate has been calibrated.

Each run creates `data/audits/semantic-eval/{run-id}/report.json`. Detailed answers
are retained in verified content-addressed gzip blobs. Missing credentials produce
`not_run` with no judgments or accuracy summary. Live runs with service failures
remain explicitly incomplete in their status; a failure is never counted as an
appropriate abstention. A completed evaluation means requests finished, not that
all labels were correct. Token costs are not calculated by this runner.

To change the corpus, review the labels separately, bump its version, regenerate
both hashes and retain old reports with their original corpus hash. Use a separate,
independently labeled set before claiming quality beyond these development cases.
