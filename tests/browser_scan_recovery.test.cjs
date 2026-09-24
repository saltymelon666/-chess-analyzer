const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const scanSource = html.split('async function scanGameInBrowser()', 2)[1]
  .split('async function analyzeGame()', 1)[0];

function makeScan(analyzeWithEngine) {
  const nodes = new Map();
  let resets = 0;
  const context = {
    moves: ['first', 'second', 'third'],
    document: { getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, { textContent: '' });
      return nodes.get(id);
    } },
    initEngine: async () => {},
    analyzeWithEngine,
    parseEngineOutput: output => ({ top_moves: output ? [{ move: output }] : [] }),
    resetLocalEngine: () => { resets += 1; },
    setTimeout,
    clearTimeout
  };
  const scan = vm.runInNewContext(`async function scanGameInBrowser()${scanSource}; scanGameInBrowser`, context);
  return { scan, nodes, getResets: () => resets };
}

test('a failed position restarts once and keeps completed positions', async () => {
  let secondCalls = 0;
  const runner = makeScan(async fen => {
    if (fen === 'second' && secondCalls++ === 0) throw new Error('worker stopped');
    return fen;
  });
  const results = await runner.scan();
  assert.deepEqual(Array.from(results, result => result.top_moves[0].move), ['first', 'second', 'third']);
  assert.equal(runner.getResets(), 1);
  assert.equal(runner.nodes.get('gameAnalyzeBtn').textContent, '扫描局面 3/3');
});

test('persistent worker failure exits after one restart', async () => {
  let calls = 0;
  const runner = makeScan(async () => { calls += 1; throw new Error('worker stopped'); });
  await assert.rejects(runner.scan(), /worker stopped/);
  assert.equal(calls, 2);
  assert.equal(runner.getResets(), 2);
});
