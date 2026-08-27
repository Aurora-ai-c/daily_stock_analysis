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
      show("login-status", "已保存。即时生效。", true);
      // Config is live object - no restart needed. Re-fetch state and switch tab:
      api("/state").then((s) => {
        if (s.logged_in) {
          setPill("ok", `已连接 ${s.owner}/${s.repo}`);
          document.getElementById("login-banner").hidden = true;
          switchTab("watchlist");
          api("/watchlist").then((w) => (document.getElementById("watchlist-input").value = w.symbols || ""));
          refreshReports();
        } else {
          setPill("warn", "未完成配置");
          document.getElementById("login-banner").hidden = false;
          switchTab("login");
        }
      }).catch(() => show("login-status", "保存失败", false));
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
      .then((d) => {
        const est = d.estimated_usd != null ? ` 预估 $${d.estimated_usd.toFixed(2)}` : "";
        show("trigger-status", "已触发运行 ✓ 可在「报告」页刷新查看" + est, true);
        refreshStatus();
      })
      .catch((e) => {
        const msg = e.message.includes("budget_exceeded") ? "预算超限: " + e.message : "触发失败: " + e.message;
        show("trigger-status", msg, false);
      });
  });

document.getElementById("reports-refresh").onclick = refreshReports;

// 状态(可观测性)
function fmtTs(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}
function refreshStatus() {
  api("/status").then((s) => {
    document.getElementById("status-last-success").textContent = fmtTs(s.last_success_ts);
    document.getElementById("status-last-failure").textContent = fmtTs(s.last_failure_ts);
    document.getElementById("status-last-checked").textContent = fmtTs(s.last_checked_ts);
    document.getElementById("status-running").textContent = s.running ? "分析中…" : "空闲";
    document.getElementById("status-stale-banner").hidden = !s.stale;
document.getElementById("status-spend").textContent = "$" + (s.today_spend_usd || 0).toFixed(2);
document.getElementById("status-budget").textContent = "$" + (s.budget_daily_usd || 0).toFixed(2);
document.getElementById("status-budget-mode").textContent = s.budget_mode || "warn";
document.getElementById("status-budget-ratio").textContent = Math.round((s.budget_usage_ratio || 0) * 100) + "%";
document.getElementById("status-budget-banner").hidden = !s.budget_over;
renderDataSourceHealth(s.data_source_health);
  }).catch((e) => show("status-msg", "状态获取失败: " + e.message, false));
}

function renderDataSourceHealth(h) {
  const banner = document.getElementById("status-allfailed-banner");
  const box = document.getElementById("status-sources");
  if (!h || !Array.isArray(h.sources) || h.sources.length === 0) {
    banner.hidden = true;
    box.innerHTML = '<li class="muted">暂无数据源健康信息(需成功运行一次后同步)。</li>';
    return;
  }
  banner.hidden = h.summary !== "all_failed";
  const stateLabel = { closed: "正常", open: "熔断", half_open: "探测中" };
  box.innerHTML = h.sources.map((s) => {
    const st = stateLabel[s.state] || s.state || "未知";
    const cls = s.state === "open" ? "tag err" : s.state === "half_open" ? "tag warn" : "tag ok";
    const avail = s.available ? "" : ' <span class="muted">(不可用)</span>';
    const pri = s.priority != null ? `P${s.priority}` : "";
    return `<li><span>${s.name} <span class="muted">${pri}</span>${avail}</span><b class="${cls}">${st}</b></li>`;
  }).join("");
}
document.getElementById("status-refresh").onclick = refreshStatus;

// 设置 - 网络
document.getElementById("settings-network-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const proxy = document.getElementById("net-proxy").value.trim();
  const ca = document.getElementById("net-ca").value.trim();
  api("/network", { method: "POST", body: { github_proxy: proxy, github_ca_bundle: ca } })
    .then(() => show("net-status", "已保存,下次请求生效 ✓", true))
    .catch((e) => show("net-status", "失败: " + e.message, false));
});

// 设置 - 成本护栏
document.getElementById("settings-budget-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const daily = parseFloat(document.getElementById("budget-daily").value) || 0;
  const mode = document.getElementById("budget-mode").value;
  api("/budget", { method: "POST", body: { budget_daily_usd: daily, budget_mode: mode } })
    .then(() => show("budget-status", "已保存 ✓", true))
    .catch((e) => show("budget-status", "失败: " + e.message, false));
});
function loadBudgetSettings() {
  api("/budget").then((b) => {
    document.getElementById("budget-daily").value = b.budget_daily_usd;
    document.getElementById("budget-mode").value = b.budget_mode;
  }).catch(() => {});
}

