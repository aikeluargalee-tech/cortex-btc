/* CORTEX-BTC — market stance dashboard. No framework, no build step. */

const S = {
  state: null,
  loading: false,
  drawer: null,
  decisions: [],
  expand: { gates: false, a: false, b: false },
};

/* ---------------- helpers ---------------- */
const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function n(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  return num.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function time(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    timeZone: "Asia/Kuala_Lumpur", hour12: false,
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }) + " MYT";
}

const cls = (status) => "b-" + String(status || "").toLowerCase().replace(/[^a-z_]/g, "_");
const stateCls = (state) => "state-" + String(state || "").toLowerCase().replace(/[^a-z_]/g, "_");

const VERDICT_CLASS = {
  "QUALIFIED BUY ZONE": "v-buy",
  "QUALIFIED SELL ZONE": "v-sell",
  "NO SETUP": "v-nosetup",
  "CONFLICT": "v-conflict",
  "DATA HALT": "v-halt",
};

function toast(message, ok = false) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  el.classList.toggle("ok", ok);
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 6000);
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `request failed (${response.status})`);
  return payload;
}

/* ---------------- data flow ---------------- */
async function load(force = false) {
  if (S.loading) return;
  S.loading = true;
  $("#refresh").disabled = true;
  try {
    S.state = await api(`/api/state${force ? "?refresh=1" : ""}`);
    render();
  } catch (error) {
    $("#view").innerHTML = `<div class="card"><div class="banner"><strong>CORTEX cannot publish a stance.</strong><br>${esc(error.message)}</div></div>`;
  } finally {
    S.loading = false;
    $("#refresh").disabled = false;
  }
}

/* ---------------- chrome ---------------- */
function renderChrome() {
  const s = S.state;
  if (!s) return;
  $("#price").textContent = s.market.price ? "$" + n(s.market.price, 2) : "—";
  $("#market-time").textContent = "market " + time(s.market.as_of);

  const critical = s.integrations.degraded_critical.length;
  const proxies = s.integrations.degraded_features.filter((f) => f.status === "PROXY").length;
  const pill = $("#data-pill");
  if (critical) {
    pill.textContent = `DATA DEGRADED ${critical}`;
    pill.className = "pill bad";
  } else {
    pill.textContent = proxies ? `DATA VERIFIED · ${proxies} PROXY` : "DATA VERIFIED";
    pill.className = "pill ok";
  }
  $("#footer-status").textContent = `strategy status: ${s.config.validation_status} · config ${s.config.config_version}`;
}

/* ---------------- blocks ---------------- */
function stat(k, v, sub = "") {
  return `<div class="stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(sub)}</div></div>`;
}

function mini(k, v, sub) {
  return `<div class="mini"><span>${esc(k)}</span><b>${esc(v)}</b><i>${esc(sub)}</i></div>`;
}

function feat(key) {
  const f = (S.state.market.features || {})[key];
  return f ? f.value : null;
}

function bookImbalance() {
  const value = feat("book_imbalance");
  if (value === null || value === undefined) return "—";
  const num = Number(value) * 100;
  return (num >= 0 ? "+" : "") + n(num, 1) + "%";
}

function conditionRows(conditions, moduleKey) {
  const expanded = S.expand[moduleKey];
  const failing = conditions.filter((c) => c.status !== "PASS");
  const visible = expanded ? conditions : failing;
  const passed = conditions.length - failing.length;

  if (!conditions.length) return `<div class="empty">No conditions were evaluated.</div>`;

  const body = visible.length
    ? `<div class="cond-list">${visible.map((c) => `
        <div class="cond">
          <span class="id">${esc(c.id)}</span>
          <span class="label">${esc(c.label)}</span>
          <span class="badge ${cls(c.status)}">${esc(c.status)}</span>
          <span class="detail">${esc(c.detail)}</span>
        </div>`).join("")}</div>`
    : `<div class="cond-list"><div class="cond"><span class="id">✓</span><span class="label">All ${conditions.length} conditions passed</span></div></div>`;

  const toggle = conditions.length > failing.length
    ? `<button class="link" data-toggle="${moduleKey}">${expanded ? "hide passing" : `show ${passed} passing`}</button>`
    : "";
  return body + `<div class="cond-foot">${visible.length}/${conditions.length} shown ${toggle}</div>`;
}

