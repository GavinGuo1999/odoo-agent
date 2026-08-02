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

  const providerDrafts = {
    deepseek: {
      base_url: "https://api.deepseek.com",
      model: "deepseek-v4-pro",
      configured: false,
      draftKey: ""
    },
    siliconflow: {
      base_url: "https://api.siliconflow.cn/v1",
      model: "deepseek-ai/DeepSeek-V3.1-Terminus",
      configured: false,
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
      showSettingsFeedback("配置已从本机后端读取。密钥只显示状态，不会回显明文。");
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
      }
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
    });
  });

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

  const sqlToggle = document.querySelector("[data-sql-toggle]");
  if (sqlToggle) {
    sqlToggle.addEventListener("click", () => {
      const sqlBlock = document.querySelector("[data-sql-block]");
      if (!sqlBlock) return;
      sqlBlock.classList.toggle("show");
      sqlToggle.textContent = sqlBlock.classList.contains("show") ? "收起 SQL" : "查看 SQL";
    });
  }

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

  function sendDemoMessage() {
    if (!chatInput || !chatThread) return;
    const question = chatInput.value.trim();
    if (!question) return;

    const userMessage = document.createElement("div");
    userMessage.className = "message user";
    userMessage.innerHTML = `<div class="message-avatar">我</div><div class="message-bubble"><p></p></div>`;
    userMessage.querySelector("p").textContent = question;
    chatThread.appendChild(userMessage);
    chatInput.value = "";

    const thinking = document.createElement("div");
    thinking.className = "message assistant-thinking";
    thinking.innerHTML = `<div class="message-avatar">AI</div><div class="message-bubble"><div class="typing"><span></span><span></span><span></span></div></div>`;
    chatThread.appendChild(thinking);
    chatThread.scrollTop = chatThread.scrollHeight;

    window.setTimeout(() => {
      thinking.remove();
      const reply = document.createElement("div");
      reply.className = "message";
      reply.innerHTML = `
        <div class="message-avatar">AI</div>
        <div>
          <div class="message-bubble">
            <div class="analysis-steps">
              <span class="step-chip done">✓ 已识别销售指标</span>
              <span class="step-chip done">✓ SQL 校验通过</span>
              <span class="step-chip done">✓ 查询完成 0.18s</span>
            </div>
            <p>演示模式已理解你的问题。实际接入后，这里会根据 Odoo 实时数据生成结论、表格和合适的图表。</p>
          </div>
          <div class="answer-card">
            <div class="answer-summary">
              <div><div class="answer-summary-label">查询结果示例</div><div class="answer-summary-value">¥ 86,420</div><div class="kpi-note"><span class="delta-up">↑ 12.8%</span> 较上月</div></div>
              <svg class="mini-chart" viewBox="0 0 170 62" role="img" aria-label="销售额上升趋势"><path d="M4 54 C22 47,34 50,51 38 S82 30,99 35 S131 18,166 7" fill="none" stroke="#714b67" stroke-width="3" stroke-linecap="round"/></svg>
            </div>
            <div class="answer-body"><div class="answer-insight">本月已确认订单共 112 张，销售额较上月增加约 ¥9,820。</div><button class="btn btn-soft" type="button" data-new-sql>查看 SQL</button></div>
          </div>
        </div>`;
      chatThread.appendChild(reply);
      const localButton = reply.querySelector("[data-new-sql]");
      localButton.addEventListener("click", () => showToast("实际版本将在此展开 SQL"));
      chatThread.scrollTop = chatThread.scrollHeight;
    }, 900);
  }

  if (sendButton) sendButton.addEventListener("click", sendDemoMessage);
  if (chatInput) {
    chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendDemoMessage();
      }
    });
  }
})();
