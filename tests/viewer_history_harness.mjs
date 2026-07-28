import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const templatePath = process.argv[2];
if (!templatePath) {
  throw new Error("template path is required");
}

const template = fs.readFileSync(templatePath, "utf8");
const constants = template.slice(
  template.indexOf("      const reloadStateOwner"),
  template.indexOf(
    "      const makeElement",
    template.indexOf("      const reloadStateOwner")
  )
);
const filterHelpers = template.slice(
  template.indexOf("      const selectedDynamicValue"),
  template.indexOf(
    "      const decodeSnapshot",
    template.indexOf("      const selectedDynamicValue")
  )
);
const historyFunctions = template.slice(
  template.indexOf("      const isViewerOwnedState"),
  template.indexOf("      const reconcileAutoRefresh")
);
const schedulerFunction = template.slice(
  template.indexOf("      const reconcileAutoRefresh"),
  template.indexOf("      const startAutoRefresh")
);
const startupBlock = template.slice(
  template.indexOf("      prepareReloadState();"),
  template.indexOf("    })();", template.indexOf("      prepareReloadState();"))
);
const expose = `
globalThis.harnessApi = {
  prepareReloadState,
  validatePendingReloadState,
  saveAutoReloadState,
  applyValidatedReloadState,
  restoreReloadEffects,
  reconcileAutoRefresh,
  selectedDynamicValue,
  setDefaultFilterValues,
  splitTags,
  get capability() { return reloadStateCapability; },
  get reloadRequested() { return reloadRequested; },
  get selectedTaskId() { return selectedTaskId; }
};`;

const task = {
  task_id: "tg_task_one",
  status: "ready",
  kind: "optional",
  lane: "VIEWER",
  priority: "high",
  tags: "viewer,offline"
};

const ownedState = (overrides = {}) => ({
  owner: "taskgov-viewer-auto-reload",
  schema_version: 1,
  captured_at_ms: 100000,
  status: "ready",
  kind: "optional",
  lane: "VIEWER",
  priority: "high",
  tag: "viewer",
  terminal: false,
  selected_task_id: "tg_task_one",
  scroll_x: 12,
  scroll_y: 345,
  focus_id: "status-filter",
  ...overrides
});