// 密钥(仓库 Secrets 自管理)
const SECRET_SCHEMA = [
  { group: "行情数据源", fields: [
    { name: "TUSHARE_TOKEN", label: "Tushare Token" },
    { name: "TICKFLOW_API_KEY", label: "TickFlow Key" },
  ]},
  { group: "LLM 分析", fields: [
    { name: "DEEPSEEK_API_KEY", label: "DeepSeek Key" },
    { name: "OPENAI_API_KEY", label: "OpenAI Key" },
    { name: "OPENAI_BASE_URL", label: "OpenAI Base URL(可选)" },
    { name: "OPENAI_MODEL", label: "OpenAI Model(可选)" },
    { name: "ANTHROPIC_API_KEY", label: "Anthropic Key" },
    { name: "GEMINI_API_KEY", label: "Gemini Key" },
  ]},
  { group: "通知渠道", fields: [
    { name: "WECHAT_WEBHOOK_URL", label: "企业微信 Webhook" },
    { name: "DINGTALK_WEBHOOK_URL", label: "钉钉 Webhook" },
    { name: "DINGTALK_SECRET", label: "钉钉 Secret" },
    { name: "FEISHU_WEBHOOK_URL", label: "飞书 Webhook" },
    { name: "FEISHU_WEBHOOK_SECRET", label: "飞书 Secret" },
    { name: "TELEGRAM_BOT_TOKEN", label: "Telegram Bot Token" },
    { name: "TELEGRAM_CHAT_ID", label: "Telegram Chat ID" },
    { name: "SERVERCHAN3_SENDKEY", label: "Server酱 SendKey" },
    { name: "PUSHPLUS_TOKEN", label: "PushPlus Token" },
  ]},
  { group: "新闻搜索(可选)", fields: [
    { name: "BOCHA_API_KEYS", label: "Bocha Key" },
    { name: "BRAVE_API_KEYS", label: "Brave Key" },
    { name: "TAVILY_API_KEYS", label: "Tavily Key" },
  ]},
];

let _secretConfigured = [];

function renderSecrets(configured) {
  _secretConfigured = configured || [];
  const root = document.getElementById("secrets-groups");
  root.innerHTML = "";
  SECRET_SCHEMA.forEach((grp) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<h3>${grp.group}</h3>`;
    grp.fields.forEach((f) => {
      const wrap = document.createElement("div");
      wrap.className = "field";
      const isSet = _secretConfigured.includes(f.name);
      wrap.innerHTML =
        `<label for="sec-${f.name}">${f.label} <span class="badge ${isSet ? "ok" : "no"}">${isSet ? "已配置" : "未配置"}</span></label>` +
        `<input id="sec-${f.name}" type="password" placeholder="${f.name}" data-secret="${f.name}">`;
      card.appendChild(wrap);
    });
    const row = document.createElement("div");
    row.className = "row";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn primary sm";
    btn.textContent = "保存本组";
    btn.onclick = () => saveSecretsGroup(grp);
    row.appendChild(btn);
    card.appendChild(row);
    root.appendChild(card);
  });
}

async function saveSecretsGroup(grp) {
  const pending = grp.fields
    .map((f) => ({ name: f.name, value: document.getElementById(`sec-${f.name}`).value }))
    .filter((x) => x.value.trim());
  if (!pending.length) { show("secrets-status", "本组没有可保存的内容", false); return; }
  let okCount = 0;
  for (const x of pending) {
    try {
      await api("/secrets", { method: "POST", body: { name: x.name, value: x.value.trim() } });
      okCount++;
    } catch (e) {
      show("secrets-status", `${x.name} 保存失败: ${e.message}`, false);
      return;
    }
  }
  show("secrets-status", `已保存 ${okCount} 项 ✓`, true);
  refreshSecretsStatus();
}

function refreshSecretsStatus() {
  api("/secrets").then((d) => renderSecrets(d.names)).catch(() => {});
}

document.getElementById("adv-secret-save").onclick = async () => {
  const name = document.getElementById("adv-secret-name").value.trim();
  const value = document.getElementById("adv-secret-value").value;
  if (!name || !value) { show("secrets-status", "名称与值均不能为空", false); return; }
  try {
    await api("/secrets", { method: "POST", body: { name, value } });
    show("secrets-status", `${name} 已保存 ✓`, true);
    document.getElementById("adv-secret-name").value = "";
    document.getElementById("adv-secret-value").value = "";
    refreshSecretsStatus();
  } catch (e) {
    show("secrets-status", `保存失败: ${e.message}`, false);
  }
};

// 初始化
currentToken = getToken();
if (currentToken) {
  api("/state").then((s) => {
    if (s.logged_in) {
      setPill("ok", `已连接 ${s.owner}/${s.repo}`);
      document.getElementById("login-banner").hidden = true;
      switchTab("watchlist");
      api("/watchlist").then((w) => (document.getElementById("watchlist-input").value = w.symbols || ""));
      refreshReports();
      refreshStatus();
      loadBudgetSettings();
      refreshSecretsStatus();
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
