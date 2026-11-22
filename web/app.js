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
    b.className = on ? "mbadge badge live" : "mbadge";
    b.textContent = on ? job.state : "";
  }
}

function select(id) {
  selected = id;
  store.set("mode", id);
  $$(".mode").forEach((el) => el.classList.toggle("sel", el.dataset.mode === id));
  renderPanel();
}

// ------------------------------------------------------------------ state machine
// The rover state machine (rover: devcontainer -> Idle -> one mission mode; ground station:
// devcontainer -> running), drawn as an SVG and lit from /api/state: whichever mode runs is
// highlighted, the devcontainer boxes follow the real containers. Click a mode to select it.
const SVGNS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid) el.append(kid);
  return el;
};
const SM_MODES = ["teleop-drive", "teleop-arm", "teleop-science", "autonomous", "astronaut"];
// The blink(1) mode light's colour per state (status_light package): a dot in each state.
const SM_LIGHT = { idle: ["idle", "yellow, slow pulse"], "teleop-drive": ["teleop", "solid blue"],
  "teleop-arm": ["teleop", "solid blue"], "teleop-science": ["teleop", "solid blue"],
  autonomous: ["autonomous", "solid red (flashing green when it arrives)"], astronaut: ["autonomous", "solid red"] };
function smLight(id, x, y) {
  const l = SM_LIGHT[id];
  return l ? svg("circle", { cx: x, cy: y, r: 4.5, class: "sm-light " + l[0] }, svg("title", { text: `Mode light: ${l[1]}` })) : null;
}
const SM_LABEL = { "teleop-drive": "Teleop-Drive", "teleop-arm": "Teleop-Arm", "teleop-science": "Teleop-Science",
  autonomous: "Autonomous-Mode", astronaut: "Astronaut-Mode" };

function smNode(id, x, y, w, hgt, label, note, extra = {}) {
  return svg("g", { class: "sm-node", id: "sm-" + id, ...extra },
    svg("title", { text: note }),
    svg("rect", { x, y, width: w, height: hgt, rx: 7 }),
    svg("text", { x: x + w / 2, y: y + (extra.labelTop ? 17 : hgt / 2 + 4.5), "text-anchor": "middle", class: "sm-label", text: label }));
}
function smEdge(id, d, label, lx, ly) {
  return svg("g", { class: "sm-edge", id: "sm-e-" + id },
    svg("path", { d, "marker-end": "url(#smArrow)" }),
    label ? svg("text", { x: lx, y: ly, "text-anchor": "middle", class: "sm-elabel", text: label }) : null);
}