const makeScenario = (options = {}) => {
  const calls = {
    reads: 0,
    replaces: [],
    replaceArgumentCounts: [],
    scrolls: [],
    scrollAttempts: [],
    focuses: [],
    focusArguments: [],
    blurs: [],
    events: [],
    renderStates: [],
    renders: 0,
    reloads: 0,
    timeouts: []
  };
  let storedState = Object.hasOwn(options, "state") ? options.state : null;
  let scrollRestorationMode = "auto";
  const history = {
    length: 7,
    replaceState(value, title) {
      calls.replaceArgumentCounts.push(arguments.length);
      if (options.throwClear && value === null) {
        throw new Error("clear failed");
      }
      if (options.throwSave && value !== null) {
        throw new Error("save failed");
      }
      calls.replaces.push([value, title]);
      calls.events.push(value === null ? "history:clear" : "history:save");
      storedState = value;
    }
  };
  Object.defineProperty(history, "state", {
    get() {
      calls.reads += 1;
      if (options.throwRead) {
        throw new Error("read failed");
      }
      return storedState;
    }
  });
  if (!options.noScrollProperty) {
    Object.defineProperty(history, "scrollRestoration", {
      configurable: true,
      get() {
        if (options.throwScrollModeRead) {
          throw new Error("scroll mode read failed");
        }
        return scrollRestorationMode;
      },
      set(value) {
        if (options.throwScrollModeWrite) {
          throw new Error("scroll mode write failed");
        }
        if (!options.ignoreScrollModeWrite) {
          scrollRestorationMode = value;
        }
      }
    });
  }

  const fixedFocusIds = new Set([
    "search-filter",
    "status-filter",
    "kind-filter",
    "lane-filter",
    "priority-filter",
    "tag-filter",
    "terminal-filter",
    "reset-filters"
  ]);
  for (const id of options.extraFocusIds ?? []) {
    fixedFocusIds.add(id);
  }
  const valueControl = (name, initialValue) => {
    let value = initialValue;
    return {
      get value() {
        return value;
      },
      set value(nextValue) {
        value = nextValue;
        calls.events.push(`set:${name}:${String(nextValue)}`);
      }
    };
  };
  let terminalChecked = false;
  const elements = {
    search: valueControl("search", "excluded search"),
    status: valueControl("status", "blocked"),
    kind: valueControl("kind", "sequential"),
    lane: valueControl("lane", "2"),
    priority: valueControl("priority", "low"),
    tag: valueControl("tag", "2"),
    terminal: {
      get checked() {
        return terminalChecked;
      },
      set checked(nextValue) {
        terminalChecked = nextValue;
        calls.events.push(`set:terminal:${String(nextValue)}`);
      }
    },
    workspace: { hidden: false },
    fatal: { hidden: true, textContent: "" }
  };
  const context = {
    statusOrder: [
      "ready",
      "in_progress",
      "paused",
      "blocked",
      "review_pending",
      "done",
      "cancelled"
    ],
    kindLabels: { sequential: "Sequential", optional: "Optional" },
    priorityLabels: {
      urgent: "Urgent",
      high: "High",
      normal: "Normal",
      low: "Low"
    },
    terminalStatuses: new Set(["done", "cancelled"]),
    autoRefreshEnabled: options.autoRefreshEnabled ?? true,
    refreshTimeoutHandle: null,
    refreshIntervalMilliseconds: 5000,
    refreshEpochMilliseconds: 0,
    reloadRequested: false,
    window: {
      location: {
        protocol: options.protocol ?? "file:",
        href: "file:///viewer.html",
        reload() {
          calls.reloads += 1;
        }
      },
      history,
      scrollX: options.scrollX ?? 12,
      scrollY: options.scrollY ?? 345,
      scrollTo(x, y) {
        calls.scrollAttempts.push([x, y]);
        if (options.throwScroll) {
          throw new Error("scroll failed");
        }
        calls.scrolls.push([x, y]);
        calls.events.push(`scroll:${x},${y}`);
      },
      clearTimeout() {},
      setTimeout(callback, delay) {
        calls.timeouts.push({ callback, delay });
        return calls.timeouts.length;
      }
    },
    document: {
      activeElement: { id: options.activeId ?? "status-filter" },
      visibilityState: options.visibilityState ?? "visible",
      getElementById(id) {
        if (!fixedFocusIds.has(id) || options.missingFocus === id) {
          return null;
        }
        const fixedElement = {
          id,
          focus(focusOptions) {
            if (options.throwFocus) {
              throw new Error("focus failed");
            }
            context.document.activeElement = fixedElement;
            calls.focuses.push(id);
            calls.focusArguments.push(focusOptions);
            calls.events.push(`focus:${id}`);
          },
          blur() {
            if (options.throwBlur) {
              throw new Error("blur failed");
            }
            if (context.document.activeElement === fixedElement) {
              context.document.activeElement = { id: "" };
            }
            calls.blurs.push(id);
            calls.events.push(`blur:${id}`);
          }
        };
        return fixedElement;
      }
    },
    performance: {
      now() {
        return options.monotonicNow ?? 5000;
      },
      getEntriesByType() {
        return [{ type: options.navigationType ?? "reload" }];
      }
    },
    Date: {
      now() {
        return options.now ?? 100000;
      }
    },
    TextEncoder,
    elements,
    selectedTaskId: Object.hasOwn(options, "selectedTaskId")
      ? options.selectedTaskId
      : null,
    laneValues: options.laneValues ?? ["VIEWER"],
    tagValues: options.tagValues ?? ["viewer", "offline"],
    snapshot: { tasks: options.tasks ?? [task] },
    decodeSnapshot() {
      calls.events.push("decode");
      if (options.throwDecode) {
        throw new Error("decode failed");
      }
      return { tasks: options.tasks ?? [task] };
    },
    initializeMetadata() {},
    initializeFilters() {
      context.harnessApi.setDefaultFilterValues();
    },
    renderStatusSummary() {},
    bindFilters() {},
    startAutoRefresh() {
      calls.events.push("start");
    },
    renderTasks() {
      const visible = context.snapshot.tasks.filter((candidate) => {
        if (
          !elements.terminal.checked
          && context.terminalStatuses.has(candidate.status)
        ) {
          return false;
        }
        if (elements.status.value && candidate.status !== elements.status.value) {
          return false;
        }
        if (elements.kind.value && candidate.kind !== elements.kind.value) {
          return false;
        }
        if (
          elements.priority.value
          && candidate.priority !== elements.priority.value
        ) {
          return false;
        }
        const lane = context.harnessApi.selectedDynamicValue(
          elements.lane,
          context.laneValues
        );
        const tag = context.harnessApi.selectedDynamicValue(
          elements.tag,
          context.tagValues
        );
        if (lane && candidate.lane !== lane) {
          return false;
        }
        if (
          tag
          && !context.harnessApi.splitTags(candidate).includes(tag)
        ) {
          return false;
        }
        return true;
      });
      if (
        !visible.some(
          (candidate) => candidate.task_id === context.selectedTaskId
        )
      ) {
        context.selectedTaskId = visible.length ? visible[0].task_id : null;
      }
      calls.renders += 1;
      calls.events.push("render");
      calls.renderStates.push({
        search: elements.search.value,
        status: elements.status.value,
        kind: elements.kind.value,
        lane: elements.lane.value,
        priority: elements.priority.value,
        tag: elements.tag.value,
        terminal: elements.terminal.checked,
        selectedTaskId: context.selectedTaskId
      });
    }
  };
  vm.createContext(context);
  vm.runInContext(
    constants + filterHelpers + historyFunctions + schedulerFunction + expose,
    context
  );
  return {
    api: context.harnessApi,
    calls,
    context,
    history,
    runStartup() {
      vm.runInContext(startupBlock, context);
    },
    get state() {
      return storedState;
    }
  };
};

