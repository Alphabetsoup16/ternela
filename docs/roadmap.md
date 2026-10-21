# Product plan

Ternela should let a developer give an agent a wait condition, leave it unattended,
and inspect why the agent resumed. The product is responsible for maintaining the
evidence behind that wait. The consumer remains responsible for its later action.

This plan distinguishes working local behavior from the requirements for a hosted
service. CLI distribution, package publishing, CI changes and deployment are outside
this implementation pass. The Python SDK still runs from a source checkout.

## User journey and acceptance criteria

| Step | Intended experience | Acceptance evidence |
|---|---|---|
| Try | Run a clearly labeled example without credentials or inference | Known replay gives pending → complete → unknown |
| Understand | See resource, conditions, phase, freshness and limitations | Interpretation exposes exact identifiers and executable checks |
| Connect | Grant access only to selected resources | Permissions, disconnect and revocation tested against the provider |
| Wait | Leave observations running without an agent polling the provider | Restart, outage and deadline tests; visible monitor status |
| Resume | Receive a durable transition and read the current decision | Crash and duplicate tests; stale transitions cannot imply current success |
| Diagnose | See the failed condition or missing evidence and next step | Structured explanations, receipts and actionable API errors |
| Stop | Stop acquisition explicitly and understand retained evidence | In-flight reads are fenced; evidence expires under its existing policy |

Target: a new developer completes their first real public GitHub wait within five
minutes after local setup. This is a usability target, not a measured result yet.
Observe three first-time developers following only the documentation; record time,
errors, questions and whether they distinguish replay from a live read.

## Delivered locally

- Public GitHub polling and reviewed exact PyPI/npm release recipes, without accounts.
- Compact status/wake-up polling, on-demand receipts and negotiated gzip HTTP responses.
- Bounded reads, leased acquisition, restart recovery and fenced commits.
- Pause/resume and monitor deadlines; retrying an unchanged start does not extend it.
- Provider-failure receipts, backoff and shared provider retry delays.
- SDK methods for refresh, monitoring, bounded waits, job waits and health checks.
- Structured decision explanations; workbench monitoring controls.
- Completed compilation lookup after restart; unfinished jobs report interruption.
- Optional Jev task-to-plan review, separate from deterministic admission.

See [monitoring](monitoring.md) for the precise contract and current limitations.

## Milestone 1: finish the local developer experience

1. Run the documented public example from a fresh checkout and from another folder.
2. Add user-supplied watch names and filtering to the delivered server watch directory.
   Saved watches already recover across browsers; opening a watch exposes its monitor controls.
3. Add explicit watch cancellation, deadlines and retention policies distinct from
   monitor pause and client wait timeout. Historical receipts remain inspectable.
4. Add bounded pending compiler admission and restart-resumable jobs. Today, one
   compiler worker runs locally; queued/in-flight work is not recovered after exit.
5. Separate immutable resources from writable application data. Support configurable
   data directories before validating wheel installation or system service operation.
6. Build a small, explicitly simulated offline onboarding path that does not first
   download full API specifications. Preserve the full-spec evaluation separately.

Exit: users can start, find, understand, resume and stop their own waits without
reading internal schemas. Validate on macOS and Linux with supported Python versions.

## Milestone 2: one complete authenticated GitHub workflow

Initial task: **wait until required checks pass for a pull request's current head**.
This is not implemented by the current fixed check-run and public release examples.

- A GitHub App installation supplies selected-repository access and least permissions.
- Resolve the PR URL to an exact repository, PR and head SHA. Make fixed-SHA versus
  following-new-head behavior explicit; ask a targeted question if ambiguous.
- Resolve applicable required checks from authoritative configuration. Distinguish
  checks from commit statuses, empty results, reruns, skipped/neutral conclusions,
  rulesets and inaccessible configuration. Do not substitute “all checks returned
  by this endpoint” for “all required checks.”
- Verify webhook signatures over raw bytes and retain delivery identity. Use events
  to invalidate affected observations, then reread canonical provider state.
- Periodically reconcile even without events. GitHub does not automatically
  redeliver failed webhook deliveries. [Provider behavior](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries)
- Handle token refresh, rate limits, repository removal and revoked access as
  explicit acquisition outcomes. Do not keep reporting a previous success.

Exit: drop and reorder events, push a new commit during observation, revoke access,
and restart the worker. No old-head result satisfies the new-head condition.
Run against a dedicated test repository with explicitly authorized app access.

## Milestone 3: reliable agent delivery

Keep the existing durable outbox as the source of delivery work. Add delivery
leases, retry schedules, authenticated destinations, dead-letter inspection and
idempotency keys. Delivery is at least once; consumers must tolerate duplicates.
Named consumer checkpoints and ordered acknowledgement are delivered locally.
Delivery leases, authenticated callbacks and retry/dead-letter routing remain future work.

A callback URL introduces outbound network and credential risks. Define allowed
destinations, request signing, DNS/redirect policy, size limits and redaction before
accepting arbitrary destinations. Do not add a generic HTTP sender as a shortcut.

Provide one Python and one TypeScript integration, then a thin agent tool adapter.
The adapter must expose the same watch and receipt semantics as HTTP; it should not
create a separate protocol or require a framework to use Ternela.

Exit: interrupt delivery before and after acknowledgement, replay a duplicate, and
delay a historical success until after invalidation. The consumer reads current
state before taking a consequential action. Observation and action remain non-atomic.

## Milestone 4: observe once, reevaluate affected waits

Represent each admitted plan's observation dependencies explicitly. Coalesce reads
only when provider, authorization scope, resource/version, request representation
and completeness requirements are compatible. Keep freshness and receipts per watch.
Never share private observations merely because resource URLs match.

Events mark dependencies dirty; deterministic evaluation identifies which decisions
changed. Cache compilation only when task meaning, context, evidence version and
validation policy match. An event is not itself proof that a condition holds.

The local worker now coalesces identical anonymous GET responses in batches of at
most 20 watches. It retains per-watch evaluation and freshness. Broader dependency
invalidation and authenticated read sharing remain future work.

Experiment: compare independent polling with shared acquisition at 1, 10 and 100
compatible watches. Measure provider reads, detection latency, CPU, storage and
incorrect outcomes under event loss. See the product-runtime audit for the bounded local experiment; it does not
establish distributed capacity or a claim that the architecture is unique.

## Evaluation and release gates

Keep regression tests separate from prospective evaluation. Freeze unfamiliar tasks
and expected semantics before changing the compiler; record every attempt, repair,
abstention, failure and cost. Include unsupported and ambiguous tasks, not only ones
with a known solution. Publish a confusion matrix by provider and failure class.

Measure incorrect completion, missed transition, abstention rate, observation age,
detection latency, restart recovery, requests per watch-hour and total compilation
cost. Zero observed errors in a finite set is not a correctness guarantee.

Before external service use, add tenant authorization, encrypted secret handling,
request quotas, backpressure, retention/garbage collection, backup/restore exercises,
operational metrics and a documented incident response path. Establish capacity and
recovery targets from measurements; do not promise an SLA from local tests.

Jev can separately flag task/plan meaning mismatches. Its thresholds need evaluation
on labeled tasks; it must not validate provider facts or override schema admission.
See [semantic review](semantic-review.md).

## Scope discipline

Keep anonymous package recipes narrow. Complete the GitHub workflow before authenticated Linear, then select the next
connector from observed customer waits. Generic snapshot ingestion already exists;
verified webhook ingress and cross-source ordering do not. Avoid a connector count,
universal legal verification, an agent framework or a custom transport protocol as
the success metric. Pricing and demand remain hypotheses until people use the wait.
