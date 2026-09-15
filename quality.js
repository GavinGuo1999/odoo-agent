/**
 * 评测与质量页。
 *
 * 只读 /api/quality/summary 并渲染，不触发任何评测——跑评测要花模型额度、要连
 * 只读业务库，那是命令行里的显式动作，不该由打开一个页面触发。
 *
 * 与 markdown.js 一样，全程用 DOM 构造，不使用 innerHTML。
 */
(function () {
  "use strict";

  if (document.body.dataset.page !== "quality") return;

  var topStatus = document.querySelector("[data-quality-top-status]");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function show(selector) {
    var node = document.querySelector(selector);
    if (node) node.hidden = false;
    return node;
  }

  function setStatus(text, tone) {
    if (!topStatus) return;
    topStatus.textContent = "";
    var dot = el("span", "status-dot");
    if (tone) dot.classList.add(tone);
    topStatus.appendChild(dot);
    topStatus.appendChild(el("span", null, text));
  }

  function percent(value) {
    return typeof value === "number" ? (value * 100).toFixed(2) + "%" : "—";
  }

  function ms(value) {
    return typeof value === "number" ? value.toFixed(0) + " ms" : "—";
  }

  function usd(value) {
    return typeof value === "number" ? "$" + value.toFixed(6) : "—";
  }

  function num(value, digits) {
    return typeof value === "number" ? value.toFixed(digits === undefined ? 4 : digits) : "—";
  }

  function integer(value) {
    return typeof value === "number" ? value.toLocaleString("zh-CN") : "—";
  }

  function localTime(value) {
    if (!value) return "—";
    var parsed = new Date(value);
    return isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN");
  }

  function appendRow(tbody, cells) {
    var row = document.createElement("tr");
    cells.forEach(function (cell) {
      row.appendChild(el("td", null, cell));
    });
    tbody.appendChild(row);
    return row;
  }

  function renderSemantic(benchmark) {
    if (!benchmark) return;
    var section = show("[data-quality-semantic]");
    if (!section) return;

    var meta = section.querySelector("[data-semantic-meta]");
    if (meta) {
      meta.textContent =
        benchmark.run_id +
        " · " +
        localTime(benchmark.created_at) +
        " · 每个语义层 " +
        (benchmark.runs_per_provider || "?") +
        " 轮 · 数据集 " +
        (benchmark.dataset || "—");
    }

    var budget = benchmark.latency_budget || {};
    var verdict = section.querySelector("[data-latency-verdict]");
    if (verdict && budget.p50_budget_ms) {
      var within = budget.within_budget !== false;
      verdict.textContent = within
        ? "延迟门禁通过（p50 ≤ " + budget.p50_budget_ms + " ms）"
        : "延迟门禁未通过：" + (budget.breaches || []).join("、");
      verdict.classList.add(within ? "ok" : "bad");
    }

    var tbody = section.querySelector("[data-semantic-table] tbody");
    if (tbody) {
      tbody.textContent = "";
      Object.keys(benchmark.providers || {}).forEach(function (name) {
        var metrics = benchmark.providers[name] || {};
        var latency = metrics.latency_ms || {};
        appendRow(tbody, [
          name,
          percent(metrics.pass_rate),
          percent(metrics.result_match_rate),
          ms(latency.p50),
          ms(latency.p95),
          integer(metrics.total_tokens),
          usd(metrics.estimated_cost_usd),
          integer(metrics.repair_count),
        ]);
      });
    }

    var comparison = benchmark.comparison || {};
    var host = section.querySelector("[data-semantic-comparison]");
    if (host && Object.keys(comparison).length) {
      host.textContent = "";
      host.appendChild(el("h3", "quality-subhead", "Wren 相对 Native"));
      var list = el("ul", "quality-deltas");
      [
        ["pass_rate_delta", "总通过率", percent],
        ["result_match_rate_delta", "结果签名", percent],
        ["p50_latency_delta_ms", "p50", ms],
        ["p95_latency_delta_ms", "p95", ms],
        ["token_delta", "Token", integer],
        ["cost_delta_usd", "Cost", usd],
        ["repair_delta", "Repair", integer],
      ].forEach(function (entry) {
        var value = comparison[entry[0]];
        if (value === undefined || value === null) return;
        var item = el("li");
        item.appendChild(el("span", "quality-delta-label", entry[1]));
        var sign = typeof value === "number" && value > 0 ? "+" : "";
        item.appendChild(el("strong", value > 0 ? "up" : value < 0 ? "down" : null, sign + entry[2](value)));
        list.appendChild(item);
      });
      host.appendChild(list);
    }
  }

  function renderRoles(benchmark) {
    if (!benchmark) return;
    var providers = benchmark.providers || {};
    var rows = [];
    Object.keys(providers).forEach(function (name) {
      var byRole = (providers[name] || {}).by_role || {};
      Object.keys(byRole).forEach(function (role) {
        rows.push([name, role, byRole[role]]);
      });
    });
    if (!rows.length) return;

    var section = show("[data-quality-roles]");
    var tbody = section && section.querySelector("[data-roles-table] tbody");
    if (!tbody) return;
    tbody.textContent = "";
    rows.forEach(function (entry) {
      var usage = entry[2] || {};
      appendRow(tbody, [
        entry[0],
        entry[1],
        integer(usage.calls),
        integer(usage.total_tokens),
        percent(usage.token_share),
        usd(usage.estimated_cost_usd),
        percent(usage.cost_share),
      ]);
    });
  }

  function renderWiki(wiki) {
    if (!wiki) return;
    var section = show("[data-quality-wiki]");
    if (!section) return;

    var meta = section.querySelector("[data-wiki-meta]");
    if (meta) {
      meta.textContent =
        wiki.run_id + " · " + localTime(wiki.generated_at) + " · " + (wiki.case_count || "?") + " 个用例";
    }

    var tbody = section.querySelector("[data-wiki-table] tbody");
    if (tbody) {
      tbody.textContent = "";
      Object.keys(wiki.retrieval || {}).forEach(function (mode) {
        var metrics = wiki.retrieval[mode] || {};
        var fallbacks = (wiki.fallbacks || {})[mode] || [];
        appendRow(tbody, [
          mode,
          num(metrics.context_recall),
          num(metrics.hit_at_k),
          num(metrics.mrr),
          num(metrics.context_precision),
          fallbacks.length ? fallbacks.join("、") : "无",
        ]);
      });
    }

    var delta = wiki.delta_hybrid_minus_lexical || {};
    var host = section.querySelector("[data-wiki-delta]");
    if (host && Object.keys(delta).length) {
      host.textContent = "";
      host.appendChild(el("h3", "quality-subhead", "hybrid 相对 lexical"));
      var list = el("ul", "quality-deltas");
      [
        ["context_recall", "recall"],
        ["hit_at_k", "hit@k"],
        ["mrr", "MRR"],
        ["context_precision", "precision"],
      ].forEach(function (entry) {
        var value = delta[entry[0]];
        if (typeof value !== "number") return;
        var item = el("li");
        item.appendChild(el("span", "quality-delta-label", entry[1]));
        item.appendChild(el("strong", value > 0 ? "up" : value < 0 ? "down" : null, (value > 0 ? "+" : "") + num(value)));
        list.appendChild(item);
      });
      host.appendChild(list);
    }
  }

  var RAGAS_LABELS = {
    not_run: "未运行",
    completed: "全部评分成功",
    partial: "部分失败",
    failed: "失败",
    unknown: "状态未知",
  };

  function renderRagas(wiki, latest) {
    if (!wiki) return;
    // RAGAS 不随每次检索评测一起跑，所以"最近一次运行"往往没有它。优先显示
    // 最近一次**真正跑过**的那份，并标明它来自哪一次——否则一次不带 --ragas 的
    // 评测就会把已经建立的 Faithfulness 基线从页面上抹掉。
    var fromRun = null;
    var ragas = wiki.ragas || { status: "not_run", metrics: {} };
    if ((!ragas.metrics || !Object.keys(ragas.metrics).length) && latest && latest.ragas) {
      ragas = latest.ragas;
      fromRun = latest.run_id;
    }
    var section = show("[data-quality-ragas]");
    if (!section) return;

    var status = section.querySelector("[data-ragas-status]");
    if (status) {
      status.textContent = RAGAS_LABELS[ragas.status] || ragas.status;
      status.classList.add(ragas.status === "completed" ? "ok" : ragas.status === "not_run" ? "muted" : "warn");
    }

    var meta = section.querySelector("[data-ragas-meta]");
    if (meta) {
      var parts = [];
      if (ragas.case_count) {
        parts.push("样本 " + ragas.case_count + " 个用例（判官为 LLM，逐条判定回答是否被检索内容支持）");
      } else {
        parts.push("需要 answer 模型与 embedding 额度，默认不随检索评测一起运行");
      }
      if (fromRun) parts.push("数据来自较早的运行 " + fromRun + "，最近一次未执行 RAGAS");
      meta.textContent = parts.join(" · ");
    }

    var body = section.querySelector("[data-ragas-body]");
    if (!body) return;
    body.textContent = "";

    var names = Object.keys(ragas.metrics || {});
    if (!names.length) {
      // 关键：没跑过不能显示成 0 分，那会被读成“质量极差”。
      body.appendChild(
        el(
          "p",
          "quality-note",
          ragas.status === "not_run"
            ? "这一层从未执行，因此没有分数。这不等于分数为 0。加 --ragas 参数重跑即可建立基线。"
            : "本次运行没有任何指标评分成功" + (ragas.error_type ? "（" + ragas.error_type + "）" : "") + "。",
        ),
      );
      return;
    }

    var table = el("table", "quality-table");
    var head = document.createElement("thead");
    var headRow = document.createElement("tr");
    ["指标", "值", "成功样本"].forEach(function (label) {
      headRow.appendChild(el("th", null, label));
    });
    head.appendChild(headRow);
    table.appendChild(head);
    var tbody = document.createElement("tbody");
    names.forEach(function (name) {
      appendRow(tbody, [
        name,
        num(ragas.metrics[name]),
        integer((ragas.scored_counts || {})[name]) || "—",
      ]);
    });
    table.appendChild(tbody);
    var scroll = el("div", "table-scroll");
    scroll.appendChild(table);
    body.appendChild(scroll);

    if ((ragas.errors || []).length) {
      var errors = el("p", "quality-note");
      errors.textContent =
        "未成功的评分：" +
        ragas.errors
          .map(function (entry) {
            return entry.metric + "@" + entry.id + "（" + entry.error_type + "）";
          })
          .join("；");
      body.appendChild(errors);
    }

    // 这两条局限必须和数字一起出现，否则 0.9075 会被当成全量结论。
    body.appendChild(
      el(
        "p",
        "quality-note",
        "解读提醒：样本量小；判官与被评回答目前用的是同一个模型，自评偏高的风险未排除。换判官模型并扩样本后需复验。",
      ),
    );
  }

  function renderHistory(payload) {
    var rows = [];
    (payload.semantic_history || []).forEach(function (run) {
      var parts = Object.keys(run.providers_summary || {}).map(function (name) {
        var metrics = run.providers_summary[name] || {};
        return name + " " + percent(metrics.pass_rate) + " / " + ms(metrics.p50) + " / " + usd(metrics.estimated_cost_usd);
      });
      rows.push([run.run_id, "语义层 A/B", parts.join("；") || "—"]);
    });
    (payload.wiki_history || []).forEach(function (run) {
      var parts = Object.keys(run.retrieval || {}).map(function (mode) {
        var metrics = run.retrieval[mode] || {};
        return mode + " recall " + num(metrics.context_recall) + " / MRR " + num(metrics.mrr);
      });
      var suffix = run.ragas_status && run.ragas_status !== "not_run" ? "；RAGAS " + (RAGAS_LABELS[run.ragas_status] || run.ragas_status) : "";
      rows.push([run.run_id, "Wiki 检索", (parts.join("；") || "—") + suffix]);
    });
    if (!rows.length) return;

    rows.sort(function (a, b) {
      return a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0;
    });

    var section = show("[data-quality-history]");
    var tbody = section && section.querySelector("[data-history-table] tbody");
    if (!tbody) return;
    tbody.textContent = "";
    rows.forEach(function (row) {
      appendRow(tbody, row);
    });
  }

  function render(payload) {
    if (!payload.available) {
      show("[data-quality-empty]");
      setStatus("尚无评测归档", "warn");
      return;
    }
    renderSemantic(payload.semantic_benchmark);
    renderRoles(payload.semantic_benchmark);
    renderWiki(payload.wiki_rag);
    renderRagas(payload.wiki_rag, payload.wiki_ragas_latest);
    renderHistory(payload);

    var counts = (payload.semantic_history || []).length + (payload.wiki_history || []).length;
    setStatus("已读取 " + counts + " 次归档", "ok");

    if ((payload.skipped || []).length) {
      var section = show("[data-quality-error]");
      var text = section && section.querySelector("[data-quality-error-text]");
      if (text) {
        text.textContent = "以下归档无法解析，已跳过：" + payload.skipped.join("、");
      }
    }
  }

  function load() {
    fetch("/api/quality/summary", { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(render)
      .catch(function (error) {
        setStatus("读取失败", "bad");
        var section = show("[data-quality-error]");
        var text = section && section.querySelector("[data-quality-error-text]");
        if (text) text.textContent = "无法读取评测归档：" + error.message;
      });
  }

  load();
})();
