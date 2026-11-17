/* CORTEX-BTC — single-screen decision dashboard. No framework, no build step. */

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

function usd(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  return "$" + n(value, digits);
}

function pct(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return n(value, digits) + "%";
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
const verdictCls = (verdict) => "v-" + String(verdict || "").toLowerCase();

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
    $("#view").innerHTML = `<div class="card"><div class="banner"><strong>CORTEX cannot build a decision.</strong><br>${esc(error.message)}</div></div>`;
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
  if (!s.integrations.integrity_ok) {
    pill.textContent = "LEDGER UNRECONCILED";
    pill.className = "pill bad";
  } else if (critical) {
    pill.textContent = `DATA DEGRADED ${critical}`;
    pill.className = "pill bad";
  } else {
    pill.textContent = proxies ? `DATA VERIFIED · ${proxies} PROXY` : "DATA VERIFIED";
    pill.className = "pill ok";
  }
  $("#mode-pill").textContent = s.seeded ? "PAPER · MANUAL" : "NO PORTFOLIO";
  $("#footer-status").textContent = `strategy status: ${s.config.validation_status} · config ${s.config.config_version}`;
}

/* ---------------- shared blocks ---------------- */
function stat(k, v, sub = "", alert = false) {
  return `<div class="stat${alert ? " alert" : ""}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(sub)}</div></div>`;
}

function mini(k, v, sub) {
  return `<div class="mini"><span>${esc(k)}</span><b>${esc(v)}</b><i>${esc(sub)}</i></div>`;
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
          <span class="badge ${cls(c.status)}">${esc(c.status === "NOT CONFIGURED" ? "NOT CONFIGURED" : c.status)}</span>
          <span class="detail">${esc(c.detail)}</span>
        </div>`).join("")}</div>`
    : `<div class="cond-list"><div class="cond"><span class="id">✓</span><span class="label">All ${conditions.length} conditions passed</span></div></div>`;

  const toggle = conditions.length > failing.length
    ? `<button class="link" data-toggle="${moduleKey}">${expanded ? "hide passing" : `show ${passed} passing`}</button>`
    : "";
  return body + `<div class="cond-foot">${visible.length}/${conditions.length} shown ${toggle}</div>`;
}

