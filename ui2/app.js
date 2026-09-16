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

const PROFILE_HEADER = 'X-MF-Profile';
const currentProfile = () => localStorage.getItem('mf_profile') || '';
/* 归因字段防御性取值（复评 S6）：服务端可能给 str / {type,...} / null */
const attrText = a => (a == null ? '' : (typeof a === 'string' ? a : (a.type || '')));

async function api(path, opts) {
  opts = opts || {};
  opts.headers = { ...(opts.headers || {}), [PROFILE_HEADER]: currentProfile() };
  const r = await fetch(path, opts);
  return r.json();
}
const apiPost = (path, body, timeoutMs) => api(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  ...(timeoutMs ? { signal: AbortSignal.timeout(timeoutMs) } : {}),
});
const todayStr = () => new Date().toLocaleDateString('sv-SE');  // 本地日历日（与后端 heatmap 口径一致）

/* ---------- KaTeX 渲染（失败显示原文，不崩） ---------- */
function katexMD(el, md) {
  if (!el) return;
  el.innerHTML = '';
  // 统一定界符：\(..\)/\[..\] → $..$$..$；截断产生的未闭合 $ 补闭合
  let s = String(md || '').replace(/\\\(/g, '$').replace(/\\\)/g, '$')
    .replace(/\\\[/g, '$$').replace(/\\\]/g, '$$');
  const dollars = (s.match(/\$/g) || []).length;
  if (dollars % 2 === 1) s += '$';
  const parts = s.split(/(\$[^$]+\$|\$\$[^$]+\$\$)/g);
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
    if (window.katex) { katex.render(tex, span, { displayMode: block, throwOnError: true }); }
    else { span.textContent = tex; }
  } catch (_) { span.textContent = tex; }   // 渲染失败回退原文（不是红色错误源码）
  el.appendChild(span);
}

/* ---------- 状态 ---------- */
const STATE = { cur: null, listVersion: 0, filter: '', reviewAll: [], nextAction: null, packKps: {} };

/* kp_id → 中文名（按包缓存；决策跨 kp 时避免裸 id，UI 红线） */
async function kpName(packId, kpId) {
  if (!STATE.packKps[packId]) {
    try {
      const d = await api('/v2/packs/' + encodeURIComponent(packId));
      const map = {};
      (d.kps || []).forEach(k => { map[k.id] = k.name; });
      STATE.packKps[packId] = map;
    } catch (_) { STATE.packKps[packId] = {}; }
  }
  return STATE.packKps[packId][kpId] || kpId;
}

/* 策略规则的学生语言映射（去黑话：不给用户看 P1 这类内部编号） */
const RULE_LABEL = {
  P1: '先补前置',
  P2: '新考点，从基础题探测',
  P3: '概念题巩固',
  P4: '同类题降档再练',
  P5: '连对升档，加点挑战',
  P6: '保持节奏',
};

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
    el.classList.add('got');                 // 达成打卡 → 徽标 pop 反馈
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

/* ---------- 页 A · 今日计划（已练最弱优先；冷启动隐藏让位推荐） ---------- */
async function loadPlan() {
  const wrap = $('#plan-wrap');
  if (!wrap) return;
  try {
    const d = await api('/v2/plan?n=3');
    const items = d.items || [];
    if (!items.length || document.body.classList.contains('empty-state')) {
      wrap.hidden = true; wrap.innerHTML = ''; return;
    }
    wrap.hidden = false;
    wrap.innerHTML = `<span class="plan-lbl">今日计划</span>`;
    items.forEach(it => {
      const b = document.createElement('button');
      b.className = 'plan-chip';
      // 「离线」角标已删（2026-09-16 用户反馈）：零 API 是工程属性，对学生是无意义的误导词
      b.innerHTML = `${esc(it.name)}`;
      b.title = it.reason || '';
      b.onclick = () => startPractice({ kp_id: it.kp_id, name: it.name }, 'review');
      wrap.appendChild(b);
    });
  } catch (_) { wrap.hidden = true; }
}

