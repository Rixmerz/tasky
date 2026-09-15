import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { parseDigest, summarize, renderDigest } from "../../tasky/web/digest.js";

const here = dirname(fileURLToPath(import.meta.url));
const focusCardsSample = readFileSync(join(here, "fixtures/focus-cards-sample.md"), "utf8");
const liveReply = readFileSync(join(here, "fixtures/focus-cards-live-reply.md"), "utf8");

// ---------- a minimal fake DOM, tracking every tag createElement was asked for ----------

function makeFakeText(text) {
  return { nodeType: 3, textContent: text };
}

function textContentOf(node) {
  if (node.nodeType === 3) return node.textContent;
  return node.children.map(textContentOf).join("");
}

function makeFakeDocument() {
  const createdTags = [];
  const doc = {
    createdTags,
    createTextNode: (text) => makeFakeText(text),
    createElement: (tag) => {
      createdTags.push(tag);
      const attrs = {};
      const el = {
        nodeType: 1,
        tagName: tag,
        children: [],
        attrs,
        className: "",
        get textContent() {
          return textContentOf(el);
        },
        setAttribute(name, value) {
          attrs[name] = String(value);
        },
        appendChild(child) {
          el.children.push(child);
          return child;
        },
        append(...nodes) {
          for (const n of nodes) el.appendChild(n);
        },
      };
      return el;
    },
  };
  return doc;
}

// ---------- dashboard spec: "Results render as readable digests" ----------

test("reply in the focus shape produces a headline and three lead-in cards", () => {
  const text = [
    "The invoice migration is written.",
    "",
    "**→ New column.** invoices.due_on is added as nullable.",
    "",
    "**→ Backfill needed.** Old rows have no due date.",
    "",
    "**→ Also risky.** The NOT NULL step can fail.",
  ].join("\n");
  const digest = parseDigest(text);
  assert.equal(digest.headline.map((t) => t.text).join(""), "The invoice migration is written.");
  assert.equal(digest.cards.length, 3);
  assert.deepEqual(
    digest.cards.map((c) => c.title.map((t) => t.text).join("")),
    ["New column.", "Backfill needed.", "Also risky."],
  );
});

test("numbered steps and a closing question mark step numbers and waiting", () => {
  const text = ["**1 →** Run staging.", "", "**2 →** Run backfill.", "", "Do you want to proceed?"].join("\n");
  const digest = parseDigest(text);
  assert.equal(digest.cards[0].tone, "step");
  assert.equal(digest.cards[0].step, 1);
  assert.equal(digest.cards[1].tone, "step");
  assert.equal(digest.cards[1].step, 2);
  assert.equal(digest.cards[2].tone, "question");
  assert.equal(digest.waiting, true);
});

test("headline is absent when the first paragraph opens with a bold lead-in", () => {
  const text = "**→ Lead.** The rest of the first paragraph, not a separate answer sentence.";
  const digest = parseDigest(text);
  assert.equal(digest.headline, null);
  assert.equal(digest.cards.length, 1);
  assert.equal(digest.cards[0].title.map((t) => t.text).join(""), "Lead.");
});

test("plain prose renders as a headline and paragraph cards", () => {
  const text = ["First paragraph, no bold.", "", "Second paragraph, still plain.", "", "Third one too."].join("\n");
  const digest = parseDigest(text);
  assert.equal(digest.headline.map((t) => t.text).join(""), "First paragraph, no bold.");
  assert.equal(digest.cards.length, 1);
  assert.equal(digest.cards[0].tone, "note");
  assert.equal(digest.cards[0].body.length, 2);
});

test("markup in a result is literal text and creates no element from it", () => {
  const text = "Here is the payload: <img src=x onerror=alert(1)>";
  const digest = parseDigest(text);
  const doc = makeFakeDocument();
  const tree = renderDigest(digest, doc);
  assert.ok(textContentOf(tree).includes("<img src=x onerror=alert(1)>"));
  assert.ok(!doc.createdTags.includes("img"));
});