function moduleCard(kind, assessment) {
  const name = kind === "a" ? "Module A — Bullish accumulation" : "Module B — Bearish distribution";
  const meta = [];
  if (assessment.reference_level) meta.push(`<span><b>ref</b> ${esc(n(assessment.reference_level, 2))}</span>`);
  if (assessment.invalidation_price) meta.push(`<span><b>invalidation</b> ${esc(n(assessment.invalidation_price, 2))}</span>`);
  if (assessment.mechanism) {
    const label = assessment.state === "SETUP" ? "candidate exit" : "mechanism";
    meta.push(`<span><b>${label}</b> ${esc(assessment.mechanism)}</span>`);
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
      <button class="link" data-toggle="gates">${expanded ? "hide passing gates" : "show all 10"}</button>
    </div>
    <div class="gates">${rows}</div>`;
}

function allocationTable(allocation) {
  const rows = allocation.constraints || [];
  if (!rows.length) return `<div class="empty">${esc(allocation.notes.join(" · ") || "No constraints evaluated.")}</div>`;
  return `<table>
    <thead><tr><th>Constraint</th><th class="num">Permitted BTC</th><th>Detail</th></tr></thead>
    <tbody>${rows.map((c) => `
      <tr class="${c.binding ? "binding" : ""}">
        <td>${esc(c.name)}${c.binding ? '<span class="tag">binding</span>' : ""}</td>
        <td class="num">${c.configured ? n(c.quantity, 6) : "NOT CONFIGURED"}</td>
        <td>${esc(c.detail)}</td>
      </tr>`).join("")}</tbody>
  </table>`;
}

function orderTable(orders) {
  if (!orders.length) return `<div class="empty">No orders recorded.</div>`;
  return `<table>
    <thead><tr><th>Order</th><th>Side</th><th>Status</th><th class="num">Limit</th><th class="num">Qty</th><th class="num">Filled</th><th>Actions</th></tr></thead>
    <tbody>${orders.map((o) => {
      const actions = [];
      if (o.status === "APPROVED") actions.push(`<button class="btn small" data-order="${esc(o.order_id)}" data-action="submit">Submitted</button>`);
      if (o.status === "SUBMITTED") actions.push(`<button class="btn small" data-order="${esc(o.order_id)}" data-action="work">Working</button>`);
      if (["WORKING", "PARTIALLY FILLED"].includes(o.status)) {
        actions.push(`<button class="btn small" data-order="${esc(o.order_id)}" data-action="fill">Record fill</button>`);
        actions.push(`<button class="btn small danger" data-order="${esc(o.order_id)}" data-action="cancel">Cancel confirmed</button>`);
      }
      return `<tr>
        <td style="font-family:var(--mono);font-size:11px">${esc(o.order_id.slice(0, 14))}…</td>
        <td>${esc(o.side)}</td>
        <td>${esc(o.status)}</td>
        <td class="num">${n(o.limit_price, 2)}</td>
        <td class="num">${n(o.quantity, 6)}</td>
        <td class="num">${n(o.filled_quantity, 6)}</td>
        <td>${actions.join(" ") || "—"}</td>
      </tr>`;
    }).join("")}</tbody>
  </table>`;
}

function orderCard(decision, orders) {
  const proposal = decision.proposed_order;
  if (!proposal) {
    const recent = (orders || []).slice(0, 3);
    if (recent.length) return orderTable(recent);
    const gaps = [...(decision.bullish.conditions || []), ...(decision.bearish.conditions || [])]
      .filter((c) => c.status !== "PASS");
    return `<p class="empty" style="text-align:left;padding:4px 0 10px">No proposed order. CORTEX is not asking you to trade.</p>
      ${gaps.length ? `<div class="hint" style="margin-bottom:6px">Evidence still missing:</div>
      <div class="cond-list">${gaps.slice(0, 6).map((c) => `
        <div class="cond">
          <span class="id">${esc(c.id)}</span>
          <span class="label">${esc(c.label)}</span>
          <span class="badge ${cls(c.status)}">${esc(c.status)}</span>
          <span class="detail">${esc(c.detail)}</span>
        </div>`).join("")}</div>` : ""}`;
  }
  const existing = (orders || []).find((o) => o.order_id === proposal.order_id);
  return `<div class="grid-4" style="margin-bottom:10px">
      ${stat(proposal.side, n(proposal.quantity, 6) + " BTC", "limit " + n(proposal.limit_price, 2))}
      ${stat("Notional", usd(proposal.notional), "fee " + usd(proposal.fee_usdt))}
      ${stat("Expiry", proposal.expiry_minutes + " min", proposal.invalidation)}
      ${stat("Loop", proposal.loop.replace("LOOP ", ""), existing ? existing.status : "not yet approved")}
    </div>
    <p class="hint" style="margin:0 0 10px">Approving records a reservation in the ledger. CORTEX never transmits the order; you place it and report the outcome.</p>
    <button class="btn primary" data-approve ${existing ? "disabled" : ""}>${existing ? "Already approved" : "Approve proposal (reserve inventory)"}</button>`;
}

/* ---------------- command centre ---------------- */
function onboarding() {
  const s = S.state;
  const d = s.decision;
  return `
    <div class="banner"><strong>No portfolio has been recorded.</strong> CORTEX will not assume balances, so it cannot size any transaction. Enter your paper balances below. Unset values stay NOT CONFIGURED rather than zero.</div>
    <div class="card onboard">
      <div>
        <div class="card-head"><h2>Set paper balances</h2><span class="note">recorded as an immutable ledger event</span></div>
        <div class="form-grid" style="grid-template-columns:repeat(2,1fr)">
          <div class="field"><label>Core BTC (protected)</label><input id="pf_btc_core" value="0.15" /></div>
          <div class="field"><label>Tactical BTC (tradable)</label><input id="pf_btc_tactical" value="0.05" /></div>
          <div class="field"><label>Core USDT</label><input id="pf_usdt_core" value="0" /></div>
          <div class="field"><label>Tactical USDT</label><input id="pf_usdt_tactical" value="5000" /></div>
          <div class="field"><label>Operator</label><input id="pf_operator" value="operator" /></div>
        </div>
        <p class="hint" style="margin-top:10px">Only tactical inventory may be traded. Core BTC is excluded from every size calculation. Values above are illustrative — replace them with your own.</p>
        <button class="btn primary" id="seed" style="margin-top:10px">Record balances</button>
      </div>
      <div>
        <div class="card-head"><h2>Market state</h2><span class="note">${esc(s.config.config_version)}</span></div>
        <div class="grid-4" style="grid-template-columns:repeat(2,1fr)">
          ${stat("BTC / USDT", n(s.market.price, 2), time(s.market.as_of))}
          ${stat("Data", s.integrations.degraded_critical.length ? "DEGRADED" : "VERIFIED", s.integrations.degraded_critical.length + " critical feeds missing")}
          ${stat("Regime", "—", "requires ledger + rules")}
          ${stat("Verdict", d.verdict, d.portfolio_state)}
        </div>
        <p class="hint" style="margin-top:10px">Gate 2 fails until balances exist. That is deliberate: an unsized BUY would be an invented transaction.</p>
      </div>
    </div>
    <div class="grid-4">
      <div class="card tight"><div class="card-head"><h2>A · Accumulate</h2></div>
        <p class="hint">Sweep, exhaust, absorb, reclaim. Never authorises a purchase on its own.</p></div>
      <div class="card tight"><div class="card-head"><h2>B · Distribute</h2></div>
        <p class="hint">Supply absorption, failed reclaim, breakdown. Classified as profit-taking, risk reduction or defensive exit.</p></div>
      <div class="card tight"><div class="card-head"><h2>C · Size</h2></div>
        <p class="hint">Available inventory, floors, sleeve, exposure, risk. Names the binding constraint.</p></div>
      <div class="card tight"><div class="card-head"><h2>D · Decide</h2></div>
        <p class="hint">Ten gates, one verdict: BUY, HOLD, SELL or ABSTAIN. You execute.</p></div>
    </div>`;
}

function commandCentre() {
  const s = S.state;
  const d = s.decision;
  const p = s.portfolio;
  const l = s.ledger;
  const regime = (d.gates.find((g) => g.gate === 3) || {}).detail || "—";

  return `
    <div class="card verdict-card ${verdictCls(d.verdict)}">
      <div class="hero-grid">
        <div>
          <div class="verdict-label">Final decision authority</div>
          <div class="verdict-word">${esc(d.verdict)}</div>
          <div class="state-line">
            Posture <b>${esc(d.portfolio_state)}</b>
            <span class="sep">·</span> Regime <b>${esc(String(regime).split(" — ")[0])}</b>
            <span class="sep">·</span> ${esc(time(d.created_at))}
          </div>
          <p class="explain">${esc(d.explanation)}</p>
        </div>
        <div class="hero-side">
          ${mini("BTC allocation", pct(p.btc_pct), usd(p.btc_value_usdt))}
          ${mini("USDT allocation", pct(p.usdt_pct), usd(l.usdt_total))}
          ${mini("Portfolio value", usd(p.total_value_usdt), "spot mark")}
        </div>
      </div>
      <p class="plain">${esc(d.plain_english)}</p>
    </div>

    <div class="grid-6">
      ${stat("Available BTC", n(p.available_btc, 6), "floor " + n(s.config.portfolio.min_btc_inventory, 6))}
      ${stat("Available USDT", usd(p.available_usdt), "floor " + usd(s.config.portfolio.min_usdt_inventory))}
      ${stat("Reserved BTC", n(l.reserved_btc, 6), "open orders")}
      ${stat("Reserved USDT", usd(l.reserved_usdt), "open orders")}
      ${stat("Core BTC", n(l.btc_core, 6), "protected")}
      ${stat("Tactical BTC", n(l.btc_tactical, 6), "tradable sleeve")}
    </div>

    <div class="grid-2-equal">
      ${moduleCard("a", d.bullish)}
      ${moduleCard("b", d.bearish)}
    </div>

    <div class="grid-6">
      ${stat("Support", n(feat("support"), 2), "structural low")}
      ${stat("Resistance", n(feat("resistance"), 2), "structural high")}
      ${stat(`VAH ${s.config.analysis.value_area_hours}h`, n(feat("vah"), 2), "context " + n(feat("vah_context"), 2))}
      ${stat(`VAL ${s.config.analysis.value_area_hours}h`, n(feat("val"), 2), "context " + n(feat("val_context"), 2))}
      ${stat("POC", n(feat("poc"), 2), "value area centre")}
      ${stat("Book imbalance", bookImbalance(), "within 0.5% of mid")}
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-head"><h2>Decision gates</h2><span class="note">no composite score</span></div>
        ${gateRows(d.gates)}
      </div>
      <div class="card">
        <div class="card-head"><h2>Proposed transaction</h2><span class="note">human execution only</span></div>
        ${orderCard(d, s.orders)}
      </div>
    </div>`;
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

/* ---------------- drawer panels ---------------- */
function drawerPortfolio() {
  const s = S.state;
  const p = s.portfolio;
  const l = s.ledger;
  const btcPct = Number(p.btc_pct || 0);
  const usdtPct = Number(p.usdt_pct || 0);
  const bands = ["defensive", "cautious", "neutral", "constructive", "strongly_constructive"];
  return `
    <div class="card tight">
      <div class="card-head"><h2>Actual allocation</h2><span class="note">${usd(p.total_value_usdt)}</span></div>
      <div class="bar">
        <div class="btc" style="width:${Math.max(0, Math.min(100, btcPct))}%"></div>
        <div class="usdt" style="width:${Math.max(0, Math.min(100, usdtPct))}%"></div>
      </div>
      <div class="legend">
        <span><i style="background:var(--amber)"></i>BTC ${pct(p.btc_pct)}</span>
        <span><i style="background:var(--cyan)"></i>USDT ${pct(p.usdt_pct)}</span>
      </div>
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Inventory ledger</h2><span class="note">event sourced</span></div>
      <table>
        <thead><tr><th>Account</th><th class="num">Quantity</th><th class="num">Value</th></tr></thead>
        <tbody>
          <tr><td>Core BTC (protected)</td><td class="num">${n(l.btc_core, 6)}</td><td class="num">${usd(Number(l.btc_core) * Number(s.market.price || 0))}</td></tr>
          <tr><td>Tactical BTC</td><td class="num">${n(l.btc_tactical, 6)}</td><td class="num">${usd(Number(l.btc_tactical) * Number(s.market.price || 0))}</td></tr>
          <tr><td>Reserved BTC</td><td class="num">${n(l.reserved_btc, 6)}</td><td class="num">—</td></tr>
          <tr><td>Core USDT</td><td class="num">${usd(l.usdt_core)}</td><td class="num">${usd(l.usdt_core)}</td></tr>
          <tr><td>Tactical USDT</td><td class="num">${usd(l.usdt_tactical)}</td><td class="num">${usd(l.usdt_tactical)}</td></tr>
          <tr><td>Reserved USDT</td><td class="num">${usd(l.reserved_usdt)}</td><td class="num">—</td></tr>
          <tr><td>Realised result</td><td class="num">${usd(l.realised_usdt)}</td><td class="num">fees ${usd(l.fees_paid_usdt)}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Exposure bands</h2><span class="note">ceilings, not targets</span></div>
      <table>
        <thead><tr><th>Condition</th><th class="num">BTC</th><th class="num">USDT</th></tr></thead>
        <tbody>${bands.map((band) => {
          const v = Number(s.config.bands[band]);
          return `<tr><td style="text-transform:capitalize">${band.replace("_", " ")}</td><td class="num">${pct(v, 0)}</td><td class="num">${pct(100 - v, 0)}</td></tr>`;
        }).join("")}</tbody>
      </table>
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Sizing constraints</h2><span class="note">${esc(s.decision.allocation.status)} · binding ${esc(s.decision.allocation.binding_constraint || "none")}</span></div>
      ${allocationTable(s.decision.allocation)}
    </div>`;
}

function drawerOrders() {
  const s = S.state;
  return `
    <div class="card tight">
      <div class="card-head"><h2>Proposed order</h2><span class="note">approval ≠ execution</span></div>
      ${orderCard(s.decision, s.orders)}
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Lifecycle</h2><span class="note">${s.orders.length} recorded</span></div>
      ${orderTable(s.orders)}
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Conversion cycles</h2><span class="note">DIBSE</span></div>
      <div class="grid-4" style="grid-template-columns:repeat(2,1fr)">
        ${stat("Loop A", "USDT → BTC → USDT", "increase realised USDT")}
        ${stat("Loop B", "BTC → USDT → BTC", "increase BTC quantity")}
        ${stat("ΔBTC realised", "0", "no completed cycles")}
        ${stat("Open cycles", "0", "open only after a fill")}
      </div>
      <p class="hint" style="margin-top:10px">A proposal or a resting limit order never counts as a completed conversion.</p>
    </div>`;
}

const CONFIG_GROUPS = [
  ["Portfolio", [
    ["portfolio.btc_core", "Core BTC"], ["portfolio.btc_tactical", "Tactical BTC"],
    ["portfolio.min_btc_inventory", "Min BTC inventory"], ["portfolio.min_usdt_inventory", "Min USDT inventory"],
    ["portfolio.max_trade_notional_usdt", "Max trade (USDT)"], ["portfolio.max_risk_per_trade_pct", "Risk per trade (%)"],
  ]],
  ["Allocation", [
    ["sleeve.active_pct", "Active sleeve (%)"], ["bands.defensive", "Defensive BTC %"],
    ["bands.cautious", "Cautious BTC %"], ["bands.neutral", "Neutral BTC %"],
    ["bands.constructive", "Constructive BTC %"], ["bands.strongly_constructive", "Strongly constructive %"],
  ]],
  ["Execution and costs", [
    ["fees.maker_bps", "Maker fee (bps)"], ["fees.taker_bps", "Taker fee (bps)"],
    ["fees.slippage_bps", "Slippage (bps)"], ["execution.max_total_cost_bps", "Max all-in cost (bps)"],
    ["execution.order_expiry_minutes", "Order expiry (min)"],
  ]],
  ["Accumulation rules", [
    ["buy.entry_zone_bps", "Entry zone (bps)"], ["buy.sweep_min_bps", "Min sweep depth (bps)"],
    ["buy.invalidation_buffer_bps", "Invalidation buffer (bps)"], ["buy.absorption_min", "Min demand absorption"],
    ["buy.delta_improvement_min", "Min delta improvement"], ["buy.max_entry_premium_bps", "Max entry premium (bps)"],
  ]],
  ["Distribution rules", [
    ["sell.resistance_zone_bps", "Resistance zone (bps)"], ["sell.absorption_min", "Min supply absorption"],
    ["sell.cvd_deterioration_max", "Max CVD slope"], ["sell.breakdown_buffer_bps", "Breakdown buffer (bps)"],
    ["sell.profit_target_pct", "Profit target (fraction)"],
  ]],
  ["Mechanism caps", [
    ["mechanisms.profit_taking_max_pct", "Profit-taking cap"], ["mechanisms.risk_reduction_max_pct", "Risk-reduction cap"],
    ["mechanisms.defensive_exit_max_pct", "Defensive-exit cap"],
  ]],
  ["Regime", [
    ["regime.sma_days", "SMA days"], ["regime.constructive_above_pct", "Constructive above (%)"],
    ["regime.defensive_below_pct", "Defensive below (%)"],
  ]],
  ["Analysis", [
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
      <div class="card-head"><h2>Strategy parameters</h2><span class="note">${esc(s.config.validation_status)}</span></div>
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
      <div class="card-head"><h2>Decision records</h2>
        <a href="/api/export/decisions.json" class="link" download>export JSON</a>
      </div>
      ${rows.length ? `<table>
        <thead><tr><th>When (MYT)</th><th>Verdict</th><th>State</th><th>A</th><th>B</th><th class="num">Size</th></tr></thead>
        <tbody>${rows.slice(0, 40).map((d) => `<tr>
          <td style="font-family:var(--mono);font-size:11px">${esc(time(d.created_at))}</td>
          <td><b style="color:${d.verdict === "BUY" ? "var(--green)" : d.verdict === "SELL" ? "var(--red)" : d.verdict === "HOLD" ? "var(--amber)" : "var(--violet)"}">${esc(d.verdict)}</b></td>
          <td>${esc(d.portfolio_state)}</td>
          <td>${esc(d.bullish.state)}</td>
          <td>${esc(d.bearish.state)}</td>
          <td class="num">${d.proposed_order ? n(d.proposed_order.quantity, 6) : "—"}</td>
        </tr>`).join("")}</tbody>
      </table>` : `<div class="empty">No decisions recorded yet.</div>`}
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Data provenance</h2><span class="note">observed vs derived</span></div>
      <table>
        <thead><tr><th>Feature</th><th>Status</th><th class="num">Value</th></tr></thead>
        <tbody>${Object.entries(s.market.features).map(([key, f]) => `<tr>
          <td style="font-family:var(--mono);font-size:11px">${esc(key)}</td>
          <td><span class="badge ${f.status === "VERIFIED" ? "b-pass" : f.status === "PROXY" ? "b-not_configured" : "b-unavailable"}">${esc(f.status)}</span></td>
          <td class="num">${f.value === null ? "—" : esc(Number(f.value).toLocaleString("en-US", { maximumFractionDigits: 4 }))}</td>
        </tr>`).join("")}</tbody>
      </table>
    </div>
    <div class="card tight">
      <div class="card-head"><h2>Configuration history</h2></div>
      ${versionsTable()}
    </div>`;
}

const PANELS = {
  portfolio: ["Portfolio & allocation", drawerPortfolio],
  orders: ["Orders & cycles", drawerOrders],
  config: ["Configuration", drawerConfig],
  audit: ["Audit & provenance", drawerAudit],
};

/* ---------------- render ---------------- */
function render() {
  if (!S.state) return;
  renderChrome();
  $("#view").innerHTML = S.state.seeded ? commandCentre() : onboarding();
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
function wireApprove() {
  document.querySelectorAll("[data-approve]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await api("/api/orders/approve", {
          method: "POST",
          body: JSON.stringify({ decision_id: S.state.decision.decision_id, operator: "operator" }),
        });
        toast(`Reserved ${result.reserved} ${result.asset}. Order is APPROVED, not executed.`, true);
        await load(true);
      } catch (error) {
        toast(error.message);
        button.disabled = false;
      }
    });
  });
}

