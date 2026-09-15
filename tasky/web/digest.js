// Tolerant markdown-lite parser + DOM renderer for task results.
//
// parseDigest/summarize never throw: adversarial or huge input degrades to
// "as much structure as was recognised", never an exception. renderDigest
// only builds nodes with createElement/createTextNode so it can run against
// a fake `doc` in tests and never assigns raw markup to an element.

/** @typedef {{type:"text",text:string}|{type:"strong",children:Inline[]}|{type:"code",text:string}|{type:"link",text:string,href:string}} Inline */
/** @typedef {{type:"paragraph",inline:Inline[]}|{type:"heading",level:number,inline:Inline[]}|{type:"list",ordered:boolean,items:Inline[][]}|{type:"table",header:Inline[][],rows:Inline[][][]}|{type:"code",lang:string,text:string}} Block */
/** @typedef {{tone:"point"|"step"|"note"|"question"|"extra",step:number|null,title:Inline[]|null,body:Block[]}} Card */
/** @typedef {{headline:Inline[]|null,cards:Card[],waiting:boolean}} Digest */

const STEP_MARKER_RE = /^(\d+)\s*→\s*/;
const ARROW_MARKER_RE = /^→\s*/;
const DOT_MARKER_RE = /^(\d+)\.\s*/;
const FENCE_RE = /^```/;
const TABLE_ROW_RE = /^\s*\|/;
const UNORDERED_RE = /^[-*]\s+/;
const ORDERED_RE = /^\d+\.\s+/;
const HEADING_RE = /^(#{1,4})\s+(.*)$/;
const SEPARATOR_CELL_RE = /^:?-+:?$/;

// ---------- inline parsing ----------

/** Finds the `)` matching the link target's opening `(` at `start - 1`, tolerating nested parens in the URL. */
function findMatchingParen(text, start) {
  let depth = 1;
  let j = start;
  while (j < text.length) {
    if (text[j] === "(") depth++;
    else if (text[j] === ")") {
      depth--;
      if (depth === 0) return j;
    }
    j++;
  }
  return -1;
}

function flushText(tokens, buffer) {
  if (buffer) tokens.push({ type: "text", text: buffer });
  return "";
}

/** Full inline grammar: strong, code, http(s) links; unmatched markers stay literal. */
function parseInline(text) {
  const tokens = [];
  let buffer = "";
  let i = 0;
  const n = text.length;
  while (i < n) {
    if (text.startsWith("**", i)) {
      const close = text.indexOf("**", i + 2);
      if (close !== -1) {
        buffer = flushText(tokens, buffer);
        tokens.push({ type: "strong", children: parseInlineCodeOnly(text.slice(i + 2, close)) });
        i = close + 2;
        continue;
      }
      buffer += "**";
      i += 2;
      continue;
    }
    if (text[i] === "`") {
      const close = text.indexOf("`", i + 1);
      if (close !== -1) {
        buffer = flushText(tokens, buffer);
        tokens.push({ type: "code", text: text.slice(i + 1, close) });
        i = close + 1;
        continue;
      }
      buffer += "`";
      i += 1;
      continue;
    }
    if (text[i] === "[") {
      const closeBracket = text.indexOf("]", i + 1);
      if (closeBracket !== -1 && text[closeBracket + 1] === "(") {
        const closeParen = findMatchingParen(text, closeBracket + 2);
        if (closeParen !== -1) {
          const linkText = text.slice(i + 1, closeBracket);
          const href = text.slice(closeBracket + 2, closeParen);
          buffer = flushText(tokens, buffer);
          if (href.startsWith("http://") || href.startsWith("https://")) {
            tokens.push({ type: "link", text: linkText, href });
          } else {
            tokens.push({ type: "text", text: linkText });
          }
          i = closeParen + 1;
          continue;
        }
      }
      buffer += text[i];
      i += 1;
      continue;
    }
    buffer += text[i];
    i += 1;
  }
  flushText(tokens, buffer);
  return tokens;
}

