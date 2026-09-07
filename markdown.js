/**
 * 助手回答的 Markdown 渲染器。
 *
 * 设计约束（docs/19 §3.5）：自托管、不引入框架和构建链、不得引入 XSS。
 * 因此这里只用 document.createElement / createTextNode 构造 DOM，全程不碰
 * innerHTML —— 模型输出中的任何标签都只会成为文本节点，无法成为元素。
 */
(function (global) {
  "use strict";

  var SAFE_LINK = /^https?:\/\//i;
  var FENCE = /^\s*```/;
  var HEADING = /^(#{1,6})\s+(.*)$/;
  var BLOCKQUOTE = /^>\s?(.*)$/;
  var UNORDERED = /^(\s*)[-*+]\s+(.*)$/;
  var ORDERED = /^(\s*)(\d+)[.)]\s+(.*)$/;
  var RULE = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
  var TABLE_ROW = /^\s*\|(.+)\|\s*$/;
  var TABLE_DIVIDER = /^\s*\|[\s:|-]+\|\s*$/;
  var INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)|(\[[^\]]*\]\([^()\s]+\))/;

  function splitCells(row) {
    return row.split("|").map(function (cell) {
      return cell.trim();
    });
  }

  function appendInline(parent, text) {
    var remaining = String(text);
    while (remaining) {
      var match = INLINE.exec(remaining);
      if (!match) {
        parent.appendChild(document.createTextNode(remaining));
        return;
      }
      if (match.index > 0) {
        parent.appendChild(document.createTextNode(remaining.slice(0, match.index)));
      }
      var token = match[0];
      if (token.charAt(0) === "`") {
        var code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        parent.appendChild(code);
      } else if (token.slice(0, 2) === "**" || token.slice(0, 2) === "__") {
        var strong = document.createElement("strong");
        appendInline(strong, token.slice(2, -2));
        parent.appendChild(strong);
      } else if (token.charAt(0) === "[") {
        appendLink(parent, token);
      } else {
        var em = document.createElement("em");
        appendInline(em, token.slice(1, -1));
        parent.appendChild(em);
      }
      remaining = remaining.slice(match.index + token.length);
    }
  }

  function appendLink(parent, token) {
    var split = token.indexOf("](");
    var label = token.slice(1, split);
    var href = token.slice(split + 2, -1);
    // 只放行 http/https。javascript: 和 data: 等协议一律降级为纯文本，
    // 宁可少一个链接，也不给注入留入口。
    if (!SAFE_LINK.test(href)) {
      parent.appendChild(document.createTextNode(token));
      return;
    }
    var anchor = document.createElement("a");
    anchor.href = href;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    appendInline(anchor, label || href);
    parent.appendChild(anchor);
  }

  function flushParagraph(parent, buffer) {
    if (!buffer.length) return;
    var paragraph = document.createElement("p");
    appendInline(paragraph, buffer.join("\n"));
    parent.appendChild(paragraph);
    buffer.length = 0;
  }

  function readCodeBlock(lines, start) {
    var body = [];
    var index = start + 1;
    while (index < lines.length && !FENCE.test(lines[index])) {
      body.push(lines[index]);
      index += 1;
    }
    var pre = document.createElement("pre");
    var code = document.createElement("code");
    var language = lines[start].replace(/^\s*```/, "").trim();
    if (language) code.className = "language-" + language.replace(/[^\w-]/g, "");
    code.textContent = body.join("\n");
    pre.appendChild(code);
    return { node: pre, next: index + 1 };
  }

  function readTable(lines, start) {
    var table = document.createElement("table");
    var head = document.createElement("thead");
    var headRow = document.createElement("tr");
    splitCells(TABLE_ROW.exec(lines[start])[1]).forEach(function (cell) {
      var th = document.createElement("th");
      appendInline(th, cell);
      headRow.appendChild(th);
    });
    head.appendChild(headRow);
    table.appendChild(head);

    var body = document.createElement("tbody");
    var index = start + 2;
    while (index < lines.length && TABLE_ROW.test(lines[index])) {
      var row = document.createElement("tr");
      splitCells(TABLE_ROW.exec(lines[index])[1]).forEach(function (cell) {
        var td = document.createElement("td");
        appendInline(td, cell);
        row.appendChild(td);
      });
      body.appendChild(row);
      index += 1;
    }
    table.appendChild(body);

    // 宽表在窄屏要能自己横向滚动，而不是把整页撑宽。
    var wrapper = document.createElement("div");
    wrapper.className = "markdown-table";
    wrapper.appendChild(table);
    return { node: wrapper, next: index };
  }

  function matchListItem(line) {
    var ordered = ORDERED.exec(line);
    if (ordered) return { indent: ordered[1].length, ordered: true, text: ordered[3] };
    var unordered = UNORDERED.exec(line);
    if (unordered) return { indent: unordered[1].length, ordered: false, text: unordered[2] };
    return null;
  }

  function isListItem(line) {
    return matchListItem(line) !== null;
  }

  function readList(lines, start) {
    // 按缩进维护一个栈，每层各自决定是 ol 还是 ul —— 模型经常写“有序列表里
    // 套无序要点”，两种 marker 必须在同一次解析里处理，否则会断成两个并列列表。
    var first = matchListItem(lines[start]);
    var root = document.createElement(first.ordered ? "ol" : "ul");
    var stack = [{ indent: first.indent, ordered: first.ordered, list: root }];
    var index = start;

    while (index < lines.length) {
      var item = matchListItem(lines[index]);
      if (!item) break;
      while (stack.length > 1 && item.indent < stack[stack.length - 1].indent) stack.pop();
      var level = stack[stack.length - 1];

      if (item.indent > level.indent) {
        var nested = document.createElement(item.ordered ? "ol" : "ul");
        var host = level.list.lastChild;
        if (!host || String(host.tagName).toLowerCase() !== "li") {
          host = document.createElement("li");
          level.list.appendChild(host);
        }
        host.appendChild(nested);
        level = { indent: item.indent, ordered: item.ordered, list: nested };
        stack.push(level);
      } else if (item.ordered !== level.ordered && stack.length === 1) {
        // 同级换了 marker 类型：这是另一个列表，交回主循环处理。
        break;
      }

      var entry = document.createElement("li");
      appendInline(entry, item.text);
      level.list.appendChild(entry);
      index += 1;
    }
    return { node: root, next: index };
  }

  /**
   * 把 Markdown 文本渲染进 parent。返回 parent 以便链式使用。
   */
  function render(text, parent) {
    var lines = String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n");
    var buffer = [];
    var index = 0;

    while (index < lines.length) {
      var line = lines[index];

      if (FENCE.test(line)) {
        flushParagraph(parent, buffer);
        var code = readCodeBlock(lines, index);
        parent.appendChild(code.node);
        index = code.next;
        continue;
      }

      if (!line.trim()) {
        flushParagraph(parent, buffer);
        index += 1;
        continue;
      }

      if (RULE.test(line)) {
        flushParagraph(parent, buffer);
        parent.appendChild(document.createElement("hr"));
        index += 1;
        continue;
      }

      var heading = HEADING.exec(line);
      if (heading) {
        flushParagraph(parent, buffer);
        // 回答内部的标题从 h4 起，避免和页面自身的标题层级抢语义。
        var level = Math.min(6, heading[1].length + 3);
        var node = document.createElement("h" + level);
        appendInline(node, heading[2]);
        parent.appendChild(node);
        index += 1;
        continue;
      }

      if (TABLE_ROW.test(line) && index + 1 < lines.length && TABLE_DIVIDER.test(lines[index + 1])) {
        flushParagraph(parent, buffer);
        var table = readTable(lines, index);
        parent.appendChild(table.node);
        index = table.next;
        continue;
      }

      if (isListItem(line)) {
        flushParagraph(parent, buffer);
        var list = readList(lines, index);
        parent.appendChild(list.node);
        index = list.next;
        continue;
      }

      var quote = BLOCKQUOTE.exec(line);
      if (quote) {
        flushParagraph(parent, buffer);
        var block = document.createElement("blockquote");
        var quoted = [];
        while (index < lines.length && BLOCKQUOTE.test(lines[index])) {
          quoted.push(BLOCKQUOTE.exec(lines[index])[1]);
          index += 1;
        }
        render(quoted.join("\n"), block);
        parent.appendChild(block);
        continue;
      }

      buffer.push(line);
      index += 1;
    }

    flushParagraph(parent, buffer);
    return parent;
  }

  global.OdooAgentMarkdown = { render: render };
})(typeof window !== "undefined" ? window : globalThis);