function renderStateMachine() {
  const ground = !!CAT.ground;
  const W = 140, TOP = 128, GAP = 20, X0 = 34;
  const IDLE = { x: 410, y: 32, w: 200, h: 34 };
  const idleX = IDLE.x + IDLE.w / 2, idleY = IDLE.y + IDLE.h;
  const kids = [
    svg("defs", {}, svg("marker", { id: "smArrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
      svg("path", { d: "M0,0 L10,5 L0,10 z", class: "sm-arrow" }))),
    // rover box
    svg("rect", { x: 10, y: 6, width: 820, height: 222, rx: 10, class: "sm-box" }),
    svg("text", { x: 420, y: 22, "text-anchor": "middle", class: "sm-title", text: "Rover · Jetson (L4T devcontainer)" }),
    svg("circle", { cx: 60, cy: 49, r: 6, class: "sm-start" }),
    smEdge("rstart", "M66,49 L94,49"),
    smNode("rdev", 96, 32, 200, 34, "Devcontainer Opens (L4T)", "The rover's l4t dev container (Systems → Rover container)"),
    smEdge("rdev", `M296,49 L${IDLE.x - 2},49`, "dashboard launches", 353, 42),
    smNode("idle", IDLE.x, IDLE.y, IDLE.w, IDLE.h, "Idle (Dashboard Launched)", "The dashboard is up and nothing is running: pick a mode, then Run\nMode light: yellow, slow pulse"),
    smLight("idle", IDLE.x + IDLE.w - 10, IDLE.y + 10),
    svg("g", { id: "sm-test", class: "sm-test", visibility: "hidden" },
      svg("rect", { x: 622, y: 36, width: 196, height: 26, rx: 6 }),
      svg("text", { x: 720, y: 53, "text-anchor": "middle", text: "" })),
  ];
  SM_MODES.forEach((id, i) => {
    const m = modeById(id) || { title: SM_LABEL[id], summary: "" };
    const x = X0 + i * (W + GAP);
    const arm = id === "teleop-arm";
    const y = arm ? TOP - 10 : TOP, hgt = arm ? 98 : 36;
    const cx = x + W / 2;
    const note = `${m.title}\n${m.summary}${m.todo ? "\nTODO: " + m.todo : ""}\n(click to select)`;
    kids.push(smEdge(id, `M${idleX},${idleY} C${idleX},${idleY + 34} ${cx},${y - 34} ${cx},${y - 2}`));
    const node = smNode(id, x, y, W, hgt, SM_LABEL[id], note, {
      "data-mode": id, class: "sm-node sm-mode" + (m.disabled ? " todo" : ""), labelTop: arm,
      role: "button", tabindex: 0, onclick: () => select(id),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(id); } },
    });
    if (arm) {  // the Xbox drive/arm toggle inside Teleop-Arm
      node.append(
        svg("rect", { x: x + 12, y: y + 28, width: W - 24, height: 22, rx: 5, class: "sm-sub" }),
        svg("text", { x: cx, y: y + 43, "text-anchor": "middle", class: "sm-sublabel", text: "Xbox controls Drive" }),
        svg("text", { x: cx, y: y + 63, "text-anchor": "middle", class: "sm-elabel", text: "⇅ toggle mode" }),
        svg("rect", { x: x + 12, y: y + 69, width: W - 24, height: 22, rx: 5, class: "sm-sub" }),
        svg("text", { x: cx, y: y + 84, "text-anchor": "middle", class: "sm-sublabel", text: "Xbox controls Arm" }));
    }
    kids.push(node, smLight(id, x + W - 10, y + 10));
  });
  // ground station box
  const rcm = CAT.links.rcm_gds.url;
  kids.push(
    svg("rect", { x: 846, y: 6, width: 244, height: 222, rx: 10, class: "sm-box" }),
    svg("text", { x: 968, y: 22, "text-anchor": "middle", class: "sm-title", text: ground ? "Ground station (x86 devcontainer)" : "Ground station (this computer)" }),
    svg("circle", { cx: 968, cy: 44, r: 6, class: "sm-start" }),
    smEdge("gstart", "M968,50 L968,68"),
    smNode("gdev", 878, 70, 180, 34, ground ? "Devcontainer Opens (x86)" : "No container here",
      ground ? "The ground station's x86 dev container (Systems → Ground container)" : "No ground-station container on this computer: every mode runs on the rover"),
    smEdge("gdev", "M968,104 L968,150", "dashboard launches", 1022, 131),
    smNode("grun", 878, 152, 180, 34, ground ? "Running" : "Dashboard running",
      `Always brings up the F´ RCM GDS at ${rcm}`),
  );
  $("#smSvg").replaceChildren(svg("svg", { viewBox: "0 0 1100 234", class: "sm-svg", role: "img",
    "aria-label": "Rover state machine: the running mode is highlighted" }, ...kids));
  const open = store.get("smOpen", true);
  $("#stateMachine").classList.toggle("closed", !open);
  $("#smToggle").setAttribute("aria-expanded", String(open));
  $("#smToggle").onclick = () => {
    const now = $("#stateMachine").classList.toggle("closed");
    store.set("smOpen", !now);
    $("#smToggle").setAttribute("aria-expanded", String(!now));
  };
  updateStateMachine();
}

function smSet(id, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  for (const c of ["on", "busy", "warn", "bad", "dim"]) el.classList.toggle(c, c === cls);
}

