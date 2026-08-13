/* PalIntel console.
 *
 * Vanilla, no build step, no CDN. The bot is local-first because a network dependency in
 * the hot path is a failure mode; a console for it should not quietly acquire one.
 *
 * The token rides in a header rather than a cookie: cookies are attached by the browser
 * to every request to the origin, which is exactly the property that makes a cross-site
 * request dangerous against a control surface. See server.py.
 */
const TOKEN = document.documentElement.dataset.token;

async function api(path) {
  const r = await fetch(path, { headers: { "X-PalIntel-Token": TOKEN } });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

/* --- formatting -----------------------------------------------------------
 * Small units matter here: a session costs half a cent, and rendering that as
 * "$0.00" reads as "nothing was spent" when the whole point is that it accumulates. */
const money = (usd) => (usd >= 1 ? `$${usd.toFixed(2)}` : `$${usd.toFixed(4)}`);
const ms = (v) => (v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`);

function ago(seconds) {
  if (seconds == null) return "unknown";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

const when = (epoch) =>
  epoch ? new Date(epoch * 1000).toLocaleString(undefined,
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

function rows(pairs) {
  const wrap = el("div", "rows");
  for (const [k, v, cls] of pairs) {
    const r = el("div", "row");
    r.append(el("span", "row-key", k));
    const val = el("span", `row-val${cls ? " " + cls : ""}`);
    if (v instanceof Node) val.append(v); else val.textContent = v;
    r.append(val);
    wrap.append(r);
  }
  return wrap;
}

/* --- views ---------------------------------------------------------------- */

document.querySelectorAll(".rail-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".rail-item").forEach((b) => b.classList.remove("is-active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("is-active"));
    btn.classList.add("is-active");
    $(`#view-${btn.dataset.view}`).classList.add("is-active");
    if (btn.dataset.view === "sessions") loadSessions();
  });
});

/* --- status --------------------------------------------------------------- */

async function loadOverview() {
  let data;
  try {
    data = await api("/api/overview");
  } catch (e) {
    $("#p-latency").textContent = `could not read: ${e.message}`;
    return;
  }

  /* Latency, against the budgets. The bar is allowed to look bad, because it IS bad:
     the voice p95 is a Phase 1 exit criterion and it has never passed. */
  const lat = $("#p-latency");
  lat.textContent = "";
  const kinds = data.latency.kinds;
  const names = Object.keys(kinds).filter((k) => kinds[k]);
  if (!names.length) {
    lat.append(el("p", "loading",
      "No timings recorded yet. They persist from 2026-08-13 onward — run a session."));
  } else {
    for (const name of names) {
      const p = kinds[name];
      const budget = data.latency.budgets[name];
      const g = el("div", "gauge");
      const head = el("div", "gauge-head");
      head.append(el("span", "gauge-name", name));
      const over = budget && p.p95 > budget;
      head.append(el("span", `gauge-fig ${budget ? (over ? "over" : "under") : ""}`,
        `p50 ${ms(p.p50)} · p95 ${ms(p.p95)}`));
      g.append(head);

      /* Scaled to whichever is larger so an over-budget bar reads as over-budget
         rather than simply full. */
      const ceiling = Math.max(p.p95, budget || 0) * 1.15;
      const track = el("div", "gauge-track");
      const fill = el("div", `gauge-fill${over ? " over" : ""}`);
      fill.style.width = `${Math.min(100, (p.p95 / ceiling) * 100)}%`;
      track.append(fill);
      if (budget) {
        const mark = el("div", "gauge-budget");
        mark.style.left = `${(budget / ceiling) * 100}%`;
        mark.title = `budget ${ms(budget)}`;
        track.append(mark);
      }
      g.append(track);
      g.append(el("div", "gauge-sub",
        budget
          ? `n=${p.n} · budget ${ms(budget)} · ${over ? "OVER" : "within"}`
          : `n=${p.n} · no stated budget`));
      lat.append(g);
    }
  }

  /* Spend. */
  const sp = $("#p-spend");
  sp.textContent = "";
  const s = data.spend;
  const big = el("div", "big", money(s.usd));
  big.append(el("span", "big-unit", "all time"));
  sp.append(big);
  const share = s.queries ? `${s.billed}/${s.queries} reached the model` : "no queries yet";
  sp.append(rows([
    ["queries", share],
    ["sessions", String(data.sessions)],
  ]));
  if (s.by_user.length > 1) {
    const t = el("table");
    t.innerHTML = "<thead><tr><th>who</th><th class='num'>q</th>"
      + "<th class='num'>model</th><th class='num'>usd</th></tr></thead>";
    const tb = el("tbody");
    for (const u of s.by_user) {
      const tr = el("tr");
      tr.append(el("td", "", u.key));
      tr.append(el("td", "num", String(u.queries)));
      tr.append(el("td", "num", String(u.billed)));
      tr.append(el("td", "num", money(u.usd)));
      tb.append(tr);
    }
    t.append(tb);
    // `Node.append()` returns undefined, so this cannot be chained - doing so threw and
    // silently cost the whole per-user panel.
    const box = el("div", "scroll-x");
    box.append(t);
    sp.append(box);
  }

  $("#t-bot").textContent = data.bot.reachable ? "connected" : "not connected";
  $("#t-bot").className = "slot-value" + (data.bot.reachable ? " is-good" : "");
  $("#t-bot").title = data.bot.note;
}