{
  const scenario = makeScenario({ state: ownedState() });
  scenario.runStartup();
  assert.equal(scenario.history.scrollRestoration, "manual");
  assert.deepEqual(scenario.calls.replaces, [[null, ""]]);
  assert.equal(scenario.calls.renders, 1);
  assert.deepEqual(scenario.calls.renderStates[0], {
    search: "",
    status: "ready",
    kind: "optional",
    lane: "1",
    priority: "high",
    tag: "1",
    terminal: false,
    selectedTaskId: "tg_task_one"
  });
  assert.deepEqual(scenario.calls.focuses, ["status-filter"]);
  assert.equal(scenario.calls.focusArguments[0].preventScroll, true);
  assert.deepEqual(scenario.calls.scrolls, [[12, 345]]);
  assert.ok(
    scenario.calls.events.indexOf("render")
      < scenario.calls.events.indexOf("focus:status-filter")
  );
  assert.ok(
    scenario.calls.events.indexOf("focus:status-filter")
      < scenario.calls.events.indexOf("scroll:12,345")
  );
  assert.ok(
    scenario.calls.events.indexOf("scroll:12,345")
      < scenario.calls.events.indexOf("start")
  );
}

{
  const scenario = makeScenario({
    state: null,
    activeId: "dynamic-task-button",
    selectedTaskId: "tg_task_one"
  });
  scenario.api.prepareReloadState();
  scenario.api.setDefaultFilterValues();
  scenario.context.elements.status.value = "ready";
  scenario.context.elements.kind.value = "optional";
  scenario.context.elements.lane.value = "1";
  scenario.context.elements.priority.value = "high";
  scenario.context.elements.tag.value = "1";
  scenario.context.elements.search.value = "private search";
  scenario.api.saveAutoReloadState();
  assert.equal(scenario.calls.replaces.length, 1);
  assert.equal(scenario.calls.replaceArgumentCounts[0], 2);
  assert.equal(scenario.state.focus_id, "");
  assert.equal(scenario.state.lane, "VIEWER");
  assert.equal(scenario.state.tag, "viewer");
  assert.deepEqual(Object.keys(scenario.state), Object.keys(ownedState()));
  assert.ok(
    new TextEncoder().encode(JSON.stringify(scenario.state)).byteLength <= 4096
  );
  assert.ok(!Object.values(scenario.state).includes("private search"));
  assert.equal(scenario.history.length, 7);
  assert.equal(scenario.context.window.location.href, "file:///viewer.html");
}

{
  const noSelection = makeScenario({
    state: null,
    selectedTaskId: null
  });
  noSelection.api.prepareReloadState();
  noSelection.api.setDefaultFilterValues();
  noSelection.api.saveAutoReloadState();
  assert.equal(noSelection.calls.replaces.length, 0);

  const unmanaged = makeScenario({
    state: null,
    autoRefreshEnabled: false
  });
  unmanaged.api.prepareReloadState();
  assert.equal(unmanaged.calls.reads, 1);
  assert.equal(unmanaged.calls.replaces.length, 0);
  assert.equal(unmanaged.history.scrollRestoration, "auto");
  assert.equal(unmanaged.api.capability, false);
}

