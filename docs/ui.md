# Local watch interface

The main path is **choose a check → read the result → optionally start automatic checks**.
New watches support GitHub service status and exact PyPI/npm package versions,
without provider accounts or a model. Creating a watch performs its first public read;
starting a schedule remains a separate, explicit action.

Live quick starts open first. Simulated payment/workspace examples sit under
**Simulated demos** and remain clearly labeled. The conditions behind a result stay
visible; API operations, original task text, plan JSON, receipts and reset live under
**Technical details**. **Activity** retains the durable notifications and their
acknowledgment action. Compilation is an advanced option and is disabled when the
host reports that its CLI is unavailable.

## One-click agent handoff

The **Run agent demo** section above the watch opens a real local export handoff.
Its five-stage timeline follows saved runner events, including an actual stopped
agent interval, then exposes the CSV and verified summary. It always uses the
clearly labeled no-model reference agent. Reload joins the saved run; repeated
clicks while active reuse it. See [the demo guide](agent-demo.md) for recovery,
retention, the protected local API, and the optional CLI model-backed version.

## Keep the interface small

There is no frontend runtime dependency, CDN, build pipeline or component framework.
The existing interface uses standard buttons, forms, selects, tables and disclosures.
For this surface, a framework migration would add more installation and update work
than it removes. Revisit that decision if complex widgets or several separate screens
need a maintained accessibility implementation.

| Responsibility | Location |
|---|---|
| Semantic structure, disclosures, labels | `workbench/static/index.html` |
| Shared palette, spacing, typography, control states and responsive rules | `workbench/static/style.css` |
| Provider/status vocabulary, watch form definitions, DOM helpers and interaction flow | `workbench/static/app.js` |
| Evidence, supported recipes, validation and persistence | Existing Python API and connector registry |

Use the existing tokens and control classes instead of appending per-element style
overrides. Add a form variant to `watchKinds` only when a reviewed backend recipe
exists; the table does not grant connector capabilities. Render API content with
`textContent`, never HTML interpolation. Keep exact serialized numbers intact; a
friendlier display must not become evidence parsing or change the downloaded receipt.

The browser remembers one watch link per quick-start task (up to 40), rather than
creating a watch every time it is selected. A reset creates another watch and retains
the previous one. Custom watch URLs should be saved with **Copy watch link**; browser
storage is a navigation convenience, not a server-wide watch directory. Clearing it
must not delete server data. Automatic schedules continue when switching views.

On small screens a native selector replaces the live navigation buttons. The evidence
table scrolls inside a labeled, keyboard-focusable region; the page itself should not
scroll horizontally. Keep status text in addition to color and retain visible focus,
reduced-motion support, form errors and disabled/loading states.

## Validate changes

Run `python3 -m ternela.check` and `node --check workbench/static/app.js`. In the browser,
exercise a live check, schedule start/pause, switching away and back, reload from a
saved URL, invalid input followed by correction, a scoped npm package, a simulated
failure, and receipt inspection. Check desktop and mobile together. An optional
formatter is development tooling only; it is not required to serve or install the app.


Saved watches now come from the service directory, with explicit Refresh list and
Load more controls. Browser storage is a quick-start convenience, not the only way
to recover a watch. The form consumes registry-owned connector descriptors. Watch
usage loads only when opened and shows diagnostic counters without a dashboard.
No frontend framework or component dependency was added.
