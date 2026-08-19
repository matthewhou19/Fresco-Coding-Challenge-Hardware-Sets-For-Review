/* Viewer for extracted hardware sets.
 *
 * The whole point of the UI is the location claim: click a set, see the box
 * it says it lives in drawn on the page; click a component row, see the lines
 * its anchors point at. Boxes are positioned in percent of the page size in
 * PDF points, so they line up at any zoom without knowing the image size.
 */

const PAD = 2;                          // points of breathing room around a box
const ZOOMS = [1, 1.5, 2, 3, 4];        // page widths, as a multiple of the pane
const el = (id) => document.getElementById(id);

const state = {
  streams: [],
  stream: null,         // payload of the selected stream
  set: null,            // selected set record
  page: null,           // page currently displayed
  comp: -1,             // highlighted component row, -1 for none
  showAll: false,
  zoom: 0,              // index into ZOOMS; dense table books need 2x or more
};

const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const api = (path) => fetch(path).then((r) => {
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
});

/* ---------- boot ---------- */

async function loadStreamList() {
  state.streams = await api("/api/streams");
  el("stream-select").innerHTML = state.streams.map((s) =>
    `<option value="${esc(s.id)}">${s.source === "upload" ? "⬆ " : ""}` +
    `${esc(s.project)} — ${esc(s.file)} (p${s.region[0]}–${s.region[1]})</option>`).join("");
}

async function init() {
  const health = await api("/api/health");
  await loadStreamList();

  const sel = el("stream-select");
  sel.onchange = () => loadStream(sel.value);

  el("upload-btn").onclick = () => el("upload-input").click();
  el("delete-btn").onclick = deleteUpload;
  el("upload-input").onchange = (e) => startUpload(e.target.files[0]);
  el("job-close").onclick = () => { el("job-panel").hidden = true; };
  el("set-filter").oninput = renderSetList;
  el("show-all").onchange = (e) => { state.showAll = e.target.checked; drawBoxes(); };
  el("zoom-in").onclick = () => setZoom(state.zoom + 1);
  el("zoom-out").onclick = () => setZoom(state.zoom - 1);

  const want = new URLSearchParams(location.hash.slice(1));
  const first = want.get("stream") && state.streams.some((s) => s.id === want.get("stream"))
    ? want.get("stream") : state.streams[0].id;
  sel.value = first;
  await loadStream(first, Number(want.get("set")) || null);
  el("stream-stats").textContent =
    `${health.n_streams} specbooks · ${health.n_sets} sets · ${health.n_components} components`;
}

async function loadStream(id, wantSeq = null) {
  state.stream = await api(`/api/streams/${id}`);
  el("export-link").href = `/api/streams/${id}/export.json`;
  const del = el("delete-btn");
  del.hidden = state.stream.source !== "upload";
  delete del.dataset.armed;
  del.textContent = "Delete this upload";
  renderSetList();
  const sets = state.stream.sets;
  selectSet(sets.find((s) => s.seq === wantSeq) || sets[0]);
}

/* ---------- set list ---------- */

function renderSetList() {
  const q = el("set-filter").value.trim().toLowerCase();
  const list = el("set-list");
  const rows = state.stream.sets.filter((s) => !q ||
    (`${s.set_number} ${s.description ?? ""} ${s.header_text ?? ""}`).toLowerCase().includes(q));

  list.innerHTML = rows.map((s) => `
    <li data-seq="${s.seq}"${state.set && s.seq === state.set.seq ? ' class="on"' : ""}>
      <span class="no">${esc(s.set_number)}</span>
      <span class="txt">${esc(s.description || s.header_text || "")}</span>
      <span class="cnt">${s.empty ? "—" : s.n_components}</span>
    </li>`).join("") ||
    '<li class="empty-state">no set matches</li>';

  list.querySelectorAll("li[data-seq]").forEach((li) => {
    li.onclick = () => selectSet(
      state.stream.sets.find((s) => s.seq === Number(li.dataset.seq)));
  });
}