{
  const unknown = { owner: "someone-else", private_value: "unchanged" };
  const scenario = makeScenario({ state: unknown });
  scenario.api.prepareReloadState();
  scenario.api.saveAutoReloadState();
  assert.equal(scenario.calls.replaces.length, 0);
  assert.deepEqual(scenario.state, unknown);
}

const JSON_EXPANDED_TEXT = "\u0000".repeat(1024);
const oversizedEnvelope = ownedState({
  lane: JSON_EXPANDED_TEXT,
  tag: JSON_EXPANDED_TEXT
});
assert.equal(new TextEncoder().encode(JSON_EXPANDED_TEXT).byteLength, 1024);
assert.ok(
  new TextEncoder().encode(JSON.stringify(oversizedEnvelope)).byteLength > 4096
);
const oversizedLane = "界".repeat(341) + "ab";
const oversizedTag = "界".repeat(341) + "ab";
assert.equal(new TextEncoder().encode(oversizedLane).byteLength, 1025);
assert.equal(new TextEncoder().encode(oversizedTag).byteLength, 1025);

const invalidCases = [
  ["schema version", { state: ownedState({ schema_version: 2 }) }],
  ["schema type", { state: ownedState({ schema_version: "1" }) }],
  ["extra key", { state: { ...ownedState(), extra: true } }],
  [
    "missing key",
    {
      state: Object.fromEntries(
        Object.entries(ownedState()).filter(([key]) => key !== "status")
      )
    }
  ],
  ["future capture", { state: ownedState({ captured_at_ms: 100001 }) }],
  ["stale capture", { state: ownedState({ captured_at_ms: -200001 }) }],
  ["capture type", { state: ownedState({ captured_at_ms: "100000" }) }],
  [
    "capture unsafe integer",
    { state: ownedState({ captured_at_ms: Number.MAX_SAFE_INTEGER + 1 }) }
  ],
  ["status type", { state: ownedState({ status: 1 }) }],
  ["status value", { state: ownedState({ status: "unknown" }) }],
  ["kind type", { state: ownedState({ kind: false }) }],
  ["kind value", { state: ownedState({ kind: "parallel" }) }],
  ["lane type", { state: ownedState({ lane: 1 }) }],
  [
    "lane missing option",
    {
      state: ownedState({ lane: "missing" }),
      tasks: [{ ...task, lane: "missing" }]
    }
  ],
  [
    "lane byte bound",
    {
      state: ownedState({ lane: oversizedLane }),
      laneValues: [oversizedLane],
      tasks: [{ ...task, lane: oversizedLane }]
    }
  ],
  ["tag type", { state: ownedState({ tag: [] }) }],
  [
    "tag missing option",
    {
      state: ownedState({ tag: "missing" }),
      tasks: [{ ...task, tags: "missing" }]
    }
  ],
  [
    "tag byte bound",
    {
      state: ownedState({ tag: oversizedTag }),
      tagValues: [oversizedTag],
      tasks: [{ ...task, tags: oversizedTag }]
    }
  ],
  ["priority type", { state: ownedState({ priority: {} }) }],
  ["priority value", { state: ownedState({ priority: "critical" }) }],
  ["terminal type", { state: ownedState({ terminal: "false" }) }],
  ["task ID type", { state: ownedState({ selected_task_id: 7 }) }],
  ["task ID empty", { state: ownedState({ selected_task_id: "" }) }],
  [
    "task ID character bound",
    {
      state: ownedState({ selected_task_id: "t".repeat(129) }),
      tasks: [{ ...task, task_id: "t".repeat(129) }]
    }
  ],
  [
    "task missing",
    { state: ownedState({ selected_task_id: "tg_task_missing" }) }
  ],
  ["task filtered out", { state: ownedState({ status: "blocked" }) }],
  [
    "task hidden by kind",
    {
      state: ownedState({
        status: "",
        kind: "sequential",
        priority: "",
        lane: "",
        tag: ""
      })
    }
  ],
  [
    "task hidden by priority",
    {
      state: ownedState({
        status: "",
        kind: "",
        priority: "low",
        lane: "",
        tag: ""
      })
    }
  ],
  [
    "terminal task hidden",
    {
      state: ownedState({
        status: "",
        kind: "",
        priority: "",
        lane: "",
        tag: "",
        selected_task_id: "tg_task_done"
      }),
      tasks: [{
        ...task,
        task_id: "tg_task_done",
        status: "done"
      }]
    }
  ],
  ["scroll x type", { state: ownedState({ scroll_x: "12" }) }],
  ["scroll x negative", { state: ownedState({ scroll_x: -1 }) }],
  [
    "scroll x upper bound",
    { state: ownedState({ scroll_x: 2147483648 }) }
  ],
  ["scroll y type", { state: ownedState({ scroll_y: null }) }],
  ["scroll y finite", { state: ownedState({ scroll_y: Infinity }) }],
  [
    "scroll y upper bound",
    { state: ownedState({ scroll_y: 2147483648 }) }
  ],
  ["focus type", { state: ownedState({ focus_id: 7 }) }],
  [
    "focus allow-list",
    {
      state: ownedState({ focus_id: "dynamic-task" }),
      extraFocusIds: ["dynamic-task"]
    }
  ],
  [
    "focus unavailable",
    { state: ownedState(), missingFocus: "status-filter" }
  ],
  [
    "navigation type",
    { state: ownedState(), navigationType: "navigate" }
  ],
  [
    "serialized size",
    {
      state: oversizedEnvelope,
      laneValues: [oversizedEnvelope.lane],
      tagValues: [oversizedEnvelope.tag],
      tasks: [{
        ...task,
        lane: oversizedEnvelope.lane,
        tags: oversizedEnvelope.tag
      }]
    }
  ]
];

