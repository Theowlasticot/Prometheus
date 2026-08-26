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
  setTimeout(() => el.classList.add("hidden"), 4000);
}

async function fetchStats() {
  try {
    const r = await fetch("/api/stats");
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    renderStats(j);
    renderMissions(j.missions || {});
    updateAssetFromStats(j.assets);
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
  const sc = c.server_code || "us";
  $("sys-server").textContent = sc;
  $("header-subtitle").textContent = `MissionChief — ${sc.toUpperCase()} Server`;
  $("file-mission").textContent = fmtTime(j.files?.mission_data_mtime);
  $("file-vehicle").textContent = fmtTime(j.files?.vehicle_data_mtime);
  const assets = j.assets;
  if (assets) {
    $("file-assets").textContent = `${assets.cached_files ?? 0}${assets.expected ? "/"+assets.expected : ""} (${sc})`;
    $("asset-code").textContent = assets.code || sc;
    $("asset-cached").textContent = assets.cached_files ?? 0;
    $("asset-expected").textContent = assets.expected ?? "—";
    $("asset-sync").textContent = fmtTime(assets.last_sync);
    $("asset-etag").textContent = assets.manifest_etag ? assets.manifest_etag : "—";
    $("asset-vehicle").textContent = assets.has_vehicle ? "yes" : "no";
  }

  const h = j.history || { missions: [], credits: [] };
  updateChart(h);
}

function updateAssetFromStats(assets) {
  if (!assets) return;
  $("cfg-asset-cached").textContent = assets.cached_files ?? 0;
  $("cfg-asset-expected").textContent = assets.expected ? `/ ${assets.expected}` : "/ —";
  $("cfg-asset-vehicle").textContent = assets.has_vehicle ? "yes" : "no";
  $("cfg-asset-etag").textContent = assets.manifest_etag || "—";
  $("cfg-asset-sync").textContent = fmtTime(assets.last_sync);
  const badge = $("asset-badge");
  if (assets.cached_files === 0) {
    badge.textContent = "not synced";
    badge.className = "px-2 py-1 rounded-full bg-red-500/15 text-red-300 border border-red-500/20 text-[11px] font-mono";
  } else if (assets.expected && assets.cached_files >= assets.expected) {
    badge.textContent = "synced";
    badge.className = "px-2 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/20 text-[11px] font-mono";
  } else if (assets.cached_files > 0) {
    badge.textContent = "partial";
    badge.className = "px-2 py-1 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/20 text-[11px] font-mono";
  }
  $("asset-msg").textContent = assets.manifest_cached ? `Cache: ${assets.cache_dir}` : "No cache yet — use Check/Download";
}

function updateChart(history) {
  const ctx = document.getElementById("sparkline");
  if (!ctx) return;
  const labels = history.missions.map((_, i) => i);
  const dataM = history.missions;
  const dataC = history.credits.map(v => Math.round(v/100));
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
    if (i++ > 120) break;
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

async function loadServers() {
  try {
    const r = await fetch("/api/servers");
    const j = await r.json();
    const sel = $("cfg-server-code");
    if (!sel) return;
    const current = j.current || "us";
    sel.innerHTML = "";
    for (const s of j.servers) {
      const opt = document.createElement("option");
      opt.value = s.code;
      opt.textContent = `${s.code} — ${new URL(s.url).hostname}`;
      if (s.code === current) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch (e) { console.error("loadServers", e); }
}

async function loadAssetStatus() {
  try {
    const r = await fetch("/api/assets/status");
    const j = await r.json();
    if (j.error) {
      $("asset-msg").textContent = j.error;
      return;
    }
    $("cfg-asset-cached").textContent = j.cached_files ?? 0;
    $("cfg-asset-expected").textContent = j.expected ? `/ ${j.expected}` : "/ —";
    $("cfg-asset-vehicle").textContent = j.has_vehicle ? "yes" : "no";
    $("cfg-asset-etag").textContent = j.manifest_etag || "—";
    $("cfg-asset-sync").textContent = fmtTime(j.last_sync);
    $("asset-msg").textContent = `Cache dir: ${j.code_dir} — ${j.cached_files} files`;
    const badge = $("asset-badge");
    if (j.cached_files === 0) {
      badge.textContent = "not synced";
      badge.className = "px-2 py-1 rounded-full bg-red-500/15 text-red-300 border border-red-500/20 text-[11px] font-mono";
    } else if (j.expected && j.cached_files >= j.expected) {
      badge.textContent = "synced";
      badge.className = "px-2 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/20 text-[11px] font-mono";
    } else {
      badge.textContent = `${j.cached_files} files`;
      badge.className = "px-2 py-1 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/20 text-[11px] font-mono";
    }
  } catch (e) { console.error(e); }
}

async function checkAssets() {
  const el = $("asset-status");
  el.textContent = "checking…";
  try {
    const r = await fetch("/api/assets/check");
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    if (j.status === 304 || j.needs_update === false) {
      el.textContent = "✓ up-to-date (304, no git changes)";
      toast("Assets up-to-date — no download needed");
    } else {
      el.textContent = `→ update available (${j.count || "?"} manifest entries, etag ${j.etag || "?"})`;
      toast(`Update available for ${j.code}: ${j.cached_files ?? 0} cached vs remote`, true);
    }
    loadAssetStatus();
  } catch (e) { el.textContent = "error: "+e; toast("Check failed: "+e, false); }
  setTimeout(()=> el.textContent="", 5000);
}

async function syncAssets() {
  const el = $("asset-status");
  const code = $("cfg-server-code")?.value || "us";
  el.textContent = `syncing ${code}…`;
  try {
    const r = await fetch("/api/assets/sync", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({code}) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || j.error || JSON.stringify(j));
    if (j.error) {
      el.textContent = j.error;
      toast(j.error, false);
    } else {
      el.textContent = j.message || `synced ${j.fetched}/${j.total}`;
      toast(j.message || `Synced ${code}: ${j.fetched}/${j.total}`, true);
    }
    loadAssetStatus();
    fetchStats();
  } catch (e) { el.textContent = "error: "+e; toast("Sync failed: "+e, false); }
  setTimeout(()=> el.textContent="", 6000);
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
    $("cfg-password").value = ""; // redacted as *** — leave empty to keep
    // server_settings
    const scode = cfg.server_settings?.code ?? "us";
    const sel = $("cfg-server-code");
    if (sel) {
      sel.value = scode;
    }
    $("cfg-auto-update").checked = (cfg.server_settings?.auto_update ?? "true").toString().toLowerCase() === "true";
    $("cfg-refresh-interval").value = cfg.server_settings?.refresh_interval ?? 3600;
    $("cfg-cache-dir").textContent = cfg.server_settings?.cache_dir ?? "assets_cache";
    // transport
    $("cfg-allow-hosp").checked = (cfg.transport_settings?.allow_alliance_hospitals ?? "true").toString().toLowerCase() === "true";
    $("cfg-allow-cells").checked = (cfg.transport_settings?.allow_alliance_cells ?? "true").toString().toLowerCase() === "true";
    $("cfg-max-distance").value = cfg.transport_settings?.max_distance ?? 0;
    // dispatch
    $("cfg-min-percent").value = cfg.dispatch_settings?.min_percent ?? 70;
    $("cfg-use-aar").checked = (cfg.dispatch_settings?.use_aar ?? "false").toString().toLowerCase() === "true";
    // mission_filter
    $("cfg-ignore-storm").checked = (cfg.mission_filter?.ignore_storm ?? "false").toString().toLowerCase() === "true";
    $("cfg-ignore-event").checked = (cfg.mission_filter?.ignore_event ?? "false").toString().toLowerCase() === "true";
    $("cfg-min-credits").value = cfg.mission_filter?.min_credits ?? 0;
    if (cfg.credentials?.password === "***") {
      $("cfg-password").placeholder = "•••••••• (set)";
    }
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
    },
    server_settings: {
      code: $("cfg-server-code").value,
      auto_update: $("cfg-auto-update").checked ? "true" : "false",
      refresh_interval: $("cfg-refresh-interval").value
    },
    transport_settings: {
      allow_alliance_hospitals: $("cfg-allow-hosp").checked ? "true" : "false",
      allow_alliance_cells: $("cfg-allow-cells").checked ? "true" : "false",
      max_distance: $("cfg-max-distance").value
    },
    dispatch_settings: {
      min_percent: $("cfg-min-percent").value,
      use_aar: $("cfg-use-aar").checked ? "true" : "false"
    },
    mission_filter: {
      ignore_storm: $("cfg-ignore-storm").checked ? "true" : "false",
      ignore_event: $("cfg-ignore-event").checked ? "true" : "false",
      min_credits: $("cfg-min-credits").value
    }
  };
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
    toast("Config saved — server="+payload.server_settings.code);
    fetchStats();
    loadAssetStatus();
    setTimeout(()=> $("save-status").textContent="", 2000);
  } catch(e) { $("save-status").textContent = "error"; toast("Save failed: "+e, false); }
}

function refreshAll(){ fetchStats(); loadConfig(); loadServers(); loadAssetStatus(); refreshBotStatus(); }

async function refreshBotStatus(){
  try{
    const r = await fetch("/api/bot/status");
    const j = await r.json();
    $("bot-status").textContent = j.running ? `● running` : "○ stopped";
    $("bot-status").className = j.running ? "text-emerald-400" : "text-slate-400";
    $("bot-pid").textContent = j.pid ?? "—";
    $("bot-mode-status").textContent = j.mode ?? "—";
    $("bot-uptime").textContent = j.running ? `${j.uptime}s` : "—";
    if(j.logs_tail && j.logs_tail.length){
      $("bot-logs").textContent = j.logs_tail.join("\n");
    }
  }catch(e){ /* ignore */ }
}
async function refreshBotLogs(){
  try{
    const r = await fetch("/api/bot/logs");
    const j = await r.json();
    $("bot-logs").textContent = j.logs.length ? j.logs.slice(-80).join("\n") : "— no logs —";
  }catch(e){ toast("Logs failed: "+e, false); }
}
async function startBot(){
  const mode = $("bot-mode").value;
  $("bot-msg").textContent = `starting mode ${mode}…`;
  try{
    const r = await fetch("/api/bot/start", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({mode})});
    const j = await r.json();
    if(!r.ok) throw new Error(j.detail || JSON.stringify(j));
    $("bot-msg").textContent = `started pid ${j.pid} mode ${j.mode}`;
    toast(`Bot started mode ${j.mode}`);
    refreshBotStatus();
  }catch(e){ $("bot-msg").textContent = "error: "+e; toast("Start failed: "+e, false); }
}
async function stopBot(){
  $("bot-msg").textContent = "stopping…";
  try{
    const r = await fetch("/api/bot/stop", {method:"POST"});
    const j = await r.json();
    $("bot-msg").textContent = j.status;
    toast("Bot stopped");
    refreshBotStatus();
  }catch(e){ $("bot-msg").textContent = "error: "+e; toast("Stop failed: "+e, false); }
}