/** Restricted grammar for text inside `**...**`: code spans only, per the digest contract. */
function parseInlineCodeOnly(text) {
  const tokens = [];
  let buffer = "";
  let i = 0;
  const n = text.length;
  while (i < n) {
    if (text[i] === "`") {
      const close = text.indexOf("`", i + 1);
      if (close !== -1) {
        buffer = flushText(tokens, buffer);
        tokens.push({ type: "code", text: text.slice(i + 1, close) });
        i = close + 1;
        continue;
      }
      buffer += "`";
      i += 1;
      continue;
    }
    buffer += text[i];
    i += 1;
  }
  flushText(tokens, buffer);
  return tokens;
}

function inlineToText(inline) {
  let out = "";
  for (const node of inline) {
    out += node.type === "strong" ? inlineToText(node.children) : node.text;
  }
  return out;
}

// ---------- block parsing ----------

function splitLines(text) {
  return text.split(/\r\n|\r|\n/);
}

function isSeparatorRow(cells) {
  return cells.every((c) => SEPARATOR_CELL_RE.test(c.trim()));
}

function splitTableRow(line) {
  let trimmed = line.trim();
  if (trimmed.startsWith("|")) trimmed = trimmed.slice(1);
  if (trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
  return trimmed.split("|").map((c) => c.trim());
}

function parseTable(rowLines) {
  const rawRows = rowLines.map(splitTableRow);
  const header = rawRows[0] || [];
  const width = header.length;
  const dataRows = [];
  for (let r = 1; r < rawRows.length; r++) {
    if (isSeparatorRow(rawRows[r])) continue;
    dataRows.push(rawRows[r]);
  }
  return {
    type: "table",
    header: header.map(parseInline),
    rows: dataRows.map((row) => {
      const padded = row.slice(0, width);
      while (padded.length < width) padded.push("");
      return padded.map(parseInline);
    }),
  };
}

function parseList(itemLines, ordered) {
  const marker = ordered ? ORDERED_RE : UNORDERED_RE;
  return { type: "list", ordered, items: itemLines.map((l) => parseInline(l.replace(marker, ""))) };
}

/** Blank lines separate blocks; everything else is grouped by the rules in the digest contract. */
function parseBlocks(text) {
  const lines = splitLines(text);
  const blocks = [];
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
      continue;
    }
    if (FENCE_RE.test(line)) {
      const lang = line.slice(3).trim();
      const codeLines = [];
      i++;
      while (i < n && lines[i].trim() !== "```") {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < n) i++; // consume the closing fence; an unclosed fence just runs to end
      blocks.push({ type: "code", lang, text: codeLines.join("\n") });
      continue;
    }
    if (TABLE_ROW_RE.test(line)) {
      const rows = [];
      while (i < n && lines[i].trim() !== "" && TABLE_ROW_RE.test(lines[i])) {
        rows.push(lines[i]);
        i++;
      }
      blocks.push(parseTable(rows));
      continue;
    }
    if (UNORDERED_RE.test(line) || ORDERED_RE.test(line)) {
      const ordered = ORDERED_RE.test(line);
      const marker = ordered ? ORDERED_RE : UNORDERED_RE;
      const itemLines = [];
      while (i < n && marker.test(lines[i])) {
        itemLines.push(lines[i]);
        i++;
      }
      blocks.push(parseList(itemLines, ordered));
      continue;
    }
    const heading = HEADING_RE.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, inline: parseInline(heading[2]) });
      i++;
      continue;
    }
    const paraLines = [];
    while (
      i < n &&
      lines[i].trim() !== "" &&
      !FENCE_RE.test(lines[i]) &&
      !TABLE_ROW_RE.test(lines[i]) &&
      !UNORDERED_RE.test(lines[i]) &&
      !ORDERED_RE.test(lines[i]) &&
      !HEADING_RE.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    blocks.push({ type: "paragraph", inline: parseInline(paraLines.join(" ").trim()) });
  }
  return blocks;
}

// ---------- card assembly ----------

function opensWithBold(block) {
  return block.type === "paragraph" && block.inline.length > 0 && block.inline[0].type === "strong";
}

/**
 * True only for the "**Lead.** rest of the sentence" shape: a bold span with
 * text after it in the same paragraph. A paragraph that is bold end to end
 * (nothing left after the closing `**`) is not a lead-in — it is the plain
 * answer sentence, wrapped in emphasis, and stays eligible to be the headline.
 */
function isBoldLeadInParagraph(block) {
  if (!opensWithBold(block)) return false;
  return trimLeadingInlineWhitespace(block.inline.slice(1)).length > 0;
}

