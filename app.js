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
  providerCards.forEach((card) => {
    card.addEventListener("click", () => {
      providerCards.forEach((item) => item.classList.remove("selected"));
      card.classList.add("selected");
      const baseUrl = document.querySelector("[data-provider-url]");
      const model = document.querySelector("[data-provider-model]");
      if (baseUrl) baseUrl.value = card.dataset.url;
      if (model) model.value = card.dataset.model;
      showToast(`已选择 ${card.dataset.provider}`);
    });
  });

  const testModelButton = document.querySelector("[data-test-model]");
  if (testModelButton) {
    testModelButton.addEventListener("click", () => {
      const original = testModelButton.textContent;
      testModelButton.disabled = true;
      testModelButton.textContent = "正在测试…";
      window.setTimeout(() => {
        testModelButton.disabled = false;
        testModelButton.textContent = "✓ 连接正常";
        showToast("模型连接测试通过（静态演示）");
        window.setTimeout(() => { testModelButton.textContent = original; }, 1800);
      }, 850);
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