async function loadSave() {
  const btn = $("#refresh");
  btn.classList.add("is-busy");
  let d;
  try {
    d = await api("/api/save");
  } catch (e) {
    $("#p-world").textContent = `could not read: ${e.message}`;
    btn.classList.remove("is-busy");
    return;
  }
  btn.classList.remove("is-busy");

  if (!d.ok) {
    $("#t-world").textContent = "unreadable";
    $("#t-world").className = "slot-value is-bad";
    $("#p-world").innerHTML = `<p class="bad">${d.error}</p>`;
    return;
  }

  /* World. */
  const w = d.world;
  $("#t-world").textContent = w ? (w.name || w.id.slice(0, 8)) : "none found";
  const pw = $("#p-world");
  pw.textContent = "";
  if (!w) {
    pw.append(el("p", "loading", "No world with a Level.sav was found."));
  } else {
    pw.append(rows([
      ["name", w.name || "(unnamed)"],
      ["host", `${w.host || "?"}${w.host_level != null ? `  ·  level ${w.host_level}` : ""}`],
      ["in-game day", w.day != null ? String(w.day) : "—"],
      ["picked", d.auto ? "automatically — newest write" : "pinned in config"],
      ["directory", el("code", "", w.id)],
    ]));
  }

  /* Freshness. The gate that refuses a stale position is the reason this is prominent. */
  const fresh = $("#t-fresh");
  fresh.textContent = d.position_age == null ? "no position" : `${ago(d.position_age)} ago`;
  fresh.className = "slot-value" + (d.position_usable ? " is-good" : " is-stale");
  fresh.title = d.position_usable
    ? "fresh enough to answer “nearest”"
    : `older than ${ago(d.max_position_age)} — “nearest” falls back to cluster size`;

  /* Players. */
  const pp = $("#p-players");
  pp.textContent = "";
  if (!d.players.length) {
    pp.append(el("p", "loading", "No players read yet."));
  } else {
    const t = el("table");
    t.innerHTML = "<thead><tr><th>player</th><th class='num'>lvl</th>"
      + "<th class='num'>tech</th><th class='num'>pts</th>"
      + "<th class='num'>pals</th><th>position</th></tr></thead>";
    const tb = el("tbody");
    for (const p of d.players) {
      const tr = el("tr");
      tr.append(el("td", "", p.name || p.uid.slice(0, 8)));
      const lvl = el("td", "num");
      lvl.textContent = p.level != null ? String(p.level) : "—";
      lvl.title = p.level != null
        ? "stated by LevelMeta.sav (host only)"
        : "not stated for a joining player; Q6 infers a floor";
      tr.append(lvl);
      tr.append(el("td", "num", String(p.technologies)));
      tr.append(el("td", "num",
        `${p.points ?? "—"}/${p.ancient_points ?? "—"}`));
      tr.append(el("td", "num", p.roster != null ? String(p.roster) : "—"));
      tr.append(el("td", "",
        p.coords ? `${p.coords[0].toFixed(0)}, ${p.coords[1].toFixed(0)}`
                 : "withheld — stale"));
      tb.append(tr);
    }
    t.append(tb);
    const box = el("div", "scroll-x");
    box.append(t);
    pp.append(box);
    if (d.players.length > 1) {
      pp.append(el("p", "panel-note",
        "Two players: each is answered from their own save. An unbound speaker gets "
        + "world-scoped answers rather than somebody else's."));
    }
  }

  /* Integrity — the cross-checks, which is the part of this project worth surfacing. */
  const pi = $("#p-integrity");
  pi.textContent = "";
  const cc = d.camp_check;
  pi.append(rows([
    ["roster, world", d.roster_world != null ? `${d.roster_world} species` : "not read"],
    ["guild-shared", d.roster_shared != null ? `${d.roster_shared} species` : "—"],
    ["base camps", d.base_camps ? `${d.base_camps.length} located` : "not read"],
    ["camp cross-check",
      cc ? cc.describe : "not run",
      cc ? (cc.agrees ? "" : "bad") : ""],
    ["position gate",
      d.position_usable
        ? `offering it — within ${ago(d.max_position_age)}`
        : `withholding it — save is ${ago(d.position_age)} old`,
      d.position_usable ? "" : "bad"],
  ]));
}