test("a result ending inside an unclosed fence still renders the rest of the digest", () => {
  const text = ["Summary line.", "", "```sh", "python run.py", "still going"].join("\n");
  const digest = parseDigest(text);
  assert.equal(digest.headline.map((t) => t.text).join(""), "Summary line.");
  const codeBlock = digest.cards[0].body[0];
  assert.equal(codeBlock.type, "code");
  assert.equal(codeBlock.text, "python run.py\nstill going");
});

test("a link target with nested parens does not leak a stray ')' into the surrounding text", () => {
  const digest = parseDigest("See [here](javascript:alert(1)) for details.");
  const text = digest.headline.map((t) => t.text).join("");
  assert.equal(text, "See here for details.");
});

test("an unsafe javascript: link renders as text, not a link", () => {
  const digest = parseDigest("See [here](javascript:alert(1)) for details.");
  const first = digest.cards.length ? digest.cards[0].body[0] : { inline: digest.headline };
  const inline = digest.headline;
  assert.ok(inline.some((t) => t.type === "text" && t.text === "here"));
  assert.ok(!inline.some((t) => t.type === "link"));
});

// ---------- focus-output-style spec: "Style sample round-trip" ----------

test("the focus-cards-sample fixture round-trips into a headline and point/step cards", () => {
  const digest = parseDigest(focusCardsSample);
  assert.ok(digest.headline, "expected a headline");
  const pointCards = digest.cards.filter((c) => c.tone === "point" || c.tone === "step");
  assert.ok(pointCards.length >= 2, `expected at least 2 point/step cards, got ${pointCards.length}`);
  for (const card of digest.cards) {
    for (const block of card.body) {
      if (block.type === "paragraph") {
        assert.ok(!(block.inline[0] && block.inline[0].type === "strong"), "unparsed bold lead-in paragraph found");
      }
    }
  }
});

test("the focus-cards-live-reply fixture round-trips into four steps, a Risk point card, and a waiting question", () => {
  const digest = parseDigest(liveReply);
  assert.ok(digest.headline, "expected a headline");
  const stepCards = digest.cards.filter((c) => c.tone === "step");
  assert.equal(stepCards.length, 4);
  assert.deepEqual(stepCards.map((c) => c.step), [1, 2, 3, 4]);
  const riskCard = digest.cards.find((c) => c.title && c.title.map((t) => t.text).join("") === "Risk:");
  assert.ok(riskCard, "expected a point card titled Risk:");
  assert.equal(riskCard.tone, "point");
  const last = digest.cards[digest.cards.length - 1];
  assert.equal(last.tone, "question");
  assert.equal(digest.waiting, true);
});

// ---------- new contract update: question via trailing parenthetical or bold lead-in ----------

test("a bold lead-in ending in '?' is a question card even with a parenthetical aside after it", () => {
  const text = "**What does the endpoint actually do?** (Fetch data from which sources, APIs, files?)";
  const digest = parseDigest(text);
  const card = digest.cards[digest.cards.length - 1];
  assert.equal(card.tone, "question");
  assert.equal(digest.waiting, true);
  assert.equal(card.title.map((t) => t.text).join(""), "What does the endpoint actually do?");
});

test("plain text ending in a trailing parenthetical after a question mark is still a question", () => {
  const text = ["Point one.", "", "Do you want caching enabled? (yes or no)"].join("\n");
  const digest = parseDigest(text);
  const card = digest.cards[digest.cards.length - 1];
  assert.equal(card.tone, "question");
  assert.equal(digest.waiting, true);
});

// ---------- collapsed-card summary spec ----------

test("collapsed card shows the headline sentence for a done task", () => {
  const { summary } = summarize("Migration written and tests pass.\n\nMore detail here.");
  assert.equal(summary, "Migration written and tests pass.");
});

test("collapsed card marks waiting when the result ends with a question", () => {
  const { waiting } = summarize("All done.\n\nShould I also update the seed data?");
  assert.equal(waiting, true);
});

// ---------- card-marker and inline details ----------

test("'**Also found:**' opens an extra-tone card", () => {
  const digest = parseDigest("**Also found:** two unused imports.");
  assert.equal(digest.cards[0].tone, "extra");
});