function moduleCard(kind, assessment) {
  const name = kind === "a" ? "Module A — Accumulation" : "Module B — Distribution";
  const meta = [];
  if (assessment.reference_level) meta.push(`<span><b>ref</b> ${esc(n(assessment.reference_level, 2))}</span>`);
  if (assessment.invalidation_price) meta.push(`<span><b>invalidation</b> ${esc(n(assessment.invalidation_price, 2))}</span>`);
  if (assessment.sell_class) {
    const label = assessment.state === "SETUP" ? "candidate" : "class";
    meta.push(`<span><b>${label}</b> ${esc(assessment.sell_class)}</span>`);
  }
  return `<div class="module ${kind}">
    <div class="module-head">
      <span class="module-name">${name}</span>
      <span class="module-state ${stateCls(assessment.state)}">${esc(assessment.state)}</span>
    </div>
    <p class="module-summary">${esc(assessment.summary)}</p>
    <div class="module-meta">${meta.join("")}</div>
    ${conditionRows(assessment.conditions, kind)}
  </div>`;
}

function gateRows(gates) {
  const expanded = S.expand.gates;
  const visible = expanded ? gates : gates.filter((g) => g.status !== "PASS");
  const passed = gates.filter((g) => g.status === "PASS").length;
  const failed = gates.filter((g) => g.status === "FAIL").length;
  const unavailable = gates.length - passed - failed;

  const rows = (visible.length ? visible : gates.slice(-1)).map((g) => `
    <div class="gate">
      <span class="n">G${g.gate}</span>
      <span class="name">${esc(g.name)}</span>
      <span class="badge ${g.status === "PASS" ? "b-pass" : g.status === "FAIL" ? "b-fail" : "b-unavailable"}">${esc(g.status)}</span>
      <span class="detail" title="${esc(g.detail)}">${esc(g.detail)}</span>
    </div>`).join("");

  return `<div class="gate-summary">
      ${passed ? `<span class="tick">${passed} passed</span>` : ""}
      ${failed ? `<span class="cross">${failed} failed</span>` : ""}
      ${unavailable ? `<span class="dash">${unavailable} unavailable</span>` : ""}
      <button class="link" data-toggle="gates">${expanded ? "hide passing gates" : `show all ${gates.length}`}</button>
    </div>
    <div class="gates">${rows}</div>`;
}

function evidenceGaps(decision) {
  const next = (assessment) =>
    assessment.conditions.find((c) => c.status !== "PASS") || null;
  const rows = [
    { module: "A", assessment: decision.bullish, gap: next(decision.bullish) },
    { module: "B", assessment: decision.bearish, gap: next(decision.bearish) },
  ];
  return `<div class="cond-list">${rows.map(({ module, assessment, gap }) => {
    if (!gap) {
      return `<div class="cond"><span class="id">${module}</span>
        <span class="label">complete — every condition passed</span>
        <span class="badge b-pass">READY</span></div>`;
    }
    return `<div class="cond">
      <span class="id">${esc(gap.id)}</span>
      <span class="label">${esc(gap.label)}</span>
      <span class="badge ${cls(gap.status)}">${esc(gap.status)}</span>
      <span class="detail">${esc(gap.detail)}</span>
    </div>`;
  }).join("")}</div>
  <p class="hint" style="margin-top:10px">Each side is a sequence. The first unmet condition is the one that decides whether the stance can advance.</p>`;
}