/** Strips a leading `->`, `N ->` or `N.` marker from a lead-in's children, keeping trailing punctuation. */
function stripLeadInMarker(children) {
  if (children.length === 0 || children[0].type !== "text") {
    return { title: children, tone: "point", step: null };
  }
  let text = children[0].text;
  let tone = "point";
  let step = null;
  let m = STEP_MARKER_RE.exec(text);
  if (m) {
    tone = "step";
    step = Number(m[1]);
    text = text.slice(m[0].length);
  } else if ((m = ARROW_MARKER_RE.exec(text))) {
    text = text.slice(m[0].length);
  } else if ((m = DOT_MARKER_RE.exec(text))) {
    text = text.slice(m[0].length);
  }
  const title = text === "" ? children.slice(1) : [{ type: "text", text }, ...children.slice(1)];
  if (inlineToText(title).trim() === "Also found:") tone = "extra";
  return { title, tone, step };
}

function trimLeadingInlineWhitespace(inline) {
  if (inline.length === 0 || inline[0].type !== "text") return inline;
  const trimmed = inline[0].text.replace(/^\s+/, "");
  return trimmed === "" ? inline.slice(1) : [{ type: "text", text: trimmed }, ...inline.slice(1)];
}

function makeCardFromLeadIn(block) {
  const { title, tone, step } = stripLeadInMarker(block.inline[0].children);
  const card = { tone, step, title, body: [] };
  const rest = trimLeadingInlineWhitespace(block.inline.slice(1));
  if (rest.length > 0) card.body.push({ type: "paragraph", inline: rest });
  return card;
}

/** Strips at most one trailing `(...)` group (no nested parens) before the `?` check. */
function stripOneTrailingParenthetical(text) {
  const m = /^(.*?)\s*\([^()]*\)\s*$/.exec(text);
  return m ? m[1] : text;
}

/**
 * A paragraph is the closing question when its own text ends with `?` (a
 * trailing parenthetical aside is ignored first), or when its bold lead-in
 * alone ends with `?` even if a parenthetical follows it.
 */
function isQuestionBlock(block) {
  if (block.type !== "paragraph") return false;
  if (opensWithBold(block) && inlineToText(block.inline[0].children).trim().endsWith("?")) {
    return true;
  }
  const full = inlineToText(block.inline).trim();
  return stripOneTrailingParenthetical(full).trim().endsWith("?");
}

function buildDigest(blocks) {
  let headline = null;
  let start = 0;
  if (blocks.length > 0 && blocks[0].type === "paragraph" && !isBoldLeadInParagraph(blocks[0])) {
    headline = blocks[0].inline;
    start = 1;
  }
  const rest = blocks.slice(start);
  const cards = [];
  let current = null;
  let waiting = false;
  for (let i = 0; i < rest.length; i++) {
    const block = rest[i];
    const isLast = i === rest.length - 1;
    if (isLast && isQuestionBlock(block)) {
      waiting = true;
      if (opensWithBold(block)) {
        const card = makeCardFromLeadIn(block);
        card.tone = "question";
        cards.push(card);
        current = card;
      } else {
        cards.push({ tone: "question", step: null, title: null, body: [block] });
        current = null;
      }
      continue;
    }
    if (opensWithBold(block)) {
      const card = makeCardFromLeadIn(block);
      cards.push(card);
      current = card;
    } else {
      if (!current) {
        current = { tone: "note", step: null, title: null, body: [] };
        cards.push(current);
      }
      current.body.push(block);
    }
  }
  return { headline, cards, waiting };
}

export function parseDigest(text) {
  try {
    const safeText = typeof text === "string" ? text : String(text ?? "");
    return buildDigest(parseBlocks(safeText));
  } catch {
    return { headline: null, cards: [], waiting: false };
  }
}

// ---------- summary ----------

function blockToText(block) {
  switch (block.type) {
    case "paragraph":
    case "heading":
      return inlineToText(block.inline);
    case "list":
      return block.items.map(inlineToText).join(" ");
    case "table":
      return [block.header, ...block.rows]
        .map((row) => row.map(inlineToText).join(" "))
        .join(" ");
    case "code":
      return block.text;
    default:
      return "";
  }
}