/* ---------- 页 A · 复习清单（按 高数/线代/概率 分类筛选） ---------- */
function skeletonHTML(n = 4) {
  let s = '';
  for (let i = 0; i < n; i++) {
    s += `<div class="skel"><div class="ln w40"></div><div class="ln w90"></div>
      <div class="ln bar"></div><div class="ln w70"></div></div>`;
  }
  return s;
}

async function loadReview() {
  const wrap = $('#review-list');
  wrap.innerHTML = skeletonHTML(4);
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
  // 有记录 → 解锁冷启动遮罩态
  document.body.classList.remove('empty-state');
  const inp = $('#intent-input');
  if (inp.disabled) { inp.disabled = false; inp.placeholder = ORIG_PLACEHOLDER; }
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
  $$('#review-list .card').forEach((c, i) => {
    c.style.setProperty('--i', Math.min(i, 8));   // stagger 入场（前 8 张有节奏，其余即现）
    c.querySelector('.practice-btn')?.addEventListener('click', () =>
      startPractice({ kp_id: c.dataset.kp, name: c.dataset.name }, 'review'));
  });
  // 卡片「最近」题干摘要走 KaTeX（data-stmt 取原文，防 attr 转义二次破坏）
  $$('#review-list .card .stmt-r').forEach(el => katexMD(el, el.dataset.stmt || ''));
  // 掌握度条从 0 生长（数据反馈动效；下一帧再设目标宽度触发 transition）
  requestAnimationFrame(() => {
    $$('#review-list .mastery-bar i').forEach(el => { el.style.width = el.dataset.w; });
  });
}