function selectSet(set) {
  if (!set) return;
  state.set = set;
  state.comp = -1;
  location.hash = `stream=${state.stream.id}&set=${set.seq}`;
  renderSetList();
  renderHead();
  renderComponents();
  renderExtras();
  showPage(set.location.length ? set.location[0].page : null);
}

/* Deleting an upload takes its whole sub-tree with it -- PDF, step output and
 * LLM cache -- so uploading the same book again is a cold run, not a replay.
 * The confirmation is inline rather than a browser dialog: a native confirm()
 * freezes the page, which is the last thing you want mid-demo. */
function armDelete() {
  const btn = el("delete-btn");
  if (btn.dataset.armed) {
    delete btn.dataset.armed;
    btn.textContent = "Delete this upload";
    return;
  }
  btn.dataset.armed = "1";
  btn.textContent = "Confirm delete";
  setTimeout(() => {
    if (btn.dataset.armed) {
      delete btn.dataset.armed;
      btn.textContent = "Delete this upload";
    }
  }, 10000);   // long enough to be a deliberate second click, short enough to forget
}

async function deleteUpload() {
  const st = state.stream;
  if (st.source !== "upload") return;
  const btn = el("delete-btn");
  if (!btn.dataset.armed) return armDelete();
  delete btn.dataset.armed;
  btn.textContent = "deleting…";

  const r = await fetch(`/api/jobs/${st.job_id}`, { method: "DELETE" });
  btn.textContent = "Delete this upload";
  if (!r.ok) {
    el("stream-stats").textContent = `could not delete: ${(await r.json()).detail || r.status}`;
    return;
  }
  el("job-panel").hidden = true;
  await loadStreamList();
  const next = state.streams[0].id;
  el("stream-select").value = next;
  await loadStream(next);
  const h = await api("/api/health");
  el("stream-stats").textContent =
    `${h.n_streams} specbooks · ${h.n_sets} sets · ${h.n_components} components`;
}

/* ---------- header ---------- */

function renderHead() {
  const s = state.set;
  const st = state.stream;
  const loc = s.location.map((l) =>
    `p${l.page} lines ${l.lines[0]}–${l.lines[1]}`).join(" · ");
  const chips = [];
  if (s.empty) chips.push('<span class="chip empty">no components</span>');
  if (st.duplicate_of) chips.push(
    `<span class="chip dup" title="same region as ${esc(st.duplicate_of)}">duplicate book</span>`);
  s.flags.forEach((f) => chips.push(`<span class="chip flag">${esc(f)}</span>`));

  el("set-head").innerHTML = `
    <h1>Set ${esc(s.set_number)}</h1>
    ${s.description ? `<span>${esc(s.description)}</span>` : ""}
    ${chips.join(" ")}
    <span class="loc">${esc(loc)}</span>`;
}

/* ---------- components ---------- */

const dot = (level, what) => level
  ? `<i class="dot ${esc(level)}" title="${esc(what)} confidence: ${esc(level)}"></i>` : "";

function renderComponents() {
  const s = state.set;
  if (!s.components.length) {
    el("components").innerHTML = `<h2>components</h2>
      <p class="empty-state">${esc(s.header_text || "no components in this set")}</p>`;
    return;
  }
  el("components").innerHTML = `<h2>components (${s.components.length})</h2>
    <table><thead><tr>
      <th>qty</th><th>description</th><th>catalog no.</th>
      <th>mfr</th><th>finish</th><th>notes</th><th></th>
    </tr></thead><tbody>
    ${s.components.map((c, i) => `<tr data-i="${i}">
      <td class="qty">${c.qty === null ? '<span class="null">—</span>' : esc(c.qty)}
        ${c.unit ? `<span class="note">${esc(c.unit)}</span>` : ""}</td>
      <td>${esc(c.description)}</td>
      <td class="code">${c.catalog_number ? esc(c.catalog_number) : '<span class="null">—</span>'}</td>
      <td class="mfr">${c.mfr ? esc(c.mfr) : '<span class="null">—</span>'}${dot(c.confidence.mfr, "mfr")}</td>
      <td class="fin">${c.finish ? esc(c.finish) : '<span class="null">—</span>'}${dot(c.confidence.finish, "finish")}</td>
      <td class="note">${esc(c.notes.join(" · "))}</td>
      <td><span class="chip ${esc(c.confidence.assembly)}">${esc(c.confidence.assembly)}</span></td>
    </tr>`).join("")}
    </tbody></table>`;

  el("components").querySelectorAll("tr[data-i]").forEach((tr) => {
    tr.onclick = () => pickComponent(Number(tr.dataset.i));
  });
}