/* ---------------- command centre ---------------- */
function commandCentre() {
  const s = S.state;
  const d = s.decision;
  const regimeGate = d.gates.find((g) => g.gate === 2) || {};
  const regime = String(regimeGate.detail || "—").split(" — ")[0];
  const verdictClass = VERDICT_CLASS[d.verdict] || "v-nosetup";

  return `
    <div class="card verdict-card ${verdictClass}">
      <div class="hero-grid">
        <div>
          <div class="verdict-label">Market stance · ${esc(time(d.created_at))}</div>
          <div class="verdict-word stance">${esc(d.verdict)}</div>
          <div class="state-line">
            Regime <b>${esc(regime)}</b>
            <span class="sep">·</span> evaluated from snapshot ${esc(String(d.market_snapshot_id).slice(0, 10))}…
            <span class="sep">·</span> config ${esc(d.config_version)}
          </div>
          ${trendLine(s)}
          <p class="explain">${esc(d.explanation)}</p>
        </div>
        <div class="hero-side">
          ${mini("Price", "$" + n(s.market.price, 2), "spot, " + esc(s.market.source))}
          ${mini("24h value area", n(feat("vah"), 0) + " / " + n(feat("val"), 0), "high / low")}
          ${mini("SMA " + esc(s.config.regime.sma_days) + "d", n(feat("sma"), 0), "regime reference")}
        </div>
      </div>
      <p class="plain">${esc(d.plain_english)}</p>
    </div>

    <div class="grid-6">
      ${stat("Support", n(feat("support"), 2), "nearest structural low")}
      ${stat("Resistance", n(feat("resistance"), 2), "nearest structural high")}
      ${stat(`VAH ${s.config.analysis.value_area_hours}h`, n(feat("vah"), 2), "context " + n(feat("vah_context"), 2))}
      ${stat(`VAL ${s.config.analysis.value_area_hours}h`, n(feat("val"), 2), "context " + n(feat("val_context"), 2))}
      ${stat("POC", n(feat("poc"), 2), "value area centre")}
      ${stat("Book imbalance", bookImbalance(), "within 0.5% of mid")}
    </div>

    <div class="grid-2-equal">
      ${moduleCard("a", d.bullish)}
      ${moduleCard("b", d.bearish)}
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-head"><h2>Stance gates</h2><span class="note">evaluated in order</span></div>
        ${gateRows(d.gates)}
      </div>
      <div class="card">
        <div class="card-head"><h2>Evidence still missing</h2><span class="note">what would change the stance</span></div>
        ${evidenceGaps(d)}
      </div>
    </div>

    <div class="disclaimer">
      CORTEX describes a market condition; it does not advise, size or execute. Past behaviour of a condition
      is not established here, and no expected return is implied.
    </div>`;
}

/* ---------------- drawers ---------------- */
const CONFIG_GROUPS = [
  ["Accumulation (Module A)", [
    ["buy.entry_zone_bps", "Entry zone (bps)"], ["buy.sweep_min_bps", "Min sweep depth (bps)"],
    ["buy.invalidation_buffer_bps", "Invalidation buffer (bps)"], ["buy.absorption_min", "Min demand absorption"],
    ["buy.delta_improvement_min", "Min delta improvement"], ["buy.max_entry_premium_bps", "Max entry premium (bps)"],
  ]],
  ["Distribution (Module B)", [
    ["sell.resistance_zone_bps", "Resistance zone (bps)"], ["sell.absorption_min", "Min supply absorption"],
    ["sell.cvd_deterioration_max", "Max CVD slope"], ["sell.breakdown_buffer_bps", "Breakdown buffer (bps)"],
  ]],
  ["Regime", [
    ["regime.sma_days", "SMA days"], ["regime.constructive_above_pct", "Constructive above (%)"],
    ["regime.defensive_below_pct", "Defensive below (%)"],
  ]],
  ["Measurement", [
    ["analysis.value_area_hours", "Value-area horizon (hours)"],
    ["analysis.context_profile_hours", "Context profile (hours)"],
  ]],
];

function dig(path) {
  return path.split(".").reduce((acc, part) => (acc == null ? acc : acc[part]), S.state.config);
}

function drawerConfig() {
  const s = S.state;
  return `
    <div class="banner">Every change is versioned: previous value, new value, operator and reason. Unset values display as NOT CONFIGURED.</div>
    <div class="card tight">
      <div class="card-head"><h2>Stance parameters</h2><span class="note">${esc(s.config.validation_status)}</span></div>
      ${CONFIG_GROUPS.map(([group, fields]) => `
        <h3 class="group-title">${esc(group)}</h3>
        <div class="form-grid" style="grid-template-columns:repeat(2,1fr)">
          ${fields.map(([path, label]) => {
            const value = dig(path);
            return `<div class="field"><label>${esc(label)}</label>
              <input data-cfg="${esc(path)}" value="${value === null || value === undefined ? "" : esc(value)}" placeholder="NOT CONFIGURED" /></div>`;
          }).join("")}
        </div>`).join("")}
      <div class="field" style="margin-top:14px"><label>Operator</label><input id="cfg_operator" value="operator" /></div>
      <div class="field" style="margin-top:10px"><label>Reason for change</label><input id="cfg_note" placeholder="why this change is being made" /></div>
      <button class="btn primary" id="save_config" style="margin-top:12px">Save as new version</button>
      <p class="hint" style="margin-top:10px">Missing: ${s.config_missing.length ? esc(s.config_missing.join(", ")) : "nothing required is missing"}.</p>
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Versions</h2><span class="note">append-only</span></div>
      ${versionsTable()}
    </div>`;
}

