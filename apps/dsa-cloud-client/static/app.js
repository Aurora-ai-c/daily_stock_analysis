"use strict";

let currentToken = "";
let currentReportHtml = "";

function getToken() {
  const m = location.hash.match(/#token=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function api(path, { method = "GET", body } = {}) {
  const url = `/api${path}?token=${encodeURIComponent(currentToken)}`;
  const init = { method, headers: { "X-Origin-Token": currentToken } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(url, init);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

function show(elId, msg, ok = true) {
  const el = document.getElementById(elId);
  el.textContent = msg;
  el.className = ok ? "ok" : "err";
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
}

function renderReports(reports) {
  const box = document.getElementById("reports-list");
  box.innerHTML = "";
  if (!reports.length) { box.textContent = "暂无报告。"; return; }
  reports.forEach((r) => {
    const card = document.createElement("div");
    card.className = "report-card";
    card.innerHTML = `<strong>${DOMPurify.sanitize(r.name || "")}</strong>`;
    if (r.expired) {
      card.innerHTML += `<em>(已过期)</em>`;
    } else {
      const btn = document.createElement("button");
      btn.textContent = "下载到本地";
      btn.onclick = async () => {
        try { await api(`/reports/${r.id}/download`, { method: "GET" }); show("reports-list", "已存档到本地", true); }
        catch (e) { show("reports-list", "下载失败: " + e.message, false); }
      };
      card.appendChild(btn);
    }
    box.appendChild(card);
  });
}

function refreshReports() {
  api("/reports").then((d) => renderReports(d.reports))
    .catch((e) => show("reports-list", "获取报告失败: " + e.message, false));
}

document.getElementById("copy-url").onclick = async () => {
  try {
    await navigator.clipboard.writeText(location.href.replace(location.hash, `#token=${currentToken}`));
  } catch (e) { /* 忽略 */ }
};

document.querySelectorAll("nav button").forEach((b) =>
  b.onclick = () => switchTab(b.dataset.tab));

// 登录
document.getElementById("login-owner").value = localStorage.getItem("dsa_owner") || "";
document.getElementById("login-repo").value = localStorage.getItem("dsa_repo") || "";
document.getElementById("login-save").onclick = () => {
  const owner = document.getElementById("login-owner").value.trim();
  const repo = document.getElementById("login-repo").value.trim();
  const pat = document.getElementById("login-pat").value.trim();
  if (!owner || !repo || !pat) { show("login-status", "请填全三项", false); return; }
  localStorage.setItem("dsa_owner", owner);
  localStorage.setItem("dsa_repo", repo);
  fetch(`/api/login?token=${encodeURIComponent(currentToken)}`, {
    method: "POST",
    headers: { "X-Origin-Token": currentToken, "Content-Type": "application/json" },
    body: JSON.stringify({ owner, repo, pat }),
  }).then((r) => r.json()).then((d) => {
    if (d.ok) show("login-status", "已保存。请重启应用生效。", true);
    else show("login-status", "保存失败", false);
  }).catch(() => show("login-status", "保存失败", false));
};

// 自选股
document.getElementById("watchlist-save").onclick = () => {
  const symbols = document.getElementById("watchlist-input").value.trim();
  api("/watchlist", { method: "PATCH", body: { symbols } })
    .then(() => show("watchlist-status", "已保存", true))
    .catch((e) => show("watchlist-status", "失败: " + e.message, false));
};

// 触发
document.getElementById("trigger-run").onclick = () => {
  const mode = document.getElementById("trigger-mode").value;
  const stock = document.getElementById("trigger-stock").value.trim();
  const body = { mode };
  if (stock) body.stock_list = stock;
  api("/trigger", { method: "POST", body })
    .then(() => show("trigger-status", "已触发运行", true))
    .catch((e) => show("trigger-status", "触发失败: " + e.message, false));
};

document.getElementById("reports-refresh").onclick = refreshReports;

// 初始化
currentToken = getToken();
if (currentToken) {
  api("/state").then((s) => {
    if (s.logged_in) {
      api("/watchlist").then((w) => (document.getElementById("watchlist-input").value = w.symbols || ""));
      refreshReports();
    } else {
      switchTab("login");
    }
  }).catch((e) => {
    switchTab("login");
    document.getElementById("login-status").textContent = "Token 无效: " + e.message;
    document.getElementById("login-status").className = "err";
  });
} else {
  switchTab("login");
}