function wireView() {
  const seed = $("#seed");
  if (seed) {
    seed.addEventListener("click", async () => {
      seed.disabled = true;
      try {
        await api("/api/portfolio", {
          method: "POST",
          body: JSON.stringify({
            btc_core: $("#pf_btc_core").value,
            btc_tactical: $("#pf_btc_tactical").value,
            usdt_core: $("#pf_usdt_core").value,
            usdt_tactical: $("#pf_usdt_tactical").value,
            operator: $("#pf_operator").value,
          }),
        });
        toast("Balances recorded in the ledger.", true);
        await load(true);
      } catch (error) {
        toast(error.message);
        seed.disabled = false;
      }
    });
  }

  wireApprove();

  document.querySelectorAll("[data-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.toggle;
      S.expand[key] = !S.expand[key];
      render();
    });
  });
}

function wireDrawer() {
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

  wireApprove();

  document.querySelectorAll("[data-order]").forEach((button) => {
    button.addEventListener("click", async () => {
      const orderId = button.dataset.order;
      const action = button.dataset.action;
      try {
        if (action === "fill") {
          const order = (S.state.orders || []).find((o) => o.order_id === orderId);
          const quantity = window.prompt("Executed quantity (BTC)", order ? order.quantity : "");
          if (!quantity) return;
          const price = window.prompt("Execution price", order ? order.limit_price : "");
          if (!price) return;
          const fee = window.prompt("Fee paid (USDT)", "0") || "0";
          await api(`/api/orders/${orderId}/fill`, { method: "POST", body: JSON.stringify({ quantity, price, fee }) });
          toast("Fill recorded. Inventory updated.", true);
        } else if (action === "cancel") {
          if (!window.confirm("Confirm the exchange has cancelled this order? The reservation is released only on a confirmed cancellation.")) return;
          await api(`/api/orders/${orderId}/cancel`, { method: "POST", body: JSON.stringify({ confirmed: true }) });
          toast("Cancellation recorded; reservation released.", true);
        } else {
          await api(`/api/orders/${orderId}/${action}`, { method: "POST", body: "{}" });
          toast("Order state updated.", true);
        }
        await load(true);
      } catch (error) {
        toast(error.message);
      }
    });
  });
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
load(true);
