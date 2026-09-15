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
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  document.querySelectorAll("[data-toast-message]").forEach((button) => {
    button.addEventListener("click", () => showToast(button.dataset.toastMessage));
  });

  function formatNumber(value, maximumFractionDigits = 2) {
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits }).format(Number(value || 0));
  }

  function formatMoney(value, currency) {
    const amount = formatNumber(value, 2);
    const symbol = currency?.symbol || currency?.code || "";
    return currency?.position === "after" ? `${amount} ${symbol}` : `${symbol} ${amount}`.trim();
  }

  function setComparisonNote(element, comparison, label) {
    if (!element) return;
    element.innerHTML = "";
    const change = comparison?.change_pct;
    if (change === null || change === undefined) {
      element.textContent = `${label}为 0，暂无百分比`;
      return;
    }
    const marker = document.createElement("span");
    marker.className = change > 0 ? "delta-up" : (change < 0 ? "delta-down" : "");
    marker.textContent = change > 0 ? `↑ ${formatNumber(change)}%` : (change < 0 ? `↓ ${formatNumber(Math.abs(change))}%` : "持平");
    element.append(marker, ` ${label}`);
  }

  function emptyChartOption(message) {
    return {
      animation: false,
      graphic: [{
        type: "text",
        left: "center",
        top: "middle",
        style: { text: message, fill: "#837985", fontSize: 13 }
      }]
    };
  }

  function renderDashboardChart(element, option) {
    if (!element) return;
    if (!window.echarts) {
      element.textContent = "图表组件没有加载，数据表仍可正常使用。";
      element.classList.add("chart-fallback");
      return;
    }
    element.classList.remove("chart-fallback");
    element.textContent = "";
    const existingChart = window.echarts.getInstanceByDom(element);
    if (existingChart) existingChart.dispose();
    const chart = window.echarts.init(element, null, { renderer: "svg" });
    chart.setOption(option);
    if (!element.dataset.resizeBound) {
      new ResizeObserver(() => window.echarts.getInstanceByDom(element)?.resize()).observe(element);
      element.dataset.resizeBound = "true";
    }
  }

  function salesLineOption(points, currency) {
    if (!points.some((point) => Number(point.sales_amount) !== 0)) {
      return emptyChartOption("所选期间暂无已确认销售订单");
    }
    return {
      animation: false,
      tooltip: {
        trigger: "axis",
        valueFormatter: (value) => formatMoney(value, currency)
      },
      grid: { left: 22, right: 24, top: 28, bottom: 34, containLabel: true },
      xAxis: { type: "category", boundaryGap: false, data: points.map((point) => point.label), axisLabel: { color: "#746b77" } },
      yAxis: { type: "value", axisLabel: { color: "#746b77" }, splitLine: { lineStyle: { color: "#eee9ef" } } },
      series: [{
        name: "销售额",
        type: "line",
        smooth: true,
        symbolSize: 7,
        data: points.map((point) => point.sales_amount),
        lineStyle: { color: "#714b67", width: 3 },
        itemStyle: { color: "#714b67" },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: "rgba(113,75,103,.28)" }, { offset: 1, color: "rgba(113,75,103,0)" }]
          }
        }
      }]
    };
  }

  function statusPieOption(items, currency) {
    if (!items.length) return emptyChartOption("所选期间暂无销售订单");
    return {
      animation: false,
      tooltip: { trigger: "item", formatter: (params) => `${params.name}<br>${formatMoney(params.value, currency)} · ${params.percent}%` },
      legend: { bottom: 2, type: "scroll" },
      color: ["#714b67", "#ef7c55", "#017e84", "#8f86bd", "#b7adb8"],
      series: [{
        type: "pie",
        radius: ["42%", "68%"],
        center: ["50%", "44%"],
        label: { formatter: "{b}\n{d}%" },
        data: items.map((item) => ({ name: `${item.label}（${item.order_count}）`, value: item.sales_amount }))
      }]
    };
  }

  function customerBarOption(items, currency) {
    if (!items.length) return emptyChartOption("所选期间暂无客户销售数据");
    const selected = items.slice(0, 7);
    return {
      animation: false,
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (value) => formatMoney(value, currency) },
      grid: { left: 16, right: 30, top: 18, bottom: 18, containLabel: true },
      xAxis: { type: "value", axisLabel: { color: "#746b77" }, splitLine: { lineStyle: { color: "#eee9ef" } } },
      yAxis: { type: "category", inverse: true, data: selected.map((item) => item.customer), axisLabel: { color: "#4f4651", width: 120, overflow: "truncate" } },
      series: [{ type: "bar", data: selected.map((item) => item.sales_amount), barMaxWidth: 24, itemStyle: { color: "#714b67", borderRadius: [0, 7, 7, 0] } }]
    };
  }

  function stateBadgeClass(state) {
    if (state === "draft" || state === "cancel") return "badge neutral";
    if (state === "sent") return "badge warning";
    return "badge";
  }

  async function loadHomeData() {
    const trendElement = document.querySelector("[data-home-trend]");
    if (!trendElement) return;
    const refreshButton = document.querySelector("[data-home-refresh]");
    if (refreshButton) refreshButton.disabled = true;
    try {
      const [data, settings] = await Promise.all([
        apiRequest("/sales/dashboard?period=month"),
        apiRequest("/settings")
      ]);
      const dateElement = document.querySelector("[data-current-date]");
      if (dateElement) {
        dateElement.textContent = `${new Intl.DateTimeFormat("zh-CN", { dateStyle: "long" }).format(new Date(data.generated_at))} · 销售数据概览`;
      }
      const selectedProvider = settings.providers[settings.selected_provider];
      const modelElement = document.querySelector("[data-home-model]");
      if (modelElement) modelElement.textContent = `${settings.selected_provider === "deepseek" ? "DeepSeek" : "硅基流动"} · ${selectedProvider.model}`;
      const sourceElement = document.querySelector("[data-home-source]");
      if (sourceElement) sourceElement.textContent = `${settings.database.database} · ${data.company_name}`;
      document.querySelectorAll("[data-currency-icon]").forEach((item) => { item.textContent = data.currency.symbol || data.currency.code; });

      const kpis = data.kpis;
      document.querySelector('[data-home-kpi="sales_amount"]').textContent = formatMoney(kpis.sales_amount.current, data.currency);
      document.querySelector('[data-home-kpi="order_count"]').textContent = formatNumber(kpis.order_count.current, 0);
      document.querySelector('[data-home-kpi="average_order_value"]').textContent = formatMoney(kpis.average_order_value.current, data.currency);
      setComparisonNote(document.querySelector('[data-home-note="sales_amount"]'), kpis.sales_amount, data.date_range.comparison_label);
      setComparisonNote(document.querySelector('[data-home-note="order_count"]'), kpis.order_count, data.date_range.comparison_label);
      setComparisonNote(document.querySelector('[data-home-note="average_order_value"]'), kpis.average_order_value, data.date_range.comparison_label);
      const pending = data.attention.find((item) => item.key === "pending_delivery");
      document.querySelector('[data-home-kpi="pending_delivery"]').textContent = formatNumber(pending?.order_count || 0, 0);
      document.querySelector('[data-home-note="pending_delivery"]').textContent = `${formatMoney(pending?.sales_amount || 0, data.currency)} 尚待交付`;
      renderDashboardChart(trendElement, salesLineOption(data.monthly_trend, data.currency));

      const tbody = document.querySelector("[data-recent-orders]");
      tbody.innerHTML = "";
      if (!data.recent_orders.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Odoo 中暂无销售订单</td></tr>';
      } else {
        data.recent_orders.forEach((order) => {
          const row = document.createElement("tr");
          const values = [order.order_name, order.customer, order.salesperson, String(order.order_date).slice(0, 10)];
          values.forEach((value, index) => {
            const cell = document.createElement("td");
            if (index === 0) {
              const strong = document.createElement("strong");
              strong.textContent = value;
              cell.appendChild(strong);
            } else cell.textContent = value;
            row.appendChild(cell);
          });
          const statusCell = document.createElement("td");
          const badge = document.createElement("span");
          badge.className = stateBadgeClass(order.state);
          badge.textContent = order.state_label;
          statusCell.appendChild(badge);
          const amountCell = document.createElement("td");
          amountCell.className = "number";
          amountCell.textContent = formatMoney(order.amount_untaxed, data.currency);
          row.append(statusCell, amountCell);
          tbody.appendChild(row);
        });
      }
      const updated = document.querySelector("[data-home-updated]");
      if (updated) updated.textContent = `来自 Odoo · ${new Date(data.generated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })} 更新`;
    } catch (error) {
      trendElement.textContent = `真实数据读取失败：${error.message}`;
      const tbody = document.querySelector("[data-recent-orders]");
      if (tbody) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 6;
        cell.className = "table-empty";
        cell.textContent = `真实数据读取失败：${error.message}`;
        row.appendChild(cell);
        tbody.replaceChildren(row);
      }
    } finally {
      if (refreshButton) refreshButton.disabled = false;
    }
  }

  let dashboardDataCache = null;
  let dashboardRequestNumber = 0;

  async function loadDashboardData() {
    const trendElement = document.querySelector("[data-dashboard-trend]");
    if (!trendElement) return;
    const requestNumber = ++dashboardRequestNumber;
    const activePeriod = document.querySelector("[data-period].active")?.dataset.period || "month";
    const salespersonFilter = document.querySelector("[data-salesperson-filter]");
    const salespersonId = salespersonFilter?.value || "";
    const refreshButton = document.querySelector("[data-dashboard-refresh]");
    if (refreshButton) refreshButton.disabled = true;
    try {
      const suffix = salespersonId ? `&salesperson_id=${encodeURIComponent(salespersonId)}` : "";
      const data = await apiRequest(`/sales/dashboard?period=${activePeriod}${suffix}`);
      if (requestNumber !== dashboardRequestNumber) return;
      dashboardDataCache = data;
      document.querySelectorAll("[data-currency-icon]").forEach((item) => { item.textContent = data.currency.symbol || data.currency.code; });
      const rangeElement = document.querySelector("[data-dashboard-range]");
      if (rangeElement) rangeElement.textContent = `${data.date_range.start} 至 ${data.date_range.end} · 已确认订单 · 未税金额`;
      const periodLabel = data.period === "week" ? "本周" : (data.period === "quarter" ? "本季度" : "本月");
      const statusSubtitle = document.querySelector("[data-dashboard-status-subtitle]");
      if (statusSubtitle) statusSubtitle.textContent = `${periodLabel}订单金额分布`;
      const customerAnalysisLink = document.querySelector("[data-dashboard-customer-ai]");
      if (customerAnalysisLink) customerAnalysisLink.href = `chat.html?q=${encodeURIComponent(`分析一下${periodLabel}销售额最高的客户`)}`;
      const updatedElement = document.querySelector("[data-dashboard-updated]");
      if (updatedElement) updatedElement.textContent = `${new Date(data.generated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })} 更新`;

      if (salespersonFilter) {
        const selected = salespersonFilter.value;
        salespersonFilter.innerHTML = '<option value="">全部销售员</option>';
        data.salespeople.forEach((person) => {
          const option = document.createElement("option");
          option.value = String(person.user_id);
          option.textContent = person.name;
          salespersonFilter.appendChild(option);
        });
        salespersonFilter.value = selected;
      }

      const keys = ["sales_amount", "order_count", "average_order_value", "active_customers"];
      keys.forEach((key) => {
        const valueElement = document.querySelector(`[data-dashboard-kpi="${key}"]`);
        const value = data.kpis[key].current;
        valueElement.textContent = key === "sales_amount" || key === "average_order_value"
          ? formatMoney(value, data.currency)
          : formatNumber(value, 0);
        setComparisonNote(document.querySelector(`[data-dashboard-note="${key}"]`), data.kpis[key], data.date_range.comparison_label);
      });
      const trendChange = document.querySelector("[data-trend-change]");
      if (trendChange) {
        const change = data.kpis.sales_amount.change_pct;
        trendChange.textContent = change === null ? "上期为 0" : `${data.date_range.comparison_label} ${change > 0 ? "+" : ""}${formatNumber(change)}%`;
        trendChange.classList.toggle("warning", Number(change) < 0);
      }
      const trendSubtitle = document.querySelector("[data-trend-subtitle]");
      if (trendSubtitle) trendSubtitle.textContent = data.period === "quarter" ? "按月销售额" : "按日销售额";
      renderDashboardChart(trendElement, salesLineOption(data.trend, data.currency));
      renderDashboardChart(document.querySelector("[data-dashboard-status]"), statusPieOption(data.order_statuses, data.currency));
      renderDashboardChart(document.querySelector("[data-dashboard-customers]"), customerBarOption(data.customers, data.currency));

      const attention = document.querySelector("[data-dashboard-attention]");
      attention.innerHTML = "";
      data.attention.forEach((item, index) => {
        const link = document.createElement("a");
        link.className = "quick-item";
        link.href = `chat.html?q=${encodeURIComponent(item.question)}`;
        const icon = document.createElement("span");
        icon.className = "quick-item-icon";
        icon.textContent = ["↗", "▤", "⌛"][index] || "!";
        const copy = document.createElement("span");
        copy.className = "quick-item-copy";
        const title = document.createElement("span");
        title.className = "quick-item-title";
        title.textContent = item.label;
        const description = document.createElement("span");
        description.className = "quick-item-desc";
        description.textContent = `${item.order_count} 张订单 · ${formatMoney(item.sales_amount, data.currency)}`;
        copy.append(title, description);
        const badge = document.createElement("span");
        badge.className = item.order_count ? "badge warning" : "badge neutral";
        badge.textContent = String(item.order_count);
        link.append(icon, copy, badge);
        attention.appendChild(link);
      });

      const products = document.querySelector("[data-product-ranking]");
      products.innerHTML = "";
      if (!data.products.length) {
        products.innerHTML = '<tr><td colspan="5" class="table-empty">所选期间暂无产品销售数据</td></tr>';
      } else {
        data.products.forEach((product, index) => {
          const row = document.createElement("tr");
          [index + 1, product.product, formatNumber(product.quantity), formatMoney(product.sales_amount, data.currency), `${formatNumber(product.percentage)}%`].forEach((value, cellIndex) => {
            const cell = document.createElement("td");
            if (cellIndex >= 2) cell.className = "number";
            if (cellIndex === 1) {
              const strong = document.createElement("strong");
              strong.textContent = String(value);
              cell.appendChild(strong);
            } else cell.textContent = String(value);
            row.appendChild(cell);
          });
          products.appendChild(row);
        });
      }
    } catch (error) {
      trendElement.textContent = `真实数据读取失败：${error.message}`;
      showToast("销售看板读取失败");
    } finally {
      if (requestNumber === dashboardRequestNumber && refreshButton) refreshButton.disabled = false;
    }
  }

  document.querySelectorAll("[data-period]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-period]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      loadDashboardData();
    });
  });

  const salespersonFilter = document.querySelector("[data-salesperson-filter]");
  if (salespersonFilter) salespersonFilter.addEventListener("change", loadDashboardData);
  const dashboardRefreshButton = document.querySelector("[data-dashboard-refresh]");
  if (dashboardRefreshButton) dashboardRefreshButton.addEventListener("click", loadDashboardData);
  const homeRefreshButton = document.querySelector("[data-home-refresh]");
  if (homeRefreshButton) homeRefreshButton.addEventListener("click", loadHomeData);

  const dashboardExportButton = document.querySelector("[data-dashboard-export]");
  if (dashboardExportButton) {
    dashboardExportButton.addEventListener("click", () => {
      if (!dashboardDataCache) return;
      const rows = [["排名", "产品", "销售数量", "销售额", "占比"]];
      dashboardDataCache.products.forEach((product, index) => rows.push([
        index + 1, product.product, product.quantity, product.sales_amount, product.percentage
      ]));
      const csv = `\uFEFF${rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\r\n")}`;
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `odoo-product-sales-${dashboardDataCache.period}.csv`;
      link.click();
      URL.revokeObjectURL(url);
      showToast("已导出当前真实产品销售数据");
    });
  }

  loadHomeData();
  loadDashboardData();

  const wikiStatusElement = document.querySelector("[data-wiki-status]");
  const wikiTopStatus = document.querySelector("[data-wiki-top-status]");
  const wikiSearchForm = document.querySelector("[data-wiki-search-form]");
  const wikiQueryInput = document.querySelector("[data-wiki-query]");
  const wikiResults = document.querySelector("[data-wiki-results]");
  const wikiReindexButton = document.querySelector("[data-wiki-reindex]");

  function wikiDate(value) {
    if (!value) return "尚未建立";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN");
  }

  function renderWikiStatus(status) {
    if (!wikiStatusElement) return;
    const values = wikiStatusElement.querySelectorAll("strong");
    if (values[0]) values[0].textContent = formatNumber(status.note_count, 0);
    if (values[1]) values[1].textContent = formatNumber(status.chunk_count, 0);
    if (values[2]) values[2].textContent = wikiDate(status.indexed_at);
    if (wikiTopStatus) {
      const text = wikiTopStatus.querySelector("span:last-child");
      if (text) text.textContent = status.available
        ? `${status.note_count} 篇已审核笔记 · 索引可用`
        : `Wiki 不可用${status.error_type ? ` · ${status.error_type}` : ""}`;
      wikiTopStatus.classList.toggle("error", !status.available);
    }
  }

  function wikiHitCard(hit, index) {
    const article = document.createElement("article");
    article.className = "card wiki-result-card";
    const rank = document.createElement("span");
    rank.className = "wiki-result-rank";
    rank.textContent = String(index + 1).padStart(2, "0");
    const body = document.createElement("div");
    const heading = document.createElement("div");
    heading.className = "wiki-result-heading";
    const title = document.createElement("h3");
    title.textContent = hit.title;
    const badge = document.createElement("span");
    badge.className = "badge neutral";
    badge.textContent = hit.via_wikilink ? "关联笔记" : `相关度 ${Number(hit.score).toFixed(1)}`;
    heading.append(title, badge);
    const section = document.createElement("strong");
    section.className = "wiki-result-section";
    section.textContent = hit.heading || "概述";
    const excerpt = document.createElement("p");
    excerpt.textContent = hit.excerpt;
    const footer = document.createElement("div");
    footer.className = "wiki-result-footer";
    const path = document.createElement("span");
    path.textContent = hit.relative_path;
    const open = document.createElement("a");
    open.className = "btn btn-soft";
    open.href = hit.obsidian_uri;
    open.textContent = "在 Obsidian 打开 ↗";
    footer.append(path, open);
    body.append(heading, section, excerpt, footer);
    article.append(rank, body);
    return article;
  }

  async function loadWikiStatus() {
    if (!wikiStatusElement) return;
    try {
      renderWikiStatus(await apiRequest("/wiki/status"));
    } catch (error) {
      if (wikiTopStatus) wikiTopStatus.textContent = `Wiki 状态读取失败：${error.message}`;
    }
  }

  async function searchWiki(query) {
    if (!wikiResults || !wikiQueryInput) return;
    const value = (query || wikiQueryInput.value).trim();
    if (value.length < 2) {
      showToast("请至少输入两个字符");
      return;
    }
    wikiQueryInput.value = value;
    wikiResults.innerHTML = '<div class="card wiki-empty">正在检索已审核笔记…</div>';
    try {
      const result = await apiRequest(`/wiki/search?q=${encodeURIComponent(value)}&limit=8`);
      wikiResults.innerHTML = "";
      if (!result.hits.length) {
        wikiResults.innerHTML = '<div class="card wiki-empty">没有找到可引用的已审核笔记。试试更具体的模型、字段或方法名。</div>';
        return;
      }
      result.hits.forEach((hit, index) => wikiResults.appendChild(wikiHitCard(hit, index)));
    } catch (error) {
      wikiResults.textContent = `Wiki 检索失败：${error.message}`;
    }
  }

  if (wikiSearchForm) {
    wikiSearchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      searchWiki();
    });
  }
  if (wikiReindexButton) {
    wikiReindexButton.addEventListener("click", async () => {
      wikiReindexButton.disabled = true;
      wikiReindexButton.textContent = "正在索引…";
      try {
        renderWikiStatus(await apiRequest("/wiki/reindex", { method: "POST" }));
        showToast("Wiki 索引已更新");
        if (wikiQueryInput?.value.trim()) await searchWiki();
      } catch (error) {
        showToast(`索引失败：${error.message}`);
      } finally {
        wikiReindexButton.disabled = false;
        wikiReindexButton.textContent = "重新建立索引";
      }
    });
  }
  if (wikiStatusElement) {
    loadWikiStatus();
    const initialWikiQuery = new URLSearchParams(window.location.search).get("q");
    if (initialWikiQuery) searchWiki(initialWikiQuery);
  }

  const providerCards = document.querySelectorAll("[data-provider]");
  const providerUrlInput = document.querySelector("[data-provider-url]");
  const providerModelInput = document.querySelector("[data-provider-model]");
  const providerModelOptions = document.querySelector("[data-provider-model-options]");
  const providerModelHelp = document.querySelector("[data-provider-model-help]");
  const providerInputPrice = document.querySelector("[data-provider-input-price]");
  const providerOutputPrice = document.querySelector("[data-provider-output-price]");
  const providerPricingCurrency = document.querySelector("[data-provider-pricing-currency]");
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
  const databaseCostLimitInput = document.querySelector("[data-db-cost-limit]");
  const databaseResponse = document.querySelector("[data-db-response]");
  const testDatabaseButton = document.querySelector("[data-test-database]");
  const metricsTable = document.querySelector("[data-metrics-table]");
  const semanticVersion = document.querySelector("[data-semantic-version]");
  const semanticProviderInput = document.querySelector("[data-semantic-provider]");
  const semanticProviderHelp = document.querySelector("[data-semantic-provider-help]");
  const semanticAuditStatus = document.querySelector("[data-semantic-audit-status]");
  const semanticAuditTime = document.querySelector("[data-semantic-audit-time]");
  const semanticAuditScope = document.querySelector("[data-semantic-audit-scope]");
  const semanticAuditCounts = document.querySelector("[data-semantic-audit-counts]");
  const semanticAuditSummary = document.querySelector("[data-semantic-audit-summary]");
  const semanticAuditReport = document.querySelector("[data-semantic-audit-report]");
  const runSemanticAuditButton = document.querySelector("[data-run-semantic-audit]");
  const schemaList = document.querySelector("[data-schema-list]");
  const queryMaxRows = document.querySelector("[data-query-max-rows]");
  const queryTimeout = document.querySelector("[data-query-timeout]");
  const roleProviderInputs = document.querySelectorAll("[data-role-provider]");
  const roleModelInputs = document.querySelectorAll("[data-role-model]");
  const sqlThinkingModeInput = document.querySelector("[data-sql-thinking-mode]");
  const stateDatabaseStatus = document.querySelector("[data-state-db-status]");
  const stateDatabaseEnabled = document.querySelector("[data-state-db-enabled]");
  const stateDatabaseHost = document.querySelector("[data-state-db-host]");
  const stateDatabasePort = document.querySelector("[data-state-db-port]");
  const stateDatabaseName = document.querySelector("[data-state-db-name]");
  const stateDatabaseUser = document.querySelector("[data-state-db-user]");
  const stateDatabasePassword = document.querySelector("[data-state-db-password]");
  const stateDatabaseHelp = document.querySelector("[data-state-db-help]");

  const providerDrafts = {
    deepseek: {
      base_url: "https://api.deepseek.com",
      model: "deepseek-v4-pro",
      configured: false,
      models: [],
      draftKey: "",
      input_price_per_million: 0.435,
      output_price_per_million: 0.87,
      pricing_currency: "USD"
    },
    siliconflow: {
      base_url: "https://api.siliconflow.cn/v1",
      model: "deepseek-ai/DeepSeek-V3.1-Terminus",
      configured: false,
      models: [],
      draftKey: "",
      input_price_per_million: 4,
      output_price_per_million: 12,
      pricing_currency: "CNY"
    }
  };
  const routingDraft = {
    sql: { provider: "deepseek", model: "deepseek-v4-pro" },
    answer: { provider: "deepseek", model: "deepseek-v4-pro" },
    general: { provider: "deepseek", model: "deepseek-v4-pro" }
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
    draft.input_price_per_million = Number(providerInputPrice?.value || 0);
    draft.output_price_per_million = Number(providerOutputPrice?.value || 0);
  }

  function renderActiveProvider() {
    const draft = providerDrafts[activeProvider];
    providerCards.forEach((card) => {
      card.classList.toggle("selected", card.dataset.provider === activeProvider);
    });
    if (providerUrlInput) providerUrlInput.value = draft.base_url;
    if (providerModelInput) providerModelInput.value = draft.model;
    if (providerInputPrice) providerInputPrice.value = draft.input_price_per_million;
    if (providerOutputPrice) providerOutputPrice.value = draft.output_price_per_million;
    if (providerPricingCurrency) {
      providerPricingCurrency.textContent = `${draft.pricing_currency} / 百万 Token，用于 Langfuse Cost 估算`;
    }
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
    if (databaseCostLimitInput) databaseCostLimitInput.value = config.explain_total_cost_limit;
    if (queryMaxRows) queryMaxRows.textContent = formatNumber(config.max_rows, 0);
    if (queryTimeout) queryTimeout.textContent = `${formatNumber(config.statement_timeout_ms / 1000)} 秒`;
    if (databasePasswordInput) {
      databasePasswordInput.value = "";
      databasePasswordInput.placeholder = config.password_configured
        ? "已配置，留空不修改"
        : "本机免密时可留空";
    }
    setStatusBadge(databaseStatus, config.configured ? "已配置" : "未配置", config.configured ? "success" : "warning");
  }

  function renderRoutingSettings(config) {
    if (!config) return;
    Object.entries(config).forEach(([role, value]) => {
      routingDraft[role] = { ...value };
      const providerInput = document.querySelector(`[data-role-provider="${role}"]`);
      const modelInput = document.querySelector(`[data-role-model="${role}"]`);
      if (providerInput) providerInput.value = value.provider;
      if (modelInput) modelInput.value = value.model;
    });
  }

  function renderSemanticSettings(config) {
    if (!config) return;
    if (semanticProviderInput) semanticProviderInput.value = config.provider;
    if (semanticProviderHelp) {
      const ready = config.wren_project_configured && config.wren_executable_configured;
      semanticProviderHelp.textContent = config.provider === "wren"
        ? (ready
          ? "Wren 已就绪：MDL 会生成语义上下文，并在 SQLGlot 校验前执行 dry-plan。"
          : "Wren 尚未就绪；请重新运行安装依赖后再启用。")
        : "原生语义层是稳定基线，可随时从 Wren 一键切回。";
    }
  }

  function captureRoutingSettings() {
    roleProviderInputs.forEach((input) => {
      const role = input.dataset.roleProvider;
      if (routingDraft[role]) routingDraft[role].provider = input.value;
    });
    roleModelInputs.forEach((input) => {
      const role = input.dataset.roleModel;
      if (routingDraft[role]) routingDraft[role].model = input.value.trim();
    });
  }

  function renderStateDatabaseSettings(config) {
    if (!config) return;
    if (stateDatabaseEnabled) stateDatabaseEnabled.checked = config.enabled;
    if (stateDatabaseHost) stateDatabaseHost.value = config.host;
    if (stateDatabasePort) stateDatabasePort.value = config.port;
    if (stateDatabaseName) stateDatabaseName.value = config.database;
    if (stateDatabaseUser) stateDatabaseUser.value = config.user;
    if (stateDatabasePassword) {
      stateDatabasePassword.value = "";
      stateDatabasePassword.placeholder = config.password_configured
        ? "已配置，留空不修改"
        : "请输入独立状态库密码";
    }
    const label = config.active_mode === "postgres"
      ? "PostgreSQL 已连接"
      : (config.error_type ? "连接失败" : "内存模式");
    setStatusBadge(
      stateDatabaseStatus,
      label,
      config.active_mode === "postgres" ? "success" : (config.error_type ? "warning" : "neutral")
    );
    if (stateDatabaseHelp) {
      stateDatabaseHelp.textContent = config.error_type
        ? `状态库启动失败：${config.error_type}。当前已安全降级为内存模式。`
        : (config.active_mode === "postgres"
          ? "会话、Checkpoint 与 Interrupt 已持久化到独立状态库。"
          : "未启用时使用进程内存；启用或修改后需要重启应用。");
    }
  }

  function stateDatabasePayload() {
    return {
      enabled: stateDatabaseEnabled?.checked ?? false,
      host: stateDatabaseHost?.value.trim() || "127.0.0.1",
      port: Number(stateDatabasePort?.value || 55432),
      database: stateDatabaseName?.value.trim() || "odoo_agent_state",
      user: stateDatabaseUser?.value.trim() || "odoo_agent_state",
      password: stateDatabasePassword?.value.trim() || null
    };
  }

  async function loadSemanticConfiguration() {
    if (!metricsTable && !schemaList) return;
    try {
      const [metrics, schema] = await Promise.all([
        apiRequest("/sales/metrics"),
        apiRequest("/database/schema")
      ]);
      if (semanticVersion) semanticVersion.textContent = metrics.version;
      if (metricsTable) {
        metricsTable.innerHTML = "";
        metrics.metrics.forEach((metric) => {
          const row = document.createElement("tr");
          const nameCell = document.createElement("td");
          const name = document.createElement("strong");
          name.textContent = metric.name;
          const description = document.createElement("div");
          description.className = "page-subtitle";
          description.textContent = metric.description;
          nameCell.append(name, description);
          const expressionCell = document.createElement("td");
          const expression = document.createElement("code");
          expression.textContent = metric.expression;
          expressionCell.appendChild(expression);
          const statesCell = document.createElement("td");
          const states = document.createElement("code");
          states.textContent = metric.states.join(", ");
          statesCell.appendChild(states);
          const dateCell = document.createElement("td");
          const dateField = document.createElement("code");
          dateField.textContent = metric.date_field;
          dateCell.appendChild(dateField);
          const enabledCell = document.createElement("td");
          const enabled = document.createElement("span");
          enabled.className = "badge";
          enabled.textContent = "已开放";
          enabledCell.appendChild(enabled);
          row.append(nameCell, expressionCell, statesCell, dateCell, enabledCell);
          metricsTable.appendChild(row);
        });
      }
      if (schemaList) {
        schemaList.innerHTML = "";
        schema.tables.forEach((table) => {
          const row = document.createElement("div");
          row.className = "schema-row";
          const copy = document.createElement("span");
          const name = document.createElement("strong");
          name.textContent = table.name;
          const description = document.createElement("span");
          description.className = "page-subtitle";
          description.textContent = `${table.description} · ${table.columns.length} 个开放字段`;
          copy.append(name, document.createElement("br"), description);
          const badge = document.createElement("span");
          badge.className = "badge";
          badge.textContent = "已开放";
          row.append(copy, badge);
          schemaList.appendChild(row);
        });
      }
    } catch (error) {
      if (semanticVersion) semanticVersion.textContent = "读取失败";
      if (metricsTable) metricsTable.innerHTML = '<tr><td colspan="5" class="table-empty">指标定义读取失败</td></tr>';
      if (schemaList) schemaList.textContent = `开放表读取失败：${error.message}`;
    }
  }

  function renderSemanticAudit(result) {
    if (!result) {
      setStatusBadge(semanticAuditStatus, "尚未审计", "neutral");
      if (semanticAuditTime) semanticAuditTime.textContent = "暂无";
      if (semanticAuditScope) semanticAuditScope.textContent = "暂无";
      if (semanticAuditCounts) semanticAuditCounts.textContent = "暂无";
      if (semanticAuditSummary) semanticAuditSummary.textContent = "点击“运行只读审计”生成第一份报告。";
      if (semanticAuditReport) semanticAuditReport.textContent = "";
      return;
    }
    const clean = result.status === "clean";
    setStatusBadge(semanticAuditStatus, clean ? "一致" : "发现差异", clean ? "success" : "warning");
    if (semanticAuditTime) {
      semanticAuditTime.textContent = new Date(result.generated_at).toLocaleString("zh-CN");
    }
    if (semanticAuditScope) {
      semanticAuditScope.textContent = `${result.model_count} 个模型 · ${result.field_count} 个开放字段 · 数据库只读`;
    }
    if (semanticAuditCounts) {
      semanticAuditCounts.textContent = `${result.error_count} 错误 · ${result.warning_count} 警告 · ${result.info_count} 提示`;
    }
    if (semanticAuditSummary) {
      const actionable = result.issues.filter((issue) => issue.severity !== "info").slice(0, 3);
      semanticAuditSummary.textContent = actionable.length
        ? `优先检查：${actionable.map((issue) => `${issue.table}${issue.field ? `.${issue.field}` : ""}（${issue.message}）`).join("；")}`
        : "未发现需要处理的结构或类型差异；源码定位提示可按需查看报告。";
    }
    if (semanticAuditReport) {
      semanticAuditReport.textContent = `报告：${result.report_path} · Wren 草稿：${result.draft_path}`;
    }
  }

  async function loadSemanticAudit() {
    if (!semanticAuditStatus) return;
    try {
      renderSemanticAudit(await apiRequest("/semantic-audit/latest"));
    } catch (error) {
      if (error.status === 404) {
        renderSemanticAudit(null);
        return;
      }
      setStatusBadge(semanticAuditStatus, "读取失败", "warning");
      if (semanticAuditSummary) semanticAuditSummary.textContent = error.message;
    }
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
      statement_timeout_ms: Number(databaseTimeoutInput?.value || 15000),
      explain_total_cost_limit: Number(databaseCostLimitInput?.value || 1000000)
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
      renderRoutingSettings(data.routing);
      if (sqlThinkingModeInput) sqlThinkingModeInput.value = data.sql_thinking_mode || "disabled";
      renderLangfuseStatus(data.langfuse);
      renderDatabaseSettings(data.database);
      renderStateDatabaseSettings(data.state_database);
      renderSemanticSettings(data.semantic);
      showSettingsFeedback("配置已从本机后端读取。密钥只显示状态，不会回显明文。");
      loadProviderModels({ quiet: true });
      refreshDatabaseStatus();
      loadSemanticConfiguration();
      loadSemanticAudit();
    } catch (error) {
      showSettingsFeedback(`读取失败：${error.message}。请从 start-odoo-agent.bat 启动应用。`, "error");
    } finally {
      settingsSaveButton.disabled = false;
    }
  }

  async function saveSettings({ quiet = false } = {}) {
    captureActiveProvider();
    captureRoutingSettings();
    const publicKey = langfusePublicKeyInput?.value.trim() || "";
    const secretKey = langfuseSecretKeyInput?.value.trim() || "";
    const body = {
      selected_provider: activeProvider,
      deepseek: {
        base_url: providerDrafts.deepseek.base_url,
        model: providerDrafts.deepseek.model,
        api_key: providerDrafts.deepseek.draftKey || null,
        input_price_per_million: providerDrafts.deepseek.input_price_per_million,
        output_price_per_million: providerDrafts.deepseek.output_price_per_million
      },
      siliconflow: {
        base_url: providerDrafts.siliconflow.base_url,
        model: providerDrafts.siliconflow.model,
        api_key: providerDrafts.siliconflow.draftKey || null,
        input_price_per_million: providerDrafts.siliconflow.input_price_per_million,
        output_price_per_million: providerDrafts.siliconflow.output_price_per_million
      },
      routing: routingDraft,
      langfuse: {
        base_url: langfuseUrlInput?.value.trim() || "https://cloud.langfuse.com",
        enabled: langfuseEnabledInput?.checked ?? true,
        public_key: publicKey || null,
        secret_key: secretKey || null
      },
      database: databasePayload(),
      state_database: stateDatabasePayload(),
      semantic: {
        provider: semanticProviderInput?.value || "native"
      },
      sql_thinking_mode: sqlThinkingModeInput?.value || "disabled"
    };

    const data = await apiRequest("/settings", {
      method: "PUT",
      body: JSON.stringify(body)
    });
    Object.entries(data.providers).forEach(([name, config]) => {
      Object.assign(providerDrafts[name], config, { draftKey: "" });
    });
    renderActiveProvider();
    renderRoutingSettings(data.routing);
    if (sqlThinkingModeInput) sqlThinkingModeInput.value = data.sql_thinking_mode || "disabled";
    renderLangfuseStatus(data.langfuse);
    renderDatabaseSettings(data.database);
    renderStateDatabaseSettings(data.state_database);
    renderSemanticSettings(data.semantic);
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

  if (runSemanticAuditButton) {
    runSemanticAuditButton.addEventListener("click", async () => {
      const original = runSemanticAuditButton.textContent;
      runSemanticAuditButton.disabled = true;
      runSemanticAuditButton.textContent = "正在审计…";
      setStatusBadge(semanticAuditStatus, "运行中", "neutral");
      if (semanticAuditSummary) {
        semanticAuditSummary.textContent = "正在读取数据库元数据，并按实际 Addon 范围扫描 Odoo 源码，通常需要数秒。";
      }
      try {
        const result = await apiRequest("/semantic-audit", { method: "POST" });
        renderSemanticAudit(result);
        showToast(result.status === "clean" ? "语义审计完成：一致" : "语义审计完成：发现差异");
      } catch (error) {
        setStatusBadge(semanticAuditStatus, "审计失败", "warning");
        if (semanticAuditSummary) semanticAuditSummary.textContent = error.message;
        showToast("语义审计失败");
      } finally {
        runSemanticAuditButton.disabled = false;
        runSemanticAuditButton.textContent = original;
      }
    });
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
  const chatTitle = document.querySelector("[data-chat-title]");
  const conversationList = document.querySelector("[data-conversation-list]");

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

  const chatSessionStorageKey = "odoo-agent-current-session";
  const createChatSessionId = () => window.crypto?.randomUUID?.() || `chat-${Date.now()}`;
  let chatSessionId = window.localStorage.getItem(chatSessionStorageKey)
    || createChatSessionId();
  window.localStorage.setItem(chatSessionStorageKey, chatSessionId);
  let chatHistory = [];
  let chatSending = false;
  let activeChatRequest = null;
  let sessionPollTimer = null;
  let pendingInterrupt = null;
  let conversations = [];
  let currentConversationTitle = "新对话";

  function fieldLabel(field, metadata = {}) {
    return metadata.column_labels?.[field] || metadata.metric_labels?.[field] || field;
  }

  function displayDateValue(value, field) {
    const text = String(value);
    const match = text.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?/);
    if (!match) return text;
    if (String(field).toLowerCase().includes("month")) {
      return `${match[1]}年${Number(match[2])}月`;
    }
    return `${match[1]}-${match[2]}-${match[3] || "01"}`;
  }

  function displayValue(value, field = "", metadata = {}) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number") {
      const format = metadata.column_formats?.[field] || "auto";
      const digits = format === "integer" ? 0 : 2;
      const formatted = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value);
      if (format === "currency") return `${formatted} ${metadata.currency || ""}`.trim();
      if (format === "percent") return `${formatted}%`;
      return formatted;
    }
    if (typeof value === "object") return JSON.stringify(value);
    if ((metadata.column_formats?.[field] || "") === "date") return displayDateValue(value, field);
    return String(value);
  }

  function plannedChartRows(spec, rows) {
    const planned = [...rows];
    if (spec.sort_by) {
      const direction = spec.sort_order === "desc" ? -1 : 1;
      planned.sort((left, right) => {
        const leftValue = left[spec.sort_by];
        const rightValue = right[spec.sort_by];
        if (leftValue === rightValue) return 0;
        if (leftValue === null || leftValue === undefined) return 1;
        if (rightValue === null || rightValue === undefined) return -1;
        if (typeof leftValue === "number" && typeof rightValue === "number") {
          return (leftValue - rightValue) * direction;
        }
        return String(leftValue).localeCompare(String(rightValue), "zh-CN") * direction;
      });
    }
    return spec.top_n ? planned.slice(0, spec.top_n) : planned;
  }

  function chartOption(spec, rows, metadata) {
    const xField = spec.x_field;
    const seriesPlans = spec.series?.length
      ? spec.series
      : (spec.y_fields || []).map((field) => ({ field, label: null }));
    const yFields = seriesPlans.map((series) => series.field);
    const seriesLabel = (field) => seriesPlans.find((series) => series.field === field)?.label || fieldLabel(field, metadata);
    const palette = ["#714b67", "#017e84", "#e28b46", "#6c63a8"];
    if (spec.type === "pie") {
      const yField = yFields[0];
      return {
        tooltip: {
          trigger: "item",
          valueFormatter: (value) => displayValue(value, yField, metadata)
        },
        legend: { bottom: 0 },
        series: [{
          name: seriesLabel(yField),
          type: "pie",
          radius: ["42%", "70%"],
          data: rows.map((row) => ({ name: displayValue(row[xField], xField, metadata), value: row[yField] })),
          itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 }
        }],
        color: palette
      };
    }
    if (spec.type === "scatter") {
      return {
        tooltip: { trigger: "item" },
        legend: { bottom: 0 },
        grid: { left: 18, right: 20, top: 24, bottom: 48, containLabel: true },
        xAxis: { type: "value", name: fieldLabel(xField, metadata), axisLabel: { color: "#6f6672" } },
        yAxis: { type: "value", axisLabel: { color: "#6f6672" }, splitLine: { lineStyle: { color: "#eee9ed" } } },
        series: yFields.map((field, index) => ({
          name: seriesLabel(field),
          type: "scatter",
          data: rows.map((row) => [row[xField], row[field]]),
          symbolSize: 9,
          itemStyle: { color: palette[index % palette.length] }
        }))
      };
    }
    return {
      tooltip: {
        trigger: "axis",
        valueFormatter: (value) => displayValue(value, yFields[0], metadata)
      },
      legend: { bottom: 0 },
      grid: { left: 18, right: 20, top: 24, bottom: 48, containLabel: true },
      xAxis: { type: "category", data: rows.map((row) => displayValue(row[xField], xField, metadata)), axisLabel: { color: "#6f6672" } },
      yAxis: { type: "value", axisLabel: { color: "#6f6672" }, splitLine: { lineStyle: { color: "#eee9ed" } } },
      series: yFields.map((field, index) => ({
        name: seriesLabel(field),
        type: spec.type,
        data: rows.map((row) => row[field]),
        smooth: spec.type === "line",
        symbolSize: 7,
        itemStyle: { color: palette[index % palette.length] },
        lineStyle: { width: 3 }
      }))
    };
  }

  function resultSection(title, detail, open = false) {
    const section = document.createElement("details");
    section.className = "result-section";
    section.open = open;
    const summary = document.createElement("summary");
    const label = document.createElement("strong");
    label.textContent = title;
    const note = document.createElement("span");
    note.textContent = detail;
    summary.append(label, note);
    section.appendChild(summary);
    return section;
  }

  const RENDERABLE_CHART_TYPES = ["kpi", "line", "bar", "pie", "scatter"];

  function appendResultCard(container, metadata) {
    const rows = metadata.rows || [];
    const columns = metadata.columns || [];
    // 更早的历史轮次只留骨架、不留明细行，这时 chart 或 row_count 仍然值得展示。
    // 注意 chart 可能是 {type:"none"}——那是规划器说"不该画图"，不能当成有图。
    const hasChart = RENDERABLE_CHART_TYPES.includes(metadata.chart?.type);
    if (!metadata.sql && !rows.length && !hasChart && !metadata.rows_trimmed
        && !(metadata.warnings || []).length) return;

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
      chip.textContent = metadata.metric_labels?.[metric] || metric;
      metricChips.appendChild(chip);
    });
    summary.append(summaryTitle, metricChips);
    const timing = document.createElement("small");
    const rowCount = metadata.row_count ?? rows.length;
    timing.textContent = `${rowCount} 行`
      + (metadata.query_ms !== null && metadata.query_ms !== undefined ? ` · ${metadata.query_ms} ms` : "")
      + (metadata.truncated ? " · 已截断" : "")
      + (metadata.rows_trimmed && !rows.length ? " · 历史明细未保留" : "");
    summary.appendChild(timing);
    card.appendChild(summary);

    if (metadata.chart?.type === "kpi" && rows.length) {
      const kpis = document.createElement("div");
      kpis.className = "result-kpis";
      metadata.chart.y_fields.forEach((field) => {
        const item = document.createElement("div");
        const label = document.createElement("span");
        label.textContent = fieldLabel(field, metadata);
        const value = document.createElement("strong");
        value.textContent = displayValue(rows[0][field], field, metadata);
        item.append(label, value);
        kpis.appendChild(item);
      });
      card.appendChild(kpis);
    } else if (["line", "bar", "pie", "scatter"].includes(metadata.chart?.type) && rows.length) {
      const plottedRows = plannedChartRows(metadata.chart, rows);
      const section = resultSection(metadata.chart.title || "图表", `${plottedRows.length} 个数据点`, true);
      const chart = document.createElement("div");
      chart.className = "agent-echart";
      section.appendChild(chart);
      card.appendChild(section);
      window.requestAnimationFrame(() => {
        if (!window.echarts) {
          chart.textContent = "图表组件未加载，表格结果仍可正常查看。";
          chart.classList.add("chart-fallback");
          return;
        }
        const instance = window.echarts.init(chart, null, { renderer: "svg" });
        instance.setOption({ animation: false, ...chartOption(metadata.chart, plottedRows, metadata) });
        const observer = new ResizeObserver(() => instance.resize());
        observer.observe(chart);
        section.addEventListener("toggle", () => {
          if (section.open) window.requestAnimationFrame(() => instance.resize());
        });
      });
    }

    if (rows.length && columns.length) {
      const section = resultSection("明细表", `${rows.length} 行 · ${columns.length} 列`);
      const wrap = document.createElement("div");
      wrap.className = "agent-table-wrap";
      const table = document.createElement("table");
      table.className = "agent-result-table";
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      columns.forEach((column) => {
        const cell = document.createElement("th");
        cell.textContent = fieldLabel(column, metadata);
        headRow.appendChild(cell);
      });
      head.appendChild(headRow);
      const body = document.createElement("tbody");
      rows.slice(0, 100).forEach((row) => {
        const tableRow = document.createElement("tr");
        columns.forEach((column) => {
          const cell = document.createElement("td");
          cell.textContent = displayValue(row[column], column, metadata);
          tableRow.appendChild(cell);
        });
        body.appendChild(tableRow);
      });
      table.append(head, body);
      wrap.appendChild(table);
      section.appendChild(wrap);
      card.appendChild(section);
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

  // 执行链路：这一轮实际走了哪些节点、每步多久。阶段事件本来就在流里，
  // 以前只当提示文字用完就丢——事后没法回答"慢在哪、修过几次 SQL"。
  function appendExecutionTrace(container, metadata) {
    var steps = metadata.trace_steps;
    if (!Array.isArray(steps) || steps.length < 2) return;

    var section = document.createElement("details");
    section.className = "execution-trace";
    var summary = document.createElement("summary");
    var total = steps[steps.length - 1].at_ms || 0;
    summary.textContent = "执行链路 " + steps.length + " 步"
      + (total ? " · " + (total / 1000).toFixed(1) + "s" : "")
      + (metadata.repair_count ? " · SQL 修复 " + metadata.repair_count + " 次" : "");
    section.appendChild(summary);

    var list = document.createElement("ol");
    list.className = "execution-trace-list";
    steps.forEach(function (step, index) {
      var item = document.createElement("li");
      var name = document.createElement("code");
      name.textContent = step.stage;
      var label = document.createElement("span");
      label.textContent = step.label || "";
      item.append(name, label);
      // 阶段事件是在**动手之前**发射的，所以某一步的耗时是它到下一步之间的间隔，
      // 不是它到上一步之间的。用错方向会把时间记到后一个节点头上——比如把
      // 答案合成的 66 秒记成"回答已完成"花了 66 秒，正好指错瓶颈。
      var next = steps[index + 1];
      var spent = next ? (next.at_ms || 0) - (step.at_ms || 0) : 0;
      if (spent >= 1) {
        var cost = document.createElement("small");
        cost.textContent = spent >= 1000
          ? (spent / 1000).toFixed(1) + "s"
          : Math.round(spent) + "ms";
        if (spent >= 3000) cost.className = "slow";
        item.appendChild(cost);
      }
      list.appendChild(item);
    });
    section.appendChild(list);
    container.appendChild(section);
  }

  function appendKnowledgeCitations(container, citations) {
    if (!Array.isArray(citations) || !citations.length) return;
    const section = document.createElement("details");
    section.className = "knowledge-citations";
    section.open = true;
    const summary = document.createElement("summary");
    summary.textContent = `知识依据 ${citations.length} 条`;
    section.appendChild(summary);
    const list = document.createElement("div");
    list.className = "knowledge-citation-list";
    citations.forEach((citation, index) => {
      const item = document.createElement("a");
      item.className = "knowledge-citation";
      item.href = citation.obsidian_uri;
      const marker = document.createElement("span");
      marker.className = "knowledge-citation-marker";
      marker.textContent = String(index + 1);
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = citation.title;
      const detail = document.createElement("small");
      detail.textContent = `${citation.heading || "概述"} · ${citation.relative_path}`;
      const excerpt = document.createElement("span");
      excerpt.className = "knowledge-citation-excerpt";
      excerpt.textContent = citation.excerpt;
      copy.append(title, detail, excerpt);
      item.append(marker, copy);
      list.appendChild(item);
    });
    section.appendChild(list);
    container.appendChild(section);
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
    if (role === "assistant" && window.OdooAgentMarkdown) {
      // 模型输出是 Markdown；渲染器只构造 DOM 节点，不使用 innerHTML。
      const answer = document.createElement("div");
      answer.className = "assistant-answer";
      window.OdooAgentMarkdown.render(text, answer);
      bubble.appendChild(answer);
    } else {
      const paragraph = document.createElement("p");
      paragraph.className = role === "assistant" ? "assistant-answer" : "";
      paragraph.textContent = text;
      bubble.appendChild(paragraph);
    }
    content.appendChild(bubble);

    if (metadata && role === "assistant") {
      appendResultCard(content, metadata);
      appendKnowledgeCitations(content, metadata.citations);
      const meta = document.createElement("div");
      meta.className = "chat-response-meta";
      // 历史消息没存 phase，只存了 intent；两者在后端是固定映射，这里补回来。
      const phase = metadata.phase || {
        general: "general-chat", knowledge: "knowledge-base", source: "knowledge-base",
        semantic: "semantic-layer", data: "text2sql", hybrid: "text2sql"
      }[metadata.intent];
      const phaseLabel = metadata.intent === "hybrid"
        ? "Odoo 实时数据 + Wiki"
        : ({ "general-chat": "普通问答", "knowledge-base": "Odoo Wiki", "semantic-layer": "指标口径", "text2sql": "Odoo 只读查询" }[phase] || "智能回答");
      // 恢复的历史消息没有 provider/model/用量，缺什么就不显示什么。
      meta.append([phaseLabel, metadata.provider, metadata.model].filter(Boolean).join(" · "));
      if (metadata.answer_mode === "deterministic") meta.append(" · 确定性摘要（省略第二次模型调用）");
      if (metadata.answer_mode === "knowledge") meta.append(" · 已引用审核笔记");
      if (metadata.usage?.estimated_cost_usd > 0) {
        meta.append(` · 估算 $${Number(metadata.usage.estimated_cost_usd).toFixed(6)}`);
      }
      if (metadata.trace_url) {
        meta.append(" · ");
        const traceLink = document.createElement("a");
        traceLink.href = metadata.trace_url;
        traceLink.target = "_blank";
        traceLink.rel = "noopener noreferrer";
        traceLink.textContent = "打开 Langfuse Trace ↗";
        meta.appendChild(traceLink);
      } else if (metadata.trace_id) {
        meta.append(" · Langfuse Trace 已记录");
      }
      content.appendChild(meta);

      appendExecutionTrace(content, metadata);

      if (metadata.trace_id) {
        const feedback = document.createElement("div");
        feedback.className = "chat-feedback";
        const label = document.createElement("span");
        label.textContent = "这个回答有帮助吗？";
        feedback.appendChild(label);
        const sendFeedback = async (positive, reason = null) => {
          feedback.querySelectorAll("button, select").forEach((item) => { item.disabled = true; });
          try {
            await apiRequest("/chat/feedback", {
              method: "POST",
              body: JSON.stringify({ trace_id: metadata.trace_id, positive, reason })
            });
            label.textContent = "已记录到 Langfuse";
          } catch (error) {
            label.textContent = `反馈未记录：${error.message}`;
            feedback.querySelectorAll("button, select").forEach((item) => { item.disabled = false; });
          }
        };
        [[true, "👍"], [false, "👎"]].forEach(([positive, symbol]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "chat-feedback-button";
          button.textContent = symbol;
          button.setAttribute("aria-label", positive ? "有帮助" : "没帮助");
          button.addEventListener("click", async () => {
            if (positive) {
              button.classList.add("selected");
              await sendFeedback(true);
              return;
            }
            if (feedback.querySelector("select")) return;
            button.classList.add("selected");
            label.textContent = "主要哪里不对？";
            const reason = document.createElement("select");
            reason.className = "chat-feedback-reason";
            [
              ["number-wrong", "数字不对"],
              ["metric-wrong", "指标口径不对"],
              ["sql-wrong", "查询逻辑不对"],
              ["missing-answer", "答非所问/缺内容"],
              ["chart-wrong", "图表不合适"],
              ["too-slow", "响应太慢"],
              ["other", "其他"]
            ].forEach(([value, text]) => reason.add(new Option(text, value)));
            const submit = document.createElement("button");
            submit.type = "button";
            submit.className = "chat-feedback-submit";
            submit.textContent = "提交";
            submit.addEventListener("click", () => sendFeedback(false, reason.value));
            feedback.append(reason, submit);
          });
          feedback.appendChild(button);
        });
        content.appendChild(feedback);
      }
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
      "新会话已开始。你可以普通聊天、询问 Odoo 业务或源码知识，也可以直接查询实时销售数据。"
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

  async function streamApi(path, body, onProgress) {
    const response = await window.fetch(`${apiBase}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(body)
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(typeof payload.detail === "string" ? payload.detail : `请求失败（HTTP ${response.status}）`);
    }
    if (!response.body) throw new Error("浏览器没有收到流式响应。");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result = null;
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.replace(/\r\n/g, "\n").split("\n\n");
      buffer = blocks.pop() || "";
      blocks.forEach((block) => {
        if (!block.trim()) return;
        let eventName = "message";
        const dataLines = [];
        block.split("\n").forEach((line) => {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        });
        if (!dataLines.length) return;
        const payload = JSON.parse(dataLines.join("\n"));
        if (eventName === "progress") onProgress?.(payload);
        if (eventName === "result") result = payload;
        if (eventName === "error") throw new Error(payload.detail || "流式请求失败");
      });
      if (done) break;
    }
    if (!result) throw new Error("流式请求结束但没有返回最终结果。");
    return result;
  }

  function shortConversationTitle(text) {
    const normalized = String(text || "").replace(/\s+/g, " ").trim();
    if (!normalized) return "新对话";
    return normalized.length > 36 ? `${normalized.slice(0, 36)}…` : normalized;
  }

  // 历史消息的 artifact 字段名与实时响应一致，补上 restored 标记后就能交给同一个
  // 渲染函数；provider/model/usage 这些没有存，渲染时会自动省略。
  function restoredMetadata(message) {
    if (message.role !== "assistant" || !message.artifact) return null;
    return { ...message.artifact, restored: true };
  }

  function titleFromHistory(history) {
    const firstQuestion = history.find((message) => message.role === "user" && message.content);
    return shortConversationTitle(firstQuestion?.content);
  }

  function setCurrentConversationTitle(title) {
    currentConversationTitle = shortConversationTitle(title);
    if (chatTitle) chatTitle.textContent = currentConversationTitle;
    document.title = `${currentConversationTitle} · Odoo Agent`;
  }

  function formatConversationTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const today = new Date();
    if (date.toDateString() === today.toDateString()) {
      return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    }
    return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  }

  function renderConversationList() {
    if (!conversationList) return;
    conversationList.innerHTML = "";
    if (!conversations.length) {
      const empty = document.createElement("div");
      empty.className = "conversation-list-status";
      empty.textContent = "还没有历史对话";
      conversationList.appendChild(empty);
      return;
    }

    conversations.forEach((conversation) => {
      const item = document.createElement("div");
      item.className = "conversation-item";
      item.classList.toggle("active", conversation.session_id === chatSessionId);

      const openButton = document.createElement("button");
      openButton.type = "button";
      openButton.className = "conversation-open";
      openButton.title = conversation.title;
      const title = document.createElement("span");
      title.className = "conversation-title";
      title.textContent = conversation.title;
      const time = document.createElement("span");
      time.className = "conversation-time";
      time.textContent = formatConversationTime(conversation.updated_at);
      openButton.append(title, time);
      openButton.addEventListener("click", () => switchChatConversation(conversation.session_id));

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "conversation-delete";
      deleteButton.textContent = "×";
      deleteButton.title = "删除对话";
      deleteButton.setAttribute("aria-label", `删除对话：${conversation.title}`);
      deleteButton.addEventListener("click", () => deleteChatConversation(conversation));
      item.append(openButton, deleteButton);
      conversationList.appendChild(item);
    });
  }

  async function refreshConversationList() {
    if (!conversationList) return conversations;
    try {
      const data = await apiRequest("/chat/conversations");
      conversations = data.conversations || [];
      const current = conversations.find((item) => item.session_id === chatSessionId);
      if (current) setCurrentConversationTitle(current.title);
      renderConversationList();
    } catch (_error) {
      conversationList.innerHTML = '<div class="conversation-list-status error">历史记录暂时无法读取</div>';
    }
    return conversations;
  }

  function stopSessionPolling() {
    if (sessionPollTimer !== null) {
      window.clearTimeout(sessionPollTimer);
      sessionPollTimer = null;
    }
  }

  function appendConversationRunNote(runStatus) {
    const messages = {
      running: "这条回答仍在处理中，完成后会自动恢复到当前会话。",
      cancelled: "上一次回答因刷新或切换被中断，问题已经保留，你可以重新发送或继续提问。",
      failed: "上一次回答没有完成，但问题已经保留，你可以重新发送或换一种问法。"
    };
    const text = messages[runStatus];
    if (!text) return;
    const note = appendChatMessage("assistant", text);
    note?.classList.add("conversation-run-note");
  }

  function pollConversationCompletion(sessionId, attempt = 0) {
    stopSessionPolling();
    if (attempt >= 40 || sessionId !== chatSessionId) return;
    sessionPollTimer = window.setTimeout(async () => {
      sessionPollTimer = null;
      if (sessionId !== chatSessionId || chatSending) return;
      try {
        const session = await apiRequest(`/chat/sessions/${encodeURIComponent(sessionId)}`);
        if (["running", "cancelled"].includes(session.run_status)) {
          pollConversationCompletion(sessionId, attempt + 1);
          return;
        }
        await restoreChatSession(sessionId, { allowEmpty: true });
        await refreshConversationList();
      } catch (_error) {
        pollConversationCompletion(sessionId, attempt + 1);
      }
    }, 1500);
  }

  function detachActiveChatRequest() {
    if (!chatSending || !activeChatRequest) return false;
    activeChatRequest.detached = true;
    activeChatRequest = null;
    chatSending = false;
    if (sendButton) sendButton.disabled = false;
    if (chatInput) chatInput.disabled = false;
    return true;
  }

  async function restoreChatSession(sessionId, { notify = false, allowEmpty = false } = {}) {
    if (!chatThread) return false;
    stopSessionPolling();
    try {
      const session = await apiRequest(`/chat/sessions/${encodeURIComponent(sessionId)}`);
      const hasContent = session.history.length > 0
        || Boolean(session.pending_interrupt)
        || !["new", "completed"].includes(session.run_status);
      if (!hasContent && !allowEmpty) return false;

      chatSessionId = sessionId;
      window.localStorage.setItem(chatSessionStorageKey, chatSessionId);
      chatThread.innerHTML = "";
      chatHistory = session.history.map(({ role, content }) => ({ role, content }));
      pendingInterrupt = session.pending_interrupt;
      setCurrentConversationTitle(titleFromHistory(chatHistory));
      if (chatHistory.length) {
        // 历史消息带 artifact 时要一起重绘，否则切走再回来图表和明细表就没了。
        session.history.forEach((message) => {
          appendChatMessage(message.role, message.content, restoredMetadata(message));
        });
      } else {
        appendWelcomeMessage();
      }
      if (pendingInterrupt) appendChatMessage("assistant", pendingInterrupt.question);
      appendConversationRunNote(session.run_status);
      if (["running", "cancelled"].includes(session.run_status)) {
        pollConversationCompletion(sessionId);
      }
      renderConversationList();
      document.body.classList.remove("menu-open");
      if (notify) {
        showToast(session.persistence_mode === "postgres" ? "已切换到历史会话" : "已切换到当前进程会话");
      }
      return true;
    } catch (_error) {
      if (notify) showToast("会话暂时无法加载");
      return false;
    }
  }

  function startNewChat({ notify = true } = {}) {
    if (!chatThread) return;
    if (chatSending) detachActiveChatRequest();
    stopSessionPolling();
    chatSessionId = createChatSessionId();
    window.localStorage.setItem(chatSessionStorageKey, chatSessionId);
    chatHistory = [];
    pendingInterrupt = null;
    chatThread.innerHTML = "";
    setCurrentConversationTitle("新对话");
    appendWelcomeMessage();
    renderConversationList();
    document.body.classList.remove("menu-open");
    if (notify) showToast("已开始新会话");
    chatInput?.focus();
  }

  async function switchChatConversation(sessionId) {
    if (sessionId === chatSessionId && chatHistory.length) return;
    if (chatSending) detachActiveChatRequest();
    stopSessionPolling();
    await restoreChatSession(sessionId, { notify: true, allowEmpty: true });
  }

  async function deleteChatConversation(conversation) {
    if (!window.confirm(`确定删除“${conversation.title}”吗？删除后无法恢复。`)) return;
    try {
      if (chatSending && conversation.session_id === chatSessionId) {
        detachActiveChatRequest();
      }
      await apiRequest(`/chat/conversations/${encodeURIComponent(conversation.session_id)}`, {
        method: "DELETE"
      });
      if (conversation.session_id === chatSessionId) startNewChat({ notify: false });
      await refreshConversationList();
      showToast("会话已删除");
    } catch (error) {
      showToast(`删除失败：${error.message}`);
    }
  }

  async function initializeChatHistory() {
    const restoredCurrent = await restoreChatSession(chatSessionId);
    const available = await refreshConversationList();
    if (!restoredCurrent && available.length) {
      await restoreChatSession(available[0].session_id, { allowEmpty: true });
    } else if (!restoredCurrent) {
      setCurrentConversationTitle("新对话");
      renderConversationList();
    }
  }

  async function sendChatMessage() {
    if (!chatInput || !chatThread || chatSending) return;
    const question = chatInput.value.trim();
    if (!question) return;

    const priorHistory = chatHistory.slice(-12);
    const isResume = Boolean(pendingInterrupt);
    const isFirstQuestion = !chatHistory.some((message) => message.role === "user");
    const requestSessionId = chatSessionId;
    const request = { sessionId: requestSessionId, detached: false };
    activeChatRequest = request;
    stopSessionPolling();
    chatHistory.push({ role: "user", content: question });
    if (isFirstQuestion) setCurrentConversationTitle(question);
    appendChatMessage("user", question);
    chatInput.value = "";

    const thinking = document.createElement("div");
    thinking.className = "message assistant-thinking";
    thinking.innerHTML = `<div class="message-avatar">AI</div><div class="message-bubble"><div class="chat-progress"><div class="typing"><span></span><span></span><span></span></div><span class="chat-progress-label">正在接收任务…</span></div></div>`;
    chatThread.appendChild(thinking);
    chatThread.scrollTop = chatThread.scrollHeight;
    chatSending = true;
    if (sendButton) sendButton.disabled = true;
    chatInput.disabled = true;

    try {
      const path = isResume ? "/chat/resume/stream" : "/chat/stream";
      const body = isResume
        ? { session_id: requestSessionId, answer: question }
        : { question, session_id: requestSessionId, history: priorHistory };
      const result = await streamApi(path, body, (event) => {
        const label = thinking.querySelector(".chat-progress-label");
        if (label) label.textContent = event.label || "正在处理…";
        chatThread.scrollTop = chatThread.scrollHeight;
      });
      thinking.remove();
      if (request.detached || requestSessionId !== chatSessionId) {
        await refreshConversationList();
        return;
      }
      appendChatMessage("assistant", result.answer, result);
      chatHistory.push({ role: "assistant", content: result.answer });
      pendingInterrupt = result.status === "interrupted" ? result.interrupt : null;
      await refreshConversationList();
    } catch (error) {
      thinking.remove();
      if (!request.detached && requestSessionId === chatSessionId) {
        const errorMessage = appendChatMessage("assistant", friendlyChatError(error));
        errorMessage?.querySelector(".message-bubble")?.classList.add("error");
      } else {
        await refreshConversationList();
      }
    } finally {
      if (activeChatRequest === request) {
        activeChatRequest = null;
        chatSending = false;
        if (sendButton) sendButton.disabled = false;
        chatInput.disabled = false;
        if (requestSessionId === chatSessionId) chatInput.focus();
      }
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
    initializeChatHistory();
  }

  document.querySelectorAll("[data-new-chat]").forEach((button) => {
    button.addEventListener("click", () => startNewChat());
  });

  window.addEventListener("pagehide", () => {
    if (!chatSending || !activeChatRequest) return;
    window.fetch(
      `${apiBase}/chat/conversations/${encodeURIComponent(activeChatRequest.sessionId)}/detach`,
      { method: "POST", keepalive: true }
    ).catch(() => {});
  });
})();