function pickComponent(i) {
  state.comp = state.comp === i ? -1 : i;
  el("components").querySelectorAll("tr[data-i]").forEach((tr) => {
    tr.classList.toggle("on", Number(tr.dataset.i) === state.comp);
  });
  const boxes = state.comp < 0 ? [] : state.set.components[state.comp].boxes;
  if (boxes.length && boxes[0].page !== state.page) showPage(boxes[0].page);
  else drawBoxes();
}

/* ---------- doors, notes, demoted ---------- */

function renderExtras() {
  const s = state.set;
  const block = (title, items) => items.length
    ? `<h2>${title} (${items.length})</h2><ul class="plain">${items.join("")}</ul>` : "";

  el("extras").innerHTML = [
    block("doors", s.doors.map((d) => `<li>${esc(d.text)}</li>`)),
    block("set notes", s.set_notes.map((n) => `<li>${esc(n.text)}</li>`)),
    block("properties", s.properties.map((p) => `<li>${esc(p.text ?? JSON.stringify(p))}</li>`)),
    block("lines kept out of components", s.demoted.map((d) =>
      `<li class="demoted">${esc(d.text)}<span class="why">${esc(d.reason)}</span></li>`)),
  ].join("");
}

/* ---------- page image + boxes ---------- */

function showPage(page) {
  state.page = page;
  const s = state.set;
  const pages = [...new Set(s.location.map((l) => l.page))];

  el("page-tabs").innerHTML = pages.map((p) =>
    `<button data-p="${p}"${p === page ? ' class="on"' : ""}>page ${p}</button>`).join("");
  el("page-tabs").querySelectorAll("button").forEach((b) => {
    b.onclick = () => showPage(Number(b.dataset.p));
  });

  const img = el("page-img");
  const dim = state.stream.pages[page];
  if (!page || !dim) {
    img.removeAttribute("src");
    el("page-note").textContent = "this set has no page location";
    return;
  }
  el("page-wrap").style.aspectRatio = `${dim.width} / ${dim.height}`;
  const src = `/api/streams/${state.stream.id}/pages/${page}.png`;
  if (img.getAttribute("src") !== src) img.src = src;
  el("page-note").textContent =
    `${state.stream.file} · page ${page} · rendered at ${state.stream.render_scale}×`;
  drawBoxes();
}

function setZoom(i) {
  state.zoom = Math.max(0, Math.min(ZOOMS.length - 1, i));
  const z = ZOOMS[state.zoom];
  el("page-wrap").style.width = `${z * 100}%`;
  el("zoom-level").textContent = `${z * 100}%`;
}

function drawBoxes() {
  const dim = state.stream.pages[state.page];
  const overlay = el("overlay");
  if (!dim) { overlay.innerHTML = ""; return; }

  const place = (bbox, cls, tag = "") => {
    const [x0, top, x1, bottom] = bbox;
    const style = `left:${((x0 - PAD) / dim.width) * 100}%;` +
      `top:${((top - PAD) / dim.height) * 100}%;` +
      `width:${((x1 - x0 + PAD * 2) / dim.width) * 100}%;` +
      `height:${((bottom - top + PAD * 2) / dim.height) * 100}%`;
    return `<div class="box ${cls}" style="${style}">${tag}</div>`;
  };

  const parts = [];
  if (state.showAll) {
    state.stream.sets.forEach((s) => {
      if (s.seq === state.set.seq) return;
      s.location.filter((l) => l.page === state.page)
        .forEach((l) => parts.push(place(l.bbox, "ghost")));
    });
  }
  state.set.location.filter((l) => l.page === state.page).forEach((l) =>
    parts.push(place(l.bbox, "set", `<span class="tag">set ${esc(state.set.set_number)}</span>`)));

  if (state.comp >= 0) {
    state.set.components[state.comp].boxes
      .filter((b) => b.page === state.page)
      .forEach((b) => parts.push(place(b.bbox, "comp")));
  }
  overlay.innerHTML = parts.join("");
}