function ellipsize(str, limit) {
  if (str.length <= limit) return str;
  if (limit <= 1) return str.slice(0, limit);
  return `${str.slice(0, limit - 1).trimEnd()}…`;
}

export function summarize(text, limit = 160) {
  try {
    const digest = parseDigest(text);
    let raw = "";
    if (digest.headline) {
      raw = inlineToText(digest.headline);
    } else if (digest.cards.length > 0) {
      const first = digest.cards[0];
      const titleText = first.title ? inlineToText(first.title) : "";
      const bodyText = first.body.map(blockToText).join(" ");
      raw = [titleText, bodyText].filter(Boolean).join(" ");
    }
    const collapsed = raw.replace(/\s+/g, " ").trim();
    return { summary: ellipsize(collapsed, limit), waiting: digest.waiting };
  } catch {
    return { summary: "", waiting: false };
  }
}

// ---------- rendering ----------

function appendInline(parent, inline, doc) {
  for (const node of inline) {
    if (node.type === "text") {
      parent.appendChild(doc.createTextNode(node.text));
    } else if (node.type === "code") {
      const code = doc.createElement("code");
      code.appendChild(doc.createTextNode(node.text));
      parent.appendChild(code);
    } else if (node.type === "strong") {
      const strong = doc.createElement("strong");
      appendInline(strong, node.children, doc);
      parent.appendChild(strong);
    } else if (node.type === "link") {
      const a = doc.createElement("a");
      a.setAttribute("href", node.href);
      a.setAttribute("rel", "noopener noreferrer");
      a.setAttribute("target", "_blank");
      a.appendChild(doc.createTextNode(node.text));
      parent.appendChild(a);
    }
  }
}

function renderBlock(block, doc) {
  if (block.type === "paragraph") {
    const p = doc.createElement("p");
    appendInline(p, block.inline, doc);
    return p;
  }
  if (block.type === "heading") {
    const p = doc.createElement("p");
    p.className = "digest-heading";
    p.setAttribute("data-level", String(block.level));
    appendInline(p, block.inline, doc);
    return p;
  }
  if (block.type === "list") {
    const list = doc.createElement(block.ordered ? "ol" : "ul");
    for (const item of block.items) {
      const li = doc.createElement("li");
      appendInline(li, item, doc);
      list.appendChild(li);
    }
    return list;
  }
  if (block.type === "table") {
    const wrap = doc.createElement("div");
    wrap.className = "digest-table-scroll";
    const table = doc.createElement("table");
    const thead = doc.createElement("thead");
    const headRow = doc.createElement("tr");
    for (const cell of block.header) {
      const th = doc.createElement("th");
      appendInline(th, cell, doc);
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = doc.createElement("tbody");
    for (const row of block.rows) {
      const tr = doc.createElement("tr");
      for (const cell of row) {
        const td = doc.createElement("td");
        appendInline(td, cell, doc);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }
  // block.type === "code"
  const wrap = doc.createElement("div");
  wrap.className = "digest-code-scroll";
  const pre = doc.createElement("pre");
  const code = doc.createElement("code");
  if (block.lang) code.setAttribute("data-lang", block.lang);
  code.appendChild(doc.createTextNode(block.text));
  pre.appendChild(code);
  wrap.appendChild(pre);
  return wrap;
}

function renderCard(card, doc) {
  const isExtra = card.tone === "extra";
  const container = doc.createElement(isExtra ? "details" : "article");
  container.className = "digest-card";
  container.setAttribute("data-tone", card.tone);
  if (card.step != null) container.setAttribute("data-step", String(card.step));

  if (card.title) {
    const titleEl = doc.createElement(isExtra ? "summary" : "p");
    titleEl.className = "digest-card-title";
    appendInline(titleEl, card.title, doc);
    container.appendChild(titleEl);
  }
  for (const block of card.body) {
    container.appendChild(renderBlock(block, doc));
  }
  return container;
}

/** Builds a digest element tree with createElement/createTextNode only. */
export function renderDigest(digest, doc) {
  const root = doc.createElement("div");
  root.className = "digest";
  if (digest.headline) {
    const headline = doc.createElement("p");
    headline.className = "digest-headline";
    appendInline(headline, digest.headline, doc);
    root.appendChild(headline);
  }
  for (const card of digest.cards) {
    root.appendChild(renderCard(card, doc));
  }
  return root;
}
