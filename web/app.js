/* BILLEE dashboard page. Talks only to the commander (billee_dash/commander.py). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
};

const store = {
  get(k, d) { try { const v = localStorage.getItem("billee." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("billee." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-Billee-Dash": "1" },
  };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (bad ? " bad" : "");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.className = ""), bad ? 6000 : 3000);
}

function confirmBox(text, go = "Go") {
  const d = $("#confirm");
  $("#confirmText").textContent = text;
  $("#confirmYes").textContent = go;
  d.returnValue = "";
  d.showModal();
  return new Promise((res) => d.addEventListener("close", () => res(d.returnValue === "yes"), { once: true }));
}

// ------------------------------------------------------------------ state
let CAT = null;           // catalog: modes, options, links, nodes
let STATE = null;         // last /api/state
let selected = store.get("mode", "teleop-drive");
const values = store.get("values", {});   // mode id -> option values the operator changed
const ALIVE = new Set(["starting", "running", "stopping"]);
const JOB_ACTIVE = new Set(["starting", "running", "degraded", "stopping"]);

function modeById(id) { return CAT.modes.find((m) => m.id === id); }
function optValues(mode) { return { ...mode.values, ...(values[mode.id] || {}) }; }

// ------------------------------------------------------------------ modes list
function renderModes() {
  for (const [group, el] of [["mission", $("#modesMission")], ["test", $("#modesTest")]]) {
    el.replaceChildren(...CAT.modes.filter((m) => m.group === group).map((m) =>
      h("button", {
        class: "mode" + (m.id === selected ? " sel" : "") + (m.disabled ? " disabled" : ""),
        "data-mode": m.id, onclick: () => select(m.id),
      },
      h("span", { class: "t" }, m.title, h("span", { class: "mbadge" }), m.todo ? h("span", { class: "badge todo", text: "todo" }) : null),
      h("span", { class: "s", text: m.summary }))));
  }
  updateModeBadges();
}

function updateModeBadges() {
  const job = STATE && STATE.job;
  for (const el of $$(".mode")) {
    const b = $(".mbadge", el);
    const on = job && job.mode === el.dataset.mode && JOB_ACTIVE.has(job.state);
    b.className = on ? "mbadge badge run" : "mbadge";
    b.textContent = on ? job.state : "";
  }
}

function select(id) {
  selected = id;
  store.set("mode", id);
  $$(".mode").forEach((el) => el.classList.toggle("sel", el.dataset.mode === id));
  renderPanel();
}

// ------------------------------------------------------------------ mode panel
function control(key, mode) {
  const spec = CAT.options[key];
  const v = optValues(mode)[key];
  const set = (val) => {
    values[mode.id] = { ...(values[mode.id] || {}), [key]: val };
    store.set("values", values);
    schedulePreview();
  };
  if (spec.type === "select" && spec.choices.length <= 4) {
    const seg = h("div", { class: "seg", role: "group", "aria-label": spec.label });
    for (const [val, label] of spec.choices) {
      seg.append(h("button", {
        class: val === v ? "on" : "", "aria-pressed": String(val === v), text: label,
        onclick: (e) => {
          $$("button", seg).forEach((b) => { b.classList.remove("on"); b.setAttribute("aria-pressed", "false"); });
          e.currentTarget.classList.add("on"); e.currentTarget.setAttribute("aria-pressed", "true");
          set(val);
        },
      }));
    }
    return seg;
  }
  if (spec.type === "select") {
    const s = h("select", { "aria-label": spec.label, onchange: (e) => set(e.target.value) },
      spec.choices.map(([val, label]) => h("option", { value: val, selected: val === v, text: label })));
    return s;
  }
  if (spec.type === "bool") {
    const id = `opt-${mode.id}-${key}`;
    return h("label", { class: "toggle", for: id },
      h("input", { type: "checkbox", id, checked: !!v, onchange: (e) => set(e.target.checked) }),
      h("span", { text: v ? "on" : "off" }));
  }
  const inp = h("input", {
    value: v ?? "", placeholder: spec.placeholder || "", spellcheck: "false", autocomplete: "off",
    "aria-label": spec.label, inputmode: spec.type === "number" ? "decimal" : null,
    oninput: (e) => set(spec.type === "number" ? e.target.value : e.target.value),
  });
  return inp;
}

function optRow(key, mode) {
  const spec = CAT.options[key];
  const row = h("div", { class: "opt", "data-key": key, title: spec.help || "" },
    h("div", { class: "lbl" }, spec.label,
      h("span", { class: `side-tag ${spec.side}`, text: spec.side === "both" ? "both" : spec.side })),
    h("div", {}, control(key, mode)));
  const t = $(".toggle input", row);
  if (t) t.addEventListener("change", () => ($(".toggle span", row).textContent = t.checked ? "on" : "off"));
  return row;
}

function renderPanel() {
  const mode = modeById(selected) || CAT.modes[0];
  const panel = $("#modePanel");
  const job = STATE && STATE.job;
  const basic = mode.options.filter((k) => CAT.options[k].basic);
  const adv = mode.options.filter((k) => !CAT.options[k].basic);
  const runBtn = h("button", { class: "run", id: "runBtn", onclick: () => runOrStop(mode) }, "Run");

  const advBody = h("div", {});
  for (const side of ["rover", "both", "ground"]) {
    const keys = adv.filter((k) => CAT.options[k].side === side);
    if (!keys.length) continue;
    advBody.append(h("div", { class: "adv-group" },
      h("h4", { text: side === "rover" ? "Rover · Jetson (l4t)" : side === "ground" ? "Ground station (x86)" : "Both" }),
      h("div", { class: "opts" }, keys.map((k) => optRow(k, mode)))));
  }

  panel.replaceChildren(...[
    h("div", { class: "mp-head" },
      h("div", {}, h("h1", { text: mode.title }), h("p", { text: mode.summary })),
      runBtn),
    mode.todo ? h("p", { class: "todo-note", text: "TODO: " + mode.todo }) : null,
    h("div", { class: "steps", id: "steps" }),
    h("p", { class: "job-msg", id: "jobMsg" }),
    basic.length ? h("div", { class: "opts" }, basic.map((k) => optRow(k, mode))) : null,
    adv.length ? h("details", { class: "more", open: store.get("advOpen", false) },
      h("summary", { text: `Advanced · ${adv.length} settings` }), advBody) : null,
    mode.disabled ? null : h("details", { class: "more", id: "cmdDetails", open: store.get("cmdOpen", false) },
      h("summary", { text: "Commands" }), h("div", { class: "cmds", id: "cmds" })),
    h("div", { class: "btn-row", style: "margin-top:18px" },
      mode.disabled ? null : h("button", { class: "ghost small", onclick: () => resetMode(mode) }, "Reset to defaults")),
  ].filter(Boolean));
  for (const d of $$("details.more", panel)) {
    d.addEventListener("toggle", () => store.set(d.id === "cmdDetails" ? "cmdOpen" : "advOpen", d.open));
  }
  updateJobUI();
  schedulePreview(0);
}

function resetMode(mode) {
  delete values[mode.id];
  store.set("values", values);
  renderPanel();
}

let previewT = null;
function schedulePreview(ms = 250) {
  clearTimeout(previewT);
  previewT = setTimeout(preview, ms);
}

async function preview() {
  const mode = modeById(selected);
  const box = $("#cmds");
  $$(".opt input").forEach((i) => i.classList.remove("bad"));
  if (!box || mode.disabled) return;
  try {
    const plan = await api("/api/mode/preview", { mode: mode.id, options: optValues(mode) });
    const rows = [];
    for (const s of plan.pre) rows.push(["rover host", s.cmd]);
    for (const s of plan.rover) rows.push(["rover · l4t container", s.cmd]);
    for (const s of plan.ground) rows.push(["ground · x86 container", s.cmd]);
    if (plan.links.length) rows.push(["opens", plan.links.map(linkName).join(" · ")]);
    box.replaceChildren(...rows.map(([w, c]) => h("div", { class: "cmd" }, h("div", { class: "w", text: w }), h("code", { text: c }))));
    $("#runBtn").title = "";
  } catch (e) {
    box.replaceChildren(h("div", { class: "cmd" }, h("code", { class: "err", text: e.message })));
    const bad = Object.keys(CAT.options).find((k) => e.message.startsWith(CAT.options[k].label + ":"));
    if (bad) { const i = $(`.opt[data-key="${bad}"] input`); if (i) i.classList.add("bad"); }
  }
}

function linkName(k) {
  return { foxglove: "Foxglove (rover bridge)", foxglove_local: "Foxglove (local)", scm_gds: "Science GDS", rcm_gds: "RCM GDS" }[k] || k;
}

async function runOrStop(mode) {
  const job = STATE && STATE.job;
  const active = job && JOB_ACTIVE.has(job.state);
  try {
    if (active && job.mode === mode.id) {
      if (job.state === "stopping") return;
      await api("/api/mode/stop", {});
      toast(`Stopping ${mode.title}`);
    } else {
      if (active) {
        const ok = await confirmBox(`${job.title} is ${job.state}.\nStop it and start ${mode.title}?`, "Switch");
        if (!ok) return;
        await api("/api/mode/stop", {});
        toast(`Stopping ${job.title}…`);
        for (let i = 0; i < 80; i++) {
          await new Promise((r) => setTimeout(r, 500));
          const st = await api("/api/state");
          if (!st.job || !JOB_ACTIVE.has(st.job.state)) break;
        }
      }
      await api("/api/mode/start", { mode: mode.id, options: optValues(mode) });
      toast(`Starting ${mode.title}`);
      openDrawer(true);
    }
    pollState();
  } catch (e) {
    toast(e.message, true);
  }
}

const STEP_DOT = { pending: "", active: "busy", done: "ok", warn: "warn", failed: "bad", cancelled: "" };

function updateJobUI() {
  const mode = modeById(selected);
  const job = STATE && STATE.job;
  const btn = $("#runBtn");
  if (!btn || !mode) return;
  const mine = job && job.mode === mode.id;
  const active = mine && JOB_ACTIVE.has(job.state);
  btn.disabled = !!mode.disabled || (active && job.state === "stopping");
  btn.textContent = mode.disabled ? "Not ready" : active ? (job.state === "stopping" ? "Stopping…" : "Stop") : "Run";
  btn.classList.toggle("stop", !!active);
  const steps = $("#steps");
  const msg = $("#jobMsg");
  if (mine) {
    steps.replaceChildren(...job.steps.map((s) => h("span", { class: "step " + s.state, title: s.message || s.state },
      h("span", { class: "dot " + (STEP_DOT[s.state] || "") }), s.name)));
    msg.textContent = job.message || (job.state === "running" ? `Running since ${new Date(job.started * 1000).toLocaleTimeString()}` : "");
    msg.className = "job-msg" + (["failed", "degraded"].includes(job.state) ? " bad" : "");
  } else {
    steps.replaceChildren();
    msg.textContent = job && JOB_ACTIVE.has(job.state) ? `${job.title} is ${job.state}. Run switches to this mode.` : "";
    msg.className = "job-msg";
  }
}

// ------------------------------------------------------------------ header + systems
function pill(label, cls, title) {
  return h("span", { class: "pill", title: title || "" }, h("span", { class: "dot " + cls }), label);
}

const CSTATE = { running: "ok", exited: "warn", created: "warn", paused: "warn", missing: "bad", error: "bad", "no-docker": "bad" };

function renderHeader() {
  const s = STATE;
  const r = s.rover || {};
  const linkCls = { up: "ok", connecting: "busy", down: "bad", off: "" }[r.link] || "";
  const rc = (r.containers || {}).rover || {};
  const gc = ((s.ground || {}).containers || {}).ground || {};
  const lk = s.links || {};
  $("#pills").replaceChildren(
    pill("Rover link", linkCls, r.message),
    pill("Rover container", r.link === "up" ? (CSTATE[rc.state] || "") : "", rc.error || rc.state),
    pill("Ground container", CSTATE[gc.state] || "", gc.error || gc.state),
    pill("RCM GDS", lk.rcm_gds ? "ok" : "bad", CAT.links.rcm_gds.url),
    pill("Science GDS", lk.scm_gds ? "ok" : "", CAT.links.scm_gds.url),
    pill("Foxglove bridge", lk.foxglove_ws ? "ok" : "", CAT.links.foxglove.ws),
  );
  const job = s.job;
  const strip = $("#stateStrip");
  if (job && JOB_ACTIVE.has(job.state)) {
    const cls = { running: "ok", starting: "busy", stopping: "busy", degraded: "warn" }[job.state];
    strip.replaceChildren(h("span", { class: "dot " + cls }), h("b", { text: job.title }), h("span", { class: "muted", text: job.state }));
  } else {
    strip.replaceChildren(h("span", { class: "dot" }), h("b", { text: "Idle" }), h("span", { class: "muted", text: "pick a mode, then Run" }));
  }
}

function renderQuick() {
  const L = CAT.links;
  $("#quick").replaceChildren(
    h("a", { href: L.rcm_gds.url, target: "_blank", rel: "noopener" }, "RCM GDS"),
    h("a", { href: L.scm_gds.url, target: "_blank", rel: "noopener" }, "Science GDS"),
    h("a", { href: L.foxglove.url, target: "_blank", rel: "noopener", title: L.foxglove.ws }, "Foxglove"),
  );
}

function sysRow(name, cls, detail, meta, ...buttons) {
  return h("div", { class: "sys-row" },
    h("span", { class: "dot " + cls }),
    h("div", {}, h("div", { class: "n", text: name }), h("div", { class: "d", text: detail || "" })),
    h("div", { class: "btn-row", style: "margin:0" }, meta ? h("span", { class: "meta", text: meta }) : null, ...buttons));
}

async function act(path, body, okMsg) {
  try { await api(path, body); if (okMsg) toast(okMsg); pollState(); }
  catch (e) { toast(e.message, true); }
}

function containerButtons(side, c, linkUp) {
  if (!linkUp) return [];
  if (c.state === "running") {
    return [h("button", { class: "small ghost", title: "Ctrl-C every ROS process the dashboard started in this container",
      onclick: async () => { if (await confirmBox(`Ctrl-C every process the dashboard started in the ${side} container?`, "Stop all")) act("/api/container", { side, action: "stop_ros" }, "Sent Ctrl-C"); } }, "Stop all")];
  }
  return [h("button", { class: "small", onclick: () => { openDrawer(true); act("/api/container", { side, action: "start" }, `Starting the ${side} container`); } }, "Start")];
}

function renderSystems() {
  const s = STATE;
  const r = s.rover || {};
  const up = r.link === "up";
  const host = r.host || {};
  const rc = (r.containers || {}).rover || {};
  const g = s.ground || {};
  const gc = (g.containers || {}).ground || {};
  const gh = g.host || {};
  const mem = (x) => (x.mem_avail_mb ? `${(x.mem_avail_mb / 1024).toFixed(1)} GB free` : "");
  const rows = [
    sysRow(`Rover · ${CAT.hosts.rover}`, { up: "ok", connecting: "busy", down: "bad" }[r.link] || "",
      up ? (host.hostname || "") : (r.message || r.link), up ? mem(host) : "",
      h("button", { class: "small ghost", title: r.ssh, onclick: () => act("/api/rover/reconnect", {}, "Reconnecting") }, "Reconnect")),
    sysRow("Rover container · l4t", up ? (CSTATE[rc.state] || "") : "", up ? (rc.error || rc.name || rc.state || "") : "waiting for the link", "",
      ...containerButtons("rover", rc, up)),
    sysRow("Ground container · x86", CSTATE[gc.state] || "", gc.error || gc.name || gc.state || "", mem(gh), ...containerButtons("ground", gc, true)),
  ];
  for (const name of CAT.services) {
    const st = (r.services || {})[name];
    const label = /rcm/.test(name) ? "RCM GDS service" : /scm/.test(name) ? "Science GDS service" : name;
    rows.push(sysRow(label,
      st === "active" ? "ok" : st ? "bad" : "", up ? `${name} · ${st || "?"}` : name, "",
      up ? h("button", { class: "small ghost", onclick: async () => {
        if (await confirmBox(`Restart ${name} on the rover?\n(sudo systemctl restart; needs the sudo rule from make setup-dash)`, "Restart")) act("/api/service", { name, action: "restart" }, `Restarting ${name}`);
      } }, "Restart") : null));
  }
  $("#sysBody").replaceChildren(...rows);
}

// ------------------------------------------------------------------ CAN
const canSel = new Set(store.get("canNodes", []));
function renderCan() {
  $("#canIface").value = store.get("canIface", CAT.can_default_iface);
  $("#canBitrate").value = store.get("canBitrate", String(CAT.can_default_bitrate));
  $("#canIface").oninput = (e) => store.set("canIface", e.target.value.trim());
  $("#canBitrate").oninput = (e) => store.set("canBitrate", e.target.value.trim());
  const box = $("#canNodes");
  box.replaceChildren(...CAT.can_nodes.map((n) => h("button", {
    class: "chip" + (canSel.has(n.node) ? " on" : ""), "aria-pressed": String(canSel.has(n.node)),
    title: n.joint, onclick: (e) => {
      canSel.has(n.node) ? canSel.delete(n.node) : canSel.add(n.node);
      e.currentTarget.classList.toggle("on"); e.currentTarget.setAttribute("aria-pressed", String(canSel.has(n.node)));
      store.set("canNodes", [...canSel]); canHint();
    },
  }, h("b", { text: `node ${n.node}` }), h("span", { text: n.where || n.joint }))));
  $("#nodesAll").onclick = () => { canSel.clear(); store.set("canNodes", []); renderCan(); };
  canHint();
}
function canHint() {
  $("#canHint").textContent = canSel.size ? `Node actions apply to ${[...canSel].sort((a, b) => a - b).join(", ")}.` : "Node actions apply to every wheel node.";
}
async function canAction(action) {
  const nodes = [...canSel].sort((a, b) => a - b);
  const iface = $("#canIface").value.trim();
  const which = nodes.length ? `node${nodes.length > 1 ? "s" : ""} ${nodes.join(", ")}` : "every wheel node";
  if (action === "force-reboot" && !await confirmBox(`Reboot ${which} on ${iface}?\n\nEach board runs its startup calibration: the wheel turns. Keep the wheels off the ground.`, "Reboot")) return;
  if (action === "reboot" && !await confirmBox(`Clear ${which}, then reboot any board whose error a clear can't fix?\n\nRebooted wheels turn during calibration.`, "Clear + reboot")) return;
  if (action === "down" && !await confirmBox(`Take ${iface} down? A running drivetrain loses its bus.`, "Take down")) return;
  try {
    await api("/api/can", { action, iface, bitrate: action === "up" ? $("#canBitrate").value.trim() : null, nodes });
    openDrawer(true); setTab("rover");
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ output
const LINES = [];
const MAX_LINES = 30000;
const MAX_DOM = 4000;
let tab = store.get("tab", "rover");
let procFilter = "";
let since = 0;

function esc(s) { return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
function ansi(text) {
  text = text.replace(/\x1b\[[0-9;?]*[A-HJKSTfhlnsu]/g, "");
  const parts = text.split(/\x1b\[([0-9;]*)m/);
  let out = "", cls = new Set();
  for (let i = 0; i < parts.length; i++) {
    if (i % 2) {
      for (const c of (parts[i] || "0").split(";")) {
        const n = +c;
        if (n === 0) cls.clear(); else if (n === 1) cls.add("a1"); else if (n === 22) cls.delete("a1");
        else if (n === 39) [...cls].filter((k) => k !== "a1").forEach((k) => cls.delete(k));
        else if ((n >= 30 && n <= 37) || (n >= 90 && n <= 97)) { [...cls].filter((k) => k !== "a1").forEach((k) => cls.delete(k)); cls.add("a" + n); }
      }
    } else if (parts[i]) {
      out += cls.size ? `<span class="${[...cls].join(" ")}">${esc(parts[i])}</span>` : esc(parts[i]);
    }
  }
  return out;
}
function matches(l) {
  if (tab !== "all" && l[2] !== tab) return false;
  return !procFilter || l[3] === procFilter;
}
function lineEl(l) {
  const [, ts, side, proc, stream, text] = l;
  const d = new Date(ts * 1000);
  const el = document.createElement("span");
  el.className = "ln" + (stream === "meta" ? " meta" : stream === "err" ? " err" : "");
  const t = d.toTimeString().slice(0, 8);
  const src = tab === "all" ? `<span class="src ${side}">${side}</span>` : "";
  const p = !procFilter && proc && proc !== "dashboard" && proc !== "agent" ? `<span class="src">${esc(proc)}</span>` : "";
  el.innerHTML = `<span class="ts">${t}</span>${src}${p}${stream === "out" ? ansi(text) : esc(text)}`;
  return el;
}
function renderOut() {
  const out = $("#out");
  const view = LINES.filter(matches).slice(-MAX_DOM);
  out.replaceChildren(...view.map(lineEl));
  if ($("#follow").checked) out.scrollTop = out.scrollHeight;
}
function appendOut(lines) {
  const out = $("#out");
  const atBottom = out.scrollHeight - out.scrollTop - out.clientHeight < 40;
  const frag = document.createDocumentFragment();
  for (const l of lines) if (matches(l)) frag.append(lineEl(l));
  out.append(frag);
  while (out.childElementCount > MAX_DOM) out.firstElementChild.remove();
  if ($("#follow").checked && atBottom) out.scrollTop = out.scrollHeight;
}
async function pullLogs() {
  for (;;) {
    try {
      const r = await api(`/api/logs?since=${since}&wait=20`);
      if (r.last < since) { since = 0; LINES.length = 0; renderOut(); continue; }
      since = r.last;
      if (r.lines.length) {
        LINES.push(...r.lines);
        if (LINES.length > MAX_LINES) LINES.splice(0, LINES.length - MAX_LINES);
        appendOut(r.lines);
      }
    } catch {
      await new Promise((res) => setTimeout(res, 2000));
    }
  }
}
function setTab(side) {
  tab = side; store.set("tab", side); procFilter = "";
  $$("#outTabs button").forEach((b) => b.classList.toggle("on", b.dataset.side === side));
  renderProcs(); renderOut();
}
function openDrawer(open) {
  const d = $("#drawer");
  d.classList.toggle("open", open);
  document.body.classList.toggle("drawer-open", open);
  $("#drawerToggle").setAttribute("aria-expanded", String(open));
  store.set("drawer", open);
  if (open) renderOut();
}
function allProcs() {
  if (!STATE) return [];
  const r = ((STATE.rover || {}).procs || []).map((p) => ({ ...p, side: "rover" }));
  const g = ((STATE.ground || {}).procs || []).map((p) => ({ ...p, side: "ground" }));
  return [...r, ...g];
}
function renderProcs() {
  const procs = allProcs().filter((p) => tab === "all" || p.side === tab).reverse();
  const sel = $("#procFilter");
  const want = procFilter;
  sel.replaceChildren(h("option", { value: "", text: "All processes" }),
    ...procs.map((p) => h("option", { value: p.id, selected: p.id === want, text: `${p.label} · ${p.state}` })));
  const box = $("#procs");
  const live = procs.filter((p) => ALIVE.has(p.state));
  const recent = procs.filter((p) => !ALIVE.has(p.state)).slice(0, 8);
  const row = (p) => h("div", { class: "proc" + (p.id === procFilter ? " sel" : ""), title: p.cmd || "",
    onclick: () => { procFilter = procFilter === p.id ? "" : p.id; renderProcs(); renderOut(); } },
    h("span", { class: "dot " + ({ running: "ok", starting: "busy", stopping: "busy", exited: "", failed: "bad", stopped: "" }[p.state] || "") }),
    h("span", { class: "pl", text: p.label }),
    ALIVE.has(p.state) && p.kind !== "task" ? h("button", { class: "small ghost", onclick: (e) => { e.stopPropagation(); act("/api/proc/stop", { side: p.side, id: p.id }, `Stopping ${p.label}`); } }, "Stop") : h("span"),
    h("span", { class: "ps", text: `${tab === "all" ? p.side + " · " : ""}${p.state}${p.rc != null && p.state !== "running" ? " · exit " + p.rc : ""}` }));
  box.replaceChildren(
    ...(live.length ? live.map(row) : [h("div", { class: "none", text: "Nothing running here." })]),
    ...(recent.length ? [h("div", { class: "none", text: "Finished" }), ...recent.map(row)] : []));
}

// ------------------------------------------------------------------ polling
let stateBusy = false;
async function pollState() {
  if (stateBusy) return;
  stateBusy = true;
  try {
    STATE = await api("/api/state");
    renderHeader(); renderSystems(); updateModeBadges(); updateJobUI(); renderProcs();
  } catch (e) {
    $("#pills").replaceChildren(pill("Dashboard server unreachable", "bad", e.message));
  } finally { stateBusy = false; }
}

async function boot() {
  const t0 = Date.now();
  for (;;) {
    try { CAT = await api("/api/catalog"); break; }
    catch { await new Promise((r) => setTimeout(r, 1500)); }
  }
  if (!modeById(selected)) selected = CAT.modes[0].id;
  renderQuick(); renderModes(); renderPanel(); renderCan();
  await pollState();
  setInterval(pollState, 1500);
  pullLogs();
  $$("#outTabs button").forEach((b) => (b.onclick = () => setTab(b.dataset.side)));
  setTab(tab);
  $("#drawerToggle").onclick = () => openDrawer(!$("#drawer").classList.contains("open"));
  $("#outBig").onclick = () => { $("#drawer").classList.toggle("big"); document.body.classList.toggle("drawer-big", $("#drawer").classList.contains("big")); if (!$("#drawer").classList.contains("open")) openDrawer(true); };
  $("#outClear").onclick = () => { LINES.length = 0; renderOut(); };
  $("#procFilter").onchange = (e) => { procFilter = e.target.value; renderProcs(); renderOut(); };
  $$("[data-can]").forEach((b) => (b.onclick = () => canAction(b.dataset.can)));
  openDrawer(store.get("drawer", false));
  setTimeout(() => $("#splash").classList.add("gone"), Math.max(0, 1300 - (Date.now() - t0)));
}

boot();
