(function () {
  const page = document.body.dataset.page;
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.classList.toggle("active", link.dataset.nav === page);
  });

  const menuButton = document.querySelector("[data-menu-toggle]");
  if (menuButton) {
    menuButton.addEventListener("click", () => document.body.classList.toggle("menu-open"));
  }

  document.addEventListener("click", (event) => {
    if (window.innerWidth > 820 || !document.body.classList.contains("menu-open")) return;
    if (event.target.closest(".sidebar") || event.target.closest("[data-menu-toggle]")) return;
    document.body.classList.remove("menu-open");
  });

  const toast = document.querySelector("[data-toast]");
  let toastTimer;
  function showToast(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2200);
  }

  const apiBase = window.location.protocol === "file:"
    ? "http://127.0.0.1:8090/api"
    : "/api";

  async function apiRequest(path, options = {}) {
    const response = await window.fetch(`${apiBase}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = typeof payload.detail === "string"
        ? payload.detail
        : `请求失败（HTTP ${response.status}）`;
      throw new Error(message);
    }
    return payload;
  }

  document.querySelectorAll("[data-toast-message]").forEach((button) => {
    button.addEventListener("click", () => showToast(button.dataset.toastMessage));
  });

  const dashboardPeriods = {
    week: {
      values: ["¥ 18,420", "23", "¥ 801", "7"],
      deltas: ["较上周 +6.4%", "较上周 +2", "较上周 -1.2%", "2 张待交付"],
      path: "M35 178 C72 157,94 168,126 138 S187 111,221 127 S279 76,319 92 S377 62,420 48 S476 73,525 36",
      labels: ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    },
    month: {
      values: ["¥ 86,420", "112", "¥ 772", "28"],
      deltas: ["较上月 +12.8%", "较上月 +9", "较上月 +4.1%", "6 张待交付"],
      path: "M35 168 C78 154,103 160,140 126 S203 102,245 115 S311 73,354 87 S424 45,475 59 S505 43,525 32",
      labels: ["第1周", "第2周", "第3周", "第4周", "本周", "", ""]
    },
    quarter: {
      values: ["¥ 238,760", "307", "¥ 778", "73"],
      deltas: ["同比 +18.6%", "同比 +31", "同比 +2.4%", "12 张待交付"],
      path: "M35 184 C76 176,102 139,141 145 S205 117,246 124 S307 94,351 80 S417 91,469 50 S504 57,525 34",
      labels: ["4月", "5月", "6月", "", "", "", ""]
    }
  };

  document.querySelectorAll("[data-period]").forEach((button) => {
    button.addEventListener("click", () => {
      const period = dashboardPeriods[button.dataset.period];
      if (!period) return;
      document.querySelectorAll("[data-period]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      document.querySelectorAll("[data-kpi-value]").forEach((item, index) => {
        item.textContent = period.values[index];
      });
      document.querySelectorAll("[data-kpi-note]").forEach((item, index) => {
        item.textContent = period.deltas[index];
      });
      const path = document.querySelector("[data-sales-path]");
      if (path) path.setAttribute("d", period.path);
      document.querySelectorAll("[data-axis-label]").forEach((item, index) => {
        item.textContent = period.labels[index] || "";
      });
      showToast("已切换数据周期（演示数据）");
    });
  });

  const providerCards = document.querySelectorAll("[data-provider]");
  const providerUrlInput = document.querySelector("[data-provider-url]");
  const providerModelInput = document.querySelector("[data-provider-model]");
  const providerModelOptions = document.querySelector("[data-provider-model-options]");
  const providerModelHelp = document.querySelector("[data-provider-model-help]");
  const refreshModelsButton = document.querySelector("[data-refresh-models]");
  const providerKeyInput = document.querySelector("[data-provider-key]");
  const providerKeyHelp = document.querySelector("[data-provider-key-help]");
  const modelStatus = document.querySelector("[data-model-status]");
  const settingsFeedback = document.querySelector("[data-settings-feedback]");
  const settingsSaveButton = document.querySelector("[data-settings-save]");
  const testModelButton = document.querySelector("[data-test-model]");
  const langfuseStatus = document.querySelector("[data-langfuse-status]");
  const langfuseRegion = document.querySelector("[data-langfuse-region]");
  const langfuseUrlInput = document.querySelector("[data-langfuse-url]");
  const langfusePublicKeyInput = document.querySelector("[data-langfuse-public-key]");
  const langfuseSecretKeyInput = document.querySelector("[data-langfuse-secret-key]");
  const langfuseKeyHelp = document.querySelector("[data-langfuse-key-help]");
  const langfuseEnabledInput = document.querySelector("[data-langfuse-enabled]");
  const databaseStatus = document.querySelector("[data-database-status]");
  const databaseHostInput = document.querySelector("[data-db-host]");
  const databasePortInput = document.querySelector("[data-db-port]");
  const databaseNameInput = document.querySelector("[data-db-name]");
  const databaseUserInput = document.querySelector("[data-db-user]");
  const databasePasswordInput = document.querySelector("[data-db-password]");
  const databaseCompanyInput = document.querySelector("[data-db-company-id]");
  const databaseMaxRowsInput = document.querySelector("[data-db-max-rows]");
  const databaseTimeoutInput = document.querySelector("[data-db-timeout]");
  const databaseResponse = document.querySelector("[data-db-response]");
  const testDatabaseButton = document.querySelector("[data-test-database]");

  const providerDrafts = {
    deepseek: {
      base_url: "https://api.deepseek.com",
      model: "deepseek-v4-pro",
      configured: false,
      models: [],
      draftKey: ""
    },
    siliconflow: {
      base_url: "https://api.siliconflow.cn/v1",
      model: "deepseek-ai/DeepSeek-V3.1-Terminus",
      configured: false,
      models: [],
      draftKey: ""
    }
  };
  let activeProvider = "deepseek";

  function showSettingsFeedback(message, tone = "success") {
    if (!settingsFeedback) return;
    settingsFeedback.textContent = message;
    settingsFeedback.classList.add("show");
    settingsFeedback.classList.toggle("error", tone === "error");
    settingsFeedback.classList.toggle("warning", tone === "warning");
  }

  function setStatusBadge(element, label, tone) {
    if (!element) return;
    element.textContent = label;
    element.classList.toggle("neutral", tone === "neutral");
    element.classList.toggle("warning", tone === "warning");
  }

  function captureActiveProvider() {
    if (!providerUrlInput || !providerModelInput || !providerKeyInput) return;
    const draft = providerDrafts[activeProvider];
    draft.base_url = providerUrlInput.value.trim();
    draft.model = providerModelInput.value.trim();
    draft.draftKey = providerKeyInput.value.trim();
  }

  function renderActiveProvider() {
    const draft = providerDrafts[activeProvider];
    providerCards.forEach((card) => {
      card.classList.toggle("selected", card.dataset.provider === activeProvider);
    });
    if (providerUrlInput) providerUrlInput.value = draft.base_url;
    if (providerModelInput) providerModelInput.value = draft.model;
    if (providerModelOptions) {
      providerModelOptions.innerHTML = "";
      draft.models.forEach((modelId) => {
        const option = document.createElement("option");
        option.value = modelId;
        providerModelOptions.appendChild(option);
      });
    }
    if (providerModelHelp) {
      providerModelHelp.textContent = draft.models.length
        ? `已读取 ${draft.models.length} 个模型；可选择或输入自定义 Model ID`
        : (draft.configured
          ? "点击“刷新模型列表”从供应商读取可用模型"
          : "先保存该供应商的 API Key，再读取模型列表");
    }
    if (providerKeyInput) {
      providerKeyInput.value = draft.draftKey;
      providerKeyInput.placeholder = draft.configured
        ? "已配置，留空不修改"
        : "请输入 API Key";
    }
    if (providerKeyHelp) {
      providerKeyHelp.textContent = draft.configured
        ? "后端已有密钥；页面不会读取或回显，留空即可保留"
        : "仅保存到当前 Windows 用户环境变量，不会回显";
    }
    setStatusBadge(
      modelStatus,
      draft.configured ? "已配置" : "未配置",
      draft.configured ? "success" : "warning"
    );
  }

  async function loadProviderModels({ quiet = false } = {}) {
    if (!providerModelInput || !refreshModelsButton) return;
    captureActiveProvider();
    const providerAtRequest = activeProvider;
    const draft = providerDrafts[providerAtRequest];
    if (!draft.configured) {
      if (providerModelHelp) providerModelHelp.textContent = "请先保存该供应商的 API Key。";
      return;
    }

    refreshModelsButton.disabled = true;
    refreshModelsButton.textContent = "读取中…";
    try {
      const data = await apiRequest(`/settings/models/${providerAtRequest}`);
      providerDrafts[providerAtRequest].models = data.models;
      if (activeProvider === providerAtRequest) renderActiveProvider();
      if (!quiet) showToast(`已读取 ${data.models.length} 个可用模型`);
    } catch (error) {
      if (activeProvider === providerAtRequest && providerModelHelp) {
        providerModelHelp.textContent = `${error.message}。仍可手工输入 Model ID。`;
      }
      if (!quiet) showToast("模型列表读取失败");
    } finally {
      refreshModelsButton.disabled = false;
      refreshModelsButton.textContent = "刷新模型列表";
    }
  }

  function langfuseRegionFromUrl(url) {
    if (url === "https://cloud.langfuse.com") return "eu";
    if (url === "https://us.cloud.langfuse.com") return "us";
    return "custom";
  }

  function renderLangfuseStatus(config) {
    if (langfuseUrlInput) langfuseUrlInput.value = config.base_url;
    if (langfuseRegion) langfuseRegion.value = langfuseRegionFromUrl(config.base_url);
    if (langfuseEnabledInput) langfuseEnabledInput.checked = config.enabled;
    if (langfusePublicKeyInput) {
      langfusePublicKeyInput.value = "";
      langfusePublicKeyInput.placeholder = config.configured
        ? "已配置，留空不修改"
        : "pk-lf-…";
    }
    if (langfuseSecretKeyInput) {
      langfuseSecretKeyInput.value = "";
      langfuseSecretKeyInput.placeholder = config.configured
        ? "已配置，留空不修改"
        : "sk-lf-…";
    }
    if (langfuseKeyHelp) {
      langfuseKeyHelp.textContent = config.configured
        ? "后端已有密钥；留空即可保留"
        : "Public Key 与 Secret Key 需要同时填写";
    }
    const label = !config.enabled ? "已关闭" : (config.configured ? "已配置" : "未配置");
    const tone = !config.enabled ? "neutral" : (config.configured ? "success" : "warning");
    setStatusBadge(langfuseStatus, label, tone);
  }

  function renderDatabaseSettings(config) {
    if (!config) return;
    if (databaseHostInput) databaseHostInput.value = config.host;
    if (databasePortInput) databasePortInput.value = config.port;
    if (databaseNameInput) databaseNameInput.value = config.database;
    if (databaseUserInput) databaseUserInput.value = config.user;
    if (databaseCompanyInput) databaseCompanyInput.value = config.company_id;
    if (databaseMaxRowsInput) databaseMaxRowsInput.value = config.max_rows;
    if (databaseTimeoutInput) databaseTimeoutInput.value = config.statement_timeout_ms;
    if (databasePasswordInput) {
      databasePasswordInput.value = "";
      databasePasswordInput.placeholder = config.password_configured
        ? "已配置，留空不修改"
        : "本机免密时可留空";
    }
    setStatusBadge(databaseStatus, config.configured ? "已配置" : "未配置", config.configured ? "success" : "warning");
  }

  function databasePayload() {
    return {
      host: databaseHostInput?.value.trim() || "127.0.0.1",
      port: Number(databasePortInput?.value || 55432),
      database: databaseNameInput?.value.trim() || "odoo19_dev",
      user: databaseUserInput?.value.trim() || "codex_readonly",
      password: databasePasswordInput?.value.trim() || null,
      company_id: Number(databaseCompanyInput?.value || 1),
      max_rows: Number(databaseMaxRowsInput?.value || 500),
      statement_timeout_ms: Number(databaseTimeoutInput?.value || 15000)
    };
  }

  async function loadSettings() {
    if (!settingsSaveButton) return;
    settingsSaveButton.disabled = true;
    showSettingsFeedback("正在读取本机配置…");
    try {
      const data = await apiRequest("/settings");
      Object.entries(data.providers).forEach(([name, config]) => {
        Object.assign(providerDrafts[name], config, { draftKey: "" });
      });
      activeProvider = data.selected_provider;
      renderActiveProvider();
      renderLangfuseStatus(data.langfuse);
      renderDatabaseSettings(data.database);
      showSettingsFeedback("配置已从本机后端读取。密钥只显示状态，不会回显明文。");
      loadProviderModels({ quiet: true });
      refreshDatabaseStatus();
    } catch (error) {
      showSettingsFeedback(`读取失败：${error.message}。请从 start-odoo-agent.bat 启动应用。`, "error");
    } finally {
      settingsSaveButton.disabled = false;
    }
  }

  async function saveSettings({ quiet = false } = {}) {
    captureActiveProvider();
    const publicKey = langfusePublicKeyInput?.value.trim() || "";
    const secretKey = langfuseSecretKeyInput?.value.trim() || "";
    const body = {
      selected_provider: activeProvider,
      deepseek: {
        base_url: providerDrafts.deepseek.base_url,
        model: providerDrafts.deepseek.model,
        api_key: providerDrafts.deepseek.draftKey || null
      },
      siliconflow: {
        base_url: providerDrafts.siliconflow.base_url,
        model: providerDrafts.siliconflow.model,
        api_key: providerDrafts.siliconflow.draftKey || null
      },
      langfuse: {
        base_url: langfuseUrlInput?.value.trim() || "https://cloud.langfuse.com",
        enabled: langfuseEnabledInput?.checked ?? true,
        public_key: publicKey || null,
        secret_key: secretKey || null
      },
      database: databasePayload()
    };

    const data = await apiRequest("/settings", {
      method: "PUT",
      body: JSON.stringify(body)
    });
    Object.entries(data.providers).forEach(([name, config]) => {
      Object.assign(providerDrafts[name], config, { draftKey: "" });
    });
    renderActiveProvider();
    renderLangfuseStatus(data.langfuse);
    renderDatabaseSettings(data.database);
    const message = data.restart_required
      ? "配置已保存。你更换了 Langfuse 连接信息，请关闭后端窗口并重新双击 BAT。"
      : "配置已保存，并已对新的模型请求生效。";
    showSettingsFeedback(message, data.restart_required ? "warning" : "success");
    if (!quiet) showToast("设置已安全保存到本机");
    return data;
  }

  providerCards.forEach((card) => {
    card.addEventListener("click", () => {
      captureActiveProvider();
      activeProvider = card.dataset.provider;
      renderActiveProvider();
      showToast(`已选择 ${card.dataset.providerLabel}`);
      loadProviderModels({ quiet: true });
    });
  });

  if (refreshModelsButton) {
    refreshModelsButton.addEventListener("click", () => loadProviderModels());
  }

  if (langfuseRegion) {
    langfuseRegion.addEventListener("change", () => {
      if (!langfuseUrlInput) return;
      if (langfuseRegion.value === "eu") langfuseUrlInput.value = "https://cloud.langfuse.com";
      if (langfuseRegion.value === "us") langfuseUrlInput.value = "https://us.cloud.langfuse.com";
    });
  }

  if (langfuseUrlInput) {
    langfuseUrlInput.addEventListener("input", () => {
      if (langfuseRegion) langfuseRegion.value = langfuseRegionFromUrl(langfuseUrlInput.value.trim().replace(/\/$/, ""));
    });
  }

  if (settingsSaveButton) {
    settingsSaveButton.addEventListener("click", async () => {
      settingsSaveButton.disabled = true;
      settingsSaveButton.textContent = "保存中…";
      try {
        await saveSettings();
      } catch (error) {
        showSettingsFeedback(`保存失败：${error.message}`, "error");
        showToast("设置保存失败");
      } finally {
        settingsSaveButton.disabled = false;
        settingsSaveButton.textContent = "保存设置";
      }
    });
    loadSettings();
  }

  if (testModelButton) {
    testModelButton.addEventListener("click", async () => {
      const original = testModelButton.textContent;
      testModelButton.disabled = true;
      testModelButton.textContent = "正在保存并测试…";
      try {
        await saveSettings({ quiet: true });
        const result = await apiRequest("/chat", {
          method: "POST",
          body: JSON.stringify({
            question: "这是模型连接测试。请只回复：模型连接正常。不要生成业务数据。",
            provider: activeProvider
          })
        });
        const traceNote = result.trace_id ? ` Trace ID：${result.trace_id}` : "";
        showSettingsFeedback(`连接成功：${result.provider} / ${result.model}。${traceNote}`);
        showToast("模型连接测试通过");
      } catch (error) {
        showSettingsFeedback(`模型测试失败：${error.message}`, "error");
        showToast("模型连接测试失败");
      } finally {
        testModelButton.disabled = false;
        testModelButton.textContent = original;
      }
    });
  }

  if (testDatabaseButton) {
    testDatabaseButton.addEventListener("click", async () => {
      const original = testDatabaseButton.textContent;
      testDatabaseButton.disabled = true;
      testDatabaseButton.textContent = "正在保存并测试…";
      try {
        await saveSettings({ quiet: true });
        const health = await apiRequest("/database/status");
        if (!health.connected || !health.read_only) throw new Error("数据库未进入只读连接状态");
        if (databaseResponse) {
          databaseResponse.textContent = `${health.response_ms} ms · ${health.company_name || `公司 ${health.company_id}`} · ${health.order_count} 张订单`;
        }
        setStatusBadge(databaseStatus, "只读已连接", "success");
        showSettingsFeedback(`数据库连接成功：${health.database} / ${health.user}，已确认只读。`);
        showToast("Odoo 只读连接测试通过");
        refreshDatabaseStatus();
      } catch (error) {
        if (databaseResponse) databaseResponse.textContent = error.message;
        setStatusBadge(databaseStatus, "连接失败", "warning");
        showSettingsFeedback(`数据库测试失败：${error.message}`, "error");
        showToast("数据库连接测试失败");
      } finally {
        testDatabaseButton.disabled = false;
        testDatabaseButton.textContent = original;
      }
    });
  }

  async function refreshDatabaseStatus() {
    const miniStatuses = document.querySelectorAll("[data-db-mini-status]");
    const contextDatabase = document.querySelector("[data-context-database]");
    const contextCurrency = document.querySelector("[data-context-currency]");
    const contextCompany = document.querySelector("[data-context-company]");
    if (!miniStatuses.length && !contextDatabase && !databaseResponse) return;
    try {
      const health = await apiRequest("/database/status");
      const label = health.connected && health.read_only
        ? `Odoo 19 · ${health.database} · 只读`
        : "Odoo 19 · 数据库未连接";
      miniStatuses.forEach((element) => {
        const text = element.querySelector("span:last-child");
        if (text) text.textContent = label;
        element.classList.toggle("offline", !health.connected);
      });
      if (contextDatabase) contextDatabase.textContent = health.connected ? `${health.database} · 只读` : "未连接";
      if (contextCurrency) contextCurrency.textContent = health.currency || "—";
      if (contextCompany) contextCompany.textContent = health.company_name || `公司 ${health.company_id}`;
      if (databaseResponse && health.connected) {
        databaseResponse.textContent = `${health.response_ms} ms · ${health.company_name || `公司 ${health.company_id}`} · ${health.order_count} 张订单`;
      }
      if (databaseStatus && health.connected) setStatusBadge(databaseStatus, "只读已连接", "success");
    } catch (_error) {
      miniStatuses.forEach((element) => element.classList.add("offline"));
      if (contextDatabase) contextDatabase.textContent = "后端未连接";
    }
  }

  refreshDatabaseStatus();

  const chatInput = document.querySelector("[data-chat-input]");
  const chatThread = document.querySelector("[data-chat-thread]");
  const sendButton = document.querySelector("[data-chat-send]");

  function setChatQuestion(question) {
    if (!chatInput) return;
    chatInput.value = question;
    chatInput.focus();
  }

  document.querySelectorAll("[data-question]").forEach((button) => {
    button.addEventListener("click", () => setChatQuestion(button.dataset.question));
  });

  const query = new URLSearchParams(window.location.search).get("q");
  if (query && chatInput) setChatQuestion(query);

  let chatSessionId = window.crypto?.randomUUID?.() || `chat-${Date.now()}`;
  let chatHistory = [];
  let chatSending = false;

  function displayValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number") {
      return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
    }
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function chartOption(spec, rows) {
    const xField = spec.x_field;
    const yFields = spec.y_fields || [];
    const palette = ["#714b67", "#017e84", "#e28b46", "#6c63a8"];
    if (spec.type === "pie") {
      const yField = yFields[0];
      return {
        tooltip: { trigger: "item" },
        legend: { bottom: 0 },
        series: [{
          name: yField,
          type: "pie",
          radius: ["42%", "70%"],
          data: rows.map((row) => ({ name: displayValue(row[xField]), value: row[yField] })),
          itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 }
        }],
        color: palette
      };
    }
    return {
      tooltip: { trigger: "axis" },
      legend: { bottom: 0 },
      grid: { left: 18, right: 20, top: 24, bottom: 48, containLabel: true },
      xAxis: { type: "category", data: rows.map((row) => displayValue(row[xField])), axisLabel: { color: "#6f6672" } },
      yAxis: { type: "value", axisLabel: { color: "#6f6672" }, splitLine: { lineStyle: { color: "#eee9ed" } } },
      series: yFields.map((field, index) => ({
        name: field,
        type: spec.type,
        data: rows.map((row) => row[field]),
        smooth: spec.type === "line",
        symbolSize: 7,
        itemStyle: { color: palette[index % palette.length] },
        lineStyle: { width: 3 }
      }))
    };
  }

  function appendResultCard(container, metadata) {
    const rows = metadata.rows || [];
    const columns = metadata.columns || [];
    if (!metadata.sql && !rows.length && !(metadata.warnings || []).length) return;

    const card = document.createElement("section");
    card.className = "agent-result-card";

    const summary = document.createElement("div");
    summary.className = "agent-result-summary";
    const summaryTitle = document.createElement("strong");
    summaryTitle.textContent = "Odoo 实时结果";
    const metricChips = document.createElement("div");
    metricChips.className = "result-chips";
    (metadata.metrics || []).forEach((metric) => {
      const chip = document.createElement("span");
      chip.textContent = metric;
      metricChips.appendChild(chip);
    });
    summary.append(summaryTitle, metricChips);
    const timing = document.createElement("small");
    timing.textContent = `${rows.length} 行${metadata.query_ms !== null && metadata.query_ms !== undefined ? ` · ${metadata.query_ms} ms` : ""}${metadata.truncated ? " · 已截断" : ""}`;
    summary.appendChild(timing);
    card.appendChild(summary);

    if (metadata.chart?.type === "kpi" && rows.length) {
      const kpis = document.createElement("div");
      kpis.className = "result-kpis";
      metadata.chart.y_fields.forEach((field) => {
        const item = document.createElement("div");
        const label = document.createElement("span");
        label.textContent = field;
        const value = document.createElement("strong");
        value.textContent = displayValue(rows[0][field]);
        item.append(label, value);
        kpis.appendChild(item);
      });
      card.appendChild(kpis);
    } else if (metadata.chart && metadata.chart.type !== "none" && rows.length) {
      const chart = document.createElement("div");
      chart.className = "agent-echart";
      card.appendChild(chart);
      window.requestAnimationFrame(() => {
        if (!window.echarts) {
          chart.textContent = "图表组件未加载，表格结果仍可正常查看。";
          chart.classList.add("chart-fallback");
          return;
        }
        const instance = window.echarts.init(chart);
        instance.setOption(chartOption(metadata.chart, rows));
        const observer = new ResizeObserver(() => instance.resize());
        observer.observe(chart);
      });
    }

    if (rows.length && columns.length) {
      const wrap = document.createElement("div");
      wrap.className = "agent-table-wrap";
      const table = document.createElement("table");
      table.className = "agent-result-table";
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      columns.forEach((column) => {
        const cell = document.createElement("th");
        cell.textContent = column;
        headRow.appendChild(cell);
      });
      head.appendChild(headRow);
      const body = document.createElement("tbody");
      rows.slice(0, 100).forEach((row) => {
        const tableRow = document.createElement("tr");
        columns.forEach((column) => {
          const cell = document.createElement("td");
          cell.textContent = displayValue(row[column]);
          tableRow.appendChild(cell);
        });
        body.appendChild(tableRow);
      });
      table.append(head, body);
      wrap.appendChild(table);
      card.appendChild(wrap);
    }

    if (metadata.sql) {
      const toggle = document.createElement("button");
      toggle.className = "btn result-sql-toggle";
      toggle.type = "button";
      toggle.textContent = "查看 SQL";
      const block = document.createElement("pre");
      block.className = "sql-block result-sql";
      block.textContent = metadata.sql;
      toggle.addEventListener("click", () => {
        block.classList.toggle("show");
        toggle.textContent = block.classList.contains("show") ? "收起 SQL" : "查看 SQL";
      });
      card.append(toggle, block);
    }

    (metadata.warnings || []).forEach((warning) => {
      const note = document.createElement("div");
      note.className = "result-warning";
      note.textContent = warning;
      card.appendChild(note);
    });
    container.appendChild(card);
  }

  function appendChatMessage(role, text, metadata = null) {
    if (!chatInput || !chatThread) return;
    const message = document.createElement("div");
    message.className = role === "user" ? "message user" : "message";

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "我" : "AI";

    const content = document.createElement("div");
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    const paragraph = document.createElement("p");
    paragraph.className = role === "assistant" ? "assistant-answer" : "";
    paragraph.textContent = text;
    bubble.appendChild(paragraph);
    content.appendChild(bubble);

    if (metadata && role === "assistant") {
      appendResultCard(content, metadata);
      const meta = document.createElement("div");
      meta.className = "chat-response-meta";
      const traceLabel = metadata.trace_id ? " · Langfuse Trace 已记录" : "";
      const phaseLabel = { "general-chat": "普通问答", "semantic-layer": "指标口径", "text2sql": "Odoo 只读查询" }[metadata.phase] || "智能回答";
      meta.textContent = `${phaseLabel} · ${metadata.provider} · ${metadata.model}${traceLabel}`;
      content.appendChild(meta);
    }

    message.appendChild(avatar);
    message.appendChild(content);
    chatThread.appendChild(message);
    chatThread.scrollTop = chatThread.scrollHeight;
    return message;
  }

  function appendWelcomeMessage() {
    appendChatMessage(
      "assistant",
      "新会话已开始。你可以普通聊天、询问销售指标口径，或直接查询 Odoo 实时销售数据。"
    );
  }

  function friendlyChatError(error) {
    const message = error?.message || "未知错误";
    if (message.includes("API key is not configured")) {
      return "当前模型还没有配置 API Key。请先到“数据与模型”页面填写并测试连接。";
    }
    if (message.includes("Model request failed") || message.includes("Agent request failed")) {
      return "模型调用失败。请到“数据与模型”页面检查 API Key、模型名称和连接地址。";
    }
    if (message.includes("Failed to fetch")) {
      return "无法连接本机后端，请确认应用仍在运行。";
    }
    return `请求失败：${message}`;
  }

  async function loadChatProviderStatus() {
    const statusElement = document.querySelector("[data-chat-provider-status]");
    if (!statusElement) return;
    try {
      const data = await apiRequest("/settings");
      const selected = data.selected_provider;
      const provider = data.providers[selected];
      const label = selected === "deepseek" ? "DeepSeek" : "硅基流动";
      statusElement.innerHTML = "";
      const dot = document.createElement("span");
      dot.className = "status-dot";
      statusElement.appendChild(dot);
      const modelLabel = provider.configured ? provider.model : "未配置";
      statusElement.append(`${label} · ${modelLabel}`);
      statusElement.title = provider.configured
        ? `当前供应商：${label}；当前模型：${provider.model}`
        : `${label} 尚未配置 API Key`;
    } catch (_error) {
      statusElement.textContent = "后端未连接";
    }
  }

  async function sendChatMessage() {
    if (!chatInput || !chatThread || chatSending) return;
    const question = chatInput.value.trim();
    if (!question) return;

    const priorHistory = chatHistory.slice(-12);
    chatHistory.push({ role: "user", content: question });
    appendChatMessage("user", question);
    chatInput.value = "";

    const thinking = document.createElement("div");
    thinking.className = "message assistant-thinking";
    thinking.innerHTML = `<div class="message-avatar">AI</div><div class="message-bubble"><div class="typing"><span></span><span></span><span></span></div></div>`;
    chatThread.appendChild(thinking);
    chatThread.scrollTop = chatThread.scrollHeight;
    chatSending = true;
    if (sendButton) sendButton.disabled = true;
    chatInput.disabled = true;

    try {
      const result = await apiRequest("/chat", {
        method: "POST",
        body: JSON.stringify({
          question,
          session_id: chatSessionId,
          history: priorHistory
        })
      });
      thinking.remove();
      appendChatMessage("assistant", result.answer, result);
      chatHistory.push({ role: "assistant", content: result.answer });
    } catch (error) {
      thinking.remove();
      chatHistory.pop();
      const errorMessage = appendChatMessage("assistant", friendlyChatError(error));
      errorMessage?.querySelector(".message-bubble")?.classList.add("error");
    } finally {
      chatSending = false;
      if (sendButton) sendButton.disabled = false;
      chatInput.disabled = false;
      chatInput.focus();
    }
  }

  if (sendButton) sendButton.addEventListener("click", sendChatMessage);
  if (chatInput) {
    chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendChatMessage();
      }
    });
    loadChatProviderStatus();
  }

  const newChatButton = document.querySelector("[data-new-chat]");
  if (newChatButton) {
    newChatButton.addEventListener("click", () => {
      chatSessionId = window.crypto?.randomUUID?.() || `chat-${Date.now()}`;
      chatHistory = [];
      chatThread.innerHTML = "";
      appendWelcomeMessage();
      showToast("已开始新会话");
      chatInput?.focus();
    });
  }
})();
