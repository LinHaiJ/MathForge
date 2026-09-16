// UI 走查（任务书 P3 验收基建，2026-09-16）——一体化版：本脚本自行 spawn Edge headless。
//
// 用法：node scripts/ui_smoke.mjs <port> [kp_id]
//   node scripts/ui_smoke.mjs 8127 calc.asymptote   // 离线：期望「AI 暂不可用…同源换数」+ 无溢出
//   node scripts/ui_smoke.mjs 8128 calc.asymptote   // 在线：期望「AI 变式 · …」+「为什么这么练」
//
// 流程：spawn Edge(headless, CDP 9333) → 390px 视口 → 打开 /ui2/ → evaluate 调
//       startPractice(kp, 'intent')（真实 AI 链路）→ 读 #provenance 与 scrollWidth → JSON 判定。

import { spawn } from 'node:child_process';
import fs from 'node:fs';

const PORT = process.argv[2] || '8128';
const KP = process.argv[3] || 'calc.asymptote';
const CDP = 'http://127.0.0.1:9333';
const sleep = ms => new Promise(r => setTimeout(r, ms));

const edgePaths = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];
const edge = edgePaths.find(p => fs.existsSync(p));
if (!edge) { console.log(JSON.stringify({ error: 'msedge not found' })); process.exit(1); }

const prof = `${process.env.TEMP || 'D:\\Temp'}\\mf_edge_${Date.now()}`;
const child = spawn(edge, [
  '--headless=new', '--remote-debugging-port=9333', '--no-first-run',
  '--no-proxy-server',   // 关键：系统代理会缓存 127.0.0.1 的静态文件，返回旧版 app.js
  '--window-size=390,900', `--user-data-dir=${prof}`, `http://127.0.0.1:${PORT}/ui2/`,
], { stdio: 'ignore' });
const cleanup = () => { try { child.kill(); } catch { /* already gone */ } };
process.on('exit', cleanup);
setTimeout(() => { console.log(JSON.stringify({ error: 'overall timeout' })); cleanup(); process.exit(1); }, 180000);

async function getWsUrl() {
  for (let i = 0; i < 30; i++) {
    try {
      const list = await (await fetch(`${CDP}/json/list`)).json();
      const page = list.find(t => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* edge warming up */ }
    await sleep(500);
  }
  throw new Error('Edge CDP unreachable');
}

let id = 0;
const pending = new Map();
function send(ws, method, params = {}) {
  return new Promise((resolve, reject) => {
    const msgId = ++id;
    pending.set(msgId, { resolve, reject });
    ws.send(JSON.stringify({ id: msgId, method, params }));
    setTimeout(() => { if (pending.has(msgId)) { pending.delete(msgId); reject(new Error(`${method} timeout`)); } }, 45000);
  });
}
async function evalJs(ws, expr) {
  const r = await send(ws, 'Runtime.evaluate', { expression: expr, returnByValue: true });
  if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 300));
  return r.result?.value;
}

const ws = new WebSocket(await getWsUrl());
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m.result ?? m.error); pending.delete(m.id); }
};

await send(ws, 'Runtime.enable');
await send(ws, 'Page.enable');
await send(ws, 'Emulation.setDeviceMetricsOverride',
  { width: 390, height: 800, deviceScaleFactor: 1, mobile: true });

for (let i = 0; i < 20; i++) {
  const ready = await evalJs(ws, "document.readyState + '|' + (typeof startPractice)");
  if (ready === 'complete|function') break;
  await sleep(500);
}
await sleep(1200);

// 真实 AI 链路：intent 路径 → /v2/variant/ai（在线 LLM / 离线降级）
await evalJs(ws, `startPractice({kp_id:'${KP}', name:'渐近线'}, 'intent')`);
await sleep(15000);   // 在线态两次 LLM 调用（缓存命中 <1s）

const jsver = await evalJs(ws, `JSON.stringify({
  hasNewFn: typeof aiVariantWithFallback === 'function',
  startUsesAi: startPractice.toString().includes('aiVariantWithFallback'),
  renderVMHasHonest: typeof renderVariantMeta === 'function' && renderVariantMeta.toString().includes('暂不可用'),
  renderVMExists: typeof renderVariantMeta === 'function',
})`);
const out = JSON.parse(await evalJs(ws, `JSON.stringify({
  prov: (document.querySelector('#provenance') || {}).innerText || '',
  stem: (document.querySelector('#q-statement') || {}).innerText || '',
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
})`));

console.log(JSON.stringify({
  port: PORT, kp: KP,
  jsver,
  overflow: out.scrollWidth > out.clientWidth + 1,
  scrollWidth: out.scrollWidth,
  ai_label: /AI 变式/.test(out.prov) && !/暂不可用/.test(out.prov),
  offline_label: /暂不可用|离线模式/.test(out.prov),
  why_label: /为什么这么练/.test(out.prov),
  has_question: out.stem.length > 10,
  prov: out.prov.slice(0, 260),
  stem_head: out.stem.replace(/\s+/g, ' ').slice(0, 90),
}, null, 1));
ws.close();
cleanup();
process.exit(0);