function cardHTML(it) {
  const name = esc(it.name || it.kp);          // 禁裸 id：永远用 name 渲染
  const rawAttr = esc(attrText(it.last_attribution));
  const attr = rawAttr === '未归因' ? '待定' : rawAttr;
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
    <div class="mastery-bar"><i data-w="${Math.min(100, Math.max(0, m))}%" style="width:0"></i></div>
    <div class="meta" title="最近 5 题，从最近往前">掌握 ${m}%${res ? ` · 近况 ${esc(res)}` : ''}${it.streak_correct ? ` · 连对 ${it.streak_correct}` : ''}</div>
    <div class="row-end" style="margin-top:8px">
      <button class="primary practice-btn">练一题</button>
    </div>
  </div>`;
}

const ORIG_PLACEHOLDER = '说一句要练的：如「来一道进阶的拉格朗日」（只做变式出题）';

function renderEmpty(wrap) {
  // 冷启动层级反转（UX 评审 #4）：空态时意图条退居二线、筛选器隐藏，推荐起点是唯一主操作
  document.body.classList.add('empty-state');
  const pw = $('#plan-wrap'); if (pw) { pw.hidden = true; pw.innerHTML = ''; }
  const inp = $('#intent-input');
  inp.disabled = true;
  inp.placeholder = '先做一道基础题，之后就能在这里点名要练的考点';
  wrap.innerHTML = '<div class="empty">还没有练习记录。先从这 3 个基础考点做一道，建立你的练习画像（离线可用，出题稳定）：</div>';
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
  const btn = $('#intent-go');
  btn.disabled = true; btn.textContent = '解析中…';   // 防连点 + 出题中反馈
  try {
    const d = await apiPost('/v2/intent', { text });
    if (!d.ok) {
      // 低置信/无 kp → 回退推荐，不生成
      hint.hidden = false;
      hint.classList.add('warn-text');
      hint.innerHTML = `没听懂要练哪个考点（置信低，不硬出题）。试试推荐：`;
      hint.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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
    hint.hidden = false;
    hint.classList.add('warn-text');
    hint.textContent = '意图解析服务暂不可用。';
  } finally {
    btn.disabled = false; btn.textContent = '出题';
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

/* 出题全链路忙碌态：触发按钮禁用（防连点）+ 题面骨架（UX 评审 #3） */
function setPracticeBusy(on) {
  $$('.practice-btn,.reco button,#intent-go').forEach(b => { b.disabled = on; });
  if (on) {
    $('#q-statement').innerHTML =
      '<div class="skel"><div class="ln w90"></div><div class="ln w40"></div><div class="ln w70"></div></div>';
  }
}

/* AI 变式优先（任务书 P3）：20s 超时（plan+generate 两次 LLM 调用的现实上限）/
   结构性失败 → 回落参数扰动 /v2/variant；缓存命中时 <1s 返回。
   回落原因挂在 __ai_fallback_reason 上，渲染层明示「AI 暂不可用」，不假装是 AI 产物。 */
async function aiVariantWithFallback(body) {
  try {
    const d = await apiPost('/v2/variant/ai', { ...body, k: 3 }, 20000);
    if (d.ok) return d;
    body.__ai_fail = String(d.reason || 'AI 变式不可用').slice(0, 60);
  } catch (e) {
    body.__ai_fail = e && e.name === 'AbortError' ? 'AI 出题超时' : 'AI 出题服务不可达';
  }
  const d2 = await apiPost('/v2/variant', body);
  d2.__ai_fallback_reason = body.__ai_fail || '';
  return d2;
}

async function startPractice(kp, via) {
  let retried = false;          // fill→solution 自动重试一次（kp 仅声明 solution 时）
  showPractice();
  setPracticeBusy(true);
  $('#q-meta').textContent = `出题中…（${esc(kp.name)}）`;
  $('#provenance').innerHTML = '';
  $('#result-area').hidden = true;
  const body = { kp_id: kp.kp_id, qtype: kp.qtype || 'fill' };
  if (kp.difficulty) body.difficulty = kp.difficulty;
  try {
    try {
      // 推荐起点（无源题）→ 母题；意图/复习 → AI 变式优先，降级回落参数扰动（任务书 P3）
      const d = via === 'mother'
        ? await apiPost('/v2/turn', body)
        : await aiVariantWithFallback(body);
      if (!d.ok) {
        if (!retried && kp.qtype !== 'solution' && /不支持题型/.test(d.reason || '')) {
          retried = true;
          return await startPractice({ ...kp, qtype: 'solution' }, via);
        }
        if (d.code === 'need_mother' || d.code === 'demo_llm_unavailable' || d.code === 'variant_llm_unavailable') {
          $('#q-statement').innerHTML = '';
          $('#q-meta').innerHTML = `<span class="verdict bad">${esc(d.reason || '该考点暂无源题/确定性模板')}</span>
            <button id="as-mother" class="primary" style="margin-left:8px">先做一道母题</button>`;
          $('#as-mother')?.addEventListener('click', () => startPractice(kp, 'mother'));
          return;
        }
        $('#q-statement').innerHTML = '';
        $('#q-meta').innerHTML = `<span class="verdict bad">出题失败：${esc(d.reason || '')}</span>`;
        return;
      }
      const q = d.question;
      STATE.cur = {
        pack_id: d.pack_id, kp_id: kp.kp_id, kp_name: kp.name || d.kp?.name,
        qtype: q.qtype || 'fill', standard_answer: q.answer_sympy || q.answer_md || '',
        statement_md: q.statement_md, analysis: q.analysis || '',
        difficulty: d.difficulty, provenance: d.provenance, event_id: null,
        // 作答通道（任务书 P2-b）：AI 变式作答记 mode='variant'，其余 answer
        mode: d.variant_meta && d.variant_meta.engine === 'ai' ? 'variant' : 'answer',
      };
      renderProvenance(d.provenance, via);
      renderVariantMeta(d.variant_meta
        || (d.__ai_fallback_reason ? { engine: 'param', degraded: true, reason: d.__ai_fallback_reason } : null));
      $('#q-meta').textContent = `${kp.name} · ${STATE.cur.qtype === 'solution' ? '解答题' : '填空'} · ${d.difficulty || ''}`;
      katexMD($('#q-statement'), q.statement_md);
      if (STATE.cur.qtype === 'solution') {
        $('#fill-area').hidden = true; $('#sol-area').hidden = false;
        $$('.grade-btn').forEach(b => { b.disabled = false; });
        katexMD($('#sol-answer'), `标准解答<br>${esc(q.answer_md || '')}<br>${q.analysis || ''}`);
      } else {
        $('#sol-area').hidden = true; $('#fill-area').hidden = false;
        $('#latex-input').value = ''; renderPreview();
        const sb = $('#submit-answer'); if (sb) sb.disabled = false;
        $('#latex-input').focus();
      }
      $('#new-turn').hidden = via !== 'intent';
      $('#result-area').hidden = true;
    } catch (_) {
      $('#q-statement').innerHTML = '';
      $('#q-meta').innerHTML = '<span class="verdict bad">出题服务不可用</span>';
    }
  } finally {
    setPracticeBusy(false);
  }
}

function renderVariantMeta(meta) {
  /* AI 变式标签（任务书 P3）：AI 成功 → 类型 + 考察计划透出（「为什么这么练」）；
     降级 → 明示「AI 暂不可用」，不假装是 AI 产物（诚实口径）。
     在 renderProvenance 之后调用，用 afterbegin 插到溯源区顶部（renderProvenance 会清空容器）。 */
  if (!meta) return;
  const el = $('#provenance');
  let html = '';
  if (meta.engine === 'ai' && !meta.degraded) {
    const kindText = meta.kind === 'multistep' ? '多步引导' : '情境改编';
    const kindHint = meta.kind === 'multistep' ? '（分步引导题：只需在空里填最终答案）' : '';
    html += `<div class="prov-row"><span class="prov-label">AI 变式 · ${kindText}</span>`
      + `<span class="mini">${kindHint}</span>`
      + (meta.g2_kind === 'isomorphic' ? '<span class="mini">（记法与母题不同，判分已按等价处理）</span>' : '')
      + `</div>`;
    if (meta.plan && meta.plan.assess) {
      html += `<div class="prov-row"><span class="prov-label">为什么这么练</span>`
        + `<span class="mini">${esc(meta.plan.assess)}`
        + (meta.plan.trap ? ` · 易错：${esc(meta.plan.trap)}` : '') + `</span></div>`;
    }
  } else if (meta.degraded) {
    html += `<div class="prov-row"><span class="prov-label">AI 变式</span>`
      + `<span class="mini">AI 暂不可用（${esc(meta.reason || '未知原因')}）——本次为同源换数（方法不变），联网正常时此入口可出情境化新题</span></div>`;
  }
  if (html) el.insertAdjacentHTML('afterbegin', html);
}

function renderProvenance(prov, via) {
  const el = $('#provenance');
  el.innerHTML = '';
  // 工程术语不透出给学生：族名等内部标识在文案层抹掉（UX 评审 #5）
  const humanize = s => String(s || '')
    .replace(/同族\s*\S+\s*换参/g, '同族模板换了一组数字（方法不变）')
    .replace(/\b[a-z]+_[a-z0-9_]+\b/gi, '');
  const renderChanges = host => {
    (prov.changes || []).forEach(c => {
      const s = document.createElement('span');
      s.className = 'prov-change';
      s.textContent = humanize(c.desc || c.type || '') || '换了一组数字（方法不变）';
      host.appendChild(s);
    });
  };
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
    renderChanges(r2);
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
    renderChanges(r2);
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

/* 填空提交（先本地空值检查 → 服务端 SymPy 解析预检 → 正式判分） */
async function submitAnswer() {
  const cur = STATE.cur; if (!cur) return;
  const ans = $('#latex-input').value.trim();
  if (!ans) return;
  const btn = $('#submit-answer'); btn.disabled = true;
  try {
    // 解析预检：不可解析的表达式判分恒错但零学习信号，拦截在事件流之外
    const pre = await apiPost('/v2/parse-check', { expr: ans });
    if (pre && pre.parseable === false) {
      renderParseError(pre.error || '无法识别的表达式');
      return;
    }
    const d = await apiPost('/v2/answer', {
      pack_id: cur.pack_id, kp: cur.kp_id, qtype: cur.qtype,
      student_answer: ans, standard_answer: cur.standard_answer,
      statement_md: cur.statement_md, analysis: cur.analysis,
      difficulty: cur.difficulty || '基础',
      mode: cur.mode || 'answer',
    });
    cur.event_id = d.event_id;
    renderResult(d, cur);
    if (d.event_recorded !== false) {
      // 作答落库后局部刷新（复评 S6）：冷启动横幅/意图条/计划条/清单当轮更新，不再僵在旧世界
      loadPlan();
      loadReview();
    }
  } catch (_) { renderResult({ ok: false, reason: '提交失败' }, cur); btn.disabled = false; }
  // 成功路径保持锁定（PM 复评：无条件解锁曾让提交锁形同虚设）——新题由 startPractice 解锁
}

function renderParseError(err) {
  const box = $('#result-area');
  box.hidden = false;
  box.innerHTML = `<div class="verdict warn">⚠ ${esc(err)}</div>
    <div class="mini" style="margin-top:4px">这次输入没有计入练习与记忆——修一修表达式再提交。支持：1/2、pi、x^2、\\frac{a}{b}、e^x、√；「x =」这类前缀可省略。</div>`;
  $('#new-turn').hidden = true;
  const inp = $('#latex-input');
  inp.classList.remove('shake'); void inp.offsetWidth; inp.classList.add('shake');
  inp.focus();
  $('#submit-answer').disabled = false;
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
  loadPlan();
  loadReview();
}

function renderResult(d, cur, grade) {
  const box = $('#result-area');
  box.hidden = false;
  const ok = d.ok && (d.correct === true || grade === '会');
  const wrong = (d.ok && d.correct === false) || grade === '不会' || grade === '部分会';
  const attr = d.attribution;
  const rule = d.decision?.rule;
  const reason = d.decision?.action?.reason || '';
  const unattr = attr === '未归因';
  const ruleLbl = RULE_LABEL[rule] || rule;
  // 决策执行（AI-PM 评审 B2）：记住策略动作，「再练一题」按它走（目标 kp/难度），不再原样重来
  STATE.nextAction = d.decision?.action || null;
  const ntBtn = $('#new-turn');
  if (STATE.nextAction?.kp_target && STATE.nextAction.kp_target !== cur.kp_id) {
    ntBtn.textContent = `按策略练：${RULE_LABEL[rule] || '切换考点'}`;
  } else if (STATE.nextAction?.difficulty && STATE.nextAction.difficulty !== cur.difficulty) {
    ntBtn.textContent = `按策略练：${RULE_LABEL[rule] || '调整难度'}`;
  } else {
    ntBtn.textContent = '再练一题';
  }
  let html = ok
    ? `<div class="verdict ok">✓ 答对了${ruleLbl ? ` · ${ruleLbl}` : ''}</div>`
    : `<div class="verdict bad">✗ ${grade ? `自评：${grade}` : '答错了'}${attr ? ` · 错因：${unattr ? '还没定' : esc(attr)}` : ''}${ruleLbl ? ` · ${ruleLbl}` : ''}</div>`;
  if (reason) html += `<div class="why">为什么是这道：${esc(reason)}</div>`;
  // 答错必看正确答案与解法（UX 评审 #1：此前闭环断在这里）；答对给折叠版
  if (d.ok && cur.qtype !== 'solution' && cur.standard_answer) {
    html += `<details ${wrong ? 'open' : ''}><summary>看正确答案与解法</summary>
      <div class="ans-line"><span class="ans-lbl">正确答案</span><span class="ans-tex"></span></div>
      ${cur.analysis ? `<div class="ans-analysis"></div>` : ''}
    </details>`;
  }
  if (unattr && wrong) html += `<div class="mini" style="margin-top:4px">自动错因分析没开，请直接点选你的真实错因：</div>`;
  if (wrong) html += attrChipsHTML(cur.event_id);
  if (d.duplicate) html += `<div class="mini" style="margin-top:4px">${esc(d.reason || '同一道题已提交过，本题不计入记忆。')}</div>`;
  box.innerHTML = html;
  // 提交锁定（复评 S4）：判分后禁用提交与自评，防照抄答案刷分；新题由 startPractice 解锁
  const sb = $('#submit-answer'); if (sb) sb.disabled = true;
  $$('.grade-btn').forEach(b => { b.disabled = true; });
  const texEl = box.querySelector('.ans-tex');
  if (texEl) { renderTex(texEl, cur.standard_answer, false); texEl.title = cur.standard_answer; }
  const anaEl = box.querySelector('.ans-analysis');
  if (anaEl) katexMD(anaEl, cur.analysis || '');
  bindChips();
  // AI 变式入口（任务书 P3）：任何题做完都给一键入口。母题用户此前全程不可见 AI 链路
  // （母题走 /v2/turn，复习清单又藏在练习覆盖层之下）——这里是唯一可达的动线修复点。
  const aiBtn = document.createElement('button');
  aiBtn.className = 'primary';
  aiBtn.style.marginTop = '10px';
  aiBtn.textContent = '让 AI 出一道变式';
  aiBtn.onclick = () => startPractice(
    { kp_id: cur.kp_id, name: cur.kp_name || cur.kp_id, qtype: cur.qtype }, 'intent');
  box.appendChild(aiBtn);
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
      const box = ch.closest('.attr-chips');
      const eid = +(box?.dataset.eid || 0);
      $$('#result-area .attr-chip').forEach(x => { x.disabled = true; });
      const d = await apiPost('/v2/attribution-override',
        { event_id: eid, type: ch.dataset.t }).catch(() => null);
      if (d && d.ok !== false) {
        box.remove();
        ch.closest('#result-area').insertAdjacentHTML('beforeend',
          `<div class="why">已按「${esc(ch.dataset.t)}」修正记录 ✓</div>`);
      } else {
        $$('#result-area .attr-chip').forEach(x => { x.disabled = false; });
        flash('修正没成功，再试一次');
      }
    });
  });
}
function flash(msg) {
  const h = $('#intent-hint'); h.hidden = false; h.textContent = msg;
  h.classList.add('warn-text');
  h.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  setTimeout(() => { h.hidden = true; h.classList.remove('warn-text'); }, 3200);
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
    // colors(r) 接收记录对象；此前误传 rec.count 数字导致热力图永不点亮（旧 bug，验收发现）
    cell.className = 'd ' + (rec ? colors(rec) : '') + (key === today ? ' today' : '');
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
      ${it.rule ? `· ${esc(RULE_LABEL[esc(it.rule)] || it.rule)}` : ''}
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
      <span class="track"><i data-w="${Math.round((it.decayed_value ?? 0) * 100)}%" style="width:0"></i></span>
      <span class="num">${Math.round((it.decayed_value ?? 0) * 100)}%</span></div>`).join('');
  requestAnimationFrame(() => {
    $$('#mastery-dist .track i').forEach(el => { el.style.width = el.dataset.w; });
  });
}
async function loadAttrs() {
  const d = await api('/v2/review?limit=500&detail=1').catch(() => ({ items: [] }));
  const el = $('#attr-dist');
  const cnt = {};
  (d.items || []).forEach(it => {
    const t = attrText(it.last_attribution) || '未归因';
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
      `<div class="li">${new Date(it.ts * 1000).toLocaleString('zh-CN')} · ${esc(it.kp_name)} · ${esc(RULE_LABEL[it.rule] || it.rule || '')}
        ${it.action?.difficulty ? `· ${esc(it.action.difficulty)}` : ''}</div>`).join('')
    : '<div class="li">暂无策略决策记录</div>';
}

/* 拍照识别答案（可选 sidecar；识别结果只作回填候选，学生核对后提交） */
async function handlePhoto(file) {
  const status = $('#photo-status'), panel = $('#photo-panel');
  if (!file) return;
  status.textContent = '识别中…（约 10-30 秒）';
  panel.hidden = true;
  try {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch('/v2/photo-recognize', { method: 'POST', body: fd });
    const d = await r.json();
    if (!d.ok) {
      status.textContent = '';
      panel.hidden = false;
      panel.className = 'photo-panel warn';
      panel.innerHTML = `<b>${esc(d.reason || '识别失败')}</b><span class="mini">${esc(d.hint || '')}</span>`;
      return;
    }
    status.textContent = '';
    const dec = d.decision;
    const decLbl = { accept: ['识别完成', ''], review: ['识别完成，有需确认的点', 'warn'], unclear: ['部分无法辨认', 'bad'] }[dec] || ['识别完成', ''];
    panel.hidden = false;
    panel.className = 'photo-panel' + (decLbl[1] ? ' ' + decLbl[1] : '');
    let html = `<b>${decLbl[0]}</b><span class="mini">AI 识别，请核对后再提交</span>`;
    if (d.answer_latex) {
      html += `<div class="photo-ans">识别到最终答案候选：<code>${esc(d.answer_latex)}</code>
        <button id="photo-fill" class="primary">填入答案框</button></div>`;
    }
    if ((d.issues || []).length) {
      html += `<details><summary>识别问题清单（${d.issues.length}）</summary><ul>${
        d.issues.map(x => `<li>${esc(x.kind || '')} ${esc(x.detail || x.message || '')}</li>`).join('')}</ul></details>`;
    }
    html += `<details><summary>完整识别结果</summary><div class="photo-md"></div></details>`;
    panel.innerHTML = html;
    const mdEl = panel.querySelector('.photo-md');
    if (mdEl) katexMD(mdEl, d.markdown || '');
    panel.querySelector('#photo-fill')?.addEventListener('click', () => {
      $('#latex-input').value = d.answer_latex;
      renderPreview();
      $('#latex-input').focus();
      const st = $('#photo-status');
      st.textContent = '已填入，请核对后提交';
      setTimeout(() => { st.textContent = ''; }, 3200);
    });
  } catch (_) {
    status.textContent = '';
    panel.hidden = false;
    panel.className = 'photo-panel warn';
    panel.innerHTML = '<b>识别服务不可用</b><span class="mini">可直接键盘输入，不影响练习</span>';
  }
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
  $('#new-turn').onclick = async () => {
    const cur = STATE.cur;
    if (!cur) return;
    const act = STATE.nextAction;
    // 决策驱动（AI-PM 评审 B2）：P1 切前置、P4 降档等都真实执行，而非回到同 kp 同难度
    if (act?.kp_target && act.kp_target !== cur.kp_id) {
      const name = await kpName(cur.pack_id, act.kp_target);
      startPractice({ kp_id: act.kp_target, name,
                      difficulty: act.difficulty || undefined, qtype: 'fill' }, 'review');
    } else if (act?.difficulty && act.difficulty !== cur.difficulty) {
      startPractice({ kp_id: cur.kp_id, name: cur.kp_name,
                      difficulty: act.difficulty, qtype: cur.qtype }, 'review');
    } else {
      startPractice({ kp_id: cur.kp_id, name: cur.kp_name, qtype: cur.qtype }, 'review');
    }
  };
  $('#close-practice').onclick = () => { $('#practice').hidden = true; loadReview(); };
  $('#latex-input').addEventListener('input', renderPreview);
  $('#photo-btn').onclick = () => $('#photo-input').click();
  $('#photo-input').addEventListener('change', e => {
    const f = e.target.files && e.target.files[0];
    handlePhoto(f);
    e.target.value = '';                    // 允许重选同一张
  });
  // 学习者档案（每人一库）：切换即整页重载，记忆/足迹/计划全部随档案切换
  const psel = $('#profile-sel');
  if (psel) {
    psel.value = currentProfile();
    if (currentProfile() && ![...psel.options].some(o => o.value === currentProfile())) {
      const opt = document.createElement('option');
      opt.value = opt.textContent = currentProfile();
      psel.appendChild(opt);
      psel.value = currentProfile();
    }
    psel.onchange = () => {
      localStorage.setItem('mf_profile', psel.value);
      location.reload();
    };
  }
  refreshCheckin();
  loadPlan();
  loadReview();
  switchPage('today');
}
document.addEventListener('DOMContentLoaded', init);
