import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const templatePath = process.argv[2];
if (!templatePath) {
  throw new Error("template path is required");
}

const template = fs.readFileSync(templatePath, "utf8");
const elementHelpers = template.slice(
  template.indexOf("      const asText"),
  template.indexOf("      const statusClass")
);
const detailValueHelper = template.slice(
  template.indexOf("      const addDetailValue"),
  template.indexOf("      const completionEvidenceText")
);
const historyRenderer = template.slice(
  template.indexOf("      const renderCompletionHistory"),
  template.indexOf("      const renderDetail")
);

class FakeElement {
  constructor(name) {
    this.name = name;
    this.className = "";
    this.textContent = "";
    this.children = [];
  }

  append(...children) {
    this.children.push(...children);
  }
}

const detail = new FakeElement("section");
const context = {
  document: {
    createElement(name) {
      return new FakeElement(name);
    }
  },
  elements: { detail }
};
vm.createContext(context);
vm.runInContext(
  `${elementHelpers}${detailValueHelper}${historyRenderer}
globalThis.renderCompletionHistoryForHarness = renderCompletionHistory;`,
  context
);

const privateValues = {
  revision: "PRIVATE_REVISION_VALUE",
  reason: "</dd><img src=x onerror=PRIVATE_REASON_VALUE>",
  receipt: "PRIVATE_RECEIPT_VALUE"
};
const cycles = Array.from({ length: 12 }, (_, index) => ({
  completion_cycle_id: `tg_completion_cycle_${String(index).padStart(16, "0")}`,
  saved_cycle_ordinal: 12 - index,
  origin: "native_done",
  completeness: "complete",
  completed_at: "2026-07-30T00:00:00Z",
  completion_evidence: {
    kind: "git_commit",
    revision: privateValues.revision,
    reason: privateValues.reason,
    completion_commit_hash: privateValues.revision
  },
  review_target: {
    kind: "git_commit",
    value: privateValues.revision,
    base_revision: privateValues.revision,
    generation: index + 1
  },
  gate_basis: {
    kind: "independent_passes",
    qualifying_receipt_ids: [privateValues.receipt]
  }
}));

context.renderCompletionHistoryForHarness({
  total: 12,
  returned_count: 10,
  truncated: true,
  legacy_history_incomplete: true,
  cycles
});

const heading = detail.children[0];
const summary = detail.children[1];
const list = detail.children[2];
assert.equal(heading.textContent, "Completion history");
assert.equal(
  summary.textContent,
  "10 of 12 completion cycles; truncated; legacy history incomplete."
);
assert.equal(list.children.length, 10);
assert.equal(
  list.children[0].children[0].children[0].textContent,
  "Cycle 12"
);
const firstCycleValues = list.children[0].children[1];
assert.equal(firstCycleValues.children[0].textContent, "Cycle ID");
assert.match(
  firstCycleValues.children[7].textContent,
  /kind git_commit; revision PRIVATE_REVISION_VALUE/
);
assert.match(
  firstCycleValues.children[9].textContent,
  /kind git_commit; value PRIVATE_REVISION_VALUE; base revision PRIVATE_REVISION_VALUE; generation 1/
);
assert.equal(
  firstCycleValues.children[13].textContent,
  privateValues.receipt
);

const allText = (node) => [
  node.textContent,
  ...node.children.flatMap(allText)
].join("\n");
const renderedText = allText(detail);
for (const value of Object.values(privateValues)) {
  assert.equal(renderedText.includes(value), true);
}

console.log("M18.3 exact shipped completion-history harness PASS");
