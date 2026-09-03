"""渲染人工抽验入口 HTML（绿标抽验 + 归因校准 20 题，KaTeX 渲染，导出按钮）。"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
data = json.load(open(HERE / "证据" / "人工校准_数据.json", encoding="utf-8"))

# 页面只嵌用户可见字段（目标类型/系统预测留在侧车，保证盲标）
page_greens = [{"id": g["id"], "stem": g["stem"], "sys_answer": g["sys_answer"],
                "trace": g["trace"], "family": g["family"]} for g in data["greens"]]
page_calib = [{"id": c["id"], "kp": c["kp"], "stem": c["stem"], "options": c["options"],
               "standard_answer": c["standard_answer"], "analysis": c["analysis"],
               "student_answer": c["student_answer"], "student_reasoning": c["student_reasoning"]}
              for c in data["calib"]]

html = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>MathForge 人工抽验 · 绿标 + 归因校准</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
<style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:900px;margin:24px auto;padding:0 16px;background:#f7f8fa;color:#1f2328;line-height:1.7}
h1{font-size:22px;border-bottom:2px solid #1a73e8;padding-bottom:8px}
h2{font-size:18px;margin-top:36px;background:#e8f0fe;padding:8px 12px;border-radius:6px}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:14px 18px;margin:12px 0}
.stem{font-size:15px}
.meta{color:#666;font-size:12px;margin:4px 0}
.ans{background:#f0f7f0;border-left:3px solid #34a853;padding:6px 10px;margin:8px 0;font-size:14px}
.stu{background:#fdf3e7;border-left:3px solid #f9ab00;padding:6px 10px;margin:8px 0;font-size:14px}
details{margin:6px 0}summary{cursor:pointer;color:#1a73e8;font-size:13px}
label.opt{display:inline-block;margin:4px 10px 4px 0;padding:4px 12px;border:1px solid #dadce0;border-radius:16px;cursor:pointer;font-size:14px;background:#fff}
input[type=radio]{margin-right:4px}
input[type=radio]:checked+span{color:#1a73e8;font-weight:600}
button{background:#1a73e8;color:#fff;border:none;border-radius:6px;padding:10px 22px;font-size:15px;cursor:pointer;margin:8px 8px 8px 0}
textarea{width:100%;height:120px;font-family:monospace;font-size:12px;margin-top:8px}
.warn{color:#d93025;font-size:13px}
.note{background:#fff8e1;border:1px solid #f9ab00;border-radius:6px;padding:10px 14px;font-size:13px}
</style></head><body>
<h1>🎯 MathForge 人工抽验（30min）</h1>
<div class="note"><b>A 绿标抽验（约10min）</b>：6 题——前 3 题为已过验证链的绿标题，后 3 题为<b>拉格朗日家族</b>恢复题（含判官分歧案例）。请以你的数学判断裁定：系统答案是否正确、题干是否有问题。<br>
<b>B 归因真人校准（约20min）</b>：20 题真实考题 + 仿真学生作答，请按四类归因（概念混淆/计算失误/方法选错/审题错误）盲标。全部标完点底部【导出结果】，把下载的 JSON 发回会话即可。</div>

<h2>A · 绿标抽验（6 题）</h2>
<div id="greens"></div>

<h2>B · 归因真人校准（20 题）</h2>
<div class="note" style="margin-bottom:8px">归因四类定义：<b>概念混淆</b>=对定义/定理条件或结论形式理解错误；<b>计算失误</b>=思路对但算错；<b>方法选错</b>=解题路径选错；<b>审题错误</b>=看错/漏看所求。拿不准选"不确定"。</div>
<div id="calib"></div>

<button onclick="exportResult()">⬇️ 导出结果（下载 JSON）</button>
<button onclick="copyResult()">📋 复制到剪贴板</button>
<span id="prog" class="meta"></span>
<textarea id="out" placeholder="点导出后，全选复制这里的内容发回会话也行"></textarea>

<script>
const greens = __GREENS__;
const calib = __CALIB__;

function opt(name, values){
  return values.map(v=>`<label class="opt"><input type="radio" name="${name}" value="${v}"><span>${v}</span></label>`).join("");
}
document.getElementById('greens').innerHTML = greens.map((g,i)=>`
<div class="card">
  <div class="meta">[${i+1}] ${g.family} │ ${g.id}</div>
  <div class="stem">${g.stem}</div>
  <div class="ans"><b>系统答案</b>：${g.sys_answer}<br><span class="meta">${g.trace}</span></div>
  ${opt('green-'+i, ['答案正确','答案错误','题干有问题（前提/表述）'])}
</div>`).join('');

document.getElementById('calib').innerHTML = calib.map((c,i)=>`
<div class="card">
  <div class="meta">[${i+1}/20] ${c.id} │ ${c.kp}</div>
  <div class="stem">${c.stem}${c.options? '<br><b>选项</b>：'+c.options.map(o=>typeof o==='string'?o:(o.label+'. '+o.content_md)).join('　') : ''}</div>
  <div class="stu"><b>学生答案</b>：${c.student_answer}<br><b>学生口述</b>：${c.student_reasoning||'（未提供）'}</div>
  <details><summary>查看标准答案与解析（判卷用）</summary><div class="ans"><b>标准答案</b>：${c.standard_answer}<br><b>解析</b>：${c.analysis}</div></details>
  ${opt('cal-'+i, ['概念混淆','计算失误','方法选错','审题错误','不确定'])}
</div>`).join('');

function collect(){
  const r = {greens:{}, calib:{}};
  let done = 0;
  greens.forEach((g,i)=>{ const v=document.querySelector(`input[name="green-${i}"]:checked`); if(v){r.greens[g.id]=v.value; done++;} });
  calib.forEach((c,i)=>{ const v=document.querySelector(`input[name="cal-${i}"]:checked`); if(v){r.calib[c.id]=v.value; done++;} });
  r.meta = {exported_at: new Date().toLocaleString(), answered: done, total: greens.length+calib.length};
  return r;
}
function refreshProg(){
  const r = collect(); document.getElementById('prog').textContent = `已标 ${r.meta.answered}/${r.meta.total}`;
}
document.body.addEventListener('change', refreshProg);
function exportResult(){
  const r = collect();
  document.getElementById('out').value = JSON.stringify(r, null, 1);
  const blob = new Blob([JSON.stringify(r,null,1)], {type:'application/json'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = '人工校准结果.json'; a.click();
  if(r.meta.answered < r.meta.total) alert(`还有 ${r.meta.total - r.meta.answered} 题未标注（已导出当前进度）`);
}
function copyResult(){
  const r = collect();
  document.getElementById('out').value = JSON.stringify(r, null, 1);
  document.getElementById('out').select();
  document.execCommand('copy');
}
window.addEventListener('load', ()=>{ if(window.renderMathInElement) renderMathInElement(document.body, {delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}], throwOnError:false}); refreshProg(); });
</script></body></html>"""

html = html.replace("__GREENS__", json.dumps(page_greens, ensure_ascii=False)) \
           .replace("__CALIB__", json.dumps(page_calib, ensure_ascii=False))
out = HERE / "证据" / "人工抽验_绿标+归因校准.html"
out.write_text(html, encoding="utf-8")
print(f"页面已生成：{out}")
print(f"大小：{out.stat().st_size // 1024} KB │ 绿标 {len(page_greens)} 题 │ 校准 {len(page_calib)} 题")