function versionsTable() {
  const history = (S.state.config_history || []).slice().reverse();
  if (!history.length) return `<div class="empty">No configuration changes recorded.</div>`;
  return `<table>
    <thead><tr><th>Version</th><th>Change</th><th class="num">From</th><th class="num">To</th><th>Operator</th></tr></thead>
    <tbody>${history.map((record) => {
      const entries = Object.entries(record.changes || {});
      return entries.map(([path, change]) => `<tr>
        <td style="font-family:var(--mono)">${esc(record.version)}</td>
        <td>${esc(path)}</td>
        <td class="num">${esc(change.from)}</td>
        <td class="num">${esc(change.to)}</td>
        <td>${esc(record.operator)}</td>
      </tr>`).join("");
    }).join("")}</tbody>
  </table>`;
}

function drawerAudit() {
  const s = S.state;
  const rows = S.decisions || [];
  return `
    <div class="card tight">
      <div class="card-head"><h2>Published stances</h2>
        <a href="/api/export/decisions.json" class="link" download>export JSON</a>
      </div>
      ${rows.length ? `<table>
        <thead><tr><th>When (MYT)</th><th>Stance</th><th>Module A</th><th>Module B</th><th>Class</th></tr></thead>
        <tbody>${rows.slice(0, 40).map((d) => `<tr>
          <td style="font-family:var(--mono);font-size:11px">${esc(time(d.created_at))}</td>
          <td><b>${esc(d.verdict)}</b></td>
          <td>${esc(d.bullish.state)}</td>
          <td>${esc(d.bearish.state)}</td>
          <td>${esc(d.bearish.sell_class || "—")}</td>
        </tr>`).join("")}</tbody>
      </table>` : `<div class="empty">No stances recorded yet.</div>`}
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Data provenance</h2><span class="note">observed vs derived</span></div>
      <table>
        <thead><tr><th>Feature</th><th>Status</th><th class="num">Value</th><th>Source</th></tr></thead>
        <tbody>${Object.entries(s.market.features).map(([key, f]) => `<tr>
          <td style="font-family:var(--mono);font-size:11px">${esc(key)}</td>
          <td><span class="badge ${f.status === "VERIFIED" ? "b-pass" : f.status === "PROXY" ? "b-not_configured" : "b-unavailable"}">${esc(f.status)}</span></td>
          <td class="num">${f.value === null ? "—" : esc(Number(f.value).toLocaleString("en-US", { maximumFractionDigits: 4 }))}</td>
          <td style="font-size:11px">${esc(f.source)}</td>
        </tr>`).join("")}</tbody>
      </table>
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Configuration history</h2></div>
      ${versionsTable()}
    </div>`;
}

function trendLine(s) {
  const t = s.trend;
  if (!t || t.position === "UNAVAILABLE") {
    return `<div class="trend-line">1D / SMA200 context unavailable</div>`;
  }
  const distance = Number(t.distance_pct);
  const tone = t.position === "BELOW" ? "neg" : "pos";
  return `<div class="trend-line">
      1D close <b>${n(t.daily_close, 2)}</b>
      <span class="sep">·</span> SMA200 <b>${n(t.sma200, 2)}</b>
      <span class="sep">·</span> <b class="${tone}">${distance > 0 ? "+" : ""}${n(distance, 2)}% ${esc(t.position)}</b>
      <span class="ctx">context only · never a gate</span>
    </div>`;
}

