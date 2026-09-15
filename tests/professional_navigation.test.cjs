const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const html = fs.readFileSync('index.html', 'utf8');
const source = html.slice(
  html.indexOf('async function requestProfessionalAnalysis('),
  html.indexOf('\nfunction renderProfessionalAnalysis(')
);

function review(index) {
  return {
    index,
    side: index % 2 ? 'white' : 'black',
    san: index % 2 ? 'e4' : 'e5',
    uci: index % 2 ? 'e2e4' : 'e7e5',
    before: {evaluation: '+0.10'},
    after: {evaluation: '+0.05'},
    centipawn_loss: 5,
    quality_symbol: '!',
    quality_label: '最佳着',
    best_move_san: index % 2 ? 'e4' : 'c5',
    best_move_uci: index % 2 ? 'e2e4' : 'c7c5',
    principal_variation: ['e4', 'e5'],
    opponent_variation: ['c5', 'Nf3'],
    openingContext: null
  };
}

function setup() {
  const elements = {};
  const calls = [];
  const element = id => elements[id] ||= {
    textContent: '',
    innerHTML: '',
    hidden: false,
    insertAdjacentHTML(_position, value) {
      this.innerHTML += value;
      if (value.includes('professionalRetryBtn')) element('professionalRetryBtn');
    }
  };
  const context = vm.createContext({
    professionalRequestToken: 0,
    professionalAnalysisCache: new Map(),
    professionalAnalysisPending: new Map(),
    gameReview: {analysis_id: 'game-a', openingSummary: null},
    OPENING_CONTEXT_UI_VERSION: 8,
    setTimeout,
    document: {getElementById: element},
    apiUrl: path => path,
    escapeHtml: value => String(value ?? ''),
    renderProfessionalAnalysis: payload => { context.rendered = payload.analysis; },
    analyzeGame: () => { context.reanalyzed = true; },
    fetchWithTimeout: (_url, options) => new Promise(resolve => {
      calls.push({body: JSON.parse(options.body), resolve});
    })
  });
  vm.runInContext(source, context);
  return {context, elements, calls, load: index => context.loadProfessionalAnalysis(review(index))};
}

const pause = () => new Promise(resolve => setTimeout(resolve, 280));
const respond = (call, analysis, status = 200) => call.resolve({
  ok: status === 200,
  status,
  json: async () => ({analysis})
});

test('cached selection invalidates an older in-flight response', async () => {
  const state = setup();
  state.context.professionalAnalysisCache.set('game-a:1:base', {
    analysis: 'cached A',
    _openingContextVersion: 8
  });
  const pendingB = state.load(2);
  await pause();
  await state.load(1);
  assert.equal(state.context.rendered, 'cached A');
  respond(state.calls[0], 'late B');
  await pendingB;
  assert.equal(state.context.rendered, 'cached A');
});

test('rapid navigation requests only the settled move and deduplicates it', async () => {
  const state = setup();
  const first = state.load(1);
  const second = state.load(2);
  await pause();
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].body.move_index, 2);
  const duplicate = state.load(2);
  await pause();
  assert.equal(state.calls.length, 1);
  respond(state.calls[0], 'move B');
  await Promise.all([first, second, duplicate]);
  assert.equal(state.context.rendered, 'move B');
});

test('failed detailed explanation shows verified fallback and retry', async () => {
  const state = setup();
  const pending = state.load(1);
  await pause();
  respond(state.calls[0], null);
  await pending;
  assert.match(state.elements.professionalAnalysisContent.innerHTML, /Stockfish 评价/);
  assert.match(state.elements.professionalAnalysisContent.innerHTML, /重新生成详细解释/);
  assert.equal(state.elements.professionalComplexity.textContent, '引擎核验');
  state.elements.professionalRetryBtn.onclick();
  await pause();
  assert.equal(state.calls.length, 2);
  respond(state.calls[1], 'recovered');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.context.rendered, 'recovered');
});

test('expired analysis offers whole-game regeneration without a blank panel', async () => {
  const state = setup();
  const pending = state.load(1);
  await pause();
  respond(state.calls[0], null, 404);
  await pending;
  assert.match(state.elements.professionalAnalysisContent.innerHTML, /Stockfish 评价/);
  state.elements.professionalRetryBtn.onclick();
  assert.equal(state.context.reanalyzed, true);
});
