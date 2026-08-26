const $ = (id) => document.getElementById(id);
let sparkChart = null;

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}
function toast(msg, ok = true) {
  const el = $("toast");
  el.textContent = msg;
  el.className = `fixed bottom-4 right-4 px-4 py-2.5 rounded-xl border text-sm shadow-xl ${ok ? "bg-emerald-900/80 border-emerald-700 text-emerald-100" : "bg-red-900/80 border-red-700 text-red-100"}`;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 3000);
}

async function fetchStats() {
  try {
    const r = await fetch("/api/stats");
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    renderStats(j);
    renderMissions(j.missions || {});
    $("last-update").textContent = new Date().toLocaleTimeString();
    $("health-dot").className = "w-2 h-2 rounded-full bg-emerald-500 animate-pulse";
  } catch (e) {
    $("health-dot").className = "w-2 h-2 rounded-full bg-red-500";
    console.error(e);
  }
}

function renderStats(j) {
  const k = j.kpis || {};
  $("kpi-missions").textContent = k.missions_pending ?? 0;
  $("kpi-credits").textContent = (k.total_credits ?? 0).toLocaleString();
  $("kpi-avg-credits").textContent = k.avg_credits ?? 0;
  $("kpi-water").textContent = k.water_needed ?? 0;
  $("kpi-foam").textContent = k.foam_needed ?? 0;
  $("kpi-vehicles").textContent = (k.total_vehicles ?? 0).toLocaleString();
  $("kpi-types").textContent = k.vehicle_types ?? 0;
  $("kpi-patients").textContent = k.patients ?? 0;

  const c = j.config || {};
  const hiringMap = { "-1": "Automatic", "0": "Disabled", "1": "1 day", "2": "2 days", "3": "3 days" };
  $("kpi-hiring").textContent = hiringMap[String(c.hiring_mode)] ?? c.hiring_mode ?? "—";
  $("kpi-share").textContent = c.share_alliance ? "yes" : "no";
  $("kpi-share").className = c.share_alliance ? "px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-xs" : "px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-xs";
  $("kpi-process").textContent = c.process_alliance ? "yes" : "no";
  $("kpi-process").className = c.process_alliance ? "px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-xs" : "px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-xs";

  $("sys-headless").textContent = c.headless ? "true" : "false";
  $("sys-browsers").textContent = c.browsers ?? "—";
  $("sys-missions").textContent = (c.missions_delay ?? "—") + "s";
  $("sys-transport").textContent = (c.transport_delay ?? "—") + "s";
  $("sys-personnel").textContent = (c.personnel_check ?? "—") + "s";

  $("file-mission").textContent = fmtTime(j.files?.mission_data_mtime);
  $("file-vehicle").textContent = fmtTime(j.files?.vehicle_data_mtime);

  // Chart
  const h = j.history || { missions: [], credits: [] };
  updateChart(h);
}

function updateChart(history) {
  const ctx = document.getElementById("sparkline");
  if (!ctx) return;
  const labels = history.missions.map((_, i) => i);
  const dataM = history.missions;
  const dataC = history.credits.map(v => Math.round(v/100)); // scale credits
  if (sparkChart) {
    sparkChart.data.labels = labels;
    sparkChart.data.datasets[0].data = dataM;
    sparkChart.data.datasets[1].data = dataC;
    sparkChart.update();
    return;
  }
  sparkChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Missions", data: dataM, borderColor: "#f59e0b", backgroundColor: "rgba(245,158,11,0.15)", tension: 0.4, pointRadius: 0, fill: true, borderWidth: 2 },
        { label: "Credits /100", data: dataC, borderColor: "#38bdf8", backgroundColor: "rgba(56,189,248,0.12)", tension: 0.4, pointRadius: 0, fill: true, borderWidth: 2 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#94a3b8", font: { size: 11, family: "JetBrains Mono" } } } },
      scales: {
        x: { ticks: { color: "#64748b", font: { size: 10 } }, grid: { color: "rgba(51,65,85,0.3)" } },
        y: { ticks: { color: "#64748b", font: { size: 10 } }, grid: { color: "rgba(51,65,85,0.3)" }, beginAtZero: true }
      }
    }
  });
}

