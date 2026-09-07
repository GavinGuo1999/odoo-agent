/**
 * 侧边栏外壳：导航的唯一定义处。
 *
 * 此前 6 个页面各存一份侧边栏副本，加一个导航项要改 6 个文件——本轮加"评测与
 * 质量"时就漏改过 index.html。现在页面只留 `<aside data-sidebar>` 挂载点，
 * 结构由这里生成。
 *
 * 必须在 app.js 之前加载：app.js 会查询侧边栏里的 `[data-db-mini-status]`、
 * `[data-conversation-list]` 等元素。两者都用 defer，顺序即执行顺序。
 *
 * 与 markdown.js / quality.js 一致：只构造 DOM，不使用 innerHTML。
 */
(function () {
  "use strict";

  var BRAND = { title: "Odoo Agent", subtitle: "销售数据智能助手", mark: "O", href: "index.html" };

  var GROUPS = [
    {
      label: "工作空间",
      ariaLabel: "主导航",
      items: [
        { nav: "home", href: "index.html", icon: "⌂", text: "工作台" },
        { nav: "chat", href: "chat.html", icon: "✦", text: "智能助手" },
        { nav: "dashboard", href: "dashboard.html", icon: "▥", text: "销售看板" },
        { nav: "wiki", href: "wiki.html", icon: "◇", text: "Odoo Wiki" },
      ],
    },
    {
      label: "系统",
      ariaLabel: "系统导航",
      items: [
        { nav: "quality", href: "quality.html", icon: "✓", text: "评测与质量" },
        { nav: "settings", href: "settings.html", icon: "⚙", text: "数据与模型" },
      ],
    },
  ];

  // 会话历史只属于智能助手页；其余页面不该出现这块，也不该为它加载相关 DOM。
  var CONVERSATION_PAGE = "chat";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function buildBrand() {
    var brand = el("a", "brand");
    brand.href = BRAND.href;
    brand.appendChild(el("span", "brand-mark", BRAND.mark));
    var label = el("span");
    label.appendChild(el("span", "brand-name", BRAND.title));
    label.appendChild(document.createElement("br"));
    label.appendChild(el("span", "brand-subtitle", BRAND.subtitle));
    brand.appendChild(label);
    return brand;
  }

  function buildNavGroup(group, page) {
    var fragment = document.createDocumentFragment();
    fragment.appendChild(el("div", "nav-label", group.label));
    var nav = el("nav", "nav-list");
    nav.setAttribute("aria-label", group.ariaLabel);
    group.items.forEach(function (item) {
      var link = el("a", "nav-link");
      link.href = item.href;
      link.dataset.nav = item.nav;
      if (item.nav === page) {
        link.classList.add("active");
        link.setAttribute("aria-current", "page");
      }
      link.appendChild(el("span", "nav-icon", item.icon));
      link.appendChild(el("span", null, item.text));
      nav.appendChild(link);
    });
    fragment.appendChild(nav);
    return fragment;
  }

  function buildConversationSection() {
    var section = el("section", "sidebar-chat-section");
    section.setAttribute("aria-label", "对话历史");
    var heading = el("div", "sidebar-chat-heading");
    heading.appendChild(el("span", null, "对话"));
    var button = el("button", "sidebar-new-chat", "＋");
    button.type = "button";
    button.dataset.newChat = "";
    button.setAttribute("aria-label", "新建对话");
    heading.appendChild(button);
    section.appendChild(heading);
    var list = el("div", "conversation-list");
    list.dataset.conversationList = "";
    list.appendChild(el("div", "conversation-list-status", "正在读取历史记录…"));
    section.appendChild(list);
    return section;
  }

  function buildFooter() {
    var footer = el("div", "sidebar-footer");
    var status = el("div", "db-mini-status");
    status.dataset.dbMiniStatus = "";
    status.appendChild(el("span", "status-dot"));
    status.appendChild(el("span", null, "Odoo 19 · 正在检查数据库"));
    footer.appendChild(status);
    return footer;
  }

  function render() {
    var host = document.querySelector("[data-sidebar]");
    if (!host) return;
    var page = document.body.dataset.page;

    host.textContent = "";
    host.classList.add("sidebar");
    host.appendChild(buildBrand());
    GROUPS.forEach(function (group) {
      host.appendChild(buildNavGroup(group, page));
    });
    if (page === CONVERSATION_PAGE) {
      host.appendChild(buildConversationSection());
    }
    host.appendChild(buildFooter());
  }

  render();
})();