function updateStateMachine() {
  if (!STATE || !$("#smSvg svg")) return;
  const r = STATE.rover || {};
  const rc = (r.containers || {}).rover || {};
  const gc = ((STATE.ground || {}).containers || {}).ground || {};
  const job = STATE.job;
  const active = job && JOB_ACTIVE.has(job.state) ? job : null;
  const jobCls = active ? ({ starting: "busy", stopping: "busy", running: "on", degraded: "warn" }[active.state]) : null;
  const mission = active && SM_MODES.includes(active.mode);

  const roverCls = r.link === "up" ? (rc.state === "running" ? "on" : rc.state ? "bad" : "dim")
    : r.link === "connecting" ? "busy" : "dim";
  smSet("sm-rdev", roverCls); smSet("sm-e-rstart", roverCls === "on" ? "on" : null);
  smSet("sm-e-rdev", roverCls === "on" ? "on" : null);
  smSet("sm-idle", active ? (mission ? null : "dim") : "on");
  for (const id of SM_MODES) {
    const cls = mission && active.mode === id ? jobCls : null;
    smSet("sm-" + id, cls); smSet("sm-e-" + id, cls);
  }
  const test = $("#sm-test");
  if (active && !mission) {
    test.setAttribute("visibility", "visible");
    test.setAttribute("class", "sm-test " + jobCls);
    $("text", test).textContent = `Test: ${active.title} · ${active.state}`;
  } else {
    test.setAttribute("visibility", "hidden");
  }
  if (STATE.ground_enabled === false) {
    smSet("sm-gdev", "dim"); smSet("sm-e-gstart", null); smSet("sm-e-gdev", null); smSet("sm-grun", "on");
  } else {
    const g = gc.state === "running" ? "on" : gc.state ? "bad" : "dim";
    smSet("sm-gdev", g); smSet("sm-e-gstart", g === "on" ? "on" : null);
    smSet("sm-e-gdev", g === "on" ? "on" : null); smSet("sm-grun", g === "on" ? "on" : "dim");
  }
  // the one-line summary in the card's header (also shown when collapsed)
  const cls = active ? jobCls : "ok";
  $("#smNow").replaceChildren(
    h("span", { class: "dot " + (cls === "on" ? "ok" : cls) }),
    h("b", { text: active ? active.title : "Idle" }),
    h("span", { class: "muted", text: active ? active.state : "pick a mode, then Run" }));
}