/* ---------- upload a new PDF and watch the funnel run ---------- */

const STATE_MARK = { waiting: "·", running: "▸", done: "✓", failed: "✕", skipped: "–" };

async function startUpload(file) {
  if (!file) return;
  el("upload-input").value = "";
  showJob({ filename: file.name, status: "queued", stages: [], log: [], result: {} });
  el("job-log").textContent = `uploading ${(file.size / 2 ** 20).toFixed(1)} MB…`;

  const body = new FormData();
  body.append("file", file);
  const r = await fetch("/api/uploads", { method: "POST", body });
  const data = await r.json();
  if (!r.ok) {
    el("job-log").textContent = `upload refused: ${data.detail || r.status}`;
    return;
  }
  pollJob(data.job.id);
}

async function pollJob(id) {
  const job = await api(`/api/jobs/${id}`);
  showJob(job);
  if (job.status === "queued" || job.status === "running") {
    setTimeout(() => pollJob(id), 1500);
    return;
  }
  if (job.status === "done") {
    await loadStreamList();
    const first = job.result.streams?.[0]?.id;
    if (first) {
      el("stream-select").value = first;
      loadStream(first);
    }
  }
}

function showJob(job) {
  const panel = el("job-panel");
  panel.hidden = false;
  el("job-title").textContent = `${job.filename} — ${job.status}`;
  el("job-stages").innerHTML = (job.stages || []).map((s) =>
    `<li class="${esc(s.state)}">${STATE_MARK[s.state] || "·"} ${esc(s.title)}` +
    `${s.seconds != null ? ` <span class="muted">${s.seconds}s</span>` : ""}</li>`).join("");
  if (job.log?.length) el("job-log").textContent = job.log.join("\n");

  const r = job.result || {};
  let foot = "";
  if (job.status === "done") {
    foot = `<b>${r.n_sets} set${r.n_sets === 1 ? "" : "s"} · ${r.n_components} components</b>` +
      `<span class="muted"> — ${(r.input_tokens || 0).toLocaleString()} in /` +
      ` ${(r.output_tokens || 0).toLocaleString()} out tokens</span>`;
  } else if (job.status === "no_sets") {
    const loc = r.locate || {};
    foot = `<b>no hardware-set region found</b><span class="muted"> — ` +
      `${esc(loc.alarm || "the funnel stopped after step 1")}</span>` +
      (loc.files || []).map((f) => `<div class="muted small">${esc(f.file)}: ` +
        `${f.pages}p, ${f.image_only_pages} image-only, ${f.rejected_regions} region(s) rejected` +
        `${f.warning ? ` — ${esc(f.warning)}` : ""}</div>`).join("");
  } else if (job.status === "error") {
    foot = `<b class="bad">failed</b><span class="muted"> — ${esc(job.error || "")}</span>`;
  }
  el("job-foot").innerHTML = foot;
}

/* Deep links: #stream=<id>&set=<seq> survives a paste into an open tab. */
window.addEventListener("hashchange", () => {
  const want = new URLSearchParams(location.hash.slice(1));
  const id = want.get("stream");
  const seq = Number(want.get("set")) || null;
  if (!id || !state.stream) return;
  if (id !== state.stream.id) {
    el("stream-select").value = id;
    loadStream(id, seq);
  } else if (seq && seq !== state.set?.seq) {
    selectSet(state.stream.sets.find((s) => s.seq === seq));
  }
});

init().catch((e) => {
  document.querySelector("main").innerHTML =
    `<p class="empty-state">could not load: ${esc(e.message)}</p>`;
});