/* --- sessions ------------------------------------------------------------- */

let sessionsLoaded = false;

async function loadSessions() {
  if (sessionsLoaded) return;
  sessionsLoaded = true;
  const list = $("#session-list");
  let items;
  try {
    items = await api("/api/sessions");
  } catch (e) {
    list.innerHTML = `<li class="bad">${e.message}</li>`;
    return;
  }
  list.textContent = "";
  if (!items.length) {
    list.innerHTML = '<li class="loading">No sessions on disk yet.</li>';
    return;
  }
  for (const s of items) {
    const li = el("li");
    const b = el("button", "session-item");
    b.type = "button";
    b.append(el("span", "session-when", when(s.started)));
    const bits = [];
    if (s.utterances) bits.push(`${s.utterances} utt`);
    if (s.clips) bits.push(`${s.clips} clips`);
    if (s.labelled) bits.push(`${s.labelled} labelled`);
    if (s.usd) bits.push(money(s.usd));
    b.append(el("span", "session-meta", bits.join(" · ") || "empty"));
    b.addEventListener("click", () => {
      document.querySelectorAll(".session-item").forEach((x) => x.classList.remove("is-active"));
      b.classList.add("is-active");
      openSession(s.session);
    });
    li.append(b);
    list.append(li);
  }
}

async function openSession(id) {
  const pane = $("#session-detail");
  pane.innerHTML = '<p class="loading">reading…</p>';
  let d;
  try {
    d = await api(`/api/sessions/${encodeURIComponent(id)}`);
  } catch (e) {
    pane.innerHTML = `<p class="bad">${e.message}</p>`;
    return;
  }
  pane.textContent = "";
  pane.append(el("h2", "pane-title", `${id} · ${when(d.started)}`));

  const latKinds = Object.entries(d.latency).filter(([, v]) => v);
  pane.append(rows([
    ["utterances", String(d.utterances.length)],
    ["spend", d.charges.queries
      ? `${money(d.charges.usd)} · ${d.charges.billed}/${d.charges.queries} reached the model`
      : "not logged"],
    ["timings", latKinds.length
      ? latKinds.map(([k, v]) => `${k} p95 ${ms(v.p95)}`).join(" · ")
      : "not logged"],
  ]));

  if (!d.utterances.length) {
    pane.append(el("p", "empty-note",
      "No utterances captured. Voice capture is a flag — `[capture] enabled` — and a "
      + "session can hold a bill and no clips."));
    return;
  }

  const t = el("table");
  t.innerHTML = "<thead><tr><th>heard</th><th>routed to</th><th>path</th>"
    + "<th>label</th><th>clip</th></tr></thead>";
  const tb = el("tbody");
  for (const u of d.utterances) {
    const tr = el("tr");

    const heard = el("td");
    heard.append(el("span", "utterance-heard", u.heard || "(nothing)"));
    if (u.note) heard.append(el("span", "note", u.note));
    tr.append(heard);

    const tool = el("td");
    tool.textContent = u.tool || "—";
    if (u.entity) {
      tool.append(el("span", "row-val"));
      tool.lastChild.innerHTML = ` <span class="sub">${u.entity}</span>`;
    }
    tr.append(tool);

    const path = el("td");
    const cls = u.outcome === "declined" ? "tag-decline"
      : u.path === "model" ? "tag-model" : "tag-fast";
    path.append(el("span", `tag ${cls}`, u.outcome === "declined" ? "decline" : (u.path || "?")));
    tr.append(path);

    /* `auto` is the router's own opinion and is deliberately not shown as a verdict -
       treating it as one is how a consistent bug ratifies itself in its own corpus. */
    const label = el("td");
    if (u.label && u.label !== "auto") {
      label.append(el("span", "tag tag-human", u.label));
    } else {
      label.textContent = "—";
    }
    tr.append(label);

    const clip = el("td");
    if (u.has_clip) {
      const a = document.createElement("audio");
      a.controls = true;
      a.preload = "none";
      a.src = `/api/sessions/${encodeURIComponent(d.session)}/clip/`
        + `${encodeURIComponent(u.uid)}?token=${encodeURIComponent(TOKEN)}`;
      clip.append(a);
    } else {
      clip.textContent = "—";
    }
    tr.append(clip);

    tb.append(tr);
  }
  t.append(tb);
  const box = el("div", "scroll-x");
  box.append(t);
  pane.append(box);
}

/* --- boot ----------------------------------------------------------------- */

$("#refresh").addEventListener("click", () => { loadSave(); loadOverview(); });
loadOverview();
loadSave();