function renderMissions(missions) {
  const body = $("missions-body");
  const empty = $("missions-empty");
  const count = Object.keys(missions).length;
  $("missions-count").textContent = count;
  const q = ($("missions-search")?.value || "").toLowerCase().trim();
  body.innerHTML = "";
  if (count === 0) { empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  let i = 0;
  for (const [id, m] of Object.entries(missions)) {
    if (i++ > 120) break; // cap render
    const name = (m.mission_name || "Unknown").toString();
    if (q && !name.toLowerCase().includes(q) && !id.includes(q)) continue;
    const credits = m.credits ?? 0;
    const patients = m.patients ?? 0;
    const water = m.water_needed ?? 0;
    const foam = m.foam_needed ?? 0;
    const reqs = (m.vehicles || []).slice(0,3).map(v => `${v.count}× ${v.name}`).join(", ") || "—";
    const tr = document.createElement("tr");
    tr.className = "hover:bg-slate-800/40";
    tr.innerHTML = `
      <td class="px-4 py-2 truncate max-w-[320px]" title="${name.replace(/"/g,'&quot;')}">${name} <span class="text-slate-600">#${id}</span></td>
      <td class="px-4 py-2 text-right">${credits}</td>
      <td class="px-4 py-2 text-right">${patients}</td>
      <td class="px-4 py-2 text-right">${water}</td>
      <td class="px-4 py-2 text-right">${foam}</td>
      <td class="px-4 py-2 truncate max-w-[320px] text-slate-300">${reqs}</td>`;
    body.appendChild(tr);
  }
  if (body.children.length === 0 && q) {
    body.innerHTML = `<tr><td colspan="6" class="px-4 py-6 text-center text-slate-500">No match for "${q}"</td></tr>`;
  }
}

async function loadConfig() {
  try {
    const r = await fetch("/api/config");
    const cfg = await r.json();
    $("cfg-headless").value = (cfg.browser_settings?.headless ?? "true").toString();
    $("cfg-browsers").value = cfg.browser_settings?.browsers ?? 2;
    $("cfg-missions").value = cfg.delays?.missions ?? 10;
    $("cfg-transport").value = cfg.delays?.transport ?? 60;
    $("cfg-personnel-check").value = cfg.delays?.personnel_check ?? 3600;
    $("cfg-hiring").value = cfg.personnel_settings?.hiring_mode ?? "3";
    $("cfg-share").checked = (cfg.mission_settings?.share_alliance ?? "true").toString().toLowerCase() === "true";
    $("cfg-process").checked = (cfg.mission_settings?.process_alliance ?? "true").toString().toLowerCase() === "true";
    $("cfg-username").value = cfg.credentials?.username ?? "";
    // password not shown — leave blank to keep
    $("cfg-password").value = "";
    $("save-status").textContent = "loaded";
    setTimeout(()=> $("save-status").textContent="", 1200);
  } catch(e) { toast("Failed to load config: "+e, false); }
}

async function saveConfig() {
  const payload = {
    browser_settings: {
      headless: $("cfg-headless").value,
      browsers: $("cfg-browsers").value
    },
    delays: {
      missions: $("cfg-missions").value,
      transport: $("cfg-transport").value,
      personnel_check: $("cfg-personnel-check").value
    },
    personnel_settings: {
      hiring_mode: $("cfg-hiring").value
    },
    mission_settings: {
      share_alliance: $("cfg-share").checked ? "true" : "false",
      process_alliance: $("cfg-process").checked ? "true" : "false"
    }
  };
  // Only send credentials if user typed something
  const u = $("cfg-username").value.trim();
  const p = $("cfg-password").value;
  if (u || p) {
    payload.credentials = {};
    if (u) payload.credentials.username = u;
    if (p) payload.credentials.password = p;
  }
  $("save-status").textContent = "saving…";
  try {
    const r = await fetch("/api/config", { method: "PUT", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload)});
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
    $("save-status").textContent = "saved ✓";
    toast("Config saved");
    fetchStats();
    setTimeout(()=> $("save-status").textContent="", 2000);
  } catch(e) { $("save-status").textContent = "error"; toast("Save failed: "+e, false); }
}

function refreshAll(){ fetchStats(); loadConfig(); }

// Tabs
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => { b.className = "tab-btn px-4 py-1.5 rounded-lg text-slate-400 hover:text-white text-sm font-medium"; });
    btn.className = "tab-btn px-4 py-1.5 rounded-lg bg-slate-800 text-white text-sm font-medium shadow";
    const tab = btn.dataset.tab;
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.add("hidden"));
    document.getElementById("panel-" + tab).classList.remove("hidden");
  });
});

$("missions-search")?.addEventListener("input", () => fetchStats());

// Init
fetchStats();
loadConfig();
setInterval(fetchStats, 5000);