// ------------------------------------------------------------------ mode panel
function control(key, mode) {
  const spec = CAT.options[key];
  const v = optValues(mode)[key];
  const set = (val) => {
    values[mode.id] = { ...(values[mode.id] || {}), [key]: val };
    store.set("values", values);
    schedulePreview();
    renderPad();
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
  if (spec.type === "range") {
    const shown = h("output", { class: "range-val", text: Number(v).toFixed(2) });
    const r = h("input", {
      type: "range", min: spec.min, max: spec.max, step: spec.step || "any", value: v,
      "aria-label": spec.label,
      oninput: (e) => { shown.textContent = Number(e.target.value).toFixed(2); set(Number(e.target.value)); },
    });
    return h("div", { class: "range" }, r, shown);
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
    h("div", { class: "pad", id: "padBox", hidden: true }),
    h("div", { class: "pad arm-sel", id: "armBox", hidden: true }),
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
    const where = (s, side) => s.where === "host"
      ? (s.cwd === "hardware_repo" ? `${side} host · billee-hardware-2027` : `${side} host`)
      : (side === "rover" ? "rover · l4t container" : "ground · x86 container");
    const cmd = (s) => s.show || s.cmd;
    for (const s of plan.pre) rows.push([where(s, "rover"), cmd(s)]);
    for (const s of plan.rover) rows.push([where(s, "rover"), cmd(s)]);
    for (const s of plan.ground) rows.push([where(s, "ground"), cmd(s)]);
    for (const s of plan.post || []) rows.push(["on Stop · " + where(s, "rover"), cmd(s)]);
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
  return { foxglove: "Foxglove (rover bridge)", foxglove_local: "Foxglove (local)", scm_gds: "Science GDS", rcm_gds: "RCM GDS", astronaut: "Astronaut page" }[k] || k;
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
  const ground = s.ground_enabled !== false;
  $("#pills").replaceChildren(...[
    pill("Rover link", linkCls, r.message),
    pill("Rover container", r.link === "up" ? (CSTATE[rc.state] || "") : "", rc.error || rc.state),
    ground ? pill("Ground container", CSTATE[gc.state] || "", gc.error || gc.state) : null,
    pill("RCM GDS", lk.rcm_gds ? "ok" : "bad", CAT.links.rcm_gds.url),
    pill("Science GDS", lk.scm_gds ? "ok" : "", CAT.links.scm_gds.url),
    pill("Foxglove bridge", lk.foxglove_ws ? "ok" : "", CAT.links.foxglove.ws),
    pill(PAD.name ? "Gamepad" : "No gamepad", PAD.name ? (PAD.sending ? "ok" : "") : "", PAD.name),
  ].filter(Boolean));
}

function renderQuick() {
  const L = CAT.links;
  $("#quick").replaceChildren(
    h("a", { href: L.rcm_gds.url, target: "_blank", rel: "noopener" }, "RCM GDS"),
    h("a", { href: L.scm_gds.url, target: "_blank", rel: "noopener" }, "Science GDS"),
    h("a", { href: L.foxglove.url, target: "_blank", rel: "noopener", title: L.foxglove.ws }, "Foxglove"),
    h("a", { href: L.astronaut.url, target: "_blank", rel: "noopener", title: "astronaut-streamer (talk-back)" }, "Astronaut"),
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
  // the agent's start / rebuild task for this container (one at a time)
  const task = allProcs().find((p) => p.side === side && p.id === `container-${side}` && ALIVE.has(p.state));
  if (task) return [h("button", { class: "small ghost", disabled: true, title: task.label }, /rebuild/.test(task.label) ? "Rebuilding…" : "Starting…")];
  const rebuild = h("button", { class: "small ghost", title: `Rebuild Container: a new container from ${CAT.devcontainers[side]}`,
    onclick: () => rebuildContainer(side) }, "Rebuild");
  if (c.state === "running") {
    return [h("button", { class: "small ghost", title: "Ctrl-C every ROS process the dashboard started in this container",
      onclick: async () => { if (await confirmBox(`Ctrl-C every process the dashboard started in the ${side} container?`, "Stop all")) act("/api/container", { side, action: "stop_ros" }, "Sent Ctrl-C"); } }, "Stop all"), rebuild];
  }
  const start = h("button", { class: "small", onclick: () => { openDrawer(true); act("/api/container", { side, action: "start" }, `Starting the ${side} container`); } }, "Start");
  return c.state === "missing" ? [start] : [start, rebuild];  // Start creates a missing one
}

async function rebuildContainer(side) {
  const ok = await confirmBox(`Rebuild the ${side} dev container?\n\n` +
    `Like VS Code's Rebuild Container: removes the container, builds its image again from ${CAT.devcontainers[side]} ` +
    "(Docker's cache keeps the slow layers) and creates a new one. Every process in it stops, and a VS Code window " +
    "attached to it disconnects. The workspace, its builds and pixi environment stay.", "Rebuild");
  if (!ok) return;
  openDrawer(true); setTab(side);
  act("/api/container", { side, action: "rebuild" }, `Rebuilding the ${side} container`);
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
    s.ground_enabled === false
      ? sysRow("Ground station", "", "no container here: rover only, gamepad from this browser", "")
      : sysRow("Ground container · x86", CSTATE[gc.state] || "", [gc.error || gc.name || gc.state || "", mem(gh)].filter(Boolean).join(" · "), "",
        ...containerButtons("ground", gc, true)),
  ];
  for (const name of CAT.services) {
    const st = (r.services || {})[name];
    const label = /scm/.test(name) ? "Science GDS service" : /gds/.test(name) ? "RCM GDS service" : name;
    const detail = st === "not-found" ? `${name} · no such unit on the rover (rover.services)` : `${name} · ${st || "?"}`;
    rows.push(sysRow(label,
      st === "active" ? "ok" : st ? "bad" : "", up ? detail : name, "",
      up ? h("button", { class: "small ghost", onclick: async () => {
        if (await confirmBox(`Restart ${name} on the rover?\n(sudo systemctl restart; needs the sudo rule from make setup-dash)`, "Restart")) act("/api/service", { name, action: "restart" }, `Restarting ${name}`);
      } }, "Restart") : null));
  }
  $("#sysBody").replaceChildren(...rows);
}

// ------------------------------------------------------------------ build
// `pixi run build` (colcon build --symlink-install over ros2_ws/src) in one or both dev
// containers. The commands come from the server (POST /api/build with preview), so they show the
// exact flags, e.g. the Jetson's low-RAM --executor sequential.
let BUILD = { cmds: {}, err: "" };
function buildBody() {
  return { packages: $("#buildPkgs").value.trim(), deps: $("#buildDeps").checked };
}
function renderBuild() {
  $$("[data-build]").forEach((b) => {
    b.hidden = !CAT.ground && b.dataset.build !== "rover";
    b.onclick = () => buildAction(b.dataset.build);
  });
  $("#buildPkgs").value = store.get("buildPkgs", "");
  $("#buildDeps").checked = store.get("buildDeps", false);
  $("#buildPkgs").oninput = (e) => { store.set("buildPkgs", e.target.value); scheduleBuildPreview(); };
  $("#buildDeps").onchange = (e) => { store.set("buildDeps", e.target.checked); scheduleBuildPreview(); };
  buildPreview();
}
let buildT = null;
function scheduleBuildPreview() { clearTimeout(buildT); buildT = setTimeout(buildPreview, 250); }
async function buildPreview() {
  try { BUILD = { cmds: (await api("/api/build", { side: "both", ...buildBody(), preview: true })).commands, err: "" }; }
  catch (e) { BUILD = { cmds: {}, err: e.message }; }
  $("#buildPkgs").classList.toggle("bad", !!BUILD.err);
  updateBuild();
}
const BUILD_DOT = { running: "busy", starting: "busy", stopping: "busy", exited: "ok", failed: "bad", stopped: "" };
function updateBuild() {
  const box = $("#buildCmds");
  if (!box) return;
  if (BUILD.err) { box.replaceChildren(h("div", { class: "err", text: BUILD.err })); return; }
  const procs = allProcs();
  box.replaceChildren(...Object.entries(BUILD.cmds).map(([side, cmd]) => {
    const p = procs.filter((q) => q.side === side && q.id === "build").pop();
    const when = p && p.ended ? " · " + new Date(p.ended * 1000).toLocaleTimeString() : "";
    const st = !p ? "" : ALIVE.has(p.state) ? "building…" : p.state === "exited" ? `done${when}`
      : `${p.state}${p.rc != null ? " · exit " + p.rc : ""}${when}`;
    return h("div", { class: "build-cmd", title: p ? p.label : "" },
      h("span", { class: "dot " + (p ? BUILD_DOT[p.state] || "" : "") }),
      h("span", {}, h("span", { class: "w", text: side === "rover" ? "Rover · l4t" : "Ground · x86" }),
        st ? h("span", { class: "st", text: " · " + st }) : null),
      h("code", { text: cmd }));
  }));
}
async function buildAction(side) {
  const job = STATE && STATE.job;
  if (job && JOB_ACTIVE.has(job.state) && !await confirmBox(`${job.title} is ${job.state}.\n\n` +
    "Building now replaces files its nodes use (run the mode again afterwards to load them), and on the " +
    "Jetson it competes with them for RAM. Build anyway?", "Build")) return;
  try {
    await api("/api/build", { side, ...buildBody() });
    toast(`Building on ${side === "both" ? "both machines" : side === "rover" ? "the rover" : "the ground station"}`);
    openDrawer(true); setTab(side === "both" ? "all" : side);
    pollState();
  } catch (e) { toast(e.message, true); }
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
  $("#canArmIface").value = store.get("canArmIface", CAT.can_arm_default_iface);
  $("#canArmBitrate").value = store.get("canArmBitrate", String(CAT.can_arm_default_bitrate));
  $("#canArmIface").oninput = (e) => store.set("canArmIface", e.target.value.trim());
  $("#canArmBitrate").oninput = (e) => store.set("canArmBitrate", e.target.value.trim());
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

async function canArmAction(action) {
  const iface = $("#canArmIface").value.trim();
  if (action === "down" && !await confirmBox(`Take ${iface} down? A running arm loses its bus.`, "Take down")) return;
  try {
    await api("/api/can", { action, iface, bitrate: action === "up" ? $("#canArmBitrate").value.trim() : null });
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

// ------------------------------------------------------------------ browser gamepad
// For "Gamepad on: This browser": the page reads the controller (USB or Bluetooth, no
// container needed, so a Mac works) and sends it to the rover's gamepad relay at 20 Hz, in the
// Linux joy_node layout teleop/config/joystick.yaml is written for:
//   axes    LX LY LT RX RY RT dpadX dpadY   (stick up / left = +1, trigger released = +1)
//   buttons A B X Y LB RB back start guide LS RS
// It stops sending when the tab is hidden or the pad disconnects; the rover then zeroes /joy.
const PAD = { sending: false, error: "", lastOk: 0, inflight: false, name: "" };
const DEADZONE = 0.15;

function dz(v) {
  const a = Math.abs(v);
  return a < DEADZONE ? 0 : Math.sign(v) * (a - DEADZONE) / (1 - DEADZONE);
}
function pad() {
  const pads = [...(navigator.getGamepads ? navigator.getGamepads() : [])].filter(Boolean);
  return pads.find((p) => p.mapping === "standard") || pads[0] || null;
}
function joyState(p) {
  const ax = (i) => dz(p.axes[i] || 0);
  const bt = (i) => (p.buttons[i] && p.buttons[i].pressed ? 1 : 0);
  const trig = (i) => 1 - 2 * ((p.buttons[i] && p.buttons[i].value) || 0);
  const axes = [-ax(0), -ax(1), trig(6), -ax(2), -ax(3), trig(7), bt(14) - bt(15), bt(12) - bt(13)];
  const buttons = [bt(0), bt(1), bt(2), bt(3), bt(4), bt(5), bt(8), bt(9), bt(16), bt(10), bt(11)];
  return { axes, buttons };
}
function padWanted() {
  const j = STATE && STATE.job;
  return !!(j && j.gamepad && ["running", "degraded"].includes(j.state));
}
async function padSend(state) {
  if (PAD.inflight) return;
  PAD.inflight = true;
  try { await api("/api/joy", state); PAD.lastOk = Date.now(); PAD.error = ""; }
  catch (e) { PAD.error = e.message; }
  finally { PAD.inflight = false; }
}
const ZERO = { axes: [0, 0, 1, 0, 0, 1, 0, 0], buttons: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] };
function padTick() {
  const p = pad();
  PAD.name = p ? p.id.replace(/\s*\(.*$/, "") : "";
  const go = padWanted() && p && !document.hidden;
  if (go) {
    PAD.sending = true;
    PAD.state = joyState(p);
    padSend(PAD.state);
  } else if (PAD.sending) {
    PAD.sending = false; PAD.state = null;
    padSend(ZERO);  // deadman released
  }
  renderPad();
}
// Teleop · Arm: the joint the gamepad has selected (B / X cycle), from the teleop's own log lines.
function renderArm() {
  const box = $("#armBox");
  if (!box) return;
  const j = STATE && STATE.job;
  const a = (STATE && STATE.arm) || {};
  const running = j && j.mode === "teleop-arm" && ["starting", "running", "degraded"].includes(j.state);
  box.hidden = !(running && a.joint);
  if (box.hidden) return;
  const state = a.locked ? ["drive mode (RT): arm locked", "warn"]
    : a.jogging ? ["jogging", "ok"] : ["holding", ""];
  box.replaceChildren(
    h("span", { class: "muted", text: "Arm" }),
    h("b", { text: a.joint }),
    a.count ? h("span", { class: "muted", text: `${a.index}/${a.count} · ${a.driver}` }) : null,
    h("span", { class: "arm-pos", text: a.pos && a.pos !== "?" ? `${a.pos} rad` : "no position yet" }),
    h("span", { class: "lb" + (state[1] === "ok" ? " on" : ""), text: state[0] }),
    a.solenoid !== undefined ? h("span", { class: "lb" + (a.solenoid ? " on" : ""), text: a.solenoid ? "solenoid ON" : "solenoid off" }) : null,
    h("span", { class: "muted arm-hint", text: "B / X: next / previous joint · LT + stick: jog · LT + A: solenoid" }));
}

function renderPad() {
  const box = $("#padBox");
  if (!box) return;
  const want = padWanted();
  const opts = (STATE && STATE.job && STATE.job.options) || {};
  const mode = modeById(selected);
  const shown = want || optValues(mode).gamepad === "browser" || opts.gamepad === "browser";
  box.hidden = !shown;
  if (!shown) return;
  const st = PAD.state;
  // Deadmen: RT (axis 5) drives, LT (axis 2) jogs the arm; held below -0.5 like the teleops.
  const lt = st && st.axes[2] < -0.5;
  const rt = st && st.axes[5] < -0.5;
  const bar = (v) => h("span", { class: "bar" }, h("i", { style: `height:${Math.round(Math.abs(v) * 100)}%;${v < 0 ? "top:50%" : "bottom:50%"}` }));
  const live = PAD.sending && Date.now() - PAD.lastOk < 500;
  box.replaceChildren(
    h("span", { class: "dot " + (!PAD.name ? "bad" : live ? "ok" : want ? "warn" : "") }),
    h("b", { text: PAD.name || "No gamepad" }),
    h("span", { class: "muted", text: !PAD.name ? "plug it in (or pair it) and press any button"
      : !want ? "sends to the rover while the mode runs"
      : PAD.error ? PAD.error : document.hidden ? "tab hidden: not sending" : live ? "sending" : "connecting…" }),
    st ? h("span", { class: "sticks" }, bar(st.axes[1] / 2), bar(st.axes[4] / 2),
      h("span", { class: "lb" + (lt ? " on" : ""), text: "LT" }),
      h("span", { class: "lb" + (rt ? " on" : ""), text: "RT" })) : null);
}
window.addEventListener("gamepadconnected", padTick);
window.addEventListener("gamepaddisconnected", padTick);
document.addEventListener("visibilitychange", padTick);

// ------------------------------------------------------------------ polling
let stateBusy = false;
async function pollState() {
  if (stateBusy) return;
  stateBusy = true;
  try {
    STATE = await api("/api/state");
    renderHeader(); renderSystems(); updateModeBadges(); updateJobUI(); renderProcs();
    updateStateMachine(); updateBuild(); renderArm();
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
  renderQuick(); renderModes(); renderStateMachine(); renderPanel(); renderCan(); renderBuild();
  await pollState();
  setInterval(pollState, 1500);
  setInterval(padTick, 50);
  pullLogs();
  $$("#outTabs button").forEach((b) => (b.onclick = () => setTab(b.dataset.side)));
  setTab(tab);
  $("#drawerToggle").onclick = () => openDrawer(!$("#drawer").classList.contains("open"));
  $("#outBig").onclick = () => { $("#drawer").classList.toggle("big"); document.body.classList.toggle("drawer-big", $("#drawer").classList.contains("big")); if (!$("#drawer").classList.contains("open")) openDrawer(true); };
  $("#outClear").onclick = () => { LINES.length = 0; renderOut(); };
  $("#procFilter").onchange = (e) => { procFilter = e.target.value; renderProcs(); renderOut(); };
  $$("[data-can]").forEach((b) => (b.onclick = () => canAction(b.dataset.can)));
  $$("[data-can-arm]").forEach((b) => (b.onclick = () => canArmAction(b.dataset.canArm)));
  openDrawer(store.get("drawer", false));
  setTimeout(() => $("#splash").classList.add("gone"), Math.max(0, 1300 - (Date.now() - t0)));
}

boot();
