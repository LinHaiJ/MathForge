/* MathForge v2 · 两页 UI（vanilla JS，仅本地 KaTeX）
 *
 * 页 A「今日」：顶部意图条（只做变式生题）→ 主区复习清单（练习入口，卡片无裸 id）
 *   → 「练一题」同页展开出题卡（源题溯源区 + 题目 + 作答）
 *   → 填空=文本输入 SymPy 判分；解答题=折叠解答 + 三档自评（机器不判步骤分）
 *   → 归因 → 记忆闭环 → 清单刷新回清单态。空态=推荐 3 基础家族 kp 建画像。
 * 页 B「足迹统计」：单页滚动只读（热力图+当日明细 / 掌握度分布 / 错因分布 /
 *   薄弱模式 / 策略时间线折叠）。只展示投影数据，无操作。
 * 硬约束：不绕记忆闭环（每轮作答都过 /v2/answer 或 /v2/selfassess 落事件）；
 *   变式必带溯源区块（/v2/variant 返回 provenance）；页面任何地方不裸显 kp id。
 */
'use strict';

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}
const apiPost = (path, body) => api(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
const todayStr = () => new Date().toLocaleDateString('sv-SE');  // 本地日历日（与后端 heatmap 口径一致）

/* ---------- KaTeX 渲染（失败显示原文，不崩） ---------- */
function katexMD(el, md) {
  if (!el) return;
  el.innerHTML = '';
  const parts = String(md || '').split(/(\$[^$]+\$|\$\$[^$]+\$\$)/g);
  for (const p of parts) {
    if (!p) continue;
    if (p.startsWith('$$') && p.endsWith('$$')) { renderTex(el, p.slice(2, -2), true); }
    else if (p.startsWith('$') && p.endsWith('$') && p.length > 2) { renderTex(el, p.slice(1, -1), false); }
    else { el.appendChild(document.createTextNode(p)); }
  }
}
function renderTex(el, tex, block) {
  const span = document.createElement('span');
  try {
    if (window.katex) { katex.render(tex, span, { displayMode: block, throwOnError: false }); }
    else { span.textContent = tex; }
  } catch (_) { span.textContent = tex; }
  el.appendChild(span);
}

/* ---------- 状态 ---------- */
const STATE = { cur: null, listVersion: 0, filter: '', reviewAll: [] };

/* ---------- 打卡（本地连续天数，诚实本地统计） ---------- */
const todayKey = () => new Date().toLocaleDateString('sv-SE');
function refreshCheckin() {
  const el = $('#checkin'); if (!el) return;
  let rec;
  try { rec = JSON.parse(localStorage.getItem('mf_checkin') || '{}'); } catch (_) { rec = {}; }
  const t = todayKey();
  const mark = () => {
    rec[t] = 1;
    let streak = rec.streak || 0, d = new Date(t);
    d.setDate(d.getDate() - 1);
    if (rec[new Date(d).toLocaleDateString('sv-SE')]) { streak += 1; } else { streak = 1; }
    rec.streak = streak;
    localStorage.setItem('mf_checkin', JSON.stringify(rec));
    el.textContent = `打卡 ${streak} 天`;
  };
  // 服务端今日有作答 → 也计打卡
  api('/v2/heatmap?days=1').then(d => {
    if (d.some(r => r.day === t && r.count > 0) && !rec[t]) mark();
  }).catch(() => {});
  el.textContent = rec[t] ? `打卡 ${rec.streak || 1} 天` : '今日未打卡';
  window.__markCheckin = mark;
}

/* ---------- 页切换（两页） ---------- */
function switchPage(page) {
  $$('.page').forEach(p => p.classList.toggle('on', p.id === 'view-' + page));
  $$('.ps').forEach(b => b.classList.toggle('on', b.dataset.page === page));
  if (page === 'facts') loadFacts();
}

/* ---------- 页 A · 复习清单（按 高数/线代/概率 分类筛选） ---------- */
async function loadReview() {
  const wrap = $('#review-list');
  try {
    const d = await api('/v2/review?limit=500&detail=1');
    STATE.listVersion++;
    STATE.reviewAll = d.items || [];
    renderReview();
  } catch (_) { wrap.innerHTML = '<div class="empty">清单加载失败</div>'; }
}

function setFilter(f) {
  STATE.filter = f || '';
  $$('#subjFilter .sf').forEach(b => b.classList.toggle('on', (b.dataset.f || '') === f));
  renderReview();
}

function renderReview() {
  const wrap = $('#review-list');
  const f = STATE.filter;
  const items = STATE.reviewAll;
  if (!items.length) { renderEmpty(wrap); return; }
  const shown = f ? items.filter(it => (it.pack_id || '') === f) : items;
  if (!shown.length) {
    wrap.innerHTML = `<div class="empty">该分类下暂无记录（当前 ${items.length} 条在其它分类）</div>`;
    return;
  }
  const bySubj = { calculus: [], linear: [], probability: [] };
  shown.forEach(it => { (bySubj[it.pack_id] || bySubj.probability).push(it); });
  const subjName = { calculus: '高数', linear: '线代', probability: '概率' };
  const wrapSel = f ? '' : subjName;    // 未筛选时才分组显示
  let html = '';
  for (const [pk, list] of Object.entries(bySubj)) {
    if (!list.length) continue;
    if (!f) html += `<div class="group-title">${subjName[pk]}</div>`;
    html += list.map(cardHTML).join('');
  }
  wrap.innerHTML = html;
  $$('#review-list .card').forEach(c => {
    c.querySelector('.practice-btn')?.addEventListener('click', () =>
      startPractice({ kp_id: c.dataset.kp, name: c.dataset.name }, 'review'));
  });
  // 卡片「最近」题干摘要走 KaTeX（data-stmt 取原文，防 attr 转义二次破坏）
  $$('#review-list .card .stmt-r').forEach(el => katexMD(el, el.dataset.stmt || ''));
}

function cardHTML(it) {
  const name = esc(it.name || it.kp);          // 禁裸 id：永远用 name 渲染
  const attr = it.last_attribution ? esc(it.last_attribution.type || it.last_attribution) : '';
  const recStmt = (it.recent || []).find(r => r.stmt) || {};
  const m = Math.round(((it.decayed_value ?? 0)) * 100);
  const res = it.trend_str || '';
  return `<div class="card" data-kp="${esc(it.kp)}" data-name="${name}">
    <div class="c-top">
      <span class="kp-name">${name}</span>
      ${attr ? `<span class="tag ${(it.last_ok ? '' : 'w')}">${attr}</span>` : ''}
      <span class="tag">${it.pack_id === 'calculus' ? '高数' : it.pack_id === 'linear' ? '线代' : it.pack_id === 'probability' ? '概率' : ''}</span>
    </div>
    ${recStmt.stmt
      ? `<span class="stmt-lbl">最近：</span><div class="stmt stmt-r" data-stmt="${esc(recStmt.stmt)}"></div>`
      : ''}
    <div class="mastery-bar"><i style="width:${Math.min(100, Math.max(0, m))}%"></i></div>
    <div class="meta">掌握 ${m}%${res ? ` · 近况 ${esc(res)}` : ''}${it.streak_correct ? ` · 连对 ${it.streak_correct}` : ''}</div>
    <div class="row-end" style="margin-top:8px">
      <button class="primary practice-btn">练一题</button>
    </div>
  </div>`;
}

function renderEmpty(wrap) {
  wrap.innerHTML = '<div class="empty">还没有练习记录。从系统推荐的 3 个基础考点开始（确定性模板，零联网）：</div>';
  const reco = document.createElement('div');
  reco.className = 'reco';
  wrap.appendChild(reco);
  api('/v2/recommend-start').then(d => {
    (d.items || []).forEach(k => {
      const b = document.createElement('button');
      b.textContent = k.name;                    // 只显 name，无裸 id
      b.onclick = () => startPractice({ kp_id: k.kp, name: k.name }, 'mother');
      reco.appendChild(b);
    });
  }).catch(() => {});
}

/* ---------- 页 A · 意图条 ---------- */
async function intentGo() {
  const inp = $('#intent-input'), hint = $('#intent-hint');
  const text = (inp.value || '').trim();
  if (!text) return;
  hint.hidden = true;
  try {
    const d = await apiPost('/v2/intent', { text });
    if (!d.ok) {
      // 低置信/无 kp → 回退推荐，不生成
      hint.hidden = false;
      hint.innerHTML = `没听懂要练哪个考点（置信低，不硬出题）。试试推荐：`;
      (d.fallback || []).forEach(k => {
        const s = document.createElement('span');
        s.className = 'chip'; s.textContent = k.name;
        s.onclick = () => { inp.value = k.name + ' 的题'; intentGo(); };
        hint.appendChild(s);
      });
      return;
    }
    const sl = d.slots;
    inp.value = '';
    startPractice({ kp_id: sl.kp_id, name: sl.kp_name, difficulty: sl.difficulty, qtype: sl.qtype }, 'intent');
  } catch (_) {
    hint.hidden = false; hint.textContent = '意图解析服务暂不可用。';
  }
}

/* ---------- 页 A · 出题卡 ---------- */
function showPractice() {
  const p = $('#practice');
  p.hidden = false;
  $('#fill-area').hidden = true; $('#sol-area').hidden = true;
  $('#new-turn').hidden = true; $('#result-area').hidden = true;
  p.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function startPractice(kp, via) {
  let retried = false;          // fill→solution 自动重试一次（kp 仅声明 solution 时）
  showPractice();
  $('#q-meta').textContent = `出题中…（${esc(kp.name)}）`;
  $('#q-statement').innerHTML = '';
  $('#provenance').innerHTML = '';
  $('#result-area').hidden = true;
  const body = { kp_id: kp.kp_id, qtype: kp.qtype || 'fill' };
  if (kp.difficulty) body.difficulty = kp.difficulty;
  try {
    // 推荐起点（无源题）→ 母题；意图/复习 → 变式（带溯源）
    const d = via === 'mother'
      ? await apiPost('/v2/turn', body)
      : await apiPost('/v2/variant', body);
    if (!d.ok) {
      if (!retried && kp.qtype !== 'solution' && /不支持题型/.test(d.reason || '')) {
        retried = true;
        return startPractice({ ...kp, qtype: 'solution' }, via);
      }
      if (d.code === 'need_mother' || d.code === 'demo_llm_unavailable' || d.code === 'variant_llm_unavailable') {
        $('#q-meta').innerHTML = `<span class="verdict bad">${esc(d.reason || '该考点暂无源题/确定性模板')}</span>
          <button id="as-mother" class="primary" style="margin-left:8px">先做一道母题</button>`;
        $('#as-mother')?.addEventListener('click', () => startPractice(kp, 'mother'));
        return;
      }
      $('#q-meta').innerHTML = `<span class="verdict bad">出题失败：${esc(d.reason || '')}</span>`;
      return;
    }
    const q = d.question;
    STATE.cur = {
      pack_id: d.pack_id, kp_id: kp.kp_id, kp_name: kp.name || d.kp?.name,
      qtype: q.qtype || 'fill', standard_answer: q.answer_sympy || q.answer_md || '',
      statement_md: q.statement_md, analysis: q.analysis || '',
      difficulty: d.difficulty, provenance: d.provenance, event_id: null,
    };
    // 溯源区（变式必须带；母题给说明）
    renderProvenance(d.provenance, via);
    $('#q-meta').textContent = `${kp.name} · ${STATE.cur.qtype === 'solution' ? '解答题' : '填空'} · ${d.difficulty || ''}`;
    katexMD($('#q-statement'), q.statement_md);
    if (STATE.cur.qtype === 'solution') {
      $('#fill-area').hidden = true; $('#sol-area').hidden = false;
      katexMD($('#sol-answer'), `标准解答<br>${esc(q.answer_md || '')}<br>${q.analysis || ''}`);
    } else {
      $('#sol-area').hidden = true; $('#fill-area').hidden = false;
      $('#latex-input').value = ''; renderPreview();
      $('#latex-input').focus();
    }
    $('#new-turn').hidden = via !== 'intent';
    $('#result-area').hidden = true;
  } catch (_) {
    $('#q-meta').innerHTML = '<span class="verdict bad">出题服务不可用</span>';
  }
}

function renderProvenance(prov, via) {
  const el = $('#provenance');
  el.innerHTML = '';
  if (via === 'mother') {
    const r = document.createElement('div');
    r.className = 'prov-row';
    r.innerHTML = '<span class="prov-label">母题</span>系统推荐/首练（尚无源题，先建立画像）';
    el.appendChild(r);
    return;
  }
  if (!prov) return;
  const r1 = document.createElement('div');
  r1.className = 'prov-row';
  if (prov.source_kind === 'history') {
    r1.innerHTML = '<span class="prov-label">源题与变化</span>';
    if (prov.source_summary) {
      const m = document.createElement('span');
      m.className = 'prov-math';
      katexMD(m, prov.source_summary.trim());   // LaTeX 走 KaTeX 渲染，不再裸串
      r1.appendChild(m);
    }
    el.appendChild(r1);
    const r2 = document.createElement('div');
    r2.className = 'prov-row prov-changes';
    (prov.changes || []).forEach(c => {
      const s = document.createElement('span');
      s.className = 'prov-change';
      s.textContent = c.desc || c.type || '';
      r2.appendChild(s);
    });
    const ok = document.createElement('span');
    ok.className = 'prov-ok';
    ok.textContent = `知识点一致性 ${prov.consistency ? '✓' : '？'}`;
    r2.appendChild(ok);
    el.appendChild(r2);
  } else {
    r1.innerHTML = '<span class="prov-label">变式说明</span>';
    el.appendChild(r1);
    const r2 = document.createElement('div');
    r2.className = 'prov-row prov-changes';
    (prov.changes || []).forEach(c => {
      const s = document.createElement('span');
      s.className = 'prov-change';
      s.textContent = c.desc || c.type || '';
      r2.appendChild(s);
    });
    if (prov.consistency !== undefined) {
      const ok = document.createElement('span');
      ok.className = 'prov-ok';
      ok.textContent = `知识点一致性 ${prov.consistency ? '✓' : '？'}`;
      r2.appendChild(ok);
    }
    el.appendChild(r2);
  }
}

function renderPreview() {
  const pv = $('#preview');
  if (!pv) return;
  const v = $('#latex-input').value.trim();
  pv.innerHTML = '';
  if (!v) { pv.textContent = 'LaTeX 预览'; return; }
  renderTex(pv, v, false);
}

/* 填空提交 */
async function submitAnswer() {
  const cur = STATE.cur; if (!cur) return;
  const ans = $('#latex-input').value.trim();
  if (!ans) return;
  const btn = $('#submit-answer'); btn.disabled = true;
  try {
    const d = await apiPost('/v2/answer', {
      pack_id: cur.pack_id, kp: cur.kp_id, qtype: cur.qtype,
      student_answer: ans, standard_answer: cur.standard_answer,
      statement_md: cur.statement_md, analysis: cur.analysis,
      difficulty: cur.difficulty || '基础',
    });
    cur.event_id = d.event_id;
    renderResult(d, cur);
  } catch (_) { renderResult({ ok: false, reason: '提交失败' }, cur); }
  btn.disabled = false;
}

/* 解答题三档自评 */
async function doGrade(g) {
  const cur = STATE.cur; if (!cur) return;
  const d = await apiPost('/v2/selfassess', {
    pack_id: cur.pack_id, kp: cur.kp_id, qtype: 'solution',
    grade: g, student_steps: [],
    statement_md: cur.statement_md, standard_answer: cur.standard_answer,
    analysis: cur.analysis,
  }).catch(() => ({ ok: false, reason: '提交失败' }));
  cur.event_id = d.event_id;
  renderResult({ ...d, correct: g === '会' ? true : false, attribution: null }, cur, g);
}

function renderResult(d, cur, grade) {
  const box = $('#result-area');
  box.hidden = false;
  const ok = d.ok && (d.correct === true || grade === '会');
  const wrong = (d.ok && d.correct === false) || grade === '不会' || grade === '部分会';
  const attr = d.attribution;
  const rule = d.decision?.rule;
  let html = ok
    ? `<div class="verdict ok">✓ 答对了${rule ? ` · 策略 P${rule}` : ''}</div>`
    : `<div class="verdict bad">✗ ${grade ? `自评：${grade}` : '答错了'}${attr ? ` · 归因：${esc(attr)}` : ''}${rule ? ` · 策略 P${rule}` : ''}</div>`;
  if (wrong) html += attrChipsHTML(cur.event_id);
  box.innerHTML = html;
  bindChips();
  $('#new-turn').hidden = false;
  window.__markCheckin && window.__markCheckin();
}

function attrChipsHTML(eventId) {
  const list = ['概念混淆', '计算失误', '方法选错', '审题错误'];
  return `<div class="mini" style="margin-top:6px">归因不对？点选修正（记忆只记录一次作答）：</div>
    <div class="attr-chips" data-eid="${esc(eventId)}">
      ${list.map(t => `<button class="attr-chip" data-t="${esc(t)}">${t}</button>`).join('')}
    </div>`;
}
function bindChips() {
  $$('#result-area .attr-chip').forEach(ch => {
    ch.addEventListener('click', async () => {
      $$('#result-area .attr-chip').forEach(x => x.classList.remove('sel'));
      ch.classList.add('sel');
      await apiPost('/v2/attribution-override', { event_id: +ch.closest('.attr-chips').dataset.eid, type: ch.dataset.t });
      flash('归因已修正');
    });
  });
}
function flash(msg) {
  const h = $('#intent-hint'); h.hidden = false; h.textContent = msg;
  setTimeout(() => { h.hidden = true; }, 2600);
}

/* ---------- 页 B · 足迹统计（只读） ---------- */
async function loadFacts() {
  loadHeatmap(); loadDaily(); loadMastery(); loadAttrs(); loadPatterns(); loadTimeline();
}
async function loadHeatmap() {
  const d = await api('/v2/heatmap?days=182').catch(() => ({ count: 0 }));
  const wrap = $('#heatmap-wrap');
  if (!d.length) { wrap.innerHTML = '<div class="mini">暂无足迹</div>'; return; }
  const byDay = Object.fromEntries(d.map(r => [r.day, r]));
  const grid = document.createElement('div');
  grid.className = 'hm';
  const today = todayStr();
  const start = new Date(); start.setDate(start.getDate() - 181);
  const colors = r => r.count >= 12 ? 'l4' : r.count >= 8 ? 'l3' : r.count >= 4 ? 'l2' : r.count ? 'l1' : '';
  const arr = [];
  for (let i = 0; i < 182; i++) {
    const dt = new Date(start); dt.setDate(start.getDate() + i);
    const key = dt.toLocaleDateString('sv-SE');
    arr.push({ key, rec: byDay[key] });
  }
  arr.forEach(({ key, rec }) => {
    const cell = document.createElement('div');
    cell.className = 'd ' + (colors(rec ? rec.count : 0)) + (key === today ? ' today' : '');
    cell.title = `${key} · ${rec ? rec.count : 0} 题（对 ${rec ? rec.correct : 0}）`;
    grid.appendChild(cell);
  });
  wrap.innerHTML = '';
  wrap.appendChild(grid);
}
async function loadDaily() {
  const d = await api('/v2/daily').catch(() => ({ items: [] }));
  const el = $('#daily-list');
  const items = d.items || [];
  if (!items.length) { el.innerHTML = '<div class="li">今天还没有作答记录</div>'; return; }
  const modeLbl = { answer: '作答', selfassess: '自评', intent: '意图' };
  const resLbl = { correct: '对', wrong: '错', partial: '半对', hit: '命中', miss: '未命中' };
  el.innerHTML = items.map((it, i) =>
    `<div class="li" id="dl-${i}">
      <b>${esc(it.kp_name)}</b> · ${modeLbl[it.mode] || it.mode}
      ${it.rule ? `· P${esc(it.rule)}` : ''}
      ${resLbl[it.result] !== undefined ? ` · ${resLbl[it.result]}` : ''}
      ${it.stmt ? `<div class="d-stmt"></div>` : ''}
    </div>`).join('');
  items.forEach((it, i) => {
    if (!it.stmt) return;
    const node = document.getElementById('dl-' + i + ' .d-stmt') || el.querySelector(`#dl-${i} .d-stmt`);
    if (node) katexMD(node, it.stmt);
  });
}
async function loadPatterns() {
  const el = $('#patterns');
  const d = await api('/v2/patterns?limit=8').catch(() => ({ items: [] }));
  const items = d.items || [];
  if (!items.length) {
    el.innerHTML = '<div class="li">暂无薄弱模式（先答错几题就会开始归纳）</div>';
    return;
  }
  el.innerHTML = items.map(it =>
    `<div class="li"><b>${esc(it.name)}</b>：${esc(it.pattern)}
      <div style="color:#3c3489">${esc(it.advice)} <span class="tag">规则蒸馏</span></div></div>`).join('');
}
async function loadMastery() {
  const d = await api('/v2/review?limit=500&detail=1').catch(() => ({ items: [] }));
  const el = $('#mastery-dist');
  const items = [...(d.items || [])].sort((a, b) => (a.decayed_value ?? 0) - (b.decayed_value ?? 0));
  if (!items.length) { el.innerHTML = '<div class="mini">暂无数据</div>'; return; }
  el.innerHTML = items.map(it =>
    `<div class="bar"><span class="nm">${esc(it.name || it.kp)}</span>
      <span class="track"><i style="width:${Math.round((it.decayed_value ?? 0) * 100)}%"></i></span>
      <span class="num">${Math.round((it.decayed_value ?? 0) * 100)}%</span></div>`).join('');
}
async function loadAttrs() {
  const d = await api('/v2/review?limit=500&detail=1').catch(() => ({ items: [] }));
  const el = $('#attr-dist');
  const cnt = {};
  (d.items || []).forEach(it => {
    const t = it.last_attribution ? (it.last_attribution.type || it.last_attribution) : '未归因';
    cnt[t] = (cnt[t] || 0) + 1;
  });
  const rows = Object.entries(cnt).sort((a, b) => b[1] - a[1]);
  if (!rows.length) { el.innerHTML = '<div class="mini">暂无错题归因数据</div>'; return; }
  const max = Math.max(...rows.map(r => r[1]), 1);
  el.innerHTML = rows.map(([t, n]) =>
    `<div class="bar"><span class="nm">${esc(t)}</span>
      <span class="track"><i style="width:${Math.round(n / max * 100)}%"></i></span>
      <span class="num">${n}</span></div>`).join('');
}
async function loadTimeline() {
  const d = await api('/v2/timeline?limit=15').catch(() => ({ items: [] }));
  const el = $('#timeline');
  el.innerHTML = (d.items || []).length
    ? d.items.map(it =>
      `<div class="li">${new Date(it.ts * 1000).toLocaleString('zh-CN')} · ${esc(it.kp_name)} · P${esc(it.rule || '')}
        ${it.action?.difficulty ? `· ${esc(it.action.difficulty)}` : ''}</div>`).join('')
    : '<div class="li">暂无策略决策记录</div>';
}

/* ---------- init ---------- */
function init() {
  api('/v2/meta').then(m => {
    if (m.sim_mode) { const b = $('#simBadge'); if (b) b.hidden = false; }
  }).catch(() => {});
  $$('.ps').forEach(b => b.onclick = () => switchPage(b.dataset.page));
  // 分类筛选 chips（高数/线代/概率 可点击）
  $$('#subjFilter .sf').forEach(b => b.onclick = () => setFilter(b.dataset.f));
  $('#intent-go').onclick = intentGo;
  $('#intent-input').addEventListener('keydown', e => { if (e.key === 'Enter') intentGo(); });
  $('#submit-answer').onclick = submitAnswer;
  $$('.grade-btn').forEach(b => b.onclick = () => doGrade(b.dataset.g));
  $('#new-turn').onclick = () => {
    const cur = STATE.cur;
    if (cur) startPractice({ kp_id: cur.kp_id, name: cur.kp_name, qtype: cur.qtype }, 'review');
  };
  $('#close-practice').onclick = () => { $('#practice').hidden = true; loadReview(); };
  $('#latex-input').addEventListener('input', renderPreview);
  refreshCheckin();
  loadReview();
  switchPage('today');
}
document.addEventListener('DOMContentLoaded', init);
