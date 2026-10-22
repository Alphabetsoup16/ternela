// SPDX-License-Identifier: Apache-2.0
"use strict";
const $ = (id) => document.getElementById(id);
let token,
  templates = [],
  current,
  watch,
  busy = false,
  polling = false,
  lastReceipt,
  jobPoll,
  lastMessages,
  lastRendered;
// Small shared vocabulary; the UI uses native controls and one stylesheet.
let providerNames = { stripe: "Stripe", slack: "Slack" },
  watchKinds = {},
  statusComponents = {},
  directoryNext = null,
  directoryItems = new Map(),
  directoryDirty = true,
  directoryReading = false;
const statusNames = {
  ready: "Ready to proceed",
  complete: "Condition met",
  blocked: "Not ready",
  pending: "Waiting",
  unknown: "No reliable result",
  unverifiable: "Cannot verify",
};
const memoryKey = "ternela.watch-links.v1";
function remembered() {
  try {
    const value = JSON.parse(localStorage.getItem(memoryKey) || "{}");
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  } catch {
    return {};
  }
}
function remember() {
  try {
    const saved = remembered();
    delete saved[current.task.id];
    saved[current.task.id] = watch.id;
    localStorage.setItem(
      memoryKey,
      JSON.stringify(Object.fromEntries(Object.entries(saved).slice(-40))),
    );
  } catch {
    /* Watch URLs still work when browser storage is unavailable. */
  }
}
function duration(seconds) {
  return seconds % 60 === 0 ? `${seconds / 60} minutes` : `${seconds} seconds`;
}
function taskName(t) {
  const context = t.task.context;
  if (t.task.provider === "github_status")
    return statusComponents[context.component_id] || "GitHub service status";
  if (["pypi", "npm"].includes(t.task.provider))
    return `${context.package} ${context.version}`;
  return t.name;
}
function formattedValue(raw) {
  if (raw === undefined) return "Unavailable";
  try {
    const value = JSON.parse(raw);
    if (typeof value === "string") return value;
    if (typeof value === "boolean") return value ? "Yes" : "No";
  } catch {
    /* Keep the exact serialized value when it cannot be parsed. */
  }
  return raw;
}
const labels = {
  success: "Matching evidence",
  quantity_11: "Quantity is 11",
  quantity_0: "Quantity is zero",
  quantity_string: "Quantity is a string",
  wrong_item: "Wrong subscription item",
  wrong_subscription: "Wrong subscription",
  over_budget: "Preview exceeds budget",
  wrong_currency: "Wrong currency",
  forbidden: "Provider denies access",
  processing: "Payment still processing",
  wrong_amount: "Wrong amount received",
  wrong_customer: "Wrong customer",
  missing_response: "No response captured",
  draft: "Release is a draft",
  prerelease: "Release is a prerelease",
  wrong_sha: "Wrong commit SHA",
  wrong_tag: "Wrong release tag",
  queued: "Check is queued",
  failure: "Check failed",
  missing_conclusion: "Conclusion is missing",
  deleted: "User is deactivated",
  bot: "User is a bot",
  wrong_team: "Wrong workspace",
  missing_field: "A required observation is absent",
};
const descriptions = {
  ready:
    "The observed preconditions pass. This does not perform the business action.",
  complete: "The observed API state satisfies this task’s checks.",
  blocked:
    "Observed evidence fails a precondition. The agent should wait or revise its action.",
  pending: "The observed state has not met the completion conditions.",
  unknown:
    "Evidence is missing, expired, inaccessible, or fails a preservation guard.",
  unverifiable:
    "The available evidence cannot establish the task’s full claim.",
};
function node(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function fail(error) {
  $("error").hidden = false;
  $("error").textContent = error.message || String(error);
}
function clearError() {
  $("error").hidden = true;
}
async function api(path, body, refreshSession = true) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Observation-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (
    response.status === 403 &&
    body !== undefined &&
    refreshSession &&
    value.error === "Invalid local session"
  ) {
    token = (await api("/api/bootstrap")).token;
    return api(path, body, false);
  }
  if (!response.ok) {
    const error = new Error(
      value.error || `Request failed (${response.status})`,
    );
    error.status = response.status;
    throw error;
  }
  return value;
}
function setText(id, text) {
  if ($(id).textContent !== text) $(id).textContent = text;
}
function taskNav() {
  const placeholder = node("option", "Choose a live check");
  placeholder.value = "";
  placeholder.disabled = true;
  placeholder.selected = current?.mode !== "live public GET";
  $("mobile-task").replaceChildren(placeholder);
  for (const id of ["tasks", "demo-tasks", "custom-tasks"])
    $(id).replaceChildren();
  for (const t of templates) {
    const live = t.mode === "live public GET";
    if (live) {
      const option = node("option", taskName(t));
      option.value = t.task.id;
      option.selected = current?.task.id === t.task.id;
      $("mobile-task").append(option);
    }
    const button = node("button", taskName(t));
    button.type = "button";
    button.disabled = busy;
    button.setAttribute("aria-current", String(current?.task.id === t.task.id));
    button.append(
      node(
        "small",
        live
          ? `${providerNames[t.task.provider] || t.task.provider} · live`
          : t.mode === "simulated replay"
            ? `${providerNames[t.task.provider] || t.task.provider} · simulated`
            : "Captured responses",
      ),
    );
    button.addEventListener("click", () => choose(t));
    $(
      live
        ? "tasks"
        : t.mode === "simulated replay"
          ? "demo-tasks"
          : "custom-tasks",
    ).append(button);
  }
  $("custom-nav").hidden = !$("custom-tasks").children.length;
}
function setBusy(value) {
  busy = value;
  for (const id of ["apply", "expire", "new-watch", "monitor-toggle"])
    $(id).disabled = value;
  document.querySelector("#snapshot-form button").disabled = value;
  $("create-submit").disabled = value;
  $("mobile-task").disabled = value;
  $("saved-watch").disabled = value;
  $("reload-watches").disabled = value;
  $("more-watches").disabled = value;
  for (const button of document.querySelectorAll(".task-rail nav button"))
    button.disabled = value;
  $("content").setAttribute("aria-busy", String(value));
  $("apply").textContent = value
    ? "Checking…"
    : current?.mode === "live public GET" ||
        (current?.mode === "caller-supplied snapshot" && watch?.live_available)
      ? "Check now"
      : "Test response";
}
async function choose(template, reset = false) {
  if (busy) return;
  clearError();
  setBusy(true);
  try {
    let next;
    const saved =
      remembered()[template.task.id] ||
      [...directoryItems.values()].find(
        (item) =>
          item.task_id === template.task.id && item.phase === template.phase,
      )?.id;
    if (!reset && typeof saved === "string" && /^[a-f0-9]{32}$/.test(saved)) {
      try {
        next = await api(`/api/watches/${saved}`);
      } catch (error) {
        if (error.status !== 404) throw error;
      }
      if (
        next &&
        (next.task.id !== template.task.id || next.phase !== template.phase)
      )
        next = null;
    }
    next ||= await api(
      "/api/watches",
      template.createBody ||
        (template.job
          ? {
              job_id: template.job,
              phase: template.phase,
              ttl: template.task.provider === "github" ? 900 : 30,
            }
          : {
              template_id: template.task.id,
              ttl: template.mode === "live public GET" ? 900 : 30,
            }),
    );
    current = template;
    watch = next;
    activateWatch();
    lastReceipt = null;
    lastMessages = null;
    taskNav();
    renderPlan();
    render();
    await refresh();
  } catch (error) {
    fail(error);
  } finally {
    setBusy(false);
  }
}
function renderPlan() {
  $("event-note").textContent = "";
  const plan = watch.active_plan || current.plan;
  $("task-title").textContent = taskName(current);
  $("task-subtitle").textContent =
    `${providerNames[current.task.provider] || current.task.provider} · ${current.mode === "live public GET" ? "Public API check. No account connected." : current.mode === "simulated replay" ? "Simulated demo. No provider is contacted." : "Compiled task. Supply captured API responses."}`;
  $("link-note").textContent = "";
  $("task-text").textContent = current.task.task;
  $("phase").textContent =
    current.phase === "before" ? "Before the action" : "After the action";
  $("plan-json").textContent = JSON.stringify(plan, null, 2);
  const requests = $("requests");
  requests.replaceChildren();
  for (const request of plan.requests) {
    const li = node("li");
    li.append(node("code", request.operation), node("p", request.purpose));
    requests.append(li);
  }
  if (!requests.children.length)
    requests.append(
      node("li", "No sufficient observation procedure was established."),
    );
  const guards = plan.preservations.filter((g) => g.phase === current.phase);
  $("guard-note").hidden = !guards.length;
  $("guard-note").textContent = guards
    .map((g) => `Preserve ${g.source_pointer} exactly. ${g.reason}`)
    .join(" ");
  const options = $("case");
  options.replaceChildren(
    ...current.cases.map((c) => {
      const option = node("option", labels[c] || c);
      option.value = c;
      return option;
    }),
  );
  const live = current.mode === "live public GET",
    custom = current.mode === "caller-supplied snapshot";
  $("mode").textContent = live
    ? "Read only"
    : custom
      ? "Developer input"
      : "Simulation";
  $("observation-heading").textContent = live
    ? "Check for an update"
    : custom
      ? "Supply a response"
      : "Try a simulated response";
  $("mode-explanation").textContent = live
    ? "Read the public API now, or turn on checks every 5 minutes."
    : custom
      ? "Use the request IDs in Technical details to submit captured responses. Supported public GitHub requests can also be checked live."
      : "Choose an example response to see how the result changes. This does not read a real account.";
  $("case-label").hidden = live || custom;
  $("expire").hidden = live || custom;
  $("replay-form").hidden = custom && !watch.live_available;
  $("snapshot-form").hidden = !custom;
  $("apply").textContent = live || custom ? "Check now" : "Test response";
  $("monitor-controls").hidden = !watch.live_available;
  $("ttl-window").textContent = duration(watch.ttl);
}
function renderMonitor() {
  const monitor = watch.monitor;
  const enabled = monitor?.enabled && monitor.deadline > Date.now() / 1000;
  $("monitor-toggle").textContent = enabled
    ? "Pause automatic checks"
    : "Start automatic checks";
  $("monitor-toggle").setAttribute("aria-pressed", String(!!enabled));
  setText(
    "monitor-note",
    enabled
      ? `Every ${duration(monitor.interval)} until ${new Date(monitor.deadline * 1000).toLocaleString()}. Next eligible check: ${new Date(monitor.due * 1000).toLocaleTimeString()}.${monitor.last_error ? " Last attempt: " + monitor.last_error.replaceAll("_", " ") + "." : ""} Keep the local service running.`
      : monitor
        ? "Paused. Start again for another 24 hours."
        : "Every 5 minutes for 24 hours. Off until you start it.",
  );
}
function freshness() {
  const receipt = watch?.receipt;
  setText(
    "expiry",
    receipt
      ? receipt.result.stale || Date.now() / 1000 >= receipt.expires_at
        ? "Needs a fresh check"
        : `Fresh until ${new Date(receipt.expires_at * 1000).toLocaleTimeString()}`
      : "No check yet",
  );
}
function render() {
  freshness();
  renderMonitor();
  const receipt = watch.receipt,
    result = receipt?.result,
    expired =
      !!receipt && (result.stale || Date.now() / 1000 >= receipt.expires_at),
    status = expired ? "unknown" : result?.status || "unknown";
  const renderedKey = `${watch.id}:${watch.revision}:${receipt?.evaluated_at}:${status}`;
  if (renderedKey === lastRendered) return;
  lastRendered = renderedKey;
  const evidenceMode = receipt?.mode || current.mode;
  $("decision-mode").textContent =
    evidenceMode === "simulated replay"
      ? "Simulated evidence"
      : evidenceMode.startsWith("live public ")
        ? "Live public read"
        : "Caller snapshot";
  document.querySelector(".decision").dataset.status = status;
  $("decision-title").textContent = receipt
    ? expired
      ? "Evidence expired"
      : statusNames[status] || "Unknown result"
    : "Not checked yet";
  $("decision-detail").textContent = receipt
    ? expired
      ? descriptions.unknown
      : watch.explanation
        ? `${watch.explanation.summary} ${watch.explanation.next_step}`
        : descriptions[status]
    : current.mode === "live public GET"
      ? "Check now to read the current API state."
      : "Submit a response to evaluate the conditions below.";
  $("revision").textContent = `Revision ${watch.revision}`;
  const checks = current.plan.checks.filter((c) => c.phase === current.phase);
  const outcomes = Object.fromEntries(
    (expired ? [] : result?.checks || []).map((c) => [c.id, c.outcome]),
  );
  $("check-count").textContent =
    receipt && !expired
      ? `${Object.values(outcomes).filter((v) => v === "true").length} of ${checks.length} met`
      : `${checks.length} conditions`;
  const rows = checks.map((check) => {
    const row = node("tr"),
      field = node("td");
    field.append(node("span", check.reason));
    const evidence = watch.check_evidence?.[check.id];
    const resultCell = node("td");
    resultCell.append(
      node(
        "span",
        { true: "Pass", false: "Not met", unknown: "Unknown" }[
          outcomes[check.id]
        ] || (expired ? "Expired" : receipt ? "Not evaluated" : "Not checked"),
        `outcome ${outcomes[check.id] || ""}`,
      ),
    );
    row.append(
      field,
      node(
        "td",
        `${{ eq: "", gt: "> ", gte: "≥ ", lt: "< ", lte: "≤ " }[check.op] ?? check.op + " "}${formattedValue(evidence?.expected_json ?? check.value_json)}`,
      ),
      node("td", receipt ? formattedValue(evidence?.actual_json) : "—"),
      resultCell,
    );
    return row;
  });
  $("checks").replaceChildren(...rows);
  const gaps = $("gaps");
  gaps.replaceChildren();
  for (const gap of result?.gaps ||
    current.plan.requirements.filter(
      (r) => r.phase === current.phase && r.coverage !== "observable",
    ))
    gaps.append(node("p", gap.limitation || gap.text));
  for (const failure of result?.preservation_failures || [])
    gaps.append(node("p", `Preservation guard: ${failure.reason}`));
  $("download").disabled = !receipt;
  $("receipt-json").textContent = receipt
    ? watch.receipt_text ||
      "Download the exact receipt JSON to inspect this observation."
    : "No receipt yet.";
  $("receipt-description").textContent = receipt
    ? `Plan ${receipt.plan_sha256.slice(0, 12)}… · Read attempts: ${receipt.read_attempts ?? Object.values(receipt.observations || {}).reduce((n, o) => n + o.trace.length, 0)} · ${receipt.mode}`
    : "Each check saves its input, result and timestamps.";
  if (receipt && lastReceipt !== receipt.event_id + status) {
    const el = document.querySelector(".decision");
    el.classList.remove("receipt-updated");
    void el.offsetWidth;
    el.classList.add("receipt-updated");
    lastReceipt = receipt.event_id + status;
  }
}
async function loadReceipt() {
  if (!watch?.receipt || watch.receipt_text) return;
  const identity = watch.id,
    event = watch.receipt.event_id,
    evaluated = watch.receipt.evaluated_at;
  const full = await api(`/api/watches/${identity}?receipt_text=1`);
  if (
    watch.id === identity &&
    watch.receipt?.event_id === event &&
    watch.receipt?.evaluated_at === evaluated &&
    full.receipt?.event_id === event &&
    full.receipt?.evaluated_at === evaluated
  ) {
    watch.receipt_text = full.receipt_text;
    $("receipt-json").textContent = full.receipt_text;
  }
}
$("receipt-json")
  .closest("details")
  .addEventListener("toggle", () => {
    if ($("receipt-json").closest("details").open) loadReceipt().catch(fail);
  });
