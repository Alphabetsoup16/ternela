# GitHub Status experiment — frozen contract v1

This contract and its 55 explicit expectations were frozen before the adapter
existed. The hash-pinned corpus is `evals/github_status_v1.json.gz`; its manifest records
baseline runtime hashes and source-document hashes. Labels are implementation-authored,
not independent human annotations. Later observations must not silently change them.

Task: **ready only when one exact GitHub Status leaf component is operational, with
no unresolved incident and no active maintenance attached to it.** This is a before-action
readiness decision, not proof that a deployment or GitHub request will succeed.

Use `GET https://www.githubstatus.com/api/v2/summary.json` with no account. The
[official API](https://www.githubstatus.com/api) describes components, unresolved incidents
and scheduled maintenance in one summary. Match the page and component IDs; labels and
the global status banner cannot substitute for identity or per-component evidence.

`investigating`, `identified` and `monitoring` incidents remain active. `in_progress`
and `verifying` maintenance remains active. Scheduled work does not establish future
readiness. Resolved/postmortem incidents and completed maintenance do not block this
current-state claim. Unattributed active records are unknown, never silently irrelevant.

Missing collections differ from observed empty arrays. Unknown status values, duplicate
identities, wrong page IDs and malformed metadata remain unknown. Only leaf components
are supported; active associations to groups or unknown IDs are conservatively unknown.
For this first strict summary adapter, malformed unrelated rows also make the view
unknown; scoping that uncertainty more narrowly is a future design question.

`page.updated_at` is not treated as a heartbeat. Read freshness comes from the existing
capture-time TTL, with no claim that the provider's publication or cache is instantaneous.
Unexpected continuation means incomplete evidence; no synthetic cursor/has-more values
may be invented to accommodate the runtime.

The experiment must exercise restart, outage, expiry, ordering and acknowledgement.
Record any shared-runtime edits, separate public-interface additions from core changes,
and retain the first failing result. No hosted account, paid model or CI deployment is
required. Live snapshots test wiring; outage transitions use explicit fault fixtures.

| Frozen case | Expected decision |
|---|---|
| `all-required-evidence-present` | ready |
| `target-degraded_performance` | blocked |
| `target-partial_outage` | blocked |
| `target-major_outage` | blocked |
| `target-under_maintenance` | blocked |
| `target-incident-investigating` | blocked |
| `target-incident-identified` | blocked |
| `target-incident-monitoring` | blocked |
| `unrelated-active-incident` | ready |
| `unrelated-outage-and-red-global-banner` | ready |
| `green-banner-does-not-override-target-outage` | blocked |
| `resolved-incident-ignored` | ready |
| `postmortem-incident-ignored` | ready |
| `target-maintenance-in_progress` | blocked |
| `target-maintenance-verifying` | blocked |
| `unrelated-active-maintenance` | ready |
| `scheduled-maintenance-does-not-predict-the-future` | ready |
| `completed-maintenance-ignored` | ready |
| `reordered-components` | ready |
| `matching-name-is-not-identity` | blocked |
| `target-component-absent` | unknown |
| `empty-component-list` | unknown |
| `wrong-page` | unknown |
| `wrong-component-page` | unknown |
| `duplicate-component-id` | unknown |
| `missing-target-status` | unknown |
| `unrecognized-component-status` | unknown |
| `wrong-status-type` | unknown |
| `unrelated-malformed-component-is-conservatively-unknown` | unknown |
| `missing-components` | unknown |
| `components-not-a-list` | unknown |
| `missing-incidents-is-not-an-empty-list` | unknown |
| `missing-maintenance-is-not-an-empty-list` | unknown |
| `active-incident-missing-associations` | unknown |
| `active-incident-empty-associations` | unknown |
| `active-incident-unknown-component` | unknown |
| `active-incident-foreign-page` | unknown |
| `unrecognized-incident-status` | unknown |
| `duplicate-incident-id` | unknown |
| `unrecognized-maintenance-status` | unknown |
| `active-maintenance-empty-associations` | unknown |
| `selected-group-is-not-a-leaf-component` | unknown |
| `old-page-updated-at-is-not-a-heartbeat` | ready |
| `missing-page-updated-at-is-not-a-heartbeat` | ready |
| `global-banner-is-not-required-evidence` | ready |
| `unexpected-continuation-is-incomplete` | unknown |
| `http-301` | unknown |
| `http-403` | unknown |
| `http-404` | unknown |
| `http-429` | unknown |
| `http-500` | unknown |
| `http-503` | unknown |
| `malformed-root-array` | unknown |
| `malformed-root-scalar` | unknown |
| `malformed-root-empty-object` | unknown |
