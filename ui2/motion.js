/* ============================================================
   MathForge v2 · motion.js — GSAP 动效增强层（2026-09-23）
   ------------------------------------------------------------
   原则：只做入场/状态转换的 transform+opacity 动画，不改任何
   功能逻辑与 DOM 契约；判定语义（对/错宽度生长）仍走 CSS。
   · gsap 缺失或 reduced-motion → 不启用，CSS 动画兜底
   · 启用后 html 加 .mfx，style.css 会关掉被接管的 CSS 动画
   ============================================================ */
(function () {
  'use strict';
  if (!window.gsap) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.documentElement.classList.add('mfx');

  const EASE = 'power3.out';
  const seen = new WeakSet();

  function sweep() {
    // 复习清单卡片：stagger 上浮入场
    const cards = [...document.querySelectorAll('#review-list .card')].filter(el => !seen.has(el));
    if (cards.length) {
      cards.forEach(el => seen.add(el));
      gsap.from(cards, {
        y: 18, autoAlpha: 0, duration: .5, ease: EASE,
        stagger: { each: .055, from: 'start' }, clearProps: 'all'
      });
    }
    // 热力格：按列弹入（365 格低频 stagger，避免拖沓）
    const cells = [...document.querySelectorAll('#heatmap-wrap .hm .d')].filter(el => !seen.has(el));
    if (cells.length) {
      cells.forEach(el => seen.add(el));
      gsap.from(cells, {
        scale: .3, autoAlpha: 0, duration: .38, ease: 'back.out(1.7)',
        stagger: { each: .0035, from: 'start' }, clearProps: 'all'
      });
    }
    // 统计条目：左侧滑入
    const bars = [...document.querySelectorAll('.bars .bar')].filter(el => !seen.has(el));
    if (bars.length) {
      bars.forEach(el => seen.add(el));
      gsap.from(bars, {
        x: -14, autoAlpha: 0, duration: .42, ease: EASE,
        stagger: .03, clearProps: 'all'
      });
    }
    // 出题卡：聚焦放大入场
    const p = document.querySelector('#practice');
    if (p && !p.hidden && !seen.has(p)) {
      seen.add(p);
      gsap.from(p, { y: 20, autoAlpha: 0, scale: .985, duration: .5, ease: EASE, clearProps: 'all' });
    }
  }

  // 渲染层（app.js）通过 innerHTML/appendChild 更新 → 监听子树变化后统一清扫
  let timer = 0;
  new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(sweep, 50);
  }).observe(document.body, { childList: true, subtree: true });

  // 页面切换（今日 ⇄ 足迹）：.on 类切换时做转场
  const pageObs = new MutationObserver(muts => muts.forEach(m => {
    if (m.target.classList.contains('on')) {
      gsap.fromTo(m.target,
        { autoAlpha: 0, y: 14 },
        { autoAlpha: 1, y: 0, duration: .45, ease: EASE, clearProps: 'all' });
    }
  }));
  ['#view-today', '#view-facts'].forEach(sel => {
    const el = document.querySelector(sel);
    if (el) pageObs.observe(el, { attributes: true, attributeFilter: ['class'] });
  });

  // 首屏：当前已 .on 的页面轻轻淡入
  const first = document.querySelector('.page.on');
  if (first) gsap.from(first, { autoAlpha: 0, y: 10, duration: .45, ease: EASE, clearProps: 'all' });
})();
