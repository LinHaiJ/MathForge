/* ============================================================
   MathForge v2 · motion.js — 动效与视觉增强层（2026-09-24）
   ------------------------------------------------------------
   原则：只读增强。不改 app.js 的任何功能逻辑、不改 DOM 契约、
   不写业务数据（概览条只做既有只读接口的聚合展示）。
   判定语义（对/错、掌握度宽度生长）仍走 CSS/应用逻辑。

   能力清单：
   · 顶栏滚动态 + 顶部滚动进度条
   · 环境光斑随鼠标/滚动的视差
   · 今日概览条：4 格数据 + 数字滚动（/v2/daily、/v2/review、/v2/heatmap 聚合）
   · 复习卡片：前 10 张 stagger 入场，其余滚动揭示（IntersectionObserver）
   · 出题卡分区序列入场（溯源 → 题干 → 作答区）
   · 判定结果弹入 + 归因 chip 错峰
   · 热力图：波浪弹入 + 图例 + 悬浮 tooltip
   · 按钮点击涟漪
   降级：gsap 缺失 或 prefers-reduced-motion → 只保留无动画的增强
         （概览数据、图例、tooltip、滚动进度），其余原样。
   ============================================================ */
(function () {
  'use strict';

  const REDUCED = !!(window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const G = window.gsap;
  const FX = !!G && !REDUCED;          // 是否启用 GSAP 动效
  if (FX) document.documentElement.classList.add('mfx');

  const EASE = 'power3.out';
  const $ = s => document.querySelector(s);
  const $$ = s => Array.prototype.slice.call(document.querySelectorAll(s));
  const PROFILE_HEADER = 'X-MF-Profile';
  const profile = () => { try { return localStorage.getItem('mf_profile') || ''; } catch (_) { return ''; } };
  const jget = p => fetch(p, { headers: { [PROFILE_HEADER]: profile() } }).then(r => r.json());
  const seen = new WeakSet();

  /* ---------------- 顶栏滚动态 + 滚动进度 ---------------- */
  function initChrome() {
    const bar = $('.scroll-progress i');
    const top = $('.topbar');
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        const y = window.pageYOffset || document.documentElement.scrollTop || 0;
        const h = document.documentElement.scrollHeight - window.innerHeight;
        if (bar) bar.style.width = (h > 0 ? Math.min(1, Math.max(0, y / h)) * 100 : 0) + '%';
        if (top) top.classList.toggle('scrolled', y > 6);
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    onScroll();
  }

  /* ---------------- 环境光视差 ---------------- */
  function initAmbient() {
    if (!FX) return;
    const blobs = $$('.ambient b');
    if (!blobs.length) return;
    const K = [30, -40, 22, 16];
    let last = 0;
    const move = (nx, ny) => {
      blobs.forEach((b, i) => {
        const k = K[i] || 20;
        G.to(b, { x: nx * k, y: ny * k * 0.7, duration: 1.2, ease: 'power2.out', overwrite: 'auto' });
      });
    };
    window.addEventListener('pointermove', e => {
      const now = Date.now();
      if (now - last < 40) return;          // 节流：40ms 一次足够顺滑
      last = now;
      move(e.clientX / window.innerWidth - 0.5, e.clientY / window.innerHeight - 0.5);
    }, { passive: true });
    window.addEventListener('scroll', () => {
      const y = window.pageYOffset || 0;
      blobs.forEach((b, i) => {
        G.to(b, { y: -y * (0.04 + i * 0.015), duration: .8, ease: 'power2.out', overwrite: 'auto' });
      });
    }, { passive: true });
  }

  /* ---------------- 今日概览条 ---------------- */
  let LAST_HEAT = null;
  function tileHTML(v, unit, label, sub, hl) {
    return `<div class="ov-tile${hl ? ' hl' : ''}">
      <span class="ov-num"><i data-to="${v}">0</i>${unit ? `<small>${unit}</small>` : ''}</span>
      <span class="ov-lbl">${label}</span>${sub ? `<span class="ov-sub">${sub}</span>` : ''}</div>`;
  }
  function countUp(root) {
    Array.prototype.slice.call(root.querySelectorAll('.ov-num i')).forEach(el => {
      const to = +el.dataset.to || 0;
      if (!FX) { el.textContent = to; return; }
      const o = { v: 0 };
      G.to(o, {
        v: to, duration: .85, ease: 'power2.out',
        onUpdate: () => { el.textContent = Math.round(o.v); },
        onComplete: () => { el.textContent = to; },
      });
    });
  }
  async function refreshOverview() {
    const ov = $('#overview');
    if (!ov) return;
    let daily = [], review = [], heat = [];
    try {
      const r = await Promise.all([
        jget('/v2/daily').then(d => d.items || []).catch(() => []),
        jget('/v2/review?limit=500&detail=1').then(d => d.items || []).catch(() => []),
        jget('/v2/heatmap?days=182').then(d => (Array.isArray(d) ? d : [])).catch(() => []),
      ]);
      daily = r[0]; review = r[1]; heat = r[2];
    } catch (_) { return; }
    LAST_HEAT = heat;
    const total = heat.reduce((s, x) => s + (x.count || 0), 0);
    const active = heat.filter(x => (x.count || 0) > 0).length;
    const kps = review.length;
    if (!kps && !total && !daily.length) { ov.hidden = true; ov.innerHTML = ''; return; }
    let streak = 0;
    try { streak = +(JSON.parse(localStorage.getItem('mf_checkin') || '{}').streak || 0); } catch (_) { }
    const avg = kps
      ? Math.round(review.reduce((s, it) => s + (it.decayed_value || 0), 0) / kps * 100) : 0;
    ov.innerHTML =
      tileHTML(daily.length, '题', '今日已练', daily.length ? '' : '还没动手', !!daily.length) +
      tileHTML(streak, '天', '连续打卡', streak ? '' : '今天打了就有了') +
      tileHTML(kps, '个', '在练考点', kps ? `平均掌握 ${avg}%` : '') +
      tileHTML(total, '题', '累计题量', `近半年活跃 ${active} 天`);
    ov.hidden = false;
    countUp(ov);
    if (FX) {
      G.from(ov.children, {
        y: 14, autoAlpha: 0, duration: .5, ease: EASE,
        stagger: .06, clearProps: 'all',
      });
    }
  }

  /* ---------------- 热力图图例 + tooltip ---------------- */
  function initHeatmapExtras() {
    const legend = $('#hmLegend');
    const wrap = $('#heatmap-wrap');
    if (!legend || !wrap) return;

    let tip = $('.hm-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'hm-tip';
      tip.hidden = true;
      document.body.appendChild(tip);
    }
    const showTip = (el, x, y) => {
      const t = el.getAttribute('title') || '';
      if (!t) return;
      tip.textContent = t;
      tip.hidden = false;
      const r = tip.getBoundingClientRect();
      let left = x + 12, top = y - r.height - 10;
      if (left + r.width > window.innerWidth - 8) left = x - r.width - 12;
      if (top < 8) top = y + 16;
      tip.style.left = Math.max(8, left) + 'px';
      tip.style.top = top + 'px';
      if (FX) G.fromTo(tip, { autoAlpha: 0, y: 4 }, { autoAlpha: 1, y: 0, duration: .18, ease: 'power2.out' });
    };
    const hideTip = () => { tip.hidden = true; };
    wrap.addEventListener('pointermove', e => {
      const d = e.target && e.target.closest ? e.target.closest('.hm .d') : null;
      if (d) showTip(d, e.clientX, e.clientY); else hideTip();
    }, { passive: true });
    wrap.addEventListener('pointerleave', hideTip, { passive: true });
    window.addEventListener('scroll', hideTip, { passive: true });

    const updateLegend = () => {
      const cells = $$('#heatmap-wrap .hm .d');
      if (!cells.length) { legend.hidden = true; legend.innerHTML = ''; return; }
      let total = 0, active = 0;
      cells.forEach(c => {
        const m = /·\s*(\d+)\s*题/.exec(c.getAttribute('title') || '');
        const n = m ? +m[1] : 0;
        total += n; if (n > 0) active++;
      });
      legend.innerHTML =
        '<span class="mini">近 26 周</span>' +
        '<span class="sc"><i></i><i class="l1"></i><i class="l2"></i><i class="l3"></i><i class="l4"></i></span>' +
        '<span class="mini">少 → 多</span>' +
        `<span class="mini">· 共 ${total} 题 · 活跃 ${active} 天</span>`;
      legend.hidden = false;
    };
    new MutationObserver(() => updateLegend()).observe(wrap, { childList: true, subtree: true });
    updateLegend();
  }

  /* ---------------- 滚动揭示（长清单用） ---------------- */
  const pending = new Set();
  const io = (FX && 'IntersectionObserver' in window)
    ? new IntersectionObserver(ents => {
      ents.forEach(en => {
        if (!en.isIntersecting) return;
        io.unobserve(en.target);
        revealCard(en.target);
      });
    }, { rootMargin: '160px 0px' })
    : null;
  function revealCard(el) {
    if (!pending.has(el)) return;
    pending.delete(el);
    if (!el.isConnected) return;
    G.to(el, { autoAlpha: 1, y: 0, duration: .5, ease: EASE, clearProps: 'all' });
  }
  function flushPending() {
    if (!pending.size) return;
    Array.prototype.slice.call(pending).forEach(el => {
      if (!el.isConnected) { pending.delete(el); return; }
      io && io.unobserve(el);
      revealCard(el);
    });
  }

  /* ---------------- 出题卡：分区序列入场 ---------------- */
  function practiceSeq() {
    const p = $('#practice');
    if (!p) return;
    const kids = [];
    const prov = p.querySelector('.provenance');
    const meta = p.querySelector('.qmeta');
    const stmt = p.querySelector('.statement');
    const act = p.querySelector('#fill-area:not([hidden])') || p.querySelector('#sol-area:not([hidden])');
    [prov, meta].forEach(x => x && kids.push(x));
    const tl = G.timeline({ defaults: { ease: EASE } });
    tl.from(p, { y: 24, autoAlpha: 0, scale: .99, duration: .5 })
      .from(kids, { y: 10, autoAlpha: 0, duration: .4, stagger: .07 }, '-=.3')
      .from(stmt, { y: 14, autoAlpha: 0, duration: .45 }, '-=.26');
    if (act) tl.from(act, { y: 14, autoAlpha: 0, duration: .42 }, '-=.3');
    return tl;
  }

  /* ---------------- 判定结果弹入 ---------------- */
  function resultFx() {
    const box = $('#result-area');
    if (!box || box.hidden) return;
    const v = box.querySelector('.verdict');
    if (!v || seen.has(v)) return;
    seen.add(v);
    const rest = Array.prototype.filter.call(box.children, el => el !== v);
    G.fromTo(v, { scale: .84, autoAlpha: 0 },
      { scale: 1, autoAlpha: 1, duration: .45, ease: 'back.out(2.2)', clearProps: 'all' });
    if (rest.length) {
      G.from(rest, { y: 12, autoAlpha: 0, duration: .42, ease: EASE, stagger: .055, delay: .08, clearProps: 'all' });
    }
    const chips = box.querySelectorAll('.attr-chips .attr-chip');
    if (chips.length) {
      G.from(chips, { scale: .9, autoAlpha: 0, duration: .32, ease: 'back.out(2)',
        stagger: .045, delay: .18, clearProps: 'all' });
    }
  }

  /* ---------------- 清扫：给新出现的元素配动画 ---------------- */
  function sweep() {
    if (!FX) return;

    // 复习清单卡片
    const cards = $$('#review-list .card').filter(el => !seen.has(el));
    if (cards.length) {
      cards.forEach(el => seen.add(el));
      const head = cards.slice(0, 10);
      G.from(head, {
        y: 20, autoAlpha: 0, duration: .5, ease: EASE,
        stagger: { each: .055, from: 'start' }, clearProps: 'all',
      });
      const rest = cards.slice(10);
      if (io) {
        rest.forEach(el => { G.set(el, { autoAlpha: 0, y: 18 }); pending.add(el); io.observe(el); });
      }
    }

    // 分组标题
    const titles = $$('#review-list .group-title').filter(el => !seen.has(el));
    if (titles.length) {
      titles.forEach(el => seen.add(el));
      G.from(titles, { x: -10, autoAlpha: 0, duration: .4, ease: EASE, stagger: .05, clearProps: 'all' });
    }

    // 热力格：按列弹入
    const cells = $$('#heatmap-wrap .hm .d').filter(el => !seen.has(el));
    if (cells.length) {
      cells.forEach(el => seen.add(el));
      G.from(cells, {
        scale: .3, autoAlpha: 0, duration: .38, ease: 'back.out(1.7)',
        stagger: { each: .0032, from: 'start' }, clearProps: 'all',
      });
    }

    // 统计条 / 列表项
    const bars = $$('.bars .bar').filter(el => !seen.has(el));
    if (bars.length) {
      bars.forEach(el => seen.add(el));
      G.from(bars, { x: -14, autoAlpha: 0, duration: .42, ease: EASE, stagger: .03, clearProps: 'all' });
    }
    const lis = $$('#daily-list .li, #patterns .li, #timeline .li').filter(el => !seen.has(el));
    if (lis.length) {
      lis.forEach(el => seen.add(el));
      G.from(lis, { y: 10, autoAlpha: 0, duration: .38, ease: EASE, stagger: .035, clearProps: 'all' });
    }

    // 计划 chip / 冷启动推荐
    const chips = $$('#plan-wrap .plan-chip, .reco button').filter(el => !seen.has(el));
    if (chips.length) {
      chips.forEach(el => seen.add(el));
      G.from(chips, { scale: .88, autoAlpha: 0, duration: .34, ease: 'back.out(2)', stagger: .06, clearProps: 'all' });
    }

    // 出题卡（题面到齐后才播，避免把加载骨架当成内容动画一次）
    const p = $('#practice');
    const qs = $('#q-statement');
    const ready = !!(qs && qs.textContent.trim() && !qs.querySelector('.skel'));
    if (p && !p.hidden && ready && !seen.has(p)) {
      seen.add(p);
      practiceSeq();
    }
    // 收起后允许下次重新入场
    if (p && p.hidden && seen.has(p)) seen.delete(p);

    resultFx();
  }

  /* ---------------- 按钮涟漪 ---------------- */
  function initRipple() {
    document.addEventListener('pointerdown', e => {
      const b = e.target && e.target.closest ? e.target.closest('button') : null;
      if (!b || b.disabled) return;
      const r = b.getBoundingClientRect();
      const s = document.createElement('span');
      const size = Math.max(r.width, r.height) * 1.7;
      s.className = 'ripple';
      s.style.width = s.style.height = size + 'px';
      s.style.left = (e.clientX - r.left) + 'px';
      s.style.top = (e.clientY - r.top) + 'px';
      b.appendChild(s);
      if (FX) {
        G.fromTo(s,
          { scale: 0, autoAlpha: .22, xPercent: -50, yPercent: -50 },
          { scale: 1, autoAlpha: 0, duration: .58, ease: 'power2.out',
            onComplete: () => s.remove() });
      } else {
        s.style.transform = 'translate(-50%, -50%) scale(1)';
        setTimeout(() => s.remove(), 380);
      }
    }, { passive: true });
  }

  /* ---------------- 页面切换转场 ---------------- */
  function initPageFx() {
    const pages = ['#view-today', '#view-facts'];
    if (FX) {
      const pageObs = new MutationObserver(muts => muts.forEach(m => {
        if (!m.target.classList.contains('on')) return;
        G.fromTo(m.target, { autoAlpha: 0, y: 14 },
          { autoAlpha: 1, y: 0, duration: .45, ease: EASE, clearProps: 'all' });
        if (m.target.id === 'view-today') setTimeout(flushPending, 120);
      }));
      pages.forEach(sel => {
        const el = $(sel);
        if (el) pageObs.observe(el, { attributes: true, attributeFilter: ['class'] });
      });
      const first = $('.page.on');
      if (first) G.from(first, { autoAlpha: 0, y: 10, duration: .45, ease: EASE, clearProps: 'all' });
    } else {
      pages.forEach(sel => {
        const el = $(sel);
        if (el) new MutationObserver(() => {
          if (el.classList.contains('on')) setTimeout(flushPending, 60);
        }).observe(el, { attributes: true, attributeFilter: ['class'] });
      });
    }
  }

  /* ---------------- 主观察器 ---------------- */
  function initObserver() {
    let sweepT = 0, ovT = 0;
    new MutationObserver(muts => {
      let listTouched = false;
      for (const m of muts) {
        const t = m.target;
        if (t && t.nodeType === 1 && t.closest && t.closest('#review-list')) listTouched = true;
      }
      clearTimeout(sweepT);
      sweepT = setTimeout(sweep, 50);
      if (listTouched) {
        clearTimeout(ovT);
        ovT = setTimeout(() => refreshOverview().catch(() => { }), 600);
      }
    }).observe(document.body, { childList: true, subtree: true });
  }

  /* ---------------- boot ---------------- */
  function boot() {
    initChrome();
    initAmbient();
    initHeatmapExtras();
    initRipple();
    initPageFx();
    initObserver();
    setTimeout(() => { sweep(); refreshOverview().catch(() => { }); }, 260);
    // 保险：4s 后强制揭示所有还没进视口的卡片（防极端情况下内容不可见）
    setTimeout(flushPending, 4000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
