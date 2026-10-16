// The amlint playground: editors, tabs and rendering. amlint itself runs in worker.js.
// Everything stays in this tab; the config is only ever put in the URL by "Share link",
// and then only after the "#", which browsers don't send to servers.

const $ = (id) => document.getElementById(id);
const root = $("amlint-playground");
const coarse = window.matchMedia("(pointer: coarse)");

// ---------------------------------------------------------------- small DOM helper

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;

// `backticks` in amlint's texts as <code>
const withCode = (text) =>
  String(text).split(/`([^`]+)`/).map((part, i) => (i % 2 ? h("code", {}, part) : part));

function setStatus(text, status) {
  $("amp-status-text").textContent = text;
  if (status) root.dataset.status = status;
}

// ---------------------------------------------------------------- the worker

const worker = new Worker(new URL("worker.js", import.meta.url));
const pending = new Map();
let nextId = 0;

worker.onmessage = (event) => {
  const { id, result, error, status } = event.data;
  if (status) {
    setStatus(status);
    return;
  }
  const call = pending.get(id);
  pending.delete(id);
  if (call) error ? call.reject(new Error(error)) : call.resolve(result);
};
worker.onerror = (event) => {
  event.preventDefault();
  failed(event.message || "the worker could not start");
};

function call(cmd, ...args) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, cmd, args });
  });
}

function failed(why) {
  setStatus(`Couldn't load Python: ${why}. The playground downloads Pyodide from ` +
            "cdn.jsdelivr.net once; check your connection or content blockers, then reload.",
            "error");
  for (const p of pending.values()) p.reject(new Error(why));
  pending.clear();
}

// Runs fn after `ms` of quiet; a result that arrives after a newer run started is dropped.
const timers = {};
const runs = {};
function debounce(name, fn, ms = 250) {
  clearTimeout(timers[name]);
  timers[name] = setTimeout(async () => {
    const run = (runs[name] = (runs[name] || 0) + 1);
    try {
      await fn(() => run !== runs[name]);
    } catch (err) {
      console.error(err);
    }
  }, ms);
}

// ---------------------------------------------------------------- state

const state = {
  ready: false,
  manifest: null,
  explains: {},
  docs: {},
  findings: [],
  tree: null,
  routeLines: new Map(), // route path -> line, from the last tree
  matched: new Set(), // route paths the "Route an alert" labels go through
  loaded: "", // the config text last loaded from an example or a link
  tab: "findings",
};

let editor, before, rules;

// ---------------------------------------------------------------- tabs

const TABS = ["findings", "tree", "route", "diff", "rules"];

function selectTab(name, { focus = false } = {}) {
  state.tab = name;
  for (const t of TABS) {
    const tab = $(`amp-tab-${t}`);
    const on = t === name;
    tab.setAttribute("aria-selected", on);
    tab.tabIndex = on ? 0 : -1;
    $(`amp-panel-${t}`).hidden = !on;
    if (on && focus) tab.focus();
  }
  if (!editor) return; // still loading
  if (name === "route") scheduleRoute(0);
  if (name === "diff") scheduleDiff(0);
  // CodeMirror measures itself when it becomes visible
  for (const ed of [before, rules]) ed.view.requestMeasure();
}

for (const t of TABS) {
  $(`amp-tab-${t}`).addEventListener("click", () => selectTab(t));
}
document.querySelector(".amp-tabs").addEventListener("keydown", (event) => {
  const i = TABS.indexOf(state.tab);
  const moves = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 };
  if (!(event.key in moves)) return;
  event.preventDefault();
  selectTab(TABS[(moves[event.key] + TABS.length) % TABS.length], { focus: true });
});

// ---------------------------------------------------------------- jumping to lines