test("a table with a separator row and a ragged row is parsed and padded", () => {
  const text = ["| Step | Reversible |", "| --- | --- |", "| Add column | Yes |", "| NOT NULL |"].join("\n");
  const digest = parseDigest(text);
  const table = digest.cards[0].body[0];
  assert.equal(table.type, "table");
  assert.deepEqual(table.header.map((c) => c.map((t) => t.text).join("")), ["Step", "Reversible"]);
  assert.equal(table.rows.length, 2);
  assert.deepEqual(table.rows[1].map((c) => c.map((t) => t.text).join("")), ["NOT NULL", ""]);
});

test("ordered and unordered lists are parsed as list blocks", () => {
  const digestUnordered = parseDigest("- one\n- two\n- three");
  const listU = digestUnordered.cards[0].body[0];
  assert.equal(listU.type, "list");
  assert.equal(listU.ordered, false);
  assert.equal(listU.items.length, 3);

  const digestOrdered = parseDigest("1. one\n2. two");
  const listO = digestOrdered.cards[0].body[0];
  assert.equal(listO.ordered, true);
  assert.equal(listO.items.length, 2);
});

test("a heading line is parsed as a heading block", () => {
  const digest = parseDigest("Some intro paragraph.\n\n### A heading\n\nMore text.");
  const heading = digest.cards[0].body.find((b) => b.type === "heading");
  assert.ok(heading);
  assert.equal(heading.level, 3);
  assert.equal(heading.inline.map((t) => t.text).join(""), "A heading");
});

test("inline code nests inside bold", () => {
  const digest = parseDigest("**→ Use `foo.bar`.** rest of sentence.");
  const title = digest.cards[0].title;
  const codeToken = title.find((t) => t.type === "code");
  assert.ok(codeToken, "expected a code token inside the title");
  assert.equal(codeToken.text, "foo.bar");
});

test("unmatched '**' stays literal", () => {
  const digest = parseDigest("a ** b with no closing marker");
  assert.equal(digest.headline.map((t) => t.text).join(""), "a ** b with no closing marker");
});

test("a plain question mid-text does not set waiting", () => {
  const text = ["Is this fine? Yes it is.", "", "Final statement, not a question."].join("\n");
  const digest = parseDigest(text);
  assert.equal(digest.waiting, false);
});

test("summarize ellipsizes at the limit and strips markdown markers", () => {
  const long = "a".repeat(200);
  const { summary } = summarize(`**→ Lead.** ${long}`, 40);
  assert.equal(summary.length, 40);
  assert.ok(summary.endsWith("…"));
  assert.ok(!summary.includes("**"));
});

// ---------- renderDigest ----------

test("renderDigest on the fixture contains every lead-in and no '**' anywhere", () => {
  const digest = parseDigest(focusCardsSample);
  const doc = makeFakeDocument();
  const tree = renderDigest(digest, doc);
  const text = textContentOf(tree);
  assert.ok(!text.includes("**"));
  const leadIns = ["New column.", "Backfill is required before the NOT NULL step.", "Also found:"];
  for (const leadIn of leadIns) {
    assert.ok(text.includes(leadIn), `expected rendered text to contain "${leadIn}"`);
  }
});

test("markup sample never creates an element whose tag came from the input text", () => {
  const digest = parseDigest("Payload: <script>alert(1)</script> and <div onclick=\"x()\">");
  const doc = makeFakeDocument();
  renderDigest(digest, doc);
  for (const tag of doc.createdTags) {
    assert.ok(!/^(script|div onclick|img)/i.test(tag));
  }
  assert.deepEqual(
    doc.createdTags.filter((t) => !["div", "p"].includes(t)),
    [],
  );
});

// ---------- never throws ----------

test("parseDigest and summarize never throw on empty, huge, or adversarial input", () => {
  const adversarial = [
    "",
    "**unbalanced",
    "`unbalanced code",
    "| ragged | table\n| only one cell",
    "```\nunclosed fence forever",
    "a".repeat(200_000),
    "[link](",
    "**".repeat(5000),
  ];
  for (const input of adversarial) {
    assert.doesNotThrow(() => parseDigest(input));
    assert.doesNotThrow(() => summarize(input));
  }
});