/* ---------------- research drawer ---------------- */
function drawerResearch() {
  const r = S.state.research;
  if (!r || r.ok === false) {
    return `<div class="banner">${esc((r && r.reason) || "No study has been run yet.")}</div>
      <div class="card tight">
        <div class="card-head"><h2>Veto study</h2><span class="note">2 years, daily + 4H</span></div>
        <p class="hint">Tests whether the external 200D / 4H hard-downtrend veto carries information: how often it fires,
        and what price did afterwards. It does not test your stance.</p>
        <button class="btn primary" id="run_research" style="margin-top:10px">Run study</button>
      </div>`;
  }
  const horizons = [1, 5, 10, 20];
  const states = ["VETO ACTIVE", "VETO INACTIVE"];
  const cell = (state, horizon, field) => {
    const row = (r.forward_returns || []).find((f) => f.state === state && f.horizon_days === horizon);
    if (!row || row[field] === null || row[field] === undefined) return "—";
    return n(row[field], field === "positive_pct" ? 1 : 2);
  };
  const days = (state) => (r.veto && r.veto.active_days !== undefined)
    ? (state === "VETO ACTIVE" ? r.veto.active_days : r.veto.inactive_days) : "—";

  return `
    <div class="card tight">
      <div class="card-head"><h2>Window</h2>
        <span class="note">${esc(r.window.from)} → ${esc(r.window.to)} · ${r.window.days} days</span></div>
      <div class="grid-4" style="grid-template-columns:repeat(2,1fr)">
        ${stat("Veto active", n(r.veto.active_share_pct, 1) + "%", r.veto.active_days + " of " + r.window.evaluated_days + " days")}
        ${stat("Current", r.veto.current.veto ? "ACTIVE" : "INACTIVE", r.veto.current.structure + " · " + r.veto.current.day)}
      </div>
    </div>

    <div class="card tight">
      <div class="card-head"><h2>Forward return after each state</h2><span class="note">percent, close to close</span></div>
      <table>
        <thead><tr><th>State</th><th class="num">Days</th>
          ${horizons.map((h) => `<th class="num">${h}d</th>`).join("")}
          <th class="num">% positive 20d</th><th class="num">Worst 20d</th></tr></thead>
        <tbody>${states.map((state) => `<tr>
          <td>${esc(state)}</td>
          <td class="num">${days(state)}</td>
          ${horizons.map((h) => `<td class="num">${cell(state, h, "mean_pct")}</td>`).join("")}
          <td class="num">${cell(state, 20, "positive_pct")}%</td>
          <td class="num">${cell(state, 20, "worst_pct")}</td>
        </tr>`).join("")}</tbody>
      </table>
      <p class="hint" style="margin-top:8px">If the two rows look alike, the veto is not separating good days from bad ones.</p>
    </div>

    <div class="card tight">
      <div class="card-head"><h2>Hold versus veto-filtered hold</h2><span class="note">no fees or slippage</span></div>
      <table>
        <thead><tr><th>Portfolio</th><th class="num">Total</th><th class="num">CAGR</th><th class="num">Max drawdown</th></tr></thead>
        <tbody>
          <tr><td>Always in BTC</td><td class="num">${n(r.strategy.always_in.total_return_pct, 1)}%</td>
            <td class="num">${n(r.strategy.always_in.cagr_pct, 1)}%</td>
            <td class="num">${n(r.strategy.always_in.max_drawdown_pct, 1)}%</td></tr>
          <tr><td>In BTC only when veto inactive</td><td class="num">${n(r.strategy.veto_filtered.total_return_pct, 1)}%</td>
            <td class="num">${n(r.strategy.veto_filtered.cagr_pct, 1)}%</td>
            <td class="num">${n(r.strategy.veto_filtered.max_drawdown_pct, 1)}%</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card tight">
      <div class="card-head"><h2>Assumptions</h2><span class="note">read before believing any number</span></div>
      <ul class="plain-list">${(r.assumptions || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
      <button class="btn" id="run_research" style="margin-top:10px">Re-run study</button>
    </div>`;
}

/* ---------------- telemetry drawer ---------------- */
function drawerTelemetry() {
  const t = S.state.telemetry;
  if (!t) {
    return `<div class="banner">No external snapshot found. Place the Data Hub report at <code>data/telemetry/latest.md</code>
      and refresh. It is ingested as context only — it can never change a stance.</div>`;
  }
  const gate = t.gate_zero || {};
  return `
    <div class="card tight">
      <div class="card-head"><h2>External snapshot</h2><span class="note">${esc(t.as_of || "no timestamp")}</span></div>
      <p class="hint">${esc(t.coverage || "")}</p>
      ${t.freshness_warning ? `<div class="banner" style="margin-top:10px">${esc(t.freshness_warning)}</div>` : ""}
      <p class="hint" style="margin-top:10px">Context only. Nothing on this panel is allowed to change the stance — it exists to show
      what a wider data set says, next to what CORTEX observed itself.</p>
    </div>

    <div class="card tight">
      <div class="card-head"><h2>Their hard-downtrend veto</h2><span class="note">as reported, not computed here</span></div>
      <table>
        <tbody>
          <tr><td>Veto status</td><td class="num">${esc(gate.veto_status || "—")}</td></tr>
          <tr><td>Condition A — 1D below SMA200</td><td class="num">${esc(gate.condition_a || "—")}</td></tr>
          <tr><td>Condition B — 4H downside structure</td><td class="num">${esc(gate.condition_b || "—")}</td></tr>
          <tr><td>Their portfolio status</td><td class="num">${esc(gate.portfolio_status || "—")}</td></tr>
          <tr><td>Their entry permission</td><td class="num">${esc(gate.new_entry || "—")}</td></tr>
        </tbody>
      </table>
    </div>

    ${(t.sections || []).map((section) => `
      <div class="card tight">
        <div class="card-head"><h2>${esc(section.section)}</h2><span class="note">${section.rows.length} rows</span></div>
        <table>
          <tbody>${section.rows.map((row) => `<tr>
            <td style="width:42%">${esc(row.label)}</td>
            <td class="num" style="text-align:left">${esc(row.value)}</td>
            <td style="width:90px">${row.status ? `<span class="badge ${row.status === "OK" ? "b-pass" : row.status === "UNAVAILABLE" ? "b-unavailable" : "b-not_configured"}">${esc(row.status)}</span>` : ""}</td>
          </tr>`).join("")}</tbody>
        </table>
      </div>`).join("")}`;
}