for (const [name, options] of invalidCases) {
  const scenario = makeScenario(options);
  scenario.api.prepareReloadState();
  assert.equal(
    scenario.api.validatePendingReloadState(),
    null,
    `accepted invalid ${name}`
  );
  assert.deepEqual(
    scenario.calls.replaces,
    [[null, ""]],
    `did not consume invalid ${name}`
  );
}

{
  const boundaryText = "界".repeat(341) + "a";
  assert.equal(new TextEncoder().encode(boundaryText).byteLength, 1024);
  const boundaryTaskId = "t".repeat(128);
  const boundaryTask = {
    ...task,
    task_id: boundaryTaskId,
    lane: boundaryText,
    tags: boundaryText
  };
  const scenario = makeScenario({
    state: ownedState({
      lane: boundaryText,
      tag: boundaryText,
      selected_task_id: boundaryTaskId,
      scroll_x: 2147483647,
      scroll_y: 2147483647
    }),
    laneValues: [boundaryText],
    tagValues: [boundaryText],
    tasks: [boundaryTask]
  });
  scenario.api.prepareReloadState();
  const accepted = scenario.api.validatePendingReloadState();
  assert.equal(accepted.selected_task_id.length, 128);
  assert.equal(accepted.scroll_x, 2147483647);
  assert.equal(accepted.scroll_y, 2147483647);
}

{
  const clearFailure = makeScenario({
    state: ownedState(),
    throwClear: true
  });
  clearFailure.api.prepareReloadState();
  assert.equal(clearFailure.api.capability, false);
  assert.equal(clearFailure.api.validatePendingReloadState(), null);

  const saveFailure = makeScenario({
    state: null,
    throwSave: true,
    selectedTaskId: "tg_task_one"
  });
  saveFailure.api.prepareReloadState();
  saveFailure.api.setDefaultFilterValues();
  saveFailure.api.reconcileAutoRefresh();
  assert.equal(saveFailure.calls.reloads, 1);
  assert.equal(saveFailure.api.reloadRequested, true);

  for (const options of [
    { noScrollProperty: true },
    { throwScrollModeWrite: true },
    { throwScrollModeRead: true },
    { ignoreScrollModeWrite: true }
  ]) {
    const noScrollMode = makeScenario({
      state: ownedState(),
      ...options
    });
    noScrollMode.api.prepareReloadState();
    assert.equal(noScrollMode.api.capability, false);
    assert.deepEqual(noScrollMode.calls.replaces, [[null, ""]]);
    assert.equal(noScrollMode.api.validatePendingReloadState(), null);
  }

  const readFailure = makeScenario({
    state: ownedState(),
    throwRead: true
  });
  readFailure.api.prepareReloadState();
  assert.equal(readFailure.calls.reads, 1);
  assert.equal(readFailure.calls.replaces.length, 0);
  assert.equal(readFailure.api.capability, false);
  readFailure.api.reconcileAutoRefresh();
  assert.equal(readFailure.calls.reloads, 1);
}

