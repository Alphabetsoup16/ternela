# One-click agent handoff — 2026-10-21

The workbench now starts the existing local process/artifact example with one button.
The browser shows saved event timestamps rather than simulating stages on a timer.
The five-second export gives visitors time to see that the agent has exited; the
completion delivery then launches the continuation and verifies its CSV summary.
This UI mode uses a deterministic reference agent and makes no model calls.

## Evidence

- Actual browser runs reached stopped and completed states; reloading recovered
  the same saved result. Desktop (1280px) and mobile (390px) captures cover both
  states. Mobile document width equaled the viewport, and the button measured 44px.
- The actual built image completed POST/GET UI API flow with a stopped interval,
  three orders and 6,800 cents. It ran nonroot with a read-only root, no network,
  dropped capabilities and temporary native storage. The README screenshot was
  present in the extracted, verified source bundle. The initial harness allowed
  only five seconds for startup and failed readiness. A diagnostic rerun with
  retained service logs and a bounded 20-second readiness wait passed without
  changing application behavior; both trial records are retained.
- 385 unit/API tests pass: 255 general, 9 safety and 121 workbench. Eight added
  tests cover real child processes, saved progress, partial gzip writes, process
  failure and timeout cleanup, interrupted/incomplete output, identity validation,
  retention and local token/origin enforcement.
- 41/41 existing simulated regression scenarios pass. Ruff lint/format and
  JavaScript syntax checks pass.
- The separate Impeccable finish reviewer returned **ship** with no material fixes
  in this scoped extension. Its documentation pass confirmed the incumbent tokens,
  controls and layout were preserved. The detector's disabled-button contrast
  finding was corrected; remaining findings concern inherited sections.

## Boundaries

The protected POST accepts only an empty object and cannot select a model, command,
folder or provider. The adapter runs the existing demo controller, retaining its
watch, evidence and consumer semantics. No frontend framework, dependency or new
scheduling subsystem was added. The browser polls progress only during the run.
Finished-run retention is limited to five previous owned runs plus the current run;
logs are compressed. This is a bounded single-service local alpha demo, not a
remote agent runner or a hostile-code sandbox.

The original v0.1.1 release and installed service remain unchanged. This feature
ships in the current source checkout. The source preview uses isolated watch
storage on port 8770; it does not update or replace the user's installed service.
Private test records and reviewer captures stay in ignored local audit folders.