const PANELS = {
  research: ["Research · veto study", drawerResearch],
  telemetry: ["External telemetry", drawerTelemetry],
  config: ["Configuration", drawerConfig],
  audit: ["Audit & provenance", drawerAudit],
};

/* ---------------- render ---------------- */
function render() {
  if (!S.state) return;
  renderChrome();
  $("#view").innerHTML = commandCentre();
  renderDrawer();
  wireView();
}

function renderDrawer() {
  const drawer = $("#drawer");
  const scrim = $("#scrim");
  if (!S.drawer || !S.state) {
    drawer.hidden = true;
    scrim.hidden = true;
    drawer.setAttribute("aria-hidden", "true");
    return;
  }
  const [title, renderPanel] = PANELS[S.drawer];
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = renderPanel();
  drawer.hidden = false;
  scrim.hidden = false;
  drawer.setAttribute("aria-hidden", "false");
  wireDrawer();
}

async function openDrawer(name) {
  S.drawer = name;
  history.replaceState(null, "", "#" + name);
  render();
  if (name === "audit" && !S.decisions.length) await loadDecisions();
}

function closeDrawer() {
  S.drawer = null;
  history.replaceState(null, "", location.pathname);
  render();
}

async function loadDecisions() {
  try {
    const payload = await api("/api/decisions?limit=100");
    S.decisions = payload.decisions;
    if (S.drawer === "audit") renderDrawer();
  } catch (error) {
    toast(error.message);
  }
}

/* ---------------- interactions ---------------- */
function wireView() {
  document.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.toggle;
      S.expand[key] = !S.expand[key];
      render();
    });
  });
}

function wireDrawer() {
  const run = $("#run_research");
  if (run) {
    run.addEventListener("click", async () => {
      run.disabled = true;
      run.textContent = "Running…";
      try {
        const report = await api("/api/research/refresh", { method: "POST", body: "{}" });
        S.state.research = report;
        toast("Veto study complete.", true);
        renderDrawer();
      } catch (error) {
        toast(error.message);
        run.disabled = false;
        run.textContent = "Run study";
      }
    });
  }

  const save = $("#save_config");
  if (save) {
    save.addEventListener("click", async () => {
      const changes = {};
      document.querySelectorAll("[data-cfg]").forEach((input) => {
        const path = input.dataset.cfg;
        const before = dig(path);
        const beforeText = before === null || before === undefined ? "" : String(before);
        if (input.value.trim() !== beforeText.trim()) changes[path] = input.value.trim();
      });
      if (!Object.keys(changes).length) {
        toast("No values changed.");
        return;
      }
      save.disabled = true;
      try {
        await api("/api/config", {
          method: "POST",
          body: JSON.stringify({ changes, operator: $("#cfg_operator").value, note: $("#cfg_note").value }),
        });
        toast("New configuration version saved.", true);
        await load(true);
      } catch (error) {
        toast(error.message);
        save.disabled = false;
      }
    });
  }
}

/* ---------------- boot ---------------- */
document.querySelectorAll("[data-drawer]").forEach((button) => {
  button.addEventListener("click", () => openDrawer(button.dataset.drawer));
});

$("#drawer-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && S.drawer) closeDrawer();
});

const initial = (location.hash || "").replace("#", "");
if (PANELS[initial]) S.drawer = initial;

$("#refresh").addEventListener("click", () => load(true));
setInterval(() => { if (!document.hidden) load(false); }, 45000);
load(true).then(() => {
  if (S.drawer === "audit") loadDecisions();
});
