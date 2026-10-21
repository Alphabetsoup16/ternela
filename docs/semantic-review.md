# Optional Jev semantic review

The compiler's deterministic admission checks schema, documented fields, dependencies
and execution policy. They do not prove that a model preserved every nuance of a
natural-language task. An optional Jev review supplies three separate judgments:
resource/version scope, condition coverage and before/after meaning.

```python
from ternela.semantic_review import review_plan

# Call only after configuring TYPESAFE_API_KEY through your secret setup.
report = review_plan(watch["task"], watch["plan"])
print(report["answers"])
```

This sends the supplied task text, provider, context and plan to TypeSafe. Do not
include secrets in that context. It makes one bounded HTTPS request, with a 30-second
timeout and no automatic paid retries. A missing credential produces an actionable
error without making a network call. No remote call occurs during ordinary watches,
compilation, replay, tests or import.

Each Choice can report `matches`, `mismatch` or `unclear`, with its probabilities and
confidence. A report includes the actual returned model and input hash. Large integers
are represented as explicit exact-integer strings across the external JSON boundary.
Direct review reports are returned to the caller; they are not automatically persisted. Use the
existing compressed Store if retaining a review as experiment evidence is appropriate.

**All judgments are advisory.** The function cannot admit a plan, change a watch,
verify provider state or execute an action. No acceptance threshold is supplied:
we have not calibrated Jev on Ternela tasks. A high confidence value is not proof
that the judgment or the whole workflow is correct.

## Evidence and next experiment

This pass tests request construction, missing credentials, response validation,
exact quantities and secret omission with mocked responses. **No live Jev judgment
was run because no API key was configured.** The mocked model name is explicitly a
fixture and is not presented as service or semantic-quality evidence.

A frozen [14-case development corpus](../evals/README.md) and evaluator are now available.
`python3 -m ternela.semantic_eval` validates it offline; append `--live` to run paid
Jev judgments when `TYPESAFE_API_KEY` is configured. Records are compressed and
failures remain visible. No live judgments were obtained in this pass.

Before using review to route real tasks, obtain independently labeled examples containing wrong
resource IDs, fixed versus moving commit scope, all/any confusion, omitted thresholds,
phase mistakes and unobservable requirements. Record every answer and service failure;
measure false acceptance, false rejection, abstention, latency and cost. Compare the
compiler with and without advisory feedback on a separate task set. Do not tune and
report quality on the same examples.

The adapter follows TypeSafe's [HTTP contract](https://docs.typesafe.ai/api/system-one),
[Choice primitive](https://docs.typesafe.ai/primitives/choice) and
[citation-checking pattern](https://docs.typesafe.ai/cookbooks/citation_check), read
on 19 October 2026. Contract changes require fresh validation against the live service.

Jev supplies typed judgments, not a code-review conversation or correctness certificate; see [TypeSafe’s coding-agent guidance](https://docs.typesafe.ai/introduction/coding-agents). Deterministic admission, contract tests and fault injection remain separate from semantic evaluation.
