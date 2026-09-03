# R5 终审工作页生成器：聚合三份家族/出题 JSON → 自包含 HTML 审核页
import json, sys, html, re
sys.stdout.reconfigure(encoding='utf-8')

SOURCES = [
    r'D:\腾讯冲刺\作品A\mathforge\证据\matrix_family_questions.json',
    r'D:\腾讯冲刺\作品A\mathforge\证据\k1_fix_questions.json',
    r'D:\腾讯冲刺\作品A\mathforge\证据\t6_questions.json',
]

def walk(obj, out, src):
    if isinstance(obj, dict):
        if 'statement_md' in obj:
            out.append({**{k: obj.get(k, '') for k in
                        ('id','kp','difficulty','qtype','statement_md','answer_md',
                         'answer_sympy','options','correct','verify_level','analysis','design_note')},
                        '_src': src})
        for v in obj.values():
            walk(v, out, src)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, out, src)

questions = []
for f in SOURCES:
    data = json.load(open(f, encoding='utf-8'))
    before = len(questions)
    walk(data, questions, f.split('\\')[-1])
    print(f'{f.split(chr(92))[-1]}: +{len(questions)-before} 题')

# 去重（按 statement_md hash）
seen, uniq = set(), []
for q in questions:
    key = hash(q['statement_md'])
    if key not in seen:
        seen.add(key)
        uniq.append(q)
questions = uniq
print(f'去重后共 {len(questions)} 题')

cards = []
for i, q in enumerate(questions):
    ans = q.get('answer_md') or (f'`{q["answer_sympy"]}`' if q.get('answer_sympy') else '—')
    opts = ''
    if q.get('options'):
        opts = '<div class="opts">' + ''.join(f'<div>{html.escape(str(o))}</div>' for o in q['options']) + '</div>'
    lv = q.get('verify_level', '?')
    badge = '🟢绿标·SymPy' if lv == 'green' else '🟡黄标·双盲' if lv == 'yellow' else lv
    note = html.escape(str(q.get('design_note', ''))[:200])
    analysis = html.escape(str(q.get('analysis', ''))[:300])
    cards.append(f'''
<div class="card" data-i="{i}">
  <div class="head">
    <b>#{i+1}</b> 〔{html.escape(str(q.get("kp","?")))} · {html.escape(str(q.get("difficulty","?")))} · {html.escape(str(q.get("qtype","?")))}〕
    <span class="lv">{badge}</span><span class="src">{html.escape(q["_src"])}</span>
  </div>
  <div class="stem">{q["statement_md"]}{opts}</div>
  <div class="ans"><b>标准答案：</b>{ans}{f'<div class="ana">解析：{analysis}</div>' if analysis else ''}</div>
  {f'<div class="note">设计注记：{note}</div>' if note else ''}
  <div class="judge">
    判定：
    <label><input type="radio" name="j{i}" value="ok">✅ 数学正确</label>
    <label><input type="radio" name="j{i}" value="stem">⚠️ 题干有问题</label>
    <label><input type="radio" name="j{i}" value="answer">❌ 答案存疑</label>
    <label><input type="radio" name="j{i}" value="hard">🔬 表述需打磨</label>
    <input type="text" name="n{i}" placeholder="备注（可选）" class="note-input">
  </div>
</div>''')

qjson = json.dumps(questions, ensure_ascii=False)

page = f'''<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>MathForge R5 题目数学语义终审</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'$',right:'$',display:false}}]}});"></script>
<style>
body{{font-family:system-ui,'Microsoft YaHei';max-width:900px;margin:0 auto;padding:16px;background:#faf8f4;color:#222}}
.card{{background:#fff;border:1px solid #e2ddd2;border-radius:10px;padding:14px 16px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.head{{color:#666;font-size:.9em;margin-bottom:8px}} .lv{{margin-left:8px}} .src{{float:right;color:#aaa;font-size:.8em}}
.stem{{font-size:1.05em;line-height:1.7}} .ans{{margin-top:10px;padding:8px 12px;background:#f4f8f4;border-radius:6px;font-size:.95em}}
.ana{{margin-top:6px;color:#555;font-size:.9em}} .note{{margin-top:6px;color:#887;font-size:.85em}}
.opts{{margin:6px 0}} .opts div{{padding:2px 0}}
.judge{{margin-top:12px;padding-top:10px;border-top:1px dashed #ddd}} .judge label{{margin-right:14px;cursor:pointer}}
.note-input{{width:340px;padding:4px 8px;border:1px solid #ccc;border-radius:4px}}
#bar{{position:sticky;top:0;background:#2f4858;color:#fff;padding:10px 16px;border-radius:0 0 8px 8px;z-index:9}}
#bar button{{padding:8px 18px;font-size:1em;cursor:pointer;border:0;border-radius:6px;background:#f6bd60}}
</style></head><body>
<div id="bar">MathForge R5 数学语义终审 │ 共 {len(questions)} 题 │
已判 <span id="cnt">0</span>/{len(questions)} │
<button onclick="exportResult()">导出结果 JSON</button>（导出后发回给 ox-alpha）</div>
{''.join(cards)}
<script>
const DATA = {qjson};
document.getElementById('bar').insertAdjacentHTML('beforeend','');
function update(){{
  let n = 0;
  DATA.forEach((_,i) => {{ if (document.querySelector(`input[name="j${{i}}"]:checked`)) n++; }});
  document.getElementById('cnt').textContent = n;
}}
document.querySelectorAll('input[type=radio]').forEach(r => r.addEventListener('change', update));
function exportResult(){{
  const out = [];
  DATA.forEach((q,i) => {{
    const j = document.querySelector(`input[name="j${{i}}"]:checked`);
    const n = document.querySelector(`input[name="n${{i}}"]`);
    out.push({{id:i+1, kp:q.kp, qtype:q.qtype, difficulty:q.difficulty,
      statement:(q.statement_md||'').slice(0,80),
      judge: j ? j.value : '未判定', note: n ? n.value : ''}});
  }});
  const blob = new Blob([JSON.stringify(out,null,2)],{{type:'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'R5终审结果.json'; a.click();
}}
</script></body></html>'''

out_path = r'D:\腾讯冲刺\作品A\mathforge\证据\R5终审.html'
open(out_path, 'w', encoding='utf-8').write(page)
print(f'已生成: {out_path} ({len(page)//1024}KB)')
