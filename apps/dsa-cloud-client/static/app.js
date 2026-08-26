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
  el.className = ok ? "status ok" : "status err";
}

function reportsStatus(msg, ok) {
  let el = document.getElementById("reports-status");
  el.hidden = !msg;
  show(el.id, msg, ok);
}

function setPill(state, text) {
  const pill = document.getElementById("conn-pill");
  pill.dataset.state = state;
  document.getElementById("conn-text").textContent = text;
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== `tab-${name}`));
  document.querySelectorAll("nav[role=tablist] button").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.tab === name)));
}

function showSkeleton(on) {
  const box = document.getElementById("reports-list");
  box.setAttribute("aria-busy", String(on));
  if (!on) return;
  box.innerHTML = '<div class="skel-card"></div><div class="skel-card"></div><div class="skel-card"></div>';
}

function renderReports(reports) {
  const box = document.getElementById("reports-list");
  box.innerHTML = "";
  if (!reports.length) {
    box.innerHTML = '<p class="empty">暂无报告,去「触发运行」生成第一份吧。</p>';
    return;
  }
  reports.forEach((r) => {
    const card = document.createElement("div");
    card.className = "report-card";
    card.innerHTML = `<strong>${DOMPurify.sanitize(r.name || "")}</strong>`;
    if (r.expired) {
      card.innerHTML += `<em>已过期</em>`;
    } else {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn ghost sm";
      btn.textContent = "下载到本地";
      btn.onclick = async () => {
        try { await api(`/reports/${r.id}/download`, { method: "GET" }); reportsStatus("已存档到本地 ✓", true); }
        catch (e) { reportsStatus("下载失败: " + e.message, false); }
      };
      card.appendChild(btn);
    }
    box.appendChild(card);
  });
}

function refreshReports() {
  showSkeleton(true);
  api("/reports").then((d) => {
    showSkeleton(false);
    renderReports(d.reports);
  }).catch((e) => {
    showSkeleton(false);
    document.getElementById("reports-list").innerHTML =
      `<p class="empty">获取报告失败: ${DOMPurify.sanitize(e.message)}</p>`;
  });
}

document.getElementById("copy-url").onclick = async () => {
  try {
    await navigator.clipboard.writeText(location.href.replace(location.hash, `#token=${currentToken}`));
    setPill(document.getElementById("conn-pill").dataset.state, "地址已复制");
  } catch (e) { /* 剪贴板不可用时静默 */ }
};

document.querySelectorAll("nav[role=tablist] button").forEach((b) =>
  b.onclick = () => switchTab(b.dataset.tab));

// 登录(表单提交统一拦截)
const ownerInput = document.getElementById("login-owner");
const repoInput = document.getElementById("login-repo");
ownerInput.value = localStorage.getItem("dsa_owner") || "";
repoInput.value = localStorage.getItem("dsa_repo") || "";

document.getElementById("login-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const owner = ownerInput.value.trim();
  const repo = repoInput.value.trim();
  const pat = document.getElementById("login-pat").value.trim();
  if (!owner || !repo || !pat) { show("login-status", "三项都需要填写", false); return; }
  localStorage.setItem("dsa_owner", owner);
  localStorage.setItem("dsa_repo", repo);
  fetch(`/api/login?token=${encodeURIComponent(currentToken)}`, {
    method: "POST",
    headers: { "X-Origin-Token": currentToken, "Content-Type": "application/json" },
    body: JSON.stringify({ owner, repo, pat }),
  }).then((r) => r.json()).then((d) => {
    if (d.ok) {
      show("login-status", "已保存。重启应用后生效。", true);
      setPill("warn", "待重启生效");
    } else show("login-status", "保存失败", false);
  }).catch(() => show("login-status", "保存失败", false));
});

// 自选股
document.getElementById("watchlist-save").closest("form")
  .addEventListener("submit", (ev) => {
    ev.preventDefault();
    const symbols = document.getElementById("watchlist-input").value.trim();
    api("/watchlist", { method: "PATCH", body: { symbols } })
      .then(() => show("watchlist-status", "已保存 ✓", true))
      .catch((e) => show("watchlist-status", "失败: " + e.message, false));
  });

// 触发
document.getElementById("trigger-run").closest("form")
  .addEventListener("submit", (ev) => {
    ev.preventDefault();
    const mode = document.getElementById("trigger-mode").value;
    const stock = document.getElementById("trigger-stock").value.trim();
    const body = { mode };
    if (stock) body.stock_list = stock;
    api("/trigger", { method: "POST", body })
      .then(() => show("trigger-status", "已触发运行 ✓ 可在「报告」页刷新查看", true))
      .catch((e) => show("trigger-status", "触发失败: " + e.message, false));
  });

document.getElementById("reports-refresh").onclick = refreshReports;

// 初始化
currentToken = getToken();
if (currentToken) {
  api("/state").then((s) => {
    if (s.logged_in) {
      setPill("ok", `已连接 ${s.owner}/${s.repo}`);
      document.getElementById("login-banner").hidden = true;
      api("/watchlist").then((w) => (document.getElementById("watchlist-input").value = w.symbols || ""));
      refreshReports();
      if (s.running) setPill("ok", "分析运行中…");
    } else {
      setPill("warn", "未完成配置");
      document.getElementById("login-banner").hidden = false;
      switchTab("login");
    }
  }).catch((e) => {
    setPill("err", "连接失败");
    switchTab("login");
    show("login-status", "Token 无效: " + e.message, false);
  });
} else {
  setPill("err", "缺少访问令牌");
  switchTab("login");
}