function jump(ed, line, column = 1) {
  if (!line) return;
  ed.jumpTo(line, column, { focus: !coarse.matches });
  const box = ed.view.dom.getBoundingClientRect();
  if (box.bottom < 0 || box.top > window.innerHeight) {
    ed.view.dom.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function lineButton(ed, line, column, label) {
  if (!line) return null;
  return h("button", {
    type: "button",
    class: "amp-loc",
    title: `Go to line ${line}`,
    onclick: (event) => {
      event.stopPropagation();
      jump(ed, line, column);
    },
  }, label || `${line}:${column || 1}`);
}

// ---------------------------------------------------------------- findings

const LEVEL = { error: "error", warn: "warning", info: "info" };
const ICON = { error: "✖", warn: "⚠", info: "ℹ" };

function docsLink(code) {
  const page = state.docs[code];
  // a new tab, so the config in this one isn't lost
  return page ? h("a", { class: "amp-code", href: `../${page}#${code}`, target: "_blank",
                         rel: "noopener" }, code)
    : h("span", { class: "amp-code" }, code);
}

function explainDetails(code) {
  const e = state.explains[code];
  if (!e) return null;
  return h("details", { class: "amp-explain" },
    h("summary", {}, "Why it matters and how to fix it"),
    e.why ? h("p", {}, withCode(e.why)) : null,
    e.bad ? [h("div", { class: "amp-explain__label" }, "Bad"), h("pre", {}, h("code", {}, e.bad))] : null,
    e.good ? [h("div", { class: "amp-explain__label" }, "Fixed"), h("pre", {}, h("code", {}, e.good))] : null,
  );
}

function findingItem(f, ed, { badge } = {}) {
  return h("li", {
    class: `amp-finding amp-finding--${f.level}`,
    dataset: { code: f.code, line: f.line || "" },
    onclick: (event) => {
      if (!event.target.closest("a, details, button")) jump(ed, f.line, f.column);
    },
  },
  h("div", { class: "amp-finding__head" },
    badge ? h("span", { class: `amp-tag amp-tag--${badge}` }, badge) : null,
    h("span", { class: "amp-level", title: LEVEL[f.level] }, ICON[f.level], " ", LEVEL[f.level]),
    docsLink(f.code),
    lineButton(ed, f.line, f.column),
    f.where ? h("span", { class: "amp-where" }, f.where) : null,
  ),
  h("p", { class: "amp-msg" }, withCode(f.message)),
  explainDetails(f.code));
}

function summarize(findings) {
  const n = { error: 0, warn: 0, info: 0 };
  for (const f of findings) n[f.level] = (n[f.level] || 0) + 1;
  return n;
}

// "Failed to parse YAML ... line 3, column 9" -> {line, column} of the problem
function yamlErrorPosition(text) {
  const marks = [...String(text).matchAll(/line (\d+), column (\d+)/g)];
  if (!marks.length) return null;
  const last = marks[marks.length - 1];
  return { line: Number(last[1]), column: Number(last[2]) };
}

function stderrNotes(text) {
  const lines = String(text || "").split("\n").map((l) => l.trim()).filter(Boolean);
  return lines.length
    ? h("ul", { class: "amp-notes" }, lines.map((l) => h("li", {}, l.replace(/^warning:\s*/, ""))))
    : null;
}

function errorBox(title, text, ed) {
  const pos = yamlErrorPosition(text);
  return h("div", { class: "amp-error" },
    h("strong", {}, title),
    pos ? [" ", lineButton(ed, pos.line, pos.column, `line ${pos.line}`)] : null,
    h("pre", {}, text || "(no details)"));
}

function renderCheck(res) {
  const out = $("amp-findings");
  const count = $("amp-count-findings");
  out.replaceChildren();
  count.className = "amp-count";
  if (res.exit === 2 && !Array.isArray(res.data)) {
    const pos = yamlErrorPosition(res.stderr);
    editor.setFindings(pos ? [{ line: pos.line, column: pos.column, severity: "error",
                                message: res.stderr, source: "yaml" }] : []);
    out.append(errorBox("amlint can't read this config.", res.stderr, editor));
    count.textContent = "!";
    count.classList.add("amp-count--error");
    $("amp-summary").textContent = "not valid";
    return;
  }
  const findings = res.data || [];
  editor.setFindings(findings.map((f) => ({
    line: f.line, column: f.column, severity: LEVEL[f.level],
    message: `${f.message} [${f.code}]`, source: "amlint",
  })));
  const n = summarize(findings);
  const parts = [n.error && plural(n.error, "error"), n.warn && plural(n.warn, "warning"),
                 n.info && `${n.info} info`].filter(Boolean);
  $("amp-summary").textContent = parts.join(" · ") || "no issues";
  count.textContent = findings.length || "";
  if (n.error) count.classList.add("amp-count--error");
  else if (n.warn) count.classList.add("amp-count--warn");

  out.append(stderrNotes(res.stderr) || "");
  if (!findings.length) {
    out.append(h("p", { class: "amp-ok" }, "✓ No issues found."));
    return;
  }
  out.append(
    h("p", { class: "amp-muted" }, parts.join(" · "), ". Click a finding to jump to its line."),
    h("ul", { class: "amp-findings" }, findings.map((f) => findingItem(f, editor))),
  );
}

// ---------------------------------------------------------------- rules status

function renderRules(status) {
  const out = $("amp-rules-status");
  const count = $("amp-count-rules");
  out.replaceChildren();
  count.className = "amp-count";
  count.textContent = "";
  if (!status) {
    out.append(h("p", { class: "amp-muted" }, "No rules: the three --rules checks are off."));
    return;
  }
  if (status.error) {
    out.append(errorBox("These rules can't be loaded, so they aren't used.", status.error, rules));
    count.textContent = "!";
    count.classList.add("amp-count--error");
    return;
  }
  count.textContent = status.alerts || "";
  out.append(
    h("p", { class: status.alerts ? "amp-ok" : "amp-muted" }, status.alerts
      ? `✓ ${plural(status.alerts, "alerting rule")} loaded. route-unknown-alertname, ` +
        "inhibit-unknown-alertname and alert-hits-default-receiver run against them."
      : "No alerting rules found (recording rules are skipped), so the --rules checks are off."),
    stderrNotes(status.notes.join("\n")) || "",
  );
}

// ---------------------------------------------------------------- route tree

function routeFindings(findings, paths) {
  // each finding goes to the deepest route whose path is a prefix of its `where`
  const byPath = new Map();
  const other = [];
  for (const f of findings) {
    let best = null;
    for (const p of paths) {
      if ((f.where === p || f.where.startsWith(p + ".") || f.where.startsWith(p + "["))
          && (!best || p.length > best.length)) best = p;
    }
    if (best) {
      if (!byPath.has(best)) byPath.set(best, []);
      byPath.get(best).push(f);
    } else {
      other.push(f);
    }
  }
  return { byPath, other };
}

function settingsText(settings) {
  return Object.entries(settings).map(([k, v]) =>
    `${k}: ${Array.isArray(v) ? v.join(", ") || "[]" : v}`).join(" · ");
}

function treeNode(node, byPath) {
  const fs = byPath.get(node.path) || [];
  const worst = fs.find((f) => f.level === "error") || fs.find((f) => f.level === "warn") || fs[0];
  const label = node.matchers.length ? `{${node.matchers.join(", ")}}`
    : node.path === "route" ? "(root)" : "(catch-all)";
  const receiver = node.receiver !== null
    ? h("strong", { class: "amp-receiver" }, node.receiver)
    : h("span", { class: "amp-inherited", title: "inherited from the parent route" },
        node.inherited_receiver ? `${node.inherited_receiver} (inherited)` : "(inherited)");
  return h("li", {
    class: ["amp-node", worst && `amp-node--${worst.level}`,
            state.matched.has(node.path) && "amp-node--matched"].filter(Boolean).join(" "),
    dataset: { path: node.path },
  },
  h("div", { class: "amp-node__row", onclick: (e) => {
    if (!e.target.closest("button, a")) jump(editor, node.line, node.column);
  } },
    lineButton(editor, node.line, node.column, String(node.line)),
    h("span", { class: "amp-matchers" }, label),
    h("span", { class: "amp-arrow", "aria-hidden": "true" }, "→"),
    receiver,
    node.continue ? h("span", { class: "amp-tag" }, "continue") : null,
  ),
  Object.keys(node.settings).length
    ? h("div", { class: "amp-node__settings" }, settingsText(node.settings)) : null,
  fs.length ? h("ul", { class: "amp-node__findings" }, fs.map((f) =>
    h("li", { class: `amp-inline amp-inline--${f.level}` },
      h("span", { class: "amp-level" }, ICON[f.level]), " ", docsLink(f.code), " ",
      h("span", {}, f.message)))) : null,
  node.children.length ? h("ul", { class: "amp-tree" }, node.children.map((c) => treeNode(c, byPath)))
    : null);
}

function renderTree() {
  const out = $("amp-tree");
  out.replaceChildren();
  const tree = state.tree;
  if (!tree) {
    out.append(h("p", { class: "amp-muted" }, "No route tree: the config has no valid route: mapping."));
    return;
  }
  const paths = [];
  (function collect(n) { paths.push(n.path); n.children.forEach(collect); })(tree);
  const { byPath, other } = routeFindings(state.findings, paths);
  out.append(
    h("p", { class: "amp-muted" }, "Alerts enter at the root and go to the first matching child " +
      "(and on, with continue). Click a route to jump to it.",
      state.matched.size ? h("span", { class: "amp-legend" }, " Highlighted: the routes of the alert in Route an alert.") : null),
    h("ul", { class: "amp-tree amp-tree--root" }, treeNode(tree, byPath)),
  );
  if (other.length) {
    out.append(h("p", { class: "amp-muted" },
      `${plural(other.length, "more finding")} outside the route tree (receivers, inhibit rules, global) — see Findings.`));
  }
}

// ---------------------------------------------------------------- route an alert

const ROUTE_SETTINGS = ["receiver", "group_by", "group_wait", "group_interval",
                        "repeat_interval", "mute_time_intervals", "active_time_intervals"];

function shown(key, value) {
  if (key === "group_by") {
    if (value.length === 1 && value[0] === "...") return "... (every label)";
    return value.length ? value.join(", ") : "(none — one group for all alerts)";
  }
  if (Array.isArray(value)) return value.join(", ");
  return value || "(none)";
}

function pathCrumbs(path) {
  return h("div", { class: "amp-crumbs" }, path.flatMap((p, i) => [
    i ? h("span", { class: "amp-arrow", "aria-hidden": "true" }, "→") : null,
    h("button", { type: "button", class: "amp-loc", title: "Go to this route",
                  onclick: () => jump(editor, state.routeLines.get(p)) }, p),
  ]));
}

function renderRoute(res) {
  const out = $("amp-route-result");
  out.replaceChildren();
  if (!res) return;
  if (res.exit === 2 || !res.data) {
    out.append(errorBox("Can't route this alert.", res.stderr, editor));
    if (/^Cannot simulate/.test(res.stderr)) {
      out.append(h("p", { class: "amp-muted" },
        "Alertmanager wouldn't load this config either. Fix the errors under Findings first."));
    }
    return;
  }
  const d = res.data;
  let verdict;
  if (d.inhibited) {
    const by = d.inhibited_by;
    verdict = h("div", { class: "amp-verdict amp-verdict--warn" },
      h("strong", {}, "Inhibited"), " by ", by.rule, " ", lineButton(editor, by.line, by.column),
      h("div", { class: "amp-muted" }, "while firing: ",
        Object.entries(by.source).map(([k, v]) => `${k}=${v}`).join(", ")),
      h("div", {}, d.receivers.length
        ? `Routed to ${d.receivers.join(", ")}, but no notification is sent while it is inhibited.`
        : "No receiver, and inhibited: the alert would not be delivered."));
  } else if (d.receivers.length) {
    verdict = h("div", { class: "amp-verdict amp-verdict--ok" },
      "Delivered to ", d.receivers.map((r, i) => [i ? ", " : "", h("strong", {}, r)]),
      d.firing.length ? h("div", { class: "amp-muted" },
        `Not inhibited by the ${plural(d.firing.length, "firing alert")}.`) : null);
  } else {
    verdict = h("div", { class: "amp-verdict amp-verdict--error" },
      "No receiver: the alert would not be delivered.");
  }
  out.append(verdict);
  d.routes.forEach((m, n) => {
    const here = m.path[m.path.length - 1];
    const rows = ROUTE_SETTINGS.filter((k) => !(k.endsWith("_time_intervals") && !m[k].length))
      .map((k) => {
        const origin = m.set_by[k];
        const source = origin === undefined ? "Alertmanager default"
          : origin === here ? "set here" : `inherited from ${origin}`;
        return h("tr", {},
          h("th", { scope: "row" }, k),
          h("td", { class: k === "receiver" ? "amp-strong" : "" }, shown(k, m[k])),
          h("td", { class: "amp-muted" }, source));
      });
    out.append(h("div", { class: "amp-match" },
      d.routes.length > 1 ? h("div", { class: "amp-muted" }, `Match ${n + 1} of ${d.routes.length}`) : null,
      pathCrumbs(m.path),
      h("table", { class: "amp-settings" }, h("tbody", {}, rows))));
  });
}

// ---------------------------------------------------------------- diff

function renderDiff(res) {
  const out = $("amp-diff-result");
  out.replaceChildren();
  if (!res) return;
  if (res.exit === 2 || !res.data) {
    const side = /\(before\)/.test(res.stderr) ? before : editor;
    out.append(errorBox("Can't compare these configs.", res.stderr, side));
    return;
  }
  const d = res.data;
  const header = (text) => h("h3", { class: "amp-subhead" }, text);
  out.append(header("Findings"));
  if (!d.added.length && !d.fixed.length) {
    out.append(h("p", { class: "amp-ok" }, `✓ No new or fixed findings (${d.unchanged} unchanged).`));
  } else {
    out.append(
      h("p", { class: "amp-muted" },
        `${d.added.length} new · ${d.fixed.length} fixed · ${d.unchanged} unchanged`),
      h("ul", { class: "amp-findings" },
        d.added.map((f) => findingItem(f, editor, { badge: "new" })),
        d.fixed.map((f) => findingItem(f, before, { badge: "fixed" }))));
  }

  out.append(header("Routing"));
  const r = d.routing;
  if (r.skipped) {
    out.append(h("p", { class: "amp-error" }, "Routing not compared: ", r.skipped));
    return;
  }
  const checked = `${plural(r.checked, "label set")} checked` +
    (r.truncated ? " (limit reached, not every combination was tried)" : "");
  if (!r.changes.length) {
    out.append(h("p", { class: "amp-ok" }, `✓ No routing changes · ${checked}`));
    return;
  }
  out.append(h("p", { class: "amp-muted" }, `${plural(r.changes.length, "routing change")} · ${checked}`));
  for (const c of r.changes) {
    out.append(h("div", { class: "amp-change" },
      h("div", { class: "amp-chips" }, Object.entries(c.labels).length
        ? Object.entries(c.labels).map(([k, v]) => h("code", { class: "amp-chip" }, `${k}=${v}`))
        : h("code", { class: "amp-chip" }, "(no labels)")),
      h("table", { class: "amp-settings" }, h("tbody", {}, c.differences.map((x) =>
        h("tr", {},
          h("th", { scope: "row" }, x.setting === x.subject ? x.subject : `${x.subject} ${x.setting}`),
          h("td", {}, h("del", {}, x.old), h("span", { class: "amp-arrow" }, " → "), h("ins", {}, x.new)))))),
      c.similar ? h("div", { class: "amp-muted" },
        `and ${plural(c.similar, "more label set")} with the same change`) : null,
      h("button", { type: "button", class: "amp-button amp-button--small",
                    onclick: () => tryLabels(c.labels) }, "Route this alert")));
  }
}

function tryLabels(labels) {
  $("amp-labels").value = Object.entries(labels).map(([k, v]) =>
    /[\s,"]/.test(v) || v === "" ? `${k}="${v.replace(/["\\]/g, "\\$&")}"` : `${k}=${v}`).join(" ");
  $("amp-firing").value = "";
  selectTab("route");
}

// ---------------------------------------------------------------- running amlint

function scheduleCheck(ms) {
  debounce("check", async (stale) => {
    if (!state.ready) return;
    const config = editor.getValue();
    const [res, tree] = await Promise.all([
      call("check", config, rules.getValue()), call("tree", config),
    ]);
    if (stale()) return;
    state.findings = Array.isArray(res.data) ? res.data : [];
    state.tree = tree.tree;
    state.routeLines = new Map();
    (function collect(n) {
      if (!n) return;
      state.routeLines.set(n.path, n.line);
      n.children.forEach(collect);
    })(state.tree);
    renderCheck(res);
    renderRules(res.rules);
    renderTree();
  }, ms);
}

function scheduleRoute(ms) {
  if (state.tab !== "route" && !state.matched.size) return;
  debounce("route", async (stale) => {
    if (!state.ready) return;
    const labels = $("amp-labels").value;
    const firing = $("amp-firing").value;
    if (!labels.trim() && !firing.trim()) {
      $("amp-route-result").replaceChildren(h("p", { class: "amp-muted" },
        "Type some labels, as label=value pairs, to see where the alert goes."));
      state.matched = new Set();
      renderTree();
      return;
    }
    const res = await call("route", editor.getValue(), labels, firing);
    if (stale()) return;
    state.matched = new Set((res.data?.routes || []).flatMap((m) => m.path));
    renderRoute(res);
    renderTree();
  }, ms);
}

function scheduleDiff(ms) {
  if (state.tab !== "diff") return;
  debounce("diff", async (stale) => {
    if (!state.ready) return;
    const res = await call("diff", before.getValue(), editor.getValue());
    if (!stale()) renderDiff(res);
  }, ms);
}

function onConfigChange() {
  scheduleCheck();
  scheduleRoute();
  scheduleDiff(400);
}

// ---------------------------------------------------------------- examples and sharing

function load({ config = "", rules: ruleText = "", before: beforeText = "", labels = "",
                firing = "", tab } = {}) {
  state.loaded = config;
  editor.setValue(config);
  rules.setValue(ruleText);
  // without a before config, the Diff tab starts from no changes
  before.setValue(beforeText || config);
  $("amp-labels").value = labels;
  $("amp-firing").value = firing;
  state.matched = new Set();
  if (tab && TABS.includes(tab)) selectTab(tab);
  scheduleRoute(0);
}

function b64url(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(text) {
  const s = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function pipe(bytes, transform) {
  const stream = new Blob([bytes]).stream().pipeThrough(transform);
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

// #z=<deflate-raw, base64url JSON> or, without CompressionStream, #j=<base64url JSON>
async function encodeShare(data) {
  const bytes = new TextEncoder().encode(JSON.stringify(data));
  if (typeof CompressionStream === "function") {
    return "z=" + b64url(await pipe(bytes, new CompressionStream("deflate-raw")));
  }
  return "j=" + b64url(bytes);
}

async function decodeShare(hash) {
  const m = /^#([zj])=([A-Za-z0-9_-]+)$/.exec(hash);
  if (!m) return null;
  let bytes = unb64url(m[2]);
  if (m[1] === "z") bytes = await pipe(bytes, new DecompressionStream("deflate-raw"));
  const d = JSON.parse(new TextDecoder().decode(bytes));
  return { config: d.c || "", rules: d.r || "", before: d.b || "", labels: d.l || "",
           firing: d.f || "", tab: d.t };
}

function shareData() {
  const data = { c: editor.getValue() };
  if (rules.getValue().trim()) data.r = rules.getValue();
  if (before.getValue().trim() && before.getValue() !== data.c) data.b = before.getValue();
  if ($("amp-labels").value.trim()) data.l = $("amp-labels").value;
  if ($("amp-firing").value.trim()) data.f = $("amp-firing").value;
  if (state.tab !== "findings") data.t = state.tab;
  return data;
}

// Keys that usually hold a credential (the *_file variants hold a path instead), with a
// value on the same line, in block or flow style; and URLs with a password in them.
const SECRET_NAME = /(password|secret|token|api_?key|service_key|routing_key|credentials|url)$/i;
const KEY_VALUE = /([A-Za-z0-9_]+)["']?\s*:\s+([^\s,}][^,}]*)/g;
const URL_PASSWORD = /\b[a-z][a-z0-9+.-]*:\/\/[^\s/:@]+:[^\s/@]+@/i;

function findSecrets(text, name) {
  const hits = [];
  text.split("\n").forEach((raw, i) => {
    const line = raw.replace(/(^|\s)#.*$/, "");
    const keys = [];
    for (const [, key, value] of line.matchAll(KEY_VALUE)) {
      const v = value.trim().replace(/^["']|["']$/g, "");
      if (SECRET_NAME.test(key) && v && !["<secret>", "null", "~"].includes(v)) keys.push(key);
    }
    if (URL_PASSWORD.test(line)) keys.push("password in a URL");
    if (keys.length) hits.push(`${name} line ${i + 1} (${[...new Set(keys)].join(", ")})`);
  });
  return hits;
}

const dialog = $("amp-share-dialog");

$("amp-share").addEventListener("click", () => {
  const data = shareData();
  const extra = [data.r && "rules", data.b && "the before config of the Diff tab"].filter(Boolean);
  $("amp-share-extra").textContent = extra.length ? ` (and ${extra.join(" and ")})` : "";
  const hits = findSecrets(data.c, "config").concat(data.b ? findSecrets(data.b, "before") : []);
  const box = $("amp-secrets");
  box.replaceChildren();
  box.hidden = !hits.length;
  if (hits.length) {
    box.append(h("div", {},
      h("strong", {}, "These lines may hold secrets:"),
      h("ul", {}, hits.slice(0, 12).map((x) => h("li", {}, x))),
      hits.length > 12 ? `and ${hits.length - 12} more.` : null));
  }
  $("amp-share-url-label").hidden = true;
  $("amp-share-note").textContent = "";
  if (typeof dialog.showModal === "function") dialog.showModal();
});

$("amp-share-copy").addEventListener("click", async () => {
  const url = new URL(location.href);
  url.search = "";
  url.hash = await encodeShare(shareData());
  const input = $("amp-share-url");
  input.value = url.href;
  $("amp-share-url-label").hidden = false;
  input.select();
  try {
    await navigator.clipboard.writeText(url.href);
    $("amp-share-note").textContent = `Copied (${url.href.length.toLocaleString()} characters).`;
  } catch {
    $("amp-share-note").textContent = "Copy the link above (your browser blocked the clipboard).";
  }
});

async function loadFromHash() {
  try {
    const shared = await decodeShare(location.hash);
    if (!shared) return false;
    load(shared);
    return true;
  } catch (err) {
    console.error(err);
    setStatus("This share link is damaged, so an example was loaded instead.");
    return false;
  }
}

window.addEventListener("hashchange", loadFromHash);

// The config is kept nowhere, so ask before leaving the page with edits in it.
window.addEventListener("beforeunload", (event) => {
  if (editor && editor.getValue() !== state.loaded) event.preventDefault();
});

$("amp-example").addEventListener("change", (event) => {
  const ex = state.manifest.examples.find((e) => e.id === event.target.value);
  event.target.value = "";
  if (!ex) return;
  if (editor.getValue() !== state.loaded && editor.getValue().trim()
      && !window.confirm("Replace your config with this example?")) return;
  load(ex);
});

$("amp-before-copy").addEventListener("click", () => before.setValue(editor.getValue()));
for (const id of ["amp-labels", "amp-firing"]) $(id).addEventListener("input", () => scheduleRoute());

// ---------------------------------------------------------------- start

async function start() {
  let createEditor;
  try {
    ({ createEditor } = await import("./dist/codemirror.js"));
    const res = await fetch(new URL("dist/manifest.json", import.meta.url));
    if (!res.ok) throw new Error(`manifest.json: HTTP ${res.status}`);
    state.manifest = await res.json();
  } catch (err) {
    console.error(err);
    setStatus("The playground isn't built: run python playground/build.py, then reload.", "error");
    return;
  }
  editor = createEditor($("amp-editor"), { label: "alertmanager.yml", onChange: onConfigChange });
  before = createEditor($("amp-before"), { label: "before.yml", onChange: () => scheduleDiff() });
  rules = createEditor($("amp-rules"), { label: "rules.yml", onChange: () => scheduleCheck() });
  state.docs = state.manifest.docs || {};
  const select = $("amp-example");
  for (const ex of state.manifest.examples) {
    select.append(h("option", { value: ex.id, title: ex.description }, ex.title));
  }
  if (!(await loadFromHash())) load(state.manifest.examples[0]);

  setStatus("Loading Python…");
  try {
    await call("init", new URL(`dist/${state.manifest.wheel}`, import.meta.url).href);
    const info = await call("info");
    state.explains = info.explains;
    state.ready = true;
    const commit = state.manifest.commit ? ` (${state.manifest.commit})` : "";
    setStatus(`amlint ${info.version}${commit} · Python ${info.python} · running in your browser`, "ready");
    scheduleCheck(0);
    scheduleRoute(0);
    scheduleDiff(0);
  } catch (err) {
    failed(err.message);
  }
}

// For the smoke test and the browser console.
window.amlintPlayground = {
  setConfig: (text) => editor.setValue(text),
  getConfig: () => editor.getValue(),
  selectTab,
};

start();
