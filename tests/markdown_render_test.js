/**
 * markdown.js 的回归测试。
 *
 * 仓库刻意没有前端构建链，所以这里用一个最小 DOM 桩在 Node 里直接跑渲染器，
 * 由 backend/tests/test_markdown_rendering.py 纳入统一的 unittest 门禁。
 */
"use strict";

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const VOID_TAGS = new Set(["hr", "br"]);

function escapeText(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

class StubNode {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.className = "";
    this._text = null;
  }

  get lastChild() {
    return this.children.length ? this.children[this.children.length - 1] : null;
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  get textContent() {
    if (this._text !== null) return this._text;
    return this.children.map((child) => child.textContent).join("");
  }

  appendChild(node) {
    this.children.push(node);
    return node;
  }

  toHtml() {
    if (this.tagName === "#text") return escapeText(this._text);
    if (VOID_TAGS.has(this.tagName)) return `<${this.tagName}>`;
    const attrs = Object.keys(this.attributes)
      .map((key) => ` ${key}="${escapeText(this.attributes[key])}"`)
      .join("");
    const cls = this.className ? ` class="${escapeText(this.className)}"` : "";
    const inner = this._text !== null
      ? escapeText(this._text)
      : this.children.map((child) => child.toHtml()).join("");
    return `<${this.tagName}${cls}${attrs}>${inner}</${this.tagName}>`;
  }
}

class StubElement extends StubNode {
  set href(value) {
    this.attributes.href = value;
  }

  get href() {
    return this.attributes.href;
  }

  set target(value) {
    this.attributes.target = value;
  }

  set rel(value) {
    this.attributes.rel = value;
  }
}

function createTextNode(value) {
  const node = new StubNode("#text");
  node._text = String(value);
  return node;
}

function loadRenderer() {
  const source = fs.readFileSync(path.join(__dirname, "..", "markdown.js"), "utf8");
  const sandbox = {
    document: {
      createElement: (tag) => new StubElement(tag),
      createTextNode,
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return sandbox.OdooAgentMarkdown;
}

const markdown = loadRenderer();

function render(text) {
  const root = new StubElement("div");
  markdown.render(text, root);
  return root.toHtml();
}

const cases = [];
function test(name, fn) {
  cases.push([name, fn]);
}

test("bold, italic and inline code become real elements", () => {
  const html = render("这是 **加粗**、*斜体* 和 `code` 的段落。");
  assert.match(html, /<strong>加粗<\/strong>/);
  assert.match(html, /<em>斜体<\/em>/);
  assert.match(html, /<code>code<\/code>/);
});

test("unordered and ordered lists render as list elements", () => {
  const unordered = render("- 第一项\n- 第二项");
  assert.match(unordered, /<ul><li>第一项<\/li><li>第二项<\/li><\/ul>/);
  const ordered = render("1. 甲\n2. 乙");
  assert.match(ordered, /<ol><li>甲<\/li><li>乙<\/li><\/ol>/);
});

test("nested list items nest inside the parent li, not beside it", () => {
  // <ul> 直接挂在 <ul> 下是无效 HTML，必须落在 <li> 里面。
  assert.strictEqual(
    render("- 外层\n  - 内层"),
    "<div><ul><li>外层<ul><li>内层</li></ul></li></ul></div>",
  );
});

test("an ordered list can contain a nested unordered list", () => {
  // 模型最常见的写法：编号要点下挂几个 bullet。此前会断成两个并列列表。
  const html = render("1. 甲\n2. 乙\n   - 细节一\n   - 细节二");
  assert.strictEqual(
    html,
    "<div><ol><li>甲</li><li>乙<ul><li>细节一</li><li>细节二</li></ul></li></ol></div>",
  );
});

test("returning to the outer indent continues the outer list", () => {
  const html = render("- 一\n  - 一之一\n- 二");
  assert.strictEqual(
    html,
    "<div><ul><li>一<ul><li>一之一</li></ul></li><li>二</li></ul></div>",
  );
});

test("tables render with a header row and scroll wrapper", () => {
  const html = render("| 语义层 | p50 |\n| --- | ---: |\n| native | 6.1s |\n| wren | 10.4s |");
  assert.match(html, /<div class="markdown-table"><table>/);
  assert.match(html, /<th>语义层<\/th><th>p50<\/th>/);
  assert.match(html, /<td>native<\/td><td>6\.1s<\/td>/);
  assert.match(html, /<td>wren<\/td>/);
});

test("fenced code blocks keep their content verbatim", () => {
  const html = render("```sql\nSELECT 1 < 2;\n```");
  assert.match(html, /<pre><code class="language-sql">SELECT 1 &lt; 2;<\/code><\/pre>/);
});

test("headings start below the page heading levels", () => {
  assert.match(render("# 标题"), /<h4>标题<\/h4>/);
  assert.match(render("### 三级"), /<h6>三级<\/h6>/);
});

test("blockquotes render nested block content", () => {
  const html = render("> 引用一句\n> - 列表项");
  assert.match(html, /<blockquote>/);
  assert.match(html, /<li>列表项<\/li>/);
});

test("http links are linkified with noopener", () => {
  const html = render("见 [Langfuse](https://cloud.langfuse.com) 文档");
  assert.match(html, /<a target="_blank" rel="noopener noreferrer" href="https:\/\/cloud\.langfuse\.com">Langfuse<\/a>|<a href="https:\/\/cloud\.langfuse\.com" target="_blank" rel="noopener noreferrer">Langfuse<\/a>/);
});

test("javascript and data URLs are never linkified", () => {
  for (const href of ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "vbscript:msgbox"]) {
    const html = render(`[点我](${href})`);
    assert.ok(!/<a /.test(html), `expected no anchor for ${href}, got ${html}`);
    assert.ok(!/javascript:/.test(html.replace(/&[a-z]+;/g, "")) || !/<a/.test(html));
  }
});

test("raw HTML in model output stays text and never becomes markup", () => {
  const payloads = [
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert(1)>",
    "<iframe src='javascript:alert(1)'></iframe>",
    "<svg/onload=alert(1)>",
  ];
  for (const payload of payloads) {
    const html = render(payload);
    assert.ok(!/<script|<img|<iframe|<svg/i.test(html), `markup leaked for ${payload}: ${html}`);
    assert.match(html, /&lt;/);
  }
});

test("html inside a table cell and a code fence is also escaped", () => {
  const table = render("| a |\n| --- |\n| <img src=x onerror=alert(1)> |");
  assert.ok(!/<img/i.test(table), table);
  const fence = render("```\n<script>alert(1)</script>\n```");
  assert.ok(!/<script/i.test(fence), fence);
});

test("empty and null input produce no output", () => {
  assert.strictEqual(render(""), "<div></div>");
  assert.strictEqual(render(null), "<div></div>");
});

test("plain multi-line prose becomes separate paragraphs", () => {
  const html = render("第一段。\n\n第二段。");
  assert.match(html, /<p>第一段。<\/p><p>第二段。<\/p>/);
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`ok   - ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`FAIL - ${name}\n       ${error.message}`);
  }
}
console.log(`\n${cases.length - failed}/${cases.length} passed`);
process.exit(failed ? 1 : 0);
