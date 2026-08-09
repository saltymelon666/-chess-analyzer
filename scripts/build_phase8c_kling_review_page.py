from __future__ import annotations

import html
import json
from pathlib import Path

import chess


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "work" / "research_books" / "phase8a_sources" / "kling-fenshot-results.json"
OUTPUT = ROOT / "docs" / "research" / "phase8c-kling-endgame-review-boards.html"


def _valid_turns(placement: str) -> list[str]:
    turns = []
    for turn in ("w", "b"):
        try:
            board = chess.Board(f"{placement} {turn} - - 0 1")
        except ValueError:
            continue
        if board.is_valid():
            turns.append(turn)
    return turns


def strict_cases(payload: dict) -> list[dict]:
    cases = []
    for item in payload["results"]:
        read = item.get("bookRead")
        if not read:
            continue
        placement = read["placement"]
        turns = _valid_turns(placement)
        if (
            placement.count("K") == 1
            and placement.count("k") == 1
            and read["minConfidence"] >= 0.7
            and read["meanConfidence"] >= 0.9
            and turns
        ):
            cases.append({**item, "validTurns": turns})
    return cases


def build_page(
    cases: list[dict],
    *,
    id_prefix: str = "KH",
    title: str = "Kling/Horwitz 残局棋盘审核",
    schema_version: str = "phase8c-kling-review-1.0",
    download_name: str = "phase8c-kling-human-review-results.json",
    intro_text: str | None = None,
) -> str:
    cards = []
    for index, case in enumerate(cases, 1):
        read = case["bookRead"]
        filename = case["file"]
        source = f"../../work/research_books/phase8a_sources/chess-studies-epub/EPUB/{filename}"
        case_id = f"{id_prefix}-{index:03d}"
        cards.append(f"""
        <article class="case" data-id="{case_id}" data-file="{html.escape(filename)}">
          <header><strong>{case_id}</strong><span>{html.escape(filename)}</span></header>
          <div class="comparison">
            <section><h2>原书棋盘</h2><img class="source" src="{source}" alt="{html.escape(filename)}"></section>
            <section><h2>程序恢复</h2><div class="board" data-placement="{html.escape(read['placement'])}"></div></section>
          </div>
          <div class="meta">
            <div><b>FEN摆放：</b><code>{html.escape(read['placement'])}</code></div>
            <div>最低置信度 {read['minConfidence']:.3f}；平均置信度 {read['meanConfidence']:.3f}</div>
          </div>
          <div class="review">
            <label><input type="radio" name="r-{index}" value="correct"> 完全一致</label>
            <label><input type="radio" name="r-{index}" value="incorrect"> 有棋子错误</label>
            <label><input type="radio" name="r-{index}" value="unsure"> 看不清</label>
          </div>
          <label class="field">如有错误，填写正确的FEN棋子摆放
            <input class="correction" value="{html.escape(read['placement'])}">
          </label>
          <label class="field">备注<input class="notes" placeholder="可留空"></label>
        </article>
        """)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--paper:#fff8e8;--ink:#18212f;--accent:#27786f;--line:#d9cdb3;--light:#f0d9b5;--dark:#b58863}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Microsoft YaHei",sans-serif}}
main{{max-width:1180px;margin:auto;padding:24px}}h1{{margin:0 0 8px}}.intro{{line-height:1.8;margin-bottom:18px}}
.toolbar{{position:sticky;top:0;z-index:3;background:#fffaf0eF;padding:12px;border:1px solid var(--line);border-radius:14px;display:flex;gap:12px;align-items:center}}
button{{border:0;border-radius:999px;background:var(--accent);color:white;padding:11px 20px;font-size:16px;cursor:pointer}}
.case{{background:white;border:1px solid var(--line);border-radius:20px;padding:18px;margin:18px 0;box-shadow:0 5px 20px #806b4420}}
.case header{{display:flex;justify-content:space-between;gap:15px;margin-bottom:12px}}.comparison{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}
.comparison section{{min-width:0}}h2{{font-size:18px;margin:0 0 10px}}.source{{display:block;max-width:100%;height:480px;object-fit:contain;margin:auto;background:#f5f5f5}}
.board{{width:min(100%,480px);aspect-ratio:1;display:grid;grid-template-columns:repeat(8,1fr);margin:auto;border:3px solid #382e24}}
.sq{{display:grid;place-items:center;font-family:"Segoe UI Symbol","Arial Unicode MS",serif;font-size:clamp(28px,5.3vw,58px);line-height:1}}
.sq.light{{background:var(--light)}}.sq.dark{{background:var(--dark)}}.meta{{line-height:1.8;margin:14px 0;overflow-wrap:anywhere}}code{{font-size:14px}}
.review{{display:flex;flex-wrap:wrap;gap:18px;padding:12px;background:#eef7f5;border-radius:12px}}.field{{display:block;margin-top:12px;font-weight:600}}
.field input{{display:block;width:100%;padding:10px;margin-top:6px;border:1px solid var(--line);border-radius:9px;font:inherit;font-weight:400}}
@media(max-width:760px){{main{{padding:12px}}.comparison{{grid-template-columns:1fr}}.source{{height:auto;max-height:430px}}}}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<p class="intro">{html.escape(intro_text or f'共 {len(cases)} 个高置信度候选。')}只检查左右双方棋子的格子、颜色和种类是否完全一致；暂时不判断谁走、胜和负或解答内容。</p>
<div class="toolbar"><button id="export">导出审核结果</button><span id="progress">已审核 0 / {len(cases)}</span></div>
{''.join(cards)}
</main><script>
const glyph={{K:'♔',Q:'♕',R:'♖',B:'♗',N:'♘',P:'♙',k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'}};
function expand(rank){{const a=[];for(const c of rank){{if(/\\d/.test(c))for(let i=0;i<Number(c);i++)a.push('');else a.push(c)}}return a}}
document.querySelectorAll('.board').forEach(board=>{{
 const squares=board.dataset.placement.split('/').flatMap(expand);
 squares.forEach((piece,i)=>{{const s=document.createElement('div');s.className='sq '+(((Math.floor(i/8)+i%8)%2)?'dark':'light');s.textContent=glyph[piece]||'';board.appendChild(s)}})
}});
function update(){{const n=document.querySelectorAll('input[type=radio]:checked').length;document.querySelector('#progress').textContent=`已审核 ${{n}} / {len(cases)}`}}
document.querySelectorAll('input[type=radio]').forEach(x=>x.addEventListener('change',update));
document.querySelector('#export').addEventListener('click',()=>{{
 const results=[...document.querySelectorAll('.case')].map(card=>({{
  id:card.dataset.id,file:card.dataset.file,
  verdict:card.querySelector('input[type=radio]:checked')?.value||'unreviewed',
  correctedPlacement:card.querySelector('.correction').value.trim(),notes:card.querySelector('.notes').value.trim()
 }}));
 const blob=new Blob([JSON.stringify({{schemaVersion:'{schema_version}',exportedAt:new Date().toISOString(),results}},null,2)],{{type:'application/json'}});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='{download_name}';a.click();URL.revokeObjectURL(a.href);
}});
</script></body></html>"""


def main() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    cases = strict_cases(payload)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_page(cases), encoding="utf-8")
    print(json.dumps({"reviewCases": len(cases), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
