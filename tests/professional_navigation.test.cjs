const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const html = fs.readFileSync('index.html','utf8');
const source = html.slice(html.indexOf('async function requestProfessionalAnalysis('), html.indexOf('\nfunction renderProfessionalAnalysis('));
function setup() {
  const elements = {};
  const calls = [];
  const context = vm.createContext({
    professionalRequestToken: 0, professionalAnalysisCache: new Map(), professionalAnalysisPending: new Map(),
    gameReview: {analysis_id:'game-a'}, AbortSignal, setTimeout,
    document: {getElementById(id) {return elements[id] ||= {textContent:'',innerHTML:'',hidden:false};}},
    apiUrl: p => p, renderProfessionalAnalysis: p => {context.rendered = p.analysis;},
    analyzeGame: () => {context.reanalyzed = true;},
    fetch: (url, options) => new Promise(resolve => calls.push({body:JSON.parse(options.body),resolve}))
  });
  vm.runInContext(source, context);
  return {context,elements,calls,load:i => context.loadProfessionalAnalysis({index:i})};
}
const pause = () => new Promise(r => setTimeout(r,280));
const respond = (call, analysis, status=200) => call.resolve({ok:status===200,status,json:async()=>({analysis})});
test('cached selection rejects older response and clears previous summary', async()=>{
  const s=setup(); s.context.professionalAnalysisCache.set('game-a:1',{analysis:'cached A'});
  const b=s.load(2); await pause();
  s.elements.researcherSummaryLine.textContent='old verdict';
  await s.load(1); assert.equal(s.elements.researcherSummaryLine.textContent,'');
  respond(s.calls[0],'late B'); await b;
  assert.equal(s.context.rendered,'cached A');
});
test('rapid navigation sends only settled selection and deduplicates pending work', async()=>{
  const s=setup(); const a=s.load(1); const b=s.load(2); await pause();
  assert.equal(s.calls.length,1); assert.equal(s.calls[0].body.move_index,2);
  const again=s.load(2); await pause(); assert.equal(s.calls.length,1);
  respond(s.calls[0],'B'); await Promise.all([a,b,again]); assert.equal(s.context.rendered,'B');
});
test('old game cannot pollute new game cache at same index', async()=>{
  const s=setup(); const a=s.load(1); await pause();
  s.context.gameReview={analysis_id:'game-b'}; const b=s.load(1); await pause();
  respond(s.calls[1],'new game'); await b; respond(s.calls[0],'old game'); await a;
  assert.equal(s.context.rendered,'new game'); assert.equal(s.context.professionalAnalysisCache.has('game-a:1'),false);
});
test('failure exits loading, exposes retry, and retry succeeds', async()=>{
  const s=setup(); const a=s.load(1); await pause(); respond(s.calls[0],null); await a;
  assert.equal(s.elements.professionalComplexity.textContent,'未完成');
  assert.equal(s.context.professionalAnalysisPending.size,0);
  s.elements.professionalRetryBtn.onclick(); await pause(); respond(s.calls[1],'recovered');
  await new Promise(r=>setImmediate(r)); assert.equal(s.context.rendered,'recovered');
});
test('expired analysis offers whole-game recovery', async()=>{
  const s=setup(); const a=s.load(1); await pause(); respond(s.calls[0],null,404); await a;
  s.elements.professionalRetryBtn.onclick(); assert.equal(s.context.reanalyzed,true);
});