async function refreshLogs(){
  const level = $("log-level")?.value || "";
  const action = $("log-action")?.value || "";
  const fix = $("log-fix")?.checked ? "true" : "";
  const search = $("log-search")?.value || "";
  const tail = $("log-tail")?.value || "200";
  const qs = new URLSearchParams();
  if(level) qs.set("level", level);
  if(action) qs.set("action", action);
  if(fix) qs.set("fix_needed", "true");
  if(search) qs.set("search", search);
  qs.set("tail", tail);
  $("logs-status").textContent = "loading…";
  try{
    const r = await fetch("/api/logs?"+qs.toString());
    const j = await r.json();
    $("logs-count").textContent = j.count ?? 0;
    const fixesBadge = $("fixes-badge");
    const fixesCountEl = $("fixes-count");
    // Also fetch fixes count for badge
    try{
      const fr = await fetch("/api/logs/fixes?hours=24&tail=200");
      const fj = await fr.json();
      const cnt = fj.count ?? 0;
      fixesCountEl.textContent = `${cnt} fixes needed`;
      fixesCountEl.className = cnt ? "px-2 py-0.5 rounded-full bg-red-500/15 text-red-300 border border-red-500/20 text-xs" : "px-2 py-0.5 rounded-full bg-slate-800 text-slate-500 text-xs";
      if(cnt){
        fixesBadge.textContent = cnt;
        fixesBadge.classList.remove("hidden");
      } else {
        fixesBadge.classList.add("hidden");
      }
    }catch(e){}
    const body = $("logs-body");
    body.innerHTML = "";
    if(!j.logs || !j.logs.length){
      body.innerHTML = `<tr><td colspan="6" class="px-3 py-6 text-center text-slate-500">No logs yet — run the bot or check <code>logs/actions.jsonl</code></td></tr>`;
      $("logs-status").textContent = "no logs";
      return;
    }
    for(const log of j.logs){
      const tr = document.createElement("tr");
      tr.className = log.fix_needed ? "bg-red-500/5" : "hover:bg-slate-800/30";
      const lvlColor = log.level==="ERROR" ? "text-red-400" : log.level==="WARNING" ? "text-amber-400" : log.level==="DEBUG" ? "text-slate-500" : "text-emerald-300";
      tr.innerHTML = `
        <td class="px-3 py-1.5 whitespace-nowrap text-slate-400">${(log.ts||"").toString().slice(0,19).replace("T"," ")}</td>
        <td class="px-3 py-1.5 ${lvlColor}">${log.level||""}</td>
        <td class="px-3 py-1.5 text-sky-300">${log.action||""}</td>
        <td class="px-3 py-1.5">${log.mission_id||""}</td>
        <td class="px-3 py-1.5 text-slate-200 truncate max-w-[420px]" title="${(log.msg||"").replace(/"/g,'&quot;')}">${log.msg||""}</td>
        <td class="px-3 py-1.5 text-center">${log.fix_needed ? "🔧" : ""}</td>`;
      body.appendChild(tr);
    }
    $("logs-status").textContent = `${j.count} logs`;
  }catch(e){ $("logs-status").textContent = "error: "+e; }
}
async function refreshFixes(){
  $("log-fix").checked = true;
  $("log-level").value = "";
  $("log-action").value = "";
  await refreshLogs();
  // Switch to logs tab
  document.querySelectorAll(".tab-btn").forEach(b=> b.className="tab-btn px-4 py-1.5 rounded-lg text-slate-400 hover:text-white text-sm font-medium");
  document.querySelector('[data-tab="logs"]').className="tab-btn px-4 py-1.5 rounded-lg bg-slate-800 text-white text-sm font-medium shadow";
  document.querySelectorAll(".tab-panel").forEach(p=> p.classList.add("hidden"));
  document.getElementById("panel-logs").classList.remove("hidden");
}
function exportLogs(){
  window.open("/api/logs?tail=1000", "_blank");
}

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
$("cfg-server-code")?.addEventListener("change", () => {
  // hint user to sync after region change
  $("asset-msg").textContent = "Region changed — click Check / Download to fetch for this region (smart md5 diff).";
});

fetchStats();
loadConfig();
loadServers();
loadAssetStatus();
refreshBotStatus();
setInterval(fetchStats, 5000);
setInterval(loadAssetStatus, 30000);
setInterval(refreshBotStatus, 3000);