async function refresh() {
  if (!watch || polling) return;
  polling = true;
  const identity = watch.id;
  try {
    const [next, messages] = await Promise.all([
      api(`/api/watches/${identity}/status`),
      api(`/api/watches/${identity}/outbox?recent=1&compact=1`),
    ]);
    if (watch.id !== identity) return;
    if (
      watch.receipt?.event_id !== next.receipt?.event_id ||
      watch.receipt?.evaluated_at !== next.receipt?.evaluated_at
    )
      delete watch.receipt_text;
    watch = { ...watch, ...next };
    render();
    if (directoryDirty) await loadDirectory();
    if ($("usage").open) await loadUsage();
    if ($("receipt-json").closest("details").open) await loadReceipt();
    $("wake-count").textContent = messages.length;
    const messageKey = JSON.stringify(messages.map((m) => [m.id, m.acked]));
    if (messageKey === lastMessages) return;
    lastMessages = messageKey;
    const list = $("wakeups");
    list.replaceChildren();
    if (!messages.length)
      list.append(node("li", "No decision changes yet.", "empty-wake"));
    for (const message of messages.slice().reverse()) {
      const li = node("li"),
        status = message.receipt.result.status;
      li.append(
        node(
          "strong",
          message.receipt.result.stale
            ? "Evidence expired"
            : statusNames[status] || "Unknown result",
        ),
      );
      const time = node(
        "time",
        new Date(message.created * 1000).toLocaleTimeString(),
      );
      time.dateTime = new Date(message.created * 1000).toISOString();
      li.append(
        time,
        node(
          "p",
          `Revision ${message.receipt.revision} · ${message.receipt.result.stale ? "Freshness window elapsed" : "Evidence decision changed"}`,
          "wake-reason",
        ),
      );
      const ack = node("button", message.acked ? "Handled" : "Mark handled");
      ack.disabled = !!message.acked;
      ack.setAttribute(
        "aria-label",
        `${message.acked ? "Handled" : "Mark handled"}: ${new Date(message.created * 1000).toLocaleString()}, revision ${message.receipt.revision}`,
      );
      ack.addEventListener("click", async () => {
        ack.disabled = true;
        try {
          await api(`/api/watches/${identity}/ack`, { id: message.id });
          await refresh();
        } catch (error) {
          ack.disabled = false;
          fail(error);
        }
      });
      li.append(ack);
      list.append(li);
    }
  } catch (error) {
    fail(error);
  } finally {
    polling = false;
  }
}
async function update(stale = false) {
  if (busy || !watch) return;
  setBusy(true);
  clearError();
  try {
    const live =
      current.mode === "live public GET" ||
      current.mode === "caller-supplied snapshot";
    const delivered = await api(
      `/api/watches/${watch.id}/${live ? "live" : "replay"}`,
      live ? {} : { case: $("case").value, stale },
    );
    $("event-note").textContent = delivered.accepted
      ? "Check saved. The result and evidence are below."
      : "Older observation ignored. Newer evidence remains in effect until it expires.";
    await refresh();
  } catch (error) {
    fail(error);
  } finally {
    setBusy(false);
  }
}
$("replay-form").addEventListener("submit", (event) => {
  event.preventDefault();
  update();
});
$("expire").addEventListener("click", () => update(true));
$("new-watch").addEventListener("click", () => choose(current, true));
$("monitor-toggle").addEventListener("click", async () => {
  if (busy || !watch) return;
  const identity = watch.id;
  setBusy(true);
  clearError();
  try {
    const enabled =
      watch.monitor?.enabled && watch.monitor.deadline > Date.now() / 1000;
    await api(
      `/api/watches/${identity}/${enabled ? "pause" : "monitor"}`,
      enabled ? {} : { interval: 300, duration: 86400 },
    );
    await refresh();
  } catch (error) {
    fail(error);
  } finally {
    setBusy(false);
  }
});
$("snapshot-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !watch) return;
  setBusy(true);
  clearError();
  try {
    const snapshots = JSON.parse($("snapshot-json").value, (key, value) => {
      if (
        typeof value === "number" &&
        (!Number.isFinite(value) ||
          (Number.isInteger(value) && !Number.isSafeInteger(value)))
      )
        throw new Error(
          "This number exceeds browser precision. Submit exact integers through the Python SDK.",
        );
      return value;
    });
    await api(`/api/watches/${watch.id}/events`, {
      event_id: crypto.randomUUID(),
      revision: watch.revision + 1,
      snapshots,
    });
    await refresh();
  } catch (error) {
    fail(error);
  } finally {
    setBusy(false);
  }
});
$("download").addEventListener("click", async () => {
  if (!watch?.receipt) return;
  try {
    const response = await fetch(`/api/watches/${watch.id}/receipt`);
    if (!response.ok)
      throw new Error("Could not download the receipt. Try again.");
    const url = URL.createObjectURL(await response.blob());
    const link = node("a");
    link.href = url;
    link.download = `evidence-${watch.id}-r${watch.revision}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    fail(error);
  }
});
$("compile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  const button = $("compile");
  button.disabled = true;
  try {
    const context = JSON.parse($("context-json").value);
    const job = await api("/api/compile", {
      task: $("new-task").value,
      provider: $("provider").value,
      context,
    });
    $("job-status").textContent =
      "Compiling from API docs… You can keep using the workbench.";
    const phase = $("new-phase").value;
    let jobReading = false;
    jobPoll = setInterval(async () => {
      if (jobReading) return;
      jobReading = true;
      try {
        const value = await api(`/api/jobs/${job.id}`);
        $("job-status").textContent =
          value.status === "failed"
            ? value.error
            : `${value.status} · ${job.id}`;
        if (value.status === "completed" || value.status === "failed") {
          clearInterval(jobPoll);
          button.disabled = false;
          if (value.status === "completed") {
            const template = {
              task: value.task,
              plan: value.plan,
              name: "Your compiled task",
              cases: [],
              phase,
              run: value.run,
              job: job.id,
              mode: "caller-supplied snapshot",
            };
            templates.push(template);
            taskNav();
            await choose(template);
            $("workspace").scrollIntoView({ behavior: "smooth" });
          }
        }
      } catch (error) {
        clearInterval(jobPoll);
        button.disabled = false;
        fail(error);
      } finally {
        jobReading = false;
      }
    }, 2000);
  } catch (error) {
    button.disabled = false;
    fail(error);
  }
});
function activateWatch() {
  directoryDirty = true;
  $("usage-summary").textContent = "Loading this watch’s usage…";
  if (current.run === "reviewed-recipe" && watchKinds[current.task.provider]) {
    current.createBody = {
      recipe: current.task.provider,
      context: current.task.context,
      ttl: watch.ttl,
    };
  }
  if (!templates.some((t) => t.task.id === current.task.id))
    templates.push(current);
  lastMessages = null;
  lastReceipt = null;
  history.replaceState(null, "", `/?watch=${watch.id}`);
  remember();
  lastRendered = null;
  if (current.mode === "simulated replay")
    document.querySelector(".demo-nav").open = true;
}
function renderWatchFields() {
  const kind = watchKinds[$("watch-kind").value];
  $("watch-fields").replaceChildren(
    ...kind.fields.map((field) => {
      const label = node("label", field.label);
      const input = node(field.options ? "select" : "input");
      input.name = field.key;
      input.required = true;
      if (field.options) {
        for (const [value, text] of Object.entries(field.options)) {
          const option = node("option", text);
          option.value = value;
          input.append(option);
        }
      } else {
        input.type = "text";
        input.placeholder = field.placeholder;
        input.maxLength = field.max_length || 214;
        input.autocomplete = "off";
        input.spellcheck = false;
      }
      label.append(input);
      return label;
    }),
  );
  $("create-hint").textContent = kind.hint;
}
function showCreate(open) {
  $("create-watch").hidden = !open;
  $("show-create").setAttribute("aria-expanded", String(open));
  $("create-status").textContent = "";
  if (open) {
    $("create-watch").scrollIntoView({ block: "start" });
    $("watch-kind").focus();
  } else $("show-create").focus();
}
$("show-create").addEventListener("click", () =>
  showCreate($("create-watch").hidden),
);
$("cancel-create").addEventListener("click", () => showCreate(false));
$("watch-kind").addEventListener("change", renderWatchFields);
$("mobile-task").addEventListener("change", () => {
  const template = templates.find((t) => t.task.id === $("mobile-task").value);
  if (template) choose(template);
});
$("copy-link").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(location.href);
    $("link-note").textContent =
      "Link copied. Save it to return to this watch.";
  } catch {
    $("link-note").textContent =
      `Copy this address to return: ${location.href}`;
  }
});
$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const provider = $("watch-kind").value;
  const context = Object.fromEntries(
    watchKinds[provider].fields.map((f) => [
      f.key,
      $("create-form").elements.namedItem(f.key).value.trim(),
    ]),
  );
  setBusy(true);
  clearError();
  $("create-status").textContent = "Creating watch…";
  try {
    const body = { recipe: provider, context, ttl: 900 };
    const next = await api("/api/watches", body);
    watch = next;
    current = {
      task: next.task,
      plan: next.plan,
      phase: next.phase,
      name: taskName({ task: next.task, name: "Your watch" }),
      cases: [],
      run: "reviewed-recipe",
      mode: "live public GET",
      createBody: body,
    };
    const existing = templates.findIndex((t) => t.task.id === current.task.id);
    if (existing < 0) templates.push(current);
    else templates[existing] = current;
    activateWatch();
    taskNav();
    renderPlan();
    render();
    $("create-watch").hidden = true;
    $("show-create").setAttribute("aria-expanded", "false");
    $("workspace").focus();
    $("apply").textContent = "Checking…";
    try {
      await api(`/api/watches/${watch.id}/live`, {});
      $("event-note").textContent = "Watch created. The first check is saved.";
    } catch (error) {
      fail(
        new Error(
          `Watch created, but the first check failed: ${error.message}. Use Check now to retry.`,
        ),
      );
    }
    await refresh();
  } catch (error) {
    fail(error);
  } finally {
    setBusy(false);
    $("create-status").textContent = "";
  }
});
async function loadDirectory(more = false) {
  if (directoryReading) return;
  directoryReading = true;
  $("directory-note").textContent = "Loading saved watches…";
  $("more-watches").disabled = true;
  try {
    const page = await api(
      `/api/watches?limit=30&after=${encodeURIComponent(more ? directoryNext || "" : "")}`,
    );
    if (!more) {
      directoryItems.clear();
      const placeholder = node("option", "Open a saved watch");
      placeholder.value = "";
      $("saved-watch").replaceChildren(placeholder);
    }
    for (const item of page.items) {
      directoryItems.set(item.id, item);
      const template = templates.find((t) => t.task.id === item.task_id);
      const label = template
        ? taskName(template)
        : taskName({
            task: { provider: item.provider, context: item.context },
            name: item.name,
          });
      const option = node(
        "option",
        `${label.slice(0, 64)} · ${statusNames[item.status] || "Not checked"} · ${item.id.slice(0, 6)}`,
      );
      option.value = item.id;
      $("saved-watch").append(option);
    }
    $("saved-watch").value = watch?.id || "";
    directoryNext = page.next;
    $("more-watches").hidden = !directoryNext;
    $("directory-note").textContent =
      page.items.length || more
        ? "Saved by this service. Refresh to update the list."
        : "No saved watches yet. Choose a quick start below.";
    directoryDirty = false;
  } catch (error) {
    $("directory-note").textContent =
      `Could not load saved watches: ${error.message}. Use Refresh list to retry.`;
  } finally {
    directoryReading = false;
    $("more-watches").disabled = busy;
  }
}
async function openSaved(identity) {
  if (!identity || busy) return;
  setBusy(true);
  clearError();
  try {
    const next = await api(`/api/watches/${encodeURIComponent(identity)}`);
    const template = templates.find((t) => t.task.id === next.task.id);
    watch = next;
    current = {
      ...(template || { name: "Your saved task", cases: [] }),
      task: next.task,
      plan: next.plan,
      phase: next.phase,
      run: next.task._origin_run || "Saved plan",
      mode: next.task._mode || template?.mode || "caller-supplied snapshot",
    };
    activateWatch();
    taskNav();
    renderPlan();
    render();
    await refresh();
  } catch (error) {
    fail(error);
  } finally {
    setBusy(false);
  }
}
$("saved-watch").addEventListener("change", () =>
  openSaved($("saved-watch").value),
);
$("reload-watches").addEventListener("click", () => loadDirectory());
$("more-watches").addEventListener("click", () => loadDirectory(true));
async function loadUsage() {
  if (!watch) return;
  const identity = watch.id;
  const usage = await api(`/api/watches/${identity}/usage`);
  if (watch.id !== identity) return;
  const count = (key, label, plural = label + "s") =>
    `${usage[key].toLocaleString()} ${usage[key] === 1 ? label : plural}`;
  $("usage-summary").textContent = [
    count("network_reads", "network read"),
    count("shared_reads", "shared read"),
    count("observations", "accepted observation"),
    count("decision_changes", "decision change"),
    count("unknown_observations", "unknown observation"),
    count("expiry_events", "expiration"),
  ].join(" · ");
}
$("usage").addEventListener("toggle", () => {
  if ($("usage").open) loadUsage().catch(fail);
});

let demoTimer;
function renderAgentDemo(run) {
  clearTimeout(demoTimer);
  const active = run.status === "running";
  $("run-agent-demo").disabled = active;
  setText(
    "run-agent-demo",
    active
      ? "Demo running…"
      : run.status === "idle"
        ? "Run agent demo"
        : "Run again",
  );
  $("agent-demo-progress").hidden = run.status === "idle";
  if (run.status === "idle") return;
  const events = new Map(run.events.map((event) => [event.event, event]));
  const stages = [
    events.get("watch"),
    events.get("watch"),
    events.get("observed"),
    events.get("resume") || events.get("recover"),
    events.get("done"),
  ];
  const start = events.get("agent")?.at;
  const last = stages.findLastIndex(Boolean);
  for (const [index, step] of [...$("agent-demo-steps").children].entries()) {
    step.dataset.state = stages[index] ? "done" : "waiting";
    if (active && index === Math.max(0, last))
      step.setAttribute("aria-current", "step");
    else step.removeAttribute("aria-current");
    step.querySelector(".demo-step-time").textContent =
      stages[index] && start !== undefined
        ? `${Math.max(0, stages[index].at - start).toFixed(1)}s`
        : "Not yet";
  }
  const status =
    run.status === "failed"
      ? run.error || "Demo stopped. Run it again to retry."
      : run.status === "completed"
        ? "Agent resumed. Summary verified and event acknowledged."
        : events.has("resume")
          ? "Agent resumed. Reading the CSV and checking its answer…"
          : events.has("watch")
            ? "Agent stopped. The separate export is running…"
            : "Starting the reference agent and creating its watch…";
  setText("agent-demo-status", status);
  $("agent-demo-answer").hidden = run.status !== "completed";
  if (run.status === "completed") {
    setText(
      "agent-demo-total",
      `${run.answer.orders} orders · $${(run.answer.total_cents / 100).toFixed(2)} USD`,
    );
    setText(
      "agent-demo-evidence",
      `${run.csv}\nVerified answer\n${JSON.stringify(run.answer, null, 2)}`,
    );
  }
  if (active) demoTimer = setTimeout(loadAgentDemo, 500);
}
async function loadAgentDemo() {
  try {
    renderAgentDemo(await api("/api/agent-demo"));
  } catch {
    renderAgentDemo({
      status: "failed",
      events: [],
      error:
        "Could not reach the demo. Run again to reconnect; an active run will be reused.",
    });
  }
}
$("run-agent-demo").addEventListener("click", async () => {
  clearTimeout(demoTimer);
  $("run-agent-demo").disabled = true;
  try {
    renderAgentDemo(await api("/api/agent-demo", {}));
  } catch (error) {
    renderAgentDemo({ status: "failed", events: [], error: error.message });
  }
});

async function start() {
  try {
    const boot = await api("/api/bootstrap");
    token = boot.token;
    loadAgentDemo();
    templates = boot.templates;
    for (const connector of boot.connectors) {
      providerNames[connector.provider] = connector.label;
      if (connector.recipe) watchKinds[connector.provider] = connector.recipe;
    }
    statusComponents = watchKinds.github_status?.fields[0].options || {};
    $("watch-kind").replaceChildren(
      ...Object.entries(watchKinds).map(([provider, recipe]) => {
        const option = node("option", recipe.label);
        option.value = provider;
        return option;
      }),
    );
    renderWatchFields();
    $("show-create").disabled = false;
    await loadDirectory();
    const health = await api("/api/health");
    if (!health.capabilities.task_compiler_available) {
      $("compiler-note").textContent =
        "Compilation needs the Codex CLI and model access on this host. It is unavailable here. Use a public check above instead.";
      for (const control of $("compile-form").elements) control.disabled = true;
    }
    $("evaluation-json").textContent = JSON.stringify(
      { evaluation: boot.evaluation, supplementary_sources: boot.sources },
      null,
      2,
    );
    $("loading").hidden = true;
    $("content").hidden = false;
    const saved = new URLSearchParams(location.search).get("watch");
    if (saved) {
      try {
        watch = await api(`/api/watches/${encodeURIComponent(saved)}`);
      } catch (error) {
        if (error.status !== 404) throw error;
        await choose(
          templates.find((t) => t.task.provider === "github_status") ||
            templates[0],
        );
        fail(
          new Error(
            "That watch was not found. A live quick start is open; choose a check or create a new watch.",
          ),
        );
      }
      const template = templates.find((t) => t.task.id === watch.task.id);
      current = {
        ...(template || { name: "Your saved task", cases: [] }),
        task: watch.task,
        plan: watch.plan,
        phase: watch.phase,
        run: watch.task._origin_run || "Saved plan",
        mode: watch.task._mode || template?.mode || "caller-supplied snapshot",
      };
      activateWatch();
      taskNav();
      renderPlan();
      render();
      await refresh();
    } else
      await choose(
        templates.find((t) => t.task.provider === "github_status") ||
          templates.find((t) => t.mode === "live public GET") ||
          templates[0],
      );
    setInterval(() => {
      if (!busy) refresh();
    }, 2000);
    setInterval(() => {
      if (watch?.receipt) render();
    }, 1000);
  } catch (error) {
    $("loading").textContent = "Could not load the workbench. Reload to retry.";
    fail(error);
  }
}
start();