{
  const nonFile = makeScenario({
    state: ownedState(),
    protocol: "https:"
  });
  nonFile.api.prepareReloadState();
  assert.equal(nonFile.calls.reads, 0);
  assert.equal(nonFile.calls.replaces.length, 0);

  const hidden = makeScenario({ state: null, visibilityState: "hidden" });
  hidden.api.prepareReloadState();
  hidden.api.reconcileAutoRefresh();
  assert.equal(hidden.calls.reloads, 0);
  assert.equal(hidden.calls.timeouts.length, 0);

  const early = makeScenario({ state: null, monotonicNow: 2000 });
  early.api.prepareReloadState();
  early.api.reconcileAutoRefresh();
  assert.equal(early.calls.reloads, 0);
  assert.equal(early.calls.timeouts.length, 1);
  assert.equal(early.calls.timeouts[0].delay, 3000);

  const repeated = makeScenario({
    state: null,
    selectedTaskId: "tg_task_one"
  });
  repeated.api.prepareReloadState();
  repeated.api.setDefaultFilterValues();
  repeated.api.reconcileAutoRefresh();
  repeated.api.reconcileAutoRefresh();
  assert.equal(repeated.calls.reloads, 1);
  assert.equal(
    repeated.calls.replaces.filter(([value]) => value !== null).length,
    1
  );
}

for (const [capturedAt, now, accepted] of [
  [100000, 100000, true],
  [0, 300000, true],
  [0, 300001, false]
]) {
  const scenario = makeScenario({
    state: ownedState({ captured_at_ms: capturedAt }),
    now
  });
  scenario.api.prepareReloadState();
  assert.equal(Boolean(scenario.api.validatePendingReloadState()), accepted);
}

{
  const missingFocus = makeScenario({
    state: ownedState(),
    missingFocus: "status-filter"
  });
  missingFocus.runStartup();
  assert.equal(missingFocus.calls.renders, 1);
  assert.deepEqual(missingFocus.calls.renderStates[0], {
    search: "",
    status: "",
    kind: "",
    lane: "",
    priority: "",
    tag: "",
    terminal: false,
    selectedTaskId: "tg_task_one"
  });
  assert.deepEqual(missingFocus.calls.scrolls, [[0, 0]]);

  const focusFailure = makeScenario({
    state: ownedState(),
    throwFocus: true
  });
  focusFailure.runStartup();
  assert.equal(focusFailure.calls.renders, 2);
  assert.deepEqual(focusFailure.calls.renderStates[1], {
    search: "",
    status: "",
    kind: "",
    lane: "",
    priority: "",
    tag: "",
    terminal: false,
    selectedTaskId: "tg_task_one"
  });
  assert.deepEqual(focusFailure.calls.scrolls, [[0, 0]]);

  const scrollFailure = makeScenario({
    state: ownedState(),
    throwScroll: true
  });
  scrollFailure.runStartup();
  assert.equal(scrollFailure.calls.renders, 2);
  assert.deepEqual(scrollFailure.calls.scrollAttempts, [
    [12, 345],
    [0, 0]
  ]);
  assert.deepEqual(scrollFailure.calls.focuses, ["status-filter"]);
  assert.deepEqual(scrollFailure.calls.blurs, ["status-filter"]);
  assert.equal(scrollFailure.context.document.activeElement.id, "");
  assert.deepEqual(scrollFailure.calls.renderStates[1], {
    search: "",
    status: "",
    kind: "",
    lane: "",
    priority: "",
    tag: "",
    terminal: false,
    selectedTaskId: "tg_task_one"
  });
}

{
  const fatalDecode = makeScenario({
    state: ownedState(),
    throwDecode: true
  });
  fatalDecode.runStartup();
  assert.deepEqual(fatalDecode.calls.replaces, [[null, ""]]);
  assert.ok(
    fatalDecode.calls.events.indexOf("history:clear")
      < fatalDecode.calls.events.indexOf("decode")
  );
  assert.equal(fatalDecode.context.elements.workspace.hidden, true);
  assert.equal(fatalDecode.context.elements.fatal.hidden, false);
  assert.equal(
    fatalDecode.context.elements.fatal.textContent,
    "Viewer data could not be loaded."
  );
}

{
  const secondReload = makeScenario({
    state: null,
    navigationType: "reload"
  });
  secondReload.runStartup();
  assert.equal(secondReload.calls.renders, 1);
  assert.deepEqual(secondReload.calls.renderStates[0], {
    search: "",
    status: "",
    kind: "",
    lane: "",
    priority: "",
    tag: "",
    terminal: false,
    selectedTaskId: "tg_task_one"
  });
  assert.deepEqual(secondReload.calls.scrolls, [[0, 0]]);
  assert.equal(secondReload.calls.replaces.length, 0);
}

console.log("M15.6 exact shipped History harness PASS");
