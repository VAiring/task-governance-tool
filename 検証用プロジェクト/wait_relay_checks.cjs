// Offline checks of the exact documented, single-round orchestration example.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const document = readFileSync(join(__dirname, 'COMMON_EXECUTION.md'), 'utf8');
const marker = '```javascript\n';
const start = document.indexOf(marker);
assert.notEqual(start, -1);
const end = document.indexOf('\n```', start + marker.length);
assert.notEqual(end, -1);
const source = document.slice(start + marker.length, end);
const collectOnce = vm.runInNewContext(`${source}\ncollectOnce`);
const plain = value => JSON.parse(JSON.stringify(value));

test('two independent completions are polled concurrently exactly once', async () => {
  const calls = [];
  const release = [];
  const collecting = collectOnce([1, 2, 1], { write_stdin: args => {
    calls.push(plain(args));
    return new Promise(resolve => release.push(() => resolve({ exit_code: 0, output: String(args.session_id) })));
  }});
  assert.deepEqual(calls.map(x => x.session_id), [1, 2]);
  assert.ok(calls.every(x => x.chars === '' && x.yield_time_ms === 10000));
  release.forEach(resolve => resolve());
  const result = plain(await collecting);
  assert.deepEqual(result.pending_session_ids, []);
  assert.deepEqual(result.observations.map(x => x.result.output), ['1', '2']);
});

test('failure does not erase unrelated running progress or invent timeout', async () => {
  const result = plain(await collectOnce([1, 2], { write_stdin: async args =>
    args.session_id === 1 ? { exit_code: 7, output: 'failed check' }
      : { session_id: 2, output: 'progress' },
  }));
  assert.equal(result.observations[0].status, 'finished');
  assert.equal(result.observations[0].result.exit_code, 7);
  assert.equal(result.observations[1].result.output, 'progress');
  assert.deepEqual(result.pending_session_ids, [2]);
});

test('empty running response ends the round without sleep or automatic repoll', async () => {
  let calls = 0;
  const result = plain(await collectOnce([1], { write_stdin: async () => {
    calls++;
    return { session_id: 1, output: '' };
  }}));
  assert.equal(calls, 1);
  assert.deepEqual(result.pending_session_ids, [1]);
});

test('poll failure and missing state remain unknown without hidden retry', async () => {
  let calls = 0;
  const result = plain(await collectOnce([1, 2], { write_stdin: async args => {
    calls++;
    if (args.session_id === 1) throw new Error('private provider detail');
    return { output: 'unclassified' };
  }}));
  assert.equal(calls, 2);
  assert.deepEqual(result.observations.map(x => x.status), ['unknown', 'unknown']);
  assert.deepEqual(result.pending_session_ids, []);
  assert.ok(!JSON.stringify(result).includes('private provider detail'));
});

test('next round never repeats a completed result', async () => {
  const calls = [];
  let secondRound = false;
  const tools = { write_stdin: async args => {
    calls.push(args.session_id);
    return args.session_id === 1 || secondRound ? { exit_code: 0, output: 'done' }
      : { session_id: 2, output: 'working' };
  }};
  const first = await collectOnce([1, 2], tools);
  secondRound = true;
  const second = plain(await collectOnce(first.pending_session_ids, tools));
  assert.deepEqual(calls, [1, 2, 2]);
  assert.deepEqual(second.pending_session_ids, []);
});

test('stop at the response boundary prevents another round, not child-stop proof', async () => {
  let calls = 0;
  const tools = { write_stdin: async () => { calls++; return { session_id: 1, output: '' }; }};
  const first = await collectOnce([1], tools);
  const userRequestedStop = true;
  if (!userRequestedStop) await collectOnce(first.pending_session_ids, tools);
  assert.equal(calls, 1);
  assert.equal(first.observations[0].status, 'running');
});

test('an execution timeout indication is preserved, not inferred from a poll window', async () => {
  const timeout = { exit_code: 124, output: 'execution owner reports timeout' };
  const result = plain(await collectOnce([1], { write_stdin: async () => timeout }));
  assert.deepEqual(result.observations[0].result, timeout);
  assert.deepEqual(result.pending_session_ids, []);
});

test('both experiment labels use the identical program and request arguments', async () => {
  const traces = [];
  for (const label of ['with-skill', 'without-skill']) {
    const calls = [];
    const result = plain(await collectOnce([1, 2], { write_stdin: async args => {
      calls.push(plain(args)); return { exit_code: 0, output: 'same fixture' };
    }}));
    traces.push({ calls, result });
  }
  assert.deepEqual(traces[0], traces[1]);
});
