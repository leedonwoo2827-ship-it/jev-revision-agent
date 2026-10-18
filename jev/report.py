"""results.json → output/errata_<ch>.html (검토 + [반영] 화면, 단일 파일)."""
import html
import json

from . import config

THRESHOLD = 0.6  # 기본 체크 기준 (수정확률)


def run(ch):
    wd = config.workdir(ch)
    res = json.loads((wd / "results.json").read_text(encoding="utf-8"))
    config.OUTPUT.mkdir(exist_ok=True)
    data = json.dumps(res, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.replace("__TITLE__", html.escape(f"정오표 검토 — {res['title']}")) \
                   .replace("__CH__", ch).replace("__THRESHOLD__", str(THRESHOLD)).replace("__DATA__", data)
    out = config.OUTPUT / f"errata_{ch}.html"
    out.write_text(page, encoding="utf-8")
    print(f"[report] {out}")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#fff;--fg:#1d2433;--mute:#5b6475;--line:#d9dee7;--head:#f2f5f9;--hi:#b42318;--lo:#05603a;--acc:#1f5eff;--del:#fde8e7;--ins:#e3f6ea}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#14171c;--fg:#e6e9ef;--mute:#9aa3b2;--line:#2c323c;--head:#1c2027;--hi:#ff7b72;--lo:#56d364;--acc:#6e9bff;--del:#3a1d1d;--ins:#16301f}}
:root[data-theme="dark"]{--bg:#14171c;--fg:#e6e9ef;--mute:#9aa3b2;--line:#2c323c;--head:#1c2027;--hi:#ff7b72;--lo:#56d364;--acc:#6e9bff;--del:#3a1d1d;--ins:#16301f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 "Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif}
.wrap{max-width:1400px;margin:0 auto;padding:20px 16px 80px}
h1{font-size:21px;margin:0 0 4px} .mute{color:var(--mute)}
.laws{font-size:13px;margin:8px 0 14px}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
.kpi{border:1px solid var(--line);border-radius:8px;padding:8px 14px;min-width:120px}
.kpi b{display:block;font-size:20px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;position:sticky;top:0;background:var(--bg);padding:10px 0;z-index:2;border-bottom:1px solid var(--line)}
button{font:inherit;border:1px solid var(--line);background:var(--head);color:var(--fg);border-radius:6px;padding:6px 12px;cursor:pointer}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:700}
input[type=text]{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
.tbl{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:1100px}
th,td{border:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}
th{background:var(--head);position:sticky;top:52px}
td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.p{font-weight:700}.p.hi{color:var(--hi)}.p.lo{color:var(--lo)}
.meter{height:6px;background:var(--line);border-radius:3px;margin-top:4px;overflow:hidden}
.meter i{display:block;height:100%;background:var(--hi)}
.old{background:var(--del)} textarea{width:100%;min-height:64px;font:inherit;background:var(--ins);color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:4px}
details summary{cursor:pointer;color:var(--acc)} details div{font-size:12.5px;margin-top:4px;white-space:pre-wrap}
tr.off td{opacity:.55}
#msg{margin:12px 0;padding:10px 12px;border-radius:6px;display:none;background:var(--head);border:1px solid var(--line);white-space:pre-wrap}
.tag{font-size:12px;border:1px solid var(--line);border-radius:10px;padding:0 6px;white-space:nowrap}
</style></head>
<body><div class="wrap">
<a href="/" style="font-size:13px">← 콘솔</a>
<h1 id="h"></h1>
<div class="mute">JEV 판정 — <b>J</b>udge(영향유형) · <b>E</b>vidence(교재/구·신 조문 인용) · <b>V</b>erdict(수정확률·수정문구). 수정확률 ≥ <span id="th"></span>% 항목이 기본 체크됩니다.</div>
<div class="laws" id="laws"></div>
<div class="kpis" id="kpis"></div>
<div class="bar">
  <label>반영자 <input type="text" id="reviewer" size="10" placeholder="이름"></label>
  <button onclick="setAll(true)">전체 체크</button><button onclick="setAll(false)">전체 해제</button>
  <label><input type="checkbox" id="hideLow" checked onchange="render()"> 유지(수정확률&lt;20%) 숨기기</label>
  <span style="flex:1"></span>
  <button class="primary" onclick="applyNow()">반영</button>
</div>
<div id="msg"></div>
<div class="tbl"><table>
<thead><tr><th>반영</th><th>#</th><th>쪽</th><th>관련 조문</th><th>유형</th><th>수정확률</th><th>유지확률</th><th style="width:22%">기존문구</th><th style="width:24%">수정문구 (편집 가능)</th><th style="width:20%">근거</th></tr></thead>
<tbody id="tb"></tbody></table></div>
<h2 style="font-size:16px;margin-top:28px">변경 조문 전체 (책 영향 없음 포함)</h2>
<div class="tbl"><table><thead><tr><th>조문</th><th>구분</th><th>책 관련 항목</th><th>변경 요약</th></tr></thead><tbody id="chg"></tbody></table></div>
</div>
<script>
const CH="__CH__", TH=__THRESHOLD__, D=__DATA__;
const st={}; D.rows.forEach(r=>st[r.id]={apply:r.p_revise>=TH, new_text:r.new_text, note:""});
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
try{document.getElementById("reviewer").value=localStorage.getItem("jev_reviewer")||""}catch(e){}
document.getElementById("h").textContent=`정오표 검토 — CHAPTER ${CH.slice(2)} ${D.title}`;
document.getElementById("th").textContent=Math.round(TH*100);
document.getElementById("laws").innerHTML=D.laws.map(l=>`${esc(l.name)}: ${esc(l.old_header)} → <b>${esc(l.new_header)}</b>`).join("<br>");
function kpis(){
  const n=D.rows.length, rv=D.rows.filter(r=>r.p_revise>=TH).length, ck=D.rows.filter(r=>st[r.id].apply).length;
  const avg=n?Math.round(D.rows.reduce((a,r)=>a+r.p_revise,0)/n*100):0;
  document.getElementById("kpis").innerHTML=[["변경 조문",D.changes.length],["책 영향 후보",n],["수정 권고(≥"+Math.round(TH*100)+"%)",rv],["그대로 둠",n-rv],["평균 수정확률",avg+"%"],["현재 체크",ck]]
   .map(([k,v])=>`<div class="kpi"><span class="mute">${k}</span><b>${v}</b></div>`).join("");
}
function render(){
  const hide=document.getElementById("hideLow").checked;
  document.getElementById("tb").innerHTML=D.rows.filter(r=>!(hide&&r.p_revise<0.2)).map(r=>{
    const s=st[r.id], pr=Math.round(r.p_revise*100), pk=100-pr;
    return `<tr class="${s.apply?"":"off"}">
<td><input type="checkbox" ${s.apply?"checked":""} onchange="st[${r.id}].apply=this.checked;render()"></td>
<td>${r.id}</td><td class="num">${r.page}</td>
<td>${esc(r.law)}<br><b>${esc(r.article)}</b> ${esc(r.title)}<br><span class="tag">${esc(r.kind)}</span></td>
<td><span class="tag">${esc(r.judge_type)}</span></td>
<td class="num"><span class="p ${pr>=60?"hi":""}">${pr}%</span><div class="meter"><i style="width:${pr}%"></i></div></td>
<td class="num"><span class="p ${pk>=60?"lo":""}">${pk}%</span></td>
<td class="old">${esc(r.old_text)}</td>
<td><textarea oninput="st[${r.id}].new_text=this.value">${esc(s.new_text)}</textarea>
<input type="text" placeholder="메모" style="width:100%;margin-top:4px" value="${esc(s.note)}" oninput="st[${r.id}].note=this.value"></td>
<td>${esc(r.reason)}<details><summary>Evidence</summary><div><b>교재</b> ${esc(r.book_quote)}

<b>구</b> ${esc(r.old_law_quote)}

<b>신</b> ${esc(r.new_law_quote)}</div></details></td></tr>`}).join("");
  kpis();
}
document.getElementById("chg").innerHTML=D.changes.map(c=>`<tr><td>${esc(c.law)} <b>${esc(c.article)}</b> ${esc(c.title)}</td><td>${esc(c.kind)}${c.failed?" ⚠":""}</td><td class="num">${c.n_items}</td><td>${esc(c.summary)}<details><summary>구/신 원문</summary><div><b>구</b>
${esc(c.old)}

<b>신</b>
${esc(c.new)}</div></details></td></tr>`).join("");
function setAll(v){D.rows.forEach(r=>st[r.id].apply=v);render()}
function decisions(){
  const reviewer=document.getElementById("reviewer").value.trim();
  try{localStorage.setItem("jev_reviewer",reviewer)}catch(e){}
  return {chapter:CH, reviewer, items:D.rows.map(r=>({id:r.id,...st[r.id]}))};
}
function show(t){const m=document.getElementById("msg");m.style.display="block";m.textContent=t}
async function applyNow(){
  const body=decisions();
  if(!body.items.some(i=>i.apply)){show("체크된 항목이 없습니다.");return}
  try{
    const r=await fetch("/apply",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if(!r.ok) throw new Error(await r.text());
    const j=await r.json();
    show(`반영 완료: 체크 ${j.counts.checked}건 / 원고 반영 ${j.counts.applied}건\n\n생성 파일:\n`+j.files.join("\n")+
      (j.manual.length?`\n\n수동 반영 필요 ${j.manual.length}건: `+j.manual.map(m=>"#"+m.id+"(p."+m.page+")").join(", "):""));
  }catch(e){
    // 서버 없이 연 경우(게시판 첨부 등): 결정 파일을 내려받아 `python -m jev.apply` 로 처리
    const a=document.createElement("a");
    a.href=URL.createObjectURL(new Blob([JSON.stringify(body,null,1)],{type:"application/json"}));
    a.download=`decisions_${CH}.json`; a.click();
    show("로컬 서버(run.bat)가 실행 중이 아니어서 결정 파일을 내려받았습니다.\n프로젝트 폴더에서: python -m jev.apply decisions_"+CH+".json");
  }
}
render();
</script></body></html>
"""

if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else "ch09")
