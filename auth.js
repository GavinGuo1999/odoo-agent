/**
 * 演示门禁：登录框 + 给所有 /api 请求带上 token。
 *
 * 关键设计：**包一层 window.fetch，而不是改每个调用点**。app.js 里有 apiRequest
 * 和 streamApi 两条路径，quality.js 还有一处裸 fetch——逐个改，以后新增一处忘了带
 * token 就是一个洞。包 fetch 只有一个入口，漏不掉。
 *
 * 未登录时 /api 请求不是失败，而是**排队等在 ready 上**。页面加载时那些自动发起的
 * 请求因此不会报一片错误，登录成功后它们自己就继续跑完了。
 *
 * token 放 sessionStorage：刷新还在（省得演示途中反复输），标签页一关就没。
 * 服务端 token 只存在进程内存里，重启即全部失效。
 *
 * 和本文件其余前端代码一样，只构造 DOM 节点，不使用 innerHTML。
 */
(function () {
  "use strict";

  var STORAGE_KEY = "odoo-agent-token";
  var nativeFetch = window.fetch.bind(window);
  var token = null;
  try {
    token = window.sessionStorage.getItem(STORAGE_KEY);
  } catch (error) {
    token = null; // 隐私模式下 sessionStorage 可能直接抛错。
  }

  var resolveReady;
  var ready = new Promise(function (resolve) { resolveReady = resolve; });
  var gate = null;
  var errorLine = null;
  var input = null;

  function remember(value) {
    token = value;
    try {
      if (value) window.sessionStorage.setItem(STORAGE_KEY, value);
      else window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      /* 存不下也不影响本次会话，token 还在内存里。 */
    }
  }

  function isApiRequest(url) {
    // 只处理本站的 /api；CDN、外链一律原样放行。
    try {
      var parsed = new URL(url, window.location.href);
      return parsed.origin === window.location.origin
        && parsed.pathname.indexOf("/api/") === 0;
    } catch (error) {
      return false;
    }
  }

  function isAuthEndpoint(url) {
    try {
      return new URL(url, window.location.href).pathname.indexOf("/api/auth/") === 0;
    } catch (error) {
      return false;
    }
  }

  function buildGate() {
    var overlay = document.createElement("div");
    overlay.className = "auth-gate";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "访问口令");

    var card = document.createElement("form");
    card.className = "auth-card";

    var title = document.createElement("strong");
    title.className = "auth-title";
    title.textContent = "Odoo Agent";
    var hint = document.createElement("p");
    hint.className = "auth-hint";
    hint.textContent = "这是一个演示环境，需要访问口令。";

    var label = document.createElement("label");
    label.className = "auth-label";
    label.setAttribute("for", "auth-password");
    label.textContent = "访问口令";

    input = document.createElement("input");
    input.type = "password";
    input.id = "auth-password";
    input.className = "auth-input";
    input.autocomplete = "current-password";
    input.required = true;

    var submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "btn auth-submit";
    submit.textContent = "进入";

    errorLine = document.createElement("p");
    errorLine.className = "auth-error";
    errorLine.setAttribute("role", "alert");

    card.append(title, hint, label, input, submit, errorLine);
    overlay.appendChild(card);

    card.addEventListener("submit", function (event) {
      event.preventDefault();
      submit.disabled = true;
      errorLine.textContent = "";
      nativeFetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: input.value })
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (payload) {
          return { ok: response.ok, status: response.status, payload: payload };
        });
      }).then(function (result) {
        if (!result.ok) {
          errorLine.textContent = typeof result.payload.detail === "string"
            ? result.payload.detail
            : "登录失败（HTTP " + result.status + "）";
          submit.disabled = false;
          input.select();
          return;
        }
        remember(result.payload.token);
        input.value = "";
        submit.disabled = false;
        hideGate();
        resolveReady();
        // 已经渲染过的页面不会自己重来一遍，最省事也最不容易出错的是重载。
        if (document.body.dataset.authRetry === "reload") window.location.reload();
      }).catch(function () {
        errorLine.textContent = "无法连接服务，请确认后端在运行。";
        submit.disabled = false;
      });
    });

    return overlay;
  }

  function showGate(message) {
    if (!gate) gate = buildGate();
    if (!gate.isConnected) document.body.appendChild(gate);
    document.body.classList.add("auth-locked");
    if (message && errorLine) errorLine.textContent = message;
    if (input) window.setTimeout(function () { input.focus(); }, 0);
  }

  function hideGate() {
    document.body.classList.remove("auth-locked");
    if (gate && gate.isConnected) gate.remove();
  }

  window.fetch = function (resource, init) {
    var url = typeof resource === "string" ? resource : (resource && resource.url) || "";
    if (!isApiRequest(url) || isAuthEndpoint(url)) {
      return nativeFetch(resource, init);
    }
    return ready.then(function () {
      var options = init || {};
      var headers = new Headers(options.headers || (typeof resource === "string" ? null : resource.headers) || {});
      if (token) headers.set("Authorization", "Bearer " + token);
      var merged = {};
      Object.keys(options).forEach(function (key) { merged[key] = options[key]; });
      merged.headers = headers;
      return nativeFetch(resource, merged).then(function (response) {
        if (response.status === 401) {
          // token 过期或服务端重启过：退回登录，并让后续请求重新排队。
          remember(null);
          ready = new Promise(function (resolve) { resolveReady = resolve; });
          document.body.dataset.authRetry = "reload";
          showGate("登录已失效，请重新输入口令。");
        }
        return response;
      });
    });
  };

  function boot() {
    nativeFetch("/api/auth/session", {
      headers: token ? { Authorization: "Bearer " + token } : {}
    }).then(function (response) {
      if (response.ok) {
        hideGate();
        resolveReady();
        return;
      }
      if (response.status === 401) {
        remember(null);
        showGate();
        return;
      }
      // 服务端异常时不要把人锁在外面——放行，让原本的错误提示自己显示。
      resolveReady();
    }).catch(function () {
      resolveReady();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.OdooAgentAuth = {
    logout: function () {
      var current = token;
      remember(null);
      return nativeFetch("/api/auth/logout", {
        method: "POST",
        headers: current ? { Authorization: "Bearer " + current } : {}
      }).catch(function () { /* 本地已经清掉了，服务端失败不影响 */ })
        .then(function () { window.location.reload(); });
    }
  };
})();
