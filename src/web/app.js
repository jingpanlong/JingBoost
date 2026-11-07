/* ================= 景同志·电脑加速 —— 前端逻辑 ================= */
window.__errs = [];
window.addEventListener('error', (e) => window.__errs.push(String(e.message)));
window.addEventListener('unhandledrejection',
  (e) => window.__errs.push(String(e.reason && e.reason.message || e.reason)));

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/* ---------------- 与 Python 通信 ---------------- */
let _apiReady = false;
function waitApi() {
  return new Promise((res) => {
    if (window.pywebview && window.pywebview.api) { _apiReady = true; return res(); }
    window.addEventListener('pywebviewready', () => { _apiReady = true; res(); }, { once: true });
    setTimeout(res, 4000);
  });
}
async function api(name, ...args) {
  await waitApi();
  try {
    const fn = window.pywebview.api[name];
    if (!fn) return { ok: false, msg: '接口不存在: ' + name };
    return await fn(...args);
  } catch (e) {
    console.error('API error', name, e);
    return { ok: false, msg: String(e && e.message ? e.message : e) };
  }
}

/* ---------------- 主题 ---------------- */
window.__theme = 'light';
try {
  if (localStorage.getItem('jingboost_theme') === 'dark') {
    document.body.classList.add('theme-dark');
    window.__theme = 'dark';
  }
} catch (e) { /* 无痕模式忽略 */ }

function applyTheme(mode) {
  const m = mode === 'dark' ? 'dark' : 'light';
  window.__theme = m;
  document.body.classList.toggle('theme-dark', m === 'dark');
  try { localStorage.setItem('jingboost_theme', m); } catch (e) { /* ignore */ }
}

/* ---------------- 程序图标 ---------------- */
const ICON_CACHE = {};
let _iconLoading = false;
let _iconQueue = new Set();

async function ensureIcons(paths, done) {
  const need = [];
  const seen = new Set();
  for (const p of paths) {
    if (!p) continue;
    const k = String(p).toLowerCase();
    if (seen.has(k)) continue;
    seen.add(k);
    if (!(k in ICON_CACHE)) need.push(p);
  }
  if (!need.length) { done && done(); return; }
  if (need.length > 150) need.length = 150;
  need.forEach((p) => _iconQueue.add(p));
  if (_iconLoading) return;
  _iconLoading = true;
  while (_iconQueue.size) {
    const batch = [..._iconQueue].slice(0, 150);
    batch.forEach((p) => _iconQueue.delete(p));
    const r = await api('get_icons', batch);
    if (r && r.ok && r.icons) {
      Object.keys(r.icons).forEach((k) => { ICON_CACHE[k] = r.icons[k]; });
    }
    batch.forEach((p) => {
      const k = p.toLowerCase();
      if (!(k in ICON_CACHE)) ICON_CACHE[k] = null;
    });
    done && done();
  }
  _iconLoading = false;
}

function fillIcons(root) {
  if (!root) return;
  root.querySelectorAll('.ico-holder[data-exe]').forEach((el) => {
    if (el.dataset.done) return;
    const src = ICON_CACHE[(el.dataset.exe || '').toLowerCase()];
    if (src) {
      el.innerHTML = `<img class="app-ico" src="${src}" alt="">`;
      el.classList.add('loaded');
      el.dataset.done = '1';
    }
  });
}

function icoCell(exe) {
  return `<td class="ico-cell"><span class="ico-holder" data-exe="${esc(exe || '')}"></span></td>`;
}

/* ---------------- 通用 UI ---------------- */
function toast(msg, type = 'info', ms = 2800) {  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = `<i></i><div>${esc(msg)}</div>`;
  $('#toastWrap').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 220); }, ms);
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
/* 进度条的可见值。
   扫描 14 个目标实测只要 160ms，真实进度一帧就冲到 100%，
   直接照抄数字等于「闪一下」，用户根本看不到 0% → 100% 的过程。
   所以真实值只当目标，可见值用定时器匀速追过去：
   单调不减、不超前于目标，且保证至少走完一小段看得见的路程。 */
let _pv = 0;          // 当前可见进度
let _pvTarget = 0;    // 后端真实进度（只增不减）
let _pvTimer = 0;
let _pvDone = null;   // 走完 100% 后要执行的回调（延迟关遮罩用）

function _paintProgress(sub) {
  const v = Math.round(_pv);
  $('#overlayBar').style.width = v + '%';
  $('#overlayPct').textContent = v + '%';
  if (sub != null) $('#overlaySub').textContent = sub;
}
function _pvStep() {
  _pvTimer = 0;
  const diff = _pvTarget - _pv;
  if (diff <= 0.5) {
    _pv = _pvTarget;
    _paintProgress();
    if (_pv >= 100 && _pvDone) {
      const fn = _pvDone;
      _pvDone = null;
      setTimeout(fn, 260);   // 停在 100% 让眼睛收一下再关
    }
    return;
  }
  // 每步至少 2%，最多补差值的 18%：快任务约 0.5s 走完，慢任务贴合真实速度
  _pv = Math.min(_pvTarget, _pv + Math.max(2, diff * 0.18));
  _paintProgress();
  _pvTimer = setTimeout(_pvStep, 26);
}

function overlay(text, sub = '请稍候，不要关闭窗口') {
  if (_pvTimer) { clearTimeout(_pvTimer); _pvTimer = 0; }
  _pvDone = null;
  _pv = 0;
  _pvTarget = 0;
  $('#overlayText').textContent = text;
  $('#overlaySub').textContent = sub;
  $('#overlayProg').classList.add('hidden');
  $('#overlaySpinner').classList.remove('hidden');
  $('#overlayBar').style.width = '0%';
  $('#overlayPct').textContent = '0%';
  $('#overlay').classList.remove('hidden');
}
/* 有真实进度时：换成进度条 + 百分比 + 当前步骤，别只转圈 */
function overlayProgress(p, sub) {
  const v = Math.max(0, Math.min(100, Math.round(Number(p) || 0)));
  $('#overlaySpinner').classList.add('hidden');
  $('#overlayProg').classList.remove('hidden');
  if (v > _pvTarget) _pvTarget = v;
  if (sub != null) $('#overlaySub').textContent = sub;
  if (!_pvTimer) _pvTimer = setTimeout(_pvStep, 0);
}
function hideOverlay() {
  // 进度条还没走完就别急着关，否则用户只看到一个残影。
  // 注意判据要包含 _pvTarget>0：极快任务可能在第一步之前就结束了。
  if (_pv < 99.5 && (_pvTimer || _pvTarget > 0)) {
    _pvTarget = 100;
    _pvDone = () => $('#overlay').classList.add('hidden');
    if (!_pvTimer) _pvTimer = setTimeout(_pvStep, 0);
    return;
  }
  if (_pvTimer) { clearTimeout(_pvTimer); _pvTimer = 0; }
  $('#overlay').classList.add('hidden');
}

let _modalResolve = null;
function confirmBox(title, text, danger = true) {
  $('#modalTitle').textContent = title;
  $('#modalText').innerHTML = esc(text).replace(/\n/g, '<br>');
  $('#modalOk').className = 'btn ' + (danger ? 'btn-danger' : 'btn-primary');
  $('#modal').classList.remove('hidden');
  return new Promise((res) => { _modalResolve = res; });
}
$('#modalCancel').onclick = () => { $('#modal').classList.add('hidden'); _modalResolve && _modalResolve(false); };
$('#modalOk').onclick = () => { $('#modal').classList.add('hidden'); _modalResolve && _modalResolve(true); };

/* WebView2 不支持 window.prompt，用自绘输入框代替 */
let _inputResolve = null;
function promptBox(title, text, def = '') {
  $('#inputTitle').textContent = title;
  $('#inputText').textContent = text;
  $('#inputVal').value = def;
  $('#inputModal').classList.remove('hidden');
  setTimeout(() => { $('#inputVal').focus(); $('#inputVal').select(); }, 60);
  return new Promise((res) => { _inputResolve = res; });
}
function _closeInput(val) {
  $('#inputModal').classList.add('hidden');
  const r = _inputResolve;
  _inputResolve = null;
  if (r) r(val);
}
$('#inputCancel').onclick = () => _closeInput(null);
$('#inputOk').onclick = () => _closeInput($('#inputVal').value.trim());
$('#inputVal').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') _closeInput($('#inputVal').value.trim());
  if (e.key === 'Escape') _closeInput(null);
});

async function pollJob(jobId, onTick) {
  let last = null;
  for (let i = 0; i < 9000; i++) {
    const r = await api('job_status', jobId);
    if (!r.ok) break;
    last = r.job;
    if (onTick) onTick(last);
    if (last.status !== 'running') break;
    // 250ms：清理任务大多 1~5 秒，650ms 轮询会让百分比直接跳 0→100
    await new Promise((r2) => setTimeout(r2, 250));
  }
  return last;
}

/* ---------------- 路由 ---------------- */
const PAGES = {
  dashboard: { title: '仪表盘', sub: '实时掌握电脑状态，一键恢复流畅', init: initDashboard },
  boost: { title: '一键加速', sub: '组合优化，一次点击完成全部加速动作', init: initBoost },
  clean: { title: '垃圾清理', sub: '先扫描预览，再安全清理', init: initClean },
  tweak: { title: '系统调优', sub: '注册表级优化，全部可一键还原', init: initTweak },
  network: { title: '网络优化', sub: 'DNS、TCP 参数与连通性诊断', init: initNetwork },
  startup: { title: '启动项', sub: '管理开机自启程序，缩短开机时间', init: initStartup },
  process: { title: '进程管理', sub: '查看资源占用，结束卡死的进程', init: initProcess },
  service: { title: '服务管理', sub: '控制系统服务，减少后台占用', init: initService },
  repair: { title: '系统修复', sub: 'SFC / DISM / 磁盘检查与优化', init: initRepair },
  disk: { title: '磁盘分析', sub: '找出大文件、重复文件与空文件夹', init: initDisk },
  sysinfo: { title: '系统信息', sub: '硬件配置与系统状态一览', init: initSysinfo },
  about: { title: '关于作者', sub: '软件介绍与联系方式', init: initAbout },
};
let currentPage = 'dashboard';
const inited = {};

function go(page) {
  if (!PAGES[page]) return;
  currentPage = page;
  $$('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.page === page));
  $$('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + page));
  $('#pageTitle').textContent = PAGES[page].title;
  $('#pageSub').textContent = PAGES[page].sub;
  $('#content').scrollTop = 0;
  if (!inited[page]) { inited[page] = true; PAGES[page].init(); }
  else if (page === 'dashboard') refreshDashboard();
}
$('#nav').addEventListener('click', (e) => {
  const item = e.target.closest('.nav-item');
  if (item) go(item.dataset.page);
});

/* ---------------- 顶部实时指标 ---------------- */
async function tickTop() {
  const m = await api('get_metrics');
  if (!m || m.error) return;
  $('#topCpu').textContent = m.cpu + '%';
  $('#topRam').textContent = m.ram_percent + '%';
  $('#topNet').textContent = m.net_down;
  if (currentPage === 'dashboard') paintMetrics(m);
  return m;
}
setInterval(tickTop, 1600);

/* ---------------- 仪表盘 ---------------- */
let firstDash = true;
async function initDashboard() {
  go('dashboard');
  await refreshDashboard();
  tickTop();
}

async function refreshDashboard() {
  const d = await api('get_overview');
  if (!d || !d.metrics) { toast('读取系统信息失败', 'err'); return; }
  const s = d.sys;
  // 完整数据里的系统名带「Microsoft Windows 11 专业版」，太长会把副标题挤成两行
  const osName = String(s.os || '').replace(/^Microsoft\s+/i, '').split('(')[0].trim();
  // 每段独立换行：以前整串长文本自动折行，窗口一窄就会出现"分"单独占一行
  setParts($('#heroDesc'), [
    osName,
    s.cpu.split('@')[0].trim(),
    `内存 ${s.ram}`,
    `已运行 ${s.uptime}`,
  ]);
  // 处理器：核心/线程 + 温度（主板没暴露传感器就不显示，别写「未知」）
  const cpuParts = [];
  cpuParts.push((s.cpu_cores && s.cpu_threads)
    ? `${s.cpu_cores} 核 ${s.cpu_threads} 线程` : '正在读取…');
  if (s.cpu_clock && s.cpu_clock !== '未知') cpuParts.push(s.cpu_clock);
  if (s.cpu_temp != null) cpuParts.push(`${s.cpu_temp}°C`);
  window._cpuParts = cpuParts.slice();
  setParts($('#cpuSub'), cpuParts);
  $('#cpuSub').title = (s.cpu_temp == null)
    ? '这台机器的主板没有暴露可读的温度传感器（ACPI 热区为空）' : '';

  // 内存：已用/总量 + 条数与类型
  window._ramExtra = s.ram_sticks
    ? `${s.ram_sticks} 条${s.ram_type ? ' ' + s.ram_type : ''}` : '';
  setParts($('#ramSub'), [
    `${d.metrics.ram_used_h || fmt(d.metrics.ram_used)} / ` +
    `${d.metrics.ram_total_h || fmt(d.metrics.ram_total)}`,
    window._ramExtra,
  ]);

  const sysDisk = (s.disks.find((x) => x.device && x.device.toUpperCase().startsWith('C:')) || s.disks[0]);
  if (sysDisk) {
    setParts($('#diskSub'), [
      `可用 ${sysDisk.free_h} / ${sysDisk.total_h}`,
      s.disk_count ? `${s.disk_count} 块硬盘` : '',
    ]);
    window._diskPct = sysDisk.percent;
  }
  setParts($('#netSub'), [`↓ ${d.metrics.net_down}`, `↑ ${d.metrics.net_up}`]);
  $('#procCount').textContent = d.metrics.proc_count + ' 个进程';

  // 评分
  const score = d.health.score;
  const ring = $('#scoreRing');
  ring.style.strokeDashoffset = String(327 - (327 * score) / 100);
  ring.style.stroke = score >= 85 ? 'var(--green)' : score >= 60 ? 'var(--blue)' : 'var(--amber)';
  $('#scoreVal').textContent = String(score);

  // 问题列表
  $('#issueList').innerHTML = d.health.issues.map((i) =>
    `<div class="issue ${i.level}"><i></i><div>${esc(i.text)}</div></div>`).join('');

  // top 进程
  const maxMem = Math.max(...d.top.map((t) => t.mem), 1);
  $('#topList').innerHTML = d.top.map((t) => `
    <div class="top-row">
      <span class="tn">${esc(t.name)}</span>
      <div class="top-bar"><i style="width:${Math.max(4, (t.mem / maxMem) * 100)}%"></i></div>
      <span class="tv">${t.mem_h}</span>
    </div>`).join('');

  if (d.admin) {
    $('#adminPill').textContent = '已获得管理员权限';
    $('#adminPill').className = 'admin-pill ok';
  }
  if (firstDash) { firstDash = false; }
  paintMetrics(d.metrics);
  // 首屏拿的是轻量数据（不含物理磁盘数等），后台预热完成后补刷。
  // 预热要连跑 3 次 PowerShell（内存条 / 温度 / 完整硬件），实测 6~9 秒，
  // 只重试一次经常扑空（硬盘数量就一直显示不出来），改成轮询直到拿到完整数据。
  if (s.partial) {
    window._dashRetry = (window._dashRetry || 0) + 1;
    if (window._dashRetry <= 6) {
      setTimeout(() => { if (currentPage === 'dashboard') refreshDashboard(); }, 2500);
    }
  } else {
    window._dashRetry = 0;
  }
}

/* 副标题按「片段」排版：每段自身不折行，换行时整段一起掉下去。
   以前是整串文本自动折行，窗口一窄就出现「硬盘」单独占一行的孤字。
   分隔符放在 span 内部而不是 ::before —— 伪元素不进 innerText，
   复制出来会变成「4 核 8 线程3.60 GHz」粘连。 */
function setParts(el, parts) {
  if (!el) return;
  const arr = (parts || []).filter(Boolean);
  // 分隔符挂在前一段末尾：换行时它留在上一行行尾，
  // 不会变成新一行的第一个字符（行首一个孤立的小圆点很难看）
  el.innerHTML = arr
    .map((t, i) => `<span>${esc(t)}${i < arr.length - 1 ? '<i>·</i>' : ''}</span>`)
    .join('');
}

function paintMetrics(m) {
  setGauge('gCpu', m.cpu);
  setGauge('gRam', m.ram_percent);
  if (window._diskPct != null) setGauge('gDisk', window._diskPct);
  setGauge('gNet', Math.min(100, (m.net_down_raw || 0) / 1048576 * 100));
  const gnet = $('#gNet .g-val');
  if (gnet) gnet.innerHTML = `${m.net_down.replace('/s', '')}<span>/s</span>`;
  const ram = $('#ramSub');
  if (ram && m.ram_used_h) {
    setParts(ram, [`${m.ram_used_h} / ${m.ram_total_h}`, window._ramExtra]);
  }
  const ns = $('#netSub');
  if (ns && m.net_down) setParts(ns, [`↓ ${m.net_down}`, `↑ ${m.net_up}`]);
  // 温度探测比其余硬件慢（WMI 传感器查询 2~4 秒），后端先给不带温度的
  // 快照，这里等实时指标里出现 temp 再补到处理器副标题上
  if (m.temp != null) {
    const cs = $('#cpuSub');
    if (cs && !cs.textContent.includes('°C')) {
      setParts(cs, [...(window._cpuParts || []), `${m.temp}°C`]);
      cs.title = '';
    }
  }
}
function setGauge(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  const p = Math.max(0, Math.min(100, pct || 0));
  const fg = el.querySelector('.g-fg');
  if (fg) {
    fg.style.strokeDashoffset = String(264 - (264 * p) / 100);
    fg.style.stroke = p > 85 ? 'var(--red)' : p > 65 ? 'var(--amber)' : 'var(--blue)';
  }
  const val = el.querySelector('.g-val');
  if (val && id !== 'gNet') val.innerHTML = `${Math.round(p)}<span>%</span>`;
}
function fmt(bytes) {
  if (!bytes) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, n = bytes;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + ' ' + u[i];
}

$('#btnRecheck').onclick = async () => { overlay('正在重新检测…'); await api('get_sysinfo'); await refreshDashboard(); hideOverlay(); toast('检测完成', 'ok'); };
$('#btnExport').onclick = async () => {
  overlay('正在生成报告…');
  const r = await api('export_report');
  hideOverlay();
  toast(r.msg, r.ok ? 'ok' : 'err', 4000);
};
$('#btnBoostHero').onclick = () => { go('boost'); setTimeout(() => $('#btnBoostRun').click(), 120); };

/* ---------------- 一键加速 ---------------- */
function initBoost() {
  $$('#boostOptions .opt').forEach((opt) => {
    const cb = opt.querySelector('input');
    opt.addEventListener('click', (e) => {
      if (e.target !== cb) cb.checked = !cb.checked;
      opt.classList.toggle('checked', cb.checked);
    });
  });
  $('#btnBoostRun').onclick = runBoost;
  $('#btnMemOnly').onclick = async () => {
    overlay('正在释放内存…');
    const r = await api('memory_boost');
    hideOverlay();
    toast(r.msg || '完成', r.ok ? 'ok' : 'err', 3500);
    showBoostResult({ steps: [{ step: 'memory', ok: r.ok, msg: r.msg }], elapsed: 0 });
  };
}

async function runBoost() {
  const opts = {};
  $$('#boostOptions input').forEach((cb) => { opts[cb.dataset.k] = cb.checked; });
  const texts = ['正在清理系统垃圾…', '正在整理内存…', '正在调整电源策略…', '正在刷新网络缓存…', '收尾中…'];
  let i = 0;
  overlay(texts[0], '整个过程约 10-40 秒');
  const timer = setInterval(() => { i = (i + 1) % texts.length; $('#overlayText').textContent = texts[i]; }, 3600);
  const r = await api('boost', opts);
  clearInterval(timer);
  hideOverlay();
  showBoostResult(r);
  toast(r.msg || '加速完成', r.ok ? 'ok' : 'err', 4200);
  firstDash = true;
  tickTop();
}

function showBoostResult(r) {
  $('#boostResultCard').style.display = '';
  $('#boostElapsed').textContent = r.elapsed ? `耗时 ${r.elapsed} 秒 · 内存 ${r.mem_before}% → ${r.mem_after}%` : '';
  const names = { clean: '垃圾清理', memory: '内存释放', power: '电源策略', network: '网络刷新', service: '服务优化', tweak: '系统调优' };
  $('#boostSteps').innerHTML = (r.steps || []).map((s) => `
    <div class="step ${s.ok ? 'ok' : 'bad'}">
      <span class="dot"></span>
      <span class="sname">${esc(names[s.step] || s.step)}</span>
      <span class="smsg">${esc(s.msg || (s.ok ? '完成' : '失败'))}</span>
    </div>`).join('');
}

/* ---------------- 垃圾清理 ---------------- */
/* 首次进入自动扫一次：以前 initClean 是空的，用户从别处（比如一键加速）
   清完再进这一页，看到的还是上次扫描的旧数字。 */
function initClean() { scanClean(); }

async function scanClean(silent) {
  if (!silent) overlay('正在扫描垃圾文件…', '逐项统计中');
  const r = await api('clean_scan_job');
  if (!r || !r.ok || !r.job_id) { if (!silent) hideOverlay(); toast('扫描失败', 'err'); return false; }
  const job = await pollJob(r.job_id, (j) => {
    if (!silent) overlayProgress(j.progress, j.message);
  });
  if (!silent) hideOverlay();
  if (!job || job.status !== 'done' || !job.result) {
    toast((job && (job.error || job.message)) || '扫描失败', 'err', 4000);
    return false;
  }
  renderClean(job.result.items || []);
  return true;
}

$('#btnCleanScan').onclick = () => scanClean(false);
function renderClean(items) {
  const total = items.reduce((a, b) => a + b.size, 0);
  const denied = items.filter((t) => t.denied).length;
  $('#cleanTotal').textContent = `共 ${items.length} 项 · 可清理 ${fmt(total)}`
    + (denied ? ` · ${denied} 项需管理员权限才能查看` : '');
  $('#cleanList').innerHTML = items.map((t) => {
    const on = (t.risk === 'safe' || t.default_on) && !t.denied;
    // 只有"真的没有东西可清"才打勾；权限不足的 0 不是清干净了
    const empty = t.size <= 0 && !t.denied;
    const badge = t.id === 'recycle_orphan' && t.size > 0
      ? '<span class="tag admin">回收站里看不到</span>' : '';
    const lock = t.denied ? '<span class="tag admin">权限不足</span>' : '';
    return `
    <label class="clean-item ${on ? 'sel' : ''} ${empty ? 'cleaned' : ''}" data-id="${t.id}">
      <input type="checkbox" ${on ? 'checked' : ''} data-size="${t.size}">
      <div class="ci-body">
        <b>${esc(t.name)}
          <span class="tag ${t.risk}">${t.risk === 'safe' ? '安全' : '谨慎'}</span>
          ${t.admin && !t.denied ? '<span class="tag admin">需管理员</span>' : ''}${badge}${lock}
        </b>
        <span>${esc(t.desc)}${t.files
          ? `<span class="ci-files" data-n="${t.files}">（${t.files} 个文件）</span>`
          : ''}</span>
      </div>
      <div class="ci-size">${esc(t.size_h)}</div>
    </label>`;
  }).join('') || '<div class="empty-state">没有找到可清理的垃圾</div>';

  $$('#cleanList .clean-item').forEach((el) => {
    const cb = el.querySelector('input');
    el.addEventListener('click', (e) => {
      if (e.target !== cb) cb.checked = !cb.checked;
      el.classList.toggle('sel', cb.checked);
      updateCleanTotal();
    });
  });
  updateCleanTotal();
}
function updateCleanTotal() {
  let sum = 0, n = 0;
  $$('#cleanList .clean-item input:checked').forEach((c) => { sum += +c.dataset.size; n++; });
  $('#cleanTotal').textContent = `已选 ${n} 项 · 预计释放 ${fmt(sum)}`;
  $('#btnCleanRun').disabled = n === 0;
}

/* 用清理返回的实测释放量就地扣减：清掉的项立刻显示 0 B，
   不用干等后台重扫（重扫只用来校正"没删掉的"部分）。 */
function applyCleanDetails(details) {
  const map = new Map((details || []).map((d) => [d.id, d]));
  let hit = 0;
  $$('#cleanList .clean-item').forEach((el) => {
    const d = map.get(el.dataset.id);
    if (!d || !d.size) return;
    const cb = el.querySelector('input');
    const left = Math.max(0, (+cb.dataset.size || 0) - d.size);
    cb.dataset.size = String(left);
    const box = el.querySelector('.ci-size');
    if (box) box.textContent = fmt(left);
    // 文件数同步扣减，别出现"0 B 却还有 4463 个文件"的自相矛盾
    const fl = el.querySelector('.ci-files');
    if (fl) {
      const leftN = Math.max(0, (+fl.dataset.n || 0) - (d.files || 0));
      fl.dataset.n = String(leftN);
      fl.textContent = leftN > 0 ? `（${leftN} 个文件）` : '（已清空）';
    }
    el.classList.toggle('cleaned', left <= 0);
    hit++;
  });
  if (hit) updateCleanTotal();
}

$('#btnCleanRun').onclick = async () => {
  const ids = $$('#cleanList .clean-item').filter((el) => el.querySelector('input').checked)
    .map((el) => el.dataset.id);
  if (!ids.length) return toast('请先选择要清理的项目', 'err');
  if (ids.includes('recycle_bin')) {
    const ok = await confirmBox('确认清空回收站？', '回收站中的文件将被永久删除，无法恢复。', true);
    if (!ok) return;
  }
  overlay('正在清理…', '准备中');
  const r = await api('clean_run', ids);
  if (!r.ok || !r.job_id) { hideOverlay(); return toast((r && r.msg) || '清理失败', 'err'); }
  const job = await pollJob(r.job_id, (j) => overlayProgress(j.progress, j.message));
  hideOverlay();
  if (!job || job.status !== 'done' || !job.result) {
    toast((job && (job.error || job.message)) || '清理失败', 'err', 4200);
    return;
  }
  const res = job.result;
  applyCleanDetails(res.details);          // 先让数字立刻变
  toast(res.msg || '清理完成', res.freed > 0 ? 'ok' : 'info', 4200);
  scanClean(true);                          // 再后台重扫校正，不弹遮罩
};

/* ---------------- 系统调优 ---------------- */
const TWEAK_NAMES = {};
async function initTweak() {
  await loadTweaks();
  $('#btnTweakAll').onclick = async () => {
    const ok = await confirmBox('应用全部调优？', '将修改若干注册表与系统设置，所有改动都会被记录，可随时一键还原。', false);
    if (!ok) return;
    overlay('正在应用系统调优…', '包含服务与注册表调整');
    const r = await api('tweak_all');
    hideOverlay();
    toast(r.msg || '完成', r.ok ? 'ok' : 'err', 4000);
    await loadTweaks();
  };
  $('#btnRestoreReg').onclick = async () => {
    const ok = await confirmBox('还原所有注册表改动？', '本工具做过的注册表修改将恢复为修改前的状态（需要重启部分程序生效）。', true);
    if (!ok) return;
    overlay('正在还原…');
    const r = await api('restore_registry');
    hideOverlay();
    toast(`已还原 ${r.restored || 0} 项`, r.failed ? 'info' : 'ok', 4000);
    await loadTweaks();
  };
  $$('[data-power]').forEach((b) => {
    b.onclick = async () => {
      const r = await api('power_set', b.dataset.power);
      toast(r.msg, r.ok ? 'ok' : 'err');
    };
  });
  $('#btnHibOff').onclick = async () => {
    const ok = await confirmBox('关闭休眠？', '会释放约等于内存大小的磁盘空间，但将无法使用休眠与快速启动。可随时用 powercfg /h on 恢复。', false);
    if (!ok) return;
    const r = await api('hibernate', false);
    toast(r.msg, r.ok ? 'ok' : 'err', 3500);
  };
  $('#btnRestartExp').onclick = async () => {
    const r = await api('restart_explorer');
    toast(r.msg, r.ok ? 'ok' : 'err');
  };
}

async function loadTweaks() {
  const [r, bk] = await Promise.all([api('tweak_list'), api('get_env')]);
  if (!r.ok) return;
  $('#tweakGrid').innerHTML = r.items.map((t) => `
    <div class="tweak ${t.applied ? 'done' : ''}" data-id="${t.id}">
      <div class="tweak-head">
        <b>${esc(t.name)}</b>
        <span class="tweak-g">${esc(t.group)}</span>
      </div>
      <p>${esc(t.desc)}</p>
      <div class="tweak-foot">
        <span class="muted">${t.applied ? '✓ 已应用' : '未应用'}</span>
        <button class="row-btn" data-apply="${t.id}">${t.applied ? '重新应用' : '立即应用'}</button>
      </div>
    </div>`).join('');
  $$('[data-apply]').forEach((b) => {
    b.onclick = async (e) => {
      e.stopPropagation();
      overlay('正在应用…');
      const res = await api('tweak_apply', b.dataset.apply);
      hideOverlay();
      toast(res.msg || '完成', res.ok ? 'ok' : 'err', 3600);
      await loadTweaks();
    };
  });
  if (bk && bk.backup) {
    $('#backupInfo').textContent = bk.backup.count
      ? `已记录 ${bk.backup.count} 处注册表改动（最近 ${bk.backup.time}），可随时还原。`
      : '所有注册表改动都会记录，可随时一键还原。';
  }
}

/* ---------------- 网络 ---------------- */
async function initNetwork() {
  loadNics();
  $$('[data-net]').forEach((b) => {
    b.onclick = async () => {
      const map = { flush: 'net_flush', tcp: 'net_optimize', winsock: 'net_winsock', tcpip: 'net_tcpip' };
      const act = b.dataset.net;
      if (act === 'winsock' || act === 'tcpip') {
        const ok = await confirmBox('确认重置网络协议栈？',
          '重置后需要重启电脑才能完全生效，可能会短暂断网。', true);
        if (!ok) return;
      }
      overlay('正在执行网络操作…');
      const r = await api(map[act]);
      hideOverlay();
      toast(r.msg || '完成', r.ok ? 'ok' : 'err', 3800);
    };
  });
  $$('[data-dns]').forEach((b) => {
    b.onclick = async () => {
      const [p, s] = b.dataset.dns.split('|');
      overlay('正在设置 DNS…');
      const r = await api('net_set_dns', p, s || '');
      hideOverlay();
      toast(r.msg, r.ok ? 'ok' : 'err', 3800);
      loadNics();
    };
  });
  $('#btnDnsReset').onclick = async () => {
    overlay('正在恢复自动 DNS…');
    const r = await api('net_reset_dns');
    hideOverlay();
    toast(r.msg, r.ok ? 'ok' : 'err');
    loadNics();
  };
  $('#btnPing').onclick = async () => {
    $('#pingList').innerHTML = '<div class="empty">正在测试各节点延迟…</div>';
    const r = await api('net_ping');
    if (!r.ok) return;
    const max = Math.max(...r.items.map((i) => i.ms || 300), 50);
    $('#pingList').innerHTML = r.items.map((i) => {
      const pct = i.ok ? Math.min(100, (i.ms / max) * 100) : 100;
      const color = !i.ok ? 'var(--red)' : i.ms < 60 ? 'var(--green)' : i.ms < 150 ? 'var(--blue)' : 'var(--amber)';
      return `<div class="ping-row">
        <span class="pn">${esc(i.label)}</span>
        <span class="pv" style="color:${color}">${i.ok ? i.ms + ' ms' : '超时'}</span>
        <div class="pbar"><i style="width:${pct}%;background:${color}"></i></div>
        <span class="muted" style="width:120px;text-align:right">${esc(i.host)}</span>
      </div>`;
    }).join('');
  };
  $('#btnHostsView').onclick = async () => {
    const r = await api('net_hosts');
    $('#hostsBox').textContent = r.ok ? (r.content || '(空)') : r.msg;
  };
  $('#btnHostsClean').onclick = async () => {
    const ok = await confirmBox('重置 hosts 文件？', '会把 hosts 恢复为系统默认内容，原文件会自动备份。', true);
    if (!ok) return;
    const r = await api('net_hosts_clean');
    toast(r.msg, r.ok ? 'ok' : 'err', 3600);
    $('#btnHostsView').click();
  };
  $('#btnHostsRestore').onclick = async () => {
    const r = await api('net_hosts_restore');
    toast(r.msg, r.ok ? 'ok' : 'err', 3600);
    $('#btnHostsView').click();
  };
}

async function loadNics() {
  const r = await api('net_adapters');
  if (!r.ok) return;
  $('#connCount').textContent = `当前 ${r.conns} 个网络连接`;
  $('#dnsInfo').innerHTML = r.dns && r.dns.length
    ? `当前 DNS：<b style="color:var(--accent-text)">${r.dns.join(' , ')}</b>`
    : '当前 DNS：自动获取';
  $('#nicTable').innerHTML = `
    <thead><tr><th>网卡</th><th>状态</th><th class="num">速率</th><th>IPv4</th><th class="num">累计接收</th><th class="num">累计发送</th></tr></thead>
    <tbody>${r.items.map((n) => `<tr>
      <td>${esc(n.name)}</td>
      <td>${n.up ? '<span class="badge run">已连接</span>' : '<span class="badge stop">未连接</span>'}</td>
      <td class="num">${esc(n.speed)}</td>
      <td class="mono">${esc(n.ip || '-')}</td>
      <td class="num">${esc(n.recv)}</td>
      <td class="num">${esc(n.sent)}</td>
    </tr>`).join('')}</tbody>`;
}

/* ---------------- 启动项 ---------------- */
async function initStartup() { loadStartup(); $('#btnStartupRefresh').onclick = loadStartup; }

async function loadStartup() {
  $('#startupTable').innerHTML = '<tbody><tr><td class="empty">读取中…</td></tr></tbody>';
  const r = await api('startup_list');
  if (!r.ok) return;
  const items = r.items || [];
  $('#startupCount').textContent = `${items.filter((i) => i.enabled).length} 项启用 / 共 ${items.length} 项`;
  $('#startupTable').innerHTML = `
    <colgroup><col style="width:40px"><col><col style="width:86px"><col style="width:34%"><col style="width:78px"><col style="width:134px"></colgroup>
    <thead><tr><th class="ico-cell" title="程序图标"></th><th>名称</th><th style="width:84px">来源</th><th>命令</th><th style="width:76px">状态</th><th style="width:132px">操作</th></tr></thead>
    <tbody>${items.map((i) => `<tr>
      ${icoCell(i.exe)}
      <td><div class="name-cell"><span class="nm">${esc(i.name)}</span></div></td>
      <td class="muted" style="white-space:nowrap">${esc(i.source)}</td>
      <td class="mono ellip" title="${esc(i.cmd)}">${esc(i.cmd)}</td>
      <td>${i.enabled ? '<span class="badge run">已启用</span>' : '<span class="badge stop">已禁用</span>'}</td>
      <td style="white-space:nowrap">
        <button class="row-btn" data-toggle="${esc(i.id)}" data-on="${i.enabled ? 1 : 0}">${i.enabled ? '禁用' : '启用'}</button>
        <button class="row-btn danger" data-del="${esc(i.id)}">删除</button>
      </td>
    </tr>`).join('')}</tbody>`;

  fillIcons($('#startupTable'));
  ensureIcons(items.map((i) => i.exe), () => fillIcons($('#startupTable')));

  $$('[data-toggle]').forEach((b) => {
    b.onclick = async () => {
      const enabled = b.dataset.on === '1';
      const r = await api('startup_toggle', b.dataset.toggle, !enabled);
      toast(r.ok ? (enabled ? '已禁用，可随时恢复' : '已重新启用') : (r.msg || '操作失败'), r.ok ? 'ok' : 'err');
      loadStartup();
    };
  });
  $$('[data-del]').forEach((b) => {
    b.onclick = async () => {
      const ok = await confirmBox('删除该启动项？', '将从系统中移除这条自启动记录，程序本身不会被卸载。', true);
      if (!ok) return;
      const r = await api('startup_remove', b.dataset.del);
      toast(r.ok ? '已删除' : (r.msg || '删除失败'), r.ok ? 'ok' : 'err');
      loadStartup();
    };
  });
}

/* ---------------- 进程 ---------------- */
let procSort = 'mem', procData = [];
async function initProcess() {
  await loadProcs();
  setTimeout(loadProcs, 1000);   // 第二次采样才能算出 CPU 占用
  $('#btnProcRefresh').onclick = loadProcs;
  $$('[data-sort]').forEach((b) => {
    b.onclick = () => { procSort = b.dataset.sort; loadProcs(); };
  });
  $('#procSearch').oninput = () => renderProcs();
}
async function loadProcs() {
  const r = await api('proc_list', procSort);
  if (!r.ok) return;
  procData = r.items || [];
  renderProcs();
}
function renderProcs() {
  const kw = $('#procSearch').value.trim().toLowerCase();
  const rows = procData.filter((p) => !kw || p.name.toLowerCase().includes(kw));
  const shown = rows.slice(0, 220);
  $('#procTable').innerHTML = `
    <colgroup><col style="width:40px"><col><col style="width:78px"><col style="width:66px"><col style="width:92px"><col style="width:112px"><col style="width:158px"></colgroup>
    <thead><tr><th class="ico-cell" title="程序图标"></th><th>进程名</th><th style="width:78px">PID</th><th class="num" style="width:64px">CPU</th><th class="num" style="width:88px">内存</th><th>用户</th><th style="width:158px">操作</th></tr></thead>
    <tbody>${shown.map((p) => `<tr>
      ${icoCell(p.exe)}
      <td><div class="name-cell"><span class="nm">${esc(p.name)}</span>${p.critical ? '<span class="badge lock">系统</span>' : ''}</div></td>
      <td class="mono">${p.pid}</td>
      <td class="num" style="color:${p.cpu > 50 ? 'var(--red)' : p.cpu > 15 ? 'var(--amber)' : 'inherit'}">${p.cpu}%</td>
      <td class="num">${p.mem_h}</td>
      <td class="muted" style="white-space:nowrap">${esc(p.user || '-')}</td>
      <td style="white-space:nowrap">
        <button class="row-btn" data-prio="${p.pid}">优先级</button>
        <button class="row-btn danger" data-kill="${p.pid}" data-name="${esc(p.name)}" ${p.critical ? 'disabled' : ''}>结束</button>
      </td>
    </tr>`).join('')}</tbody>`;

  fillIcons($('#procTable'));
  ensureIcons(shown.map((p) => p.exe), () => fillIcons($('#procTable')));

  $$('[data-kill]').forEach((b) => {
    b.onclick = async () => {
      const ok = await confirmBox('结束进程？', `即将结束 ${b.dataset.name}（PID ${b.dataset.kill}），未保存的数据可能丢失。`, true);
      if (!ok) return;
      const r = await api('proc_kill', +b.dataset.kill);
      toast(r.msg, r.ok ? 'ok' : 'err');
      loadProcs();
    };
  });
  $$('[data-prio]').forEach((b) => {
    b.onclick = async () => {
      const level = await promptBox('设置进程优先级',
        '可选：idle / below / normal / above / high（数字越靠后越高）', 'high');
      if (!level) return;
      const r = await api('proc_priority', +b.dataset.prio, level.trim());
      toast(r.msg, r.ok ? 'ok' : 'err');
    };
  });
}

/* ---------------- 服务 ---------------- */
let svcData = [];
async function initService() {
  await loadSvcs();
  $('#btnSvcRefresh').onclick = () => loadSvcs(true);
  $('#svcSearch').oninput = renderSvcs;
  $('#btnSvcDisable').onclick = async () => {
    const ok = await confirmBox('优化非必要服务？',
      '将停止遥测、Xbox、传真等非必要服务并改为手动启动。不会影响系统核心功能，可在列表中随时恢复。', false);
    if (!ok) return;
    overlay('正在优化服务…', '部分服务需要几秒响应');
    const r = await api('svc_disable_recommended');
    hideOverlay();
    toast(r.msg || '完成', r.ok ? 'ok' : 'err', 4000);
    loadSvcs(true);
  };
}
async function loadSvcs(force) {
  $('#svcTable').innerHTML = '<tbody><tr><td class="empty">读取服务列表…</td></tr></tbody>';
  const r = await api('svc_list', !!force);
  if (!r.ok) return;
  svcData = r.items || [];
  renderSvcs();
}
function renderSvcs() {
  const kw = $('#svcSearch').value.trim().toLowerCase();
  const rows = svcData.filter((s) => !kw ||
    (s.name + s.display).toLowerCase().includes(kw));
  const st = { automatic: '自动', auto: '自动', manual: '手动', demand: '手动', disabled: '已禁用', boot: '引导', system: '系统' };
  $('#svcTable').innerHTML = `
    <thead><tr><th>服务名称</th><th>显示名</th><th>状态</th><th>启动类型</th><th style="width:210px">操作</th></tr></thead>
    <tbody>${rows.slice(0, 300).map((s) => `<tr>
      <td class="mono">${esc(s.name)}</td>
      <td>${esc(s.display)} ${s.recommend_off ? '<span class="badge rec">可优化</span>' : ''}</td>
      <td>${s.status === 'running' ? '<span class="badge run">运行中</span>' : '<span class="badge stop">已停止</span>'}</td>
      <td class="muted">${st[s.start_type] || s.start_type || '-'}</td>
      <td>
        <button class="row-btn" data-svc="${esc(s.name)}" data-act="start">启动</button>
        <button class="row-btn" data-svc="${esc(s.name)}" data-act="stop">停止</button>
        <button class="row-btn" data-svc="${esc(s.name)}" data-act="type">启动类型</button>
      </td>
    </tr>`).join('')}</tbody>`;

  $$('[data-svc]').forEach((b) => {
    b.onclick = async () => {
      if (b.dataset.act === 'type') {
        const t = await promptBox('设置服务启动类型',
          '可选：auto（自动）/ manual（手动）/ disabled（禁用）', 'manual');
        if (!t) return;
        const r = await api('svc_start_type', b.dataset.svc, t.trim());
        toast(r.msg, r.ok ? 'ok' : 'err', 3500);
      } else {
        const r = await api('svc_control', b.dataset.svc, b.dataset.act);
        toast(r.msg, r.ok ? 'ok' : 'err', 3500);
      }
      loadSvcs(true);
    };
  });
}

/* ---------------- 系统修复 ---------------- */
async function initRepair() {
  loadRepairOverview();
  $$('[data-repair]').forEach((b) => {
    b.onclick = async () => {
      const kind = b.dataset.repair;
      const names = { sfc: 'SFC 系统文件检查', dism: 'DISM 映像修复', chkdsk: '磁盘错误检查', optimize: '磁盘优化', restore_point: '创建还原点' };
      if (kind === 'sfc' || kind === 'dism') {
        const ok = await confirmBox('开始' + names[kind] + '？',
          '该过程可能持续 5-30 分钟，期间请不要关闭程序。可以最小化窗口继续做别的事情。', false);
        if (!ok) return;
      }
      const r = await api('repair_start', kind);
      if (!r.ok) return toast(r.msg || '启动失败', 'err');
      runJob(r.job_id, names[kind]);
    };
  });
}

async function runJob(jobId, title) {
  $('#jobCard').style.display = '';
  $('#jobTitle').textContent = title;
  $('#jobBar').style.width = '2%';
  $('#jobLog').textContent = '正在启动…';
  const job = await pollJob(jobId, (j) => {
    $('#jobBar').style.width = Math.max(2, j.progress) + '%';
    $('#jobElapsed').textContent = `已用时 ${j.elapsed} 秒`;
    $('#jobLog').textContent = (j.logs || []).join('\n') || j.message;
    $('#jobLog').scrollTop = $('#jobLog').scrollHeight;
  });
  if (!job) return;
  $('#jobBar').style.width = '100%';
  if (job.status === 'done') {
    const msg = (job.result && job.result.msg) || '任务完成';
    toast(msg, 'ok', 5000);
    $('#jobLog').textContent += '\n\n===== ' + msg + ' =====';
    if (job.result && job.result.output) {
      $('#jobLog').textContent += '\n' + job.result.output;
    }
  } else {
    toast(job.error || '任务失败', 'err', 5000);
  }
  if (job.kind === 'restore_point') loadRepairOverview();
}

async function loadRepairOverview() {
  const r = await api('repair_overview');
  if (!r.ok) return;
  $('#diskHealth').innerHTML = (r.disks || []).map((d) => `
    <div class="health-row">
      <div><b>${esc(d.name)}</b> <span class="muted">${esc(d.media || '')} ${esc(d.size || '')}</span></div>
      <span class="badge ${d.health === '健康' ? 'run' : 'rec'}">${esc(d.health)}</span>
    </div>`).join('') || '<div class="empty">未获取到磁盘信息</div>';

  const pts = r.points || [];
  $('#restoreList').innerHTML = pts.length
    ? pts.slice(-5).reverse().map((p) => `<div class="health-row"><span>${esc(p.time || '')}</span><span class="muted">${esc(p.desc || '还原点')}</span></div>`).join('')
    : '<div class="empty" style="padding:14px 0">暂无系统还原点</div>';
}

/* ---------------- 磁盘分析 ---------------- */
let diskTab = 'big', scanJobId = null, scanResult = null;
function initDisk() {
  $$('#diskTabs .tab').forEach((t) => {
    t.onclick = () => {
      diskTab = t.dataset.tab;
      $$('#diskTabs .tab').forEach((x) => x.classList.toggle('active', x === t));
      $('#bigOpts').style.display = diskTab === 'big' ? '' : 'none';
      $('#dupOpts').style.display = diskTab === 'dup' ? '' : 'none';
      $('#scanTable').innerHTML = '';
      $('#scanHint').textContent = '';
    };
  });
  $('#btnScanStart').onclick = startScan;
  $('#btnScanCancel').onclick = async () => {
    await api('job_cancel');
    toast('已请求取消', 'info');
  };
}

async function startScan() {
  const params = diskTab === 'big'
    ? { min_mb: +$('#bigSize').value }
    : { min_kb: +$('#dupSize').value };
  overlay('正在准备扫描…', '全盘扫描可能需要几分钟');
  const r = await api('scan_start', diskTab, params);
  hideOverlay();
  if (!r.ok) return toast(r.msg || '启动失败', 'err');
  scanJobId = r.job_id;
  $('#scanProgressWrap').style.display = '';
  $('#btnScanCancel').style.display = '';
  $('#scanBar').style.width = '3%';
  $('#scanTable').innerHTML = '';
  $('#scanHint').textContent = '扫描中…';
  const job = await pollJob(scanJobId, (j) => {
    $('#scanBar').style.width = Math.max(3, j.progress) + '%';
    $('#scanHint').textContent = j.message;
  });
  $('#scanProgressWrap').style.display = 'none';
  $('#btnScanCancel').style.display = 'none';
  if (!job || job.status !== 'done') {
    $('#scanHint').textContent = '扫描未完成' + (job && job.error ? '：' + job.error : '');
    return;
  }
  scanResult = job.result;
  $('#scanBar').style.width = '100%';
  renderScan(scanResult, job.kind);
}

function renderScan(res, kind) {
  if (kind === 'dup') {
    $('#scanHint').textContent = `共 ${res.total_groups} 组重复文件，可释放 ${res.waste_h}（已扫描 ${res.scanned} 个文件）`;
    if (!res.groups.length) {
      $('#scanTable').innerHTML = '<tbody><tr><td class="empty">没有发现重复文件</td></tr></tbody>';
      return;
    }
    $('#scanTable').innerHTML = `
      <thead><tr><th>重复文件组</th><th class="num">单文件大小</th><th class="num">数量</th><th class="num">可释放</th><th style="width:110px">操作</th></tr></thead>
      <tbody>${res.groups.map((g, gi) => `
        <tr class="grp" data-g="${gi}" style="cursor:pointer">
          <td><span class="badge rec">${g.count} 个相同</span> <span class="mono">${esc(g.hash)}</span></td>
          <td class="num">${g.size_h}</td>
          <td class="num">${g.count}</td>
          <td class="num" style="color:var(--accent-text)">${g.waste_h}</td>
          <td><button class="row-btn" data-open="${gi}">展开</button></td>
        </tr>
        <tr class="detail" data-d="${gi}" style="display:none"><td colspan="5" style="padding:0 12px 12px">
          <div style="display:flex;flex-direction:column;gap:6px">
          ${g.files.map((f, fi) => `<div style="display:flex;align-items:center;gap:10px;font-size:11.5px">
            <span class="mono" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(f.path)}">${esc(f.path)}</span>
            ${fi === 0 ? '<span class="badge run">保留</span>' : `<button class="row-btn danger" data-delfile="${esc(f.path)}">删除</button>`}
          </div>`).join('')}
          </div>
        </td></tr>`).join('')}</tbody>`;
    $$('[data-open]').forEach((b) => {
      b.onclick = (e) => {
        e.stopPropagation();
        const d = $(`[data-d="${b.dataset.open}"]`);
        d.style.display = d.style.display === 'none' ? '' : 'none';
      };
    });
    $$('[data-delfile]').forEach((b) => {
      b.onclick = async () => {
        const ok = await confirmBox('删除这个文件？', b.dataset.delfile, true);
        if (!ok) return;
        const r = await api('delete_files', [b.dataset.delfile]);
        toast(r.msg, 'ok');
        b.closest('div').style.opacity = '.35';
        b.remove();
      };
    });
    return;
  }
  if (kind === 'empty') {
    $('#scanHint').textContent = `共 ${res.count} 个空文件夹`;
    $('#scanTable').innerHTML = `
      <thead><tr><th>路径</th><th style="width:110px">操作</th></tr></thead>
      <tbody>${(res.dirs || []).map((d) => `<tr>
        <td class="mono ellip" title="${esc(d)}">${esc(d)}</td>
        <td><button class="row-btn" data-opendir="${esc(d)}">打开</button></td>
      </tr>`).join('') || '<tr><td class="empty" colspan="2">没有空文件夹</td></tr>'}</tbody>`;
    $$('[data-opendir]').forEach((b) => b.onclick = () => api('open_path', b.dataset.opendir));
    return;
  }
  // 大文件
  $('#scanHint').textContent = `共 ${res.count} 个大于阈值的文件，合计 ${res.total_h}（已扫描 ${res.scanned} 个文件）`;
  $('#scanTable').innerHTML = `
    <thead><tr><th>文件名</th><th>所在目录</th><th class="num">大小</th><th>修改日期</th><th style="width:110px">操作</th></tr></thead>
    <tbody>${(res.items || []).map((f) => `<tr>
      <td class="ellip" title="${esc(f.name)}">${esc(f.name)}</td>
      <td class="mono ellip" title="${esc(f.dir)}">${esc(f.dir)}</td>
      <td class="num" style="color:var(--accent-text)">${f.size_h}</td>
      <td class="muted">${esc(f.date)}</td>
      <td><button class="row-btn" data-openfile="${esc(f.dir)}">打开位置</button></td>
    </tr>`).join('')}</tbody>`;
  $$('[data-openfile]').forEach((b) => b.onclick = () => api('open_path', b.dataset.openfile));
}

/* ---------------- 系统信息 ---------------- */
async function initSysinfo() {
  await loadSysinfo();
  $('#btnSysRefresh').onclick = async () => { overlay('正在重新读取硬件信息…'); await loadSysinfo(); hideOverlay(); };
}
async function loadSysinfo() {
  const d = await api('get_sysinfo');
  if (!d || !d.os) return;
  const disks = d.phys_disks || [];
  const diskText = !disks.length ? '未知'
    : disks.length === 1 ? disks[0]
    : `${disks[0]} 等 ${disks.length} 块`;
  const diskFull = disks.join(' / ') || '未知';
  const rows = [
    ['操作系统', d.os], ['系统架构', d.os_arch], ['激活状态', d.activated],
    ['设备型号', d.vendor || '未知'], ['主板', d.board], ['BIOS', d.bios],
    ['处理器', d.cpu], ['核心 / 线程', `${d.cpu_cores} / ${d.cpu_threads}`],
    ['CPU 主频', d.cpu_clock],
    ['CPU 温度', d.cpu_temp != null ? `${d.cpu_temp} °C`
      : (d.cpu_temp_known ? '主板未暴露传感器' : '读取中…')],
    ['内存容量', `${d.ram_total_h}${d.ram_sticks ? ` · ${d.ram_sticks} 条` : ''}`],
    ['内存类型', [d.ram_type, d.ram_speed].filter(Boolean).join(' ') || '未知'],
    ['显卡', (d.gpu || []).join(' / ')], ['分辨率', d.res],
    ['计算机名', d.hostname], ['当前用户', d.user],
    ['开机时间', d.boot_time], ['已运行', d.uptime],
    ['网卡', (d.net_adapters || []).join(' / ') || '未检测到'],
    ['物理磁盘', diskText, '物理磁盘：' + diskFull],
  ];
  $('#specGrid').innerHTML = rows.map(([k, v, tip]) =>
    `<div class="spec" title="${esc(tip || k + '：' + v)}"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
  $('#diskList').innerHTML = (d.disks || []).map((x) => `
    <div class="disk-item">
      <div class="disk-top">
        <div><b>${esc(x.device)}</b> <span class="muted">${esc(x.media)} · ${esc(x.fstype)}</span></div>
        <span class="muted">可用 ${x.free_h} / 共 ${x.total_h}</span>
      </div>
      <div class="dbar"><i style="width:${x.percent}%"></i></div>
    </div>`).join('');
}

/* ---------------- 关于 ---------------- */
async function initAbout() {
  const env = await api('get_env');
  $('#aboutAdmin').textContent = env.admin ? '管理员' : '普通用户';
  $$('.contact-item').forEach((el) => {
    el.onclick = async () => {
      // GitHub 等外链：交给后端用系统浏览器打开（WebView2 里直接跳转会把界面顶掉）
      const url = el.dataset.url;
      if (url) {
        const r = await api('open_url', url);
        toast(r && r.ok ? '已在浏览器中打开 GitHub 仓库' : '打不开浏览器，请手动访问：' + url,
              r && r.ok ? 'ok' : 'err', 3200);
        return;
      }
      const text = el.dataset.copy;
      try {
        await navigator.clipboard.writeText(text);
        toast(`已复制「${text}」，去微信 / 小红书搜索吧`, 'ok', 3200);
      } catch (e) {
        toast('复制失败，请手动输入：' + text, 'err');
      }
    };
  });
}

/* ---------------- 托盘 / 自启 / 悬浮胶囊设置 ---------------- */
const SET_SWITCHES = [
  ['setAutostart', 'autostart'],
  ['setWidget', 'widget'],
  ['setEdge', 'widget_edge_hide'],
  ['setTray', 'close_to_tray'],
];

/* 胶囊外观：透明度 / 主题色 / 显示内容 */
const LOOK = { opacity: 100, accent: 'blue', color: '#2f6bff', items: ['score', 'cpu', 'ram', 'net'] };
let _opTimer = null;

function paintLook() {
  $('#setOpacity').value = LOOK.opacity;
  $('#setOpacityVal').textContent = LOOK.opacity + '%';
  $$('#setAccent .swatch').forEach((el) => {
    el.classList.toggle('on', el.dataset.accent === LOOK.accent);
  });
  if (/^#[0-9a-f]{6}$/i.test(LOOK.color)) $('#setColor').value = LOOK.color;
  $$('#setItems .chip').forEach((el) => {
    el.classList.toggle('on', LOOK.items.includes(el.dataset.item));
  });
}
function loadLook(s) {
  LOOK.opacity = Number.isFinite(+s.widget_opacity) ? +s.widget_opacity : 100;
  LOOK.accent = s.widget_accent || 'blue';
  LOOK.color = s.widget_color || '#2f6bff';
  LOOK.items = Array.isArray(s.widget_items) ? s.widget_items.slice() : ['score', 'cpu', 'ram', 'net'];
  paintLook();
}

$('#setOpacity').addEventListener('input', (e) => {
  LOOK.opacity = +e.target.value;
  $('#setOpacityVal').textContent = LOOK.opacity + '%';
  clearTimeout(_opTimer);
  _opTimer = setTimeout(() => api('set_ui_option', 'widget_opacity', LOOK.opacity), 150);
});
$$('#setAccent .swatch').forEach((el) => {
  el.onclick = async () => {
    LOOK.accent = el.dataset.accent;
    paintLook();
    const r = await api('set_ui_option', 'widget_accent', LOOK.accent);
    if (r && r.ok === false) toast(r.msg || '设置失败', 'err');
  };
});
$('#setColor').addEventListener('change', async (e) => {
  LOOK.color = e.target.value;
  LOOK.accent = 'custom';
  paintLook();
  const r = await api('set_ui_option', 'widget_color', LOOK.color);
  if (r && r.ok === false) toast(r.msg || '设置失败', 'err');
  else toast('已应用自定义颜色', 'ok', 1500);
});
$$('#setItems .chip').forEach((el) => {
  el.onclick = async () => {
    const k = el.dataset.item;
    const i = LOOK.items.indexOf(k);
    if (i >= 0) LOOK.items.splice(i, 1); else LOOK.items.push(k);
    paintLook();
    const r = await api('set_ui_option', 'widget_items', LOOK.items);
    if (r && r.ok === false) toast(r.msg || '设置失败', 'err');
  };
});

async function openSettings() {
  try {
    const r = await api('get_ui_settings');
    if (!r || !r.ok) { toast('读取设置失败', 'err'); return; }
    const s = r.settings || {};
    $('#setAutostart').checked = !!r.autostart;
    $('#setWidget').checked = s.widget !== false;
    $('#setEdge').checked = s.widget_edge_hide !== false;
    $('#setTray').checked = s.close_to_tray !== false;
    $('#btnWidget').classList.toggle('active', s.widget !== false);
    loadLook(s);
  } catch (e) { toast('读取设置失败', 'err'); }
  $('#settingsModal').classList.remove('hidden');
}
function closeSettings() { $('#settingsModal').classList.add('hidden'); }

SET_SWITCHES.forEach(([id, key]) => {
  $('#' + id).addEventListener('change', async (e) => {
    const val = e.target.checked;
    const r = await api('set_ui_option', key, val);
    if (r && r.ok === false) {
      e.target.checked = !val;
      toast(r.msg || '设置失败', 'err');
      return;
    }
    if (key === 'widget') $('#btnWidget').classList.toggle('active', val);
    const names = {
      autostart: val ? '已开启开机自启' : '已关闭开机自启',
      widget: val ? '悬浮胶囊已显示' : '悬浮胶囊已隐藏',
      widget_edge_hide: val ? '已开启贴边隐藏' : '已关闭贴边隐藏',
      close_to_tray: val ? '关闭时将收进托盘' : '关闭时将直接退出',
    };
    toast(names[key] || '设置已保存', 'ok', 1800);
  });
});

$('#btnSettings').onclick = openSettings;
$('#btnSettingsDone').onclick = closeSettings;
$('#settingsModal').addEventListener('click', (e) => {
  if (e.target.id === 'settingsModal') closeSettings();
});
$('#btnSettingsHideTray').onclick = async () => {
  closeSettings();
  await api('minimize_to_tray');
};
$('#btnAppQuit').onclick = async () => {
  // 设置弹窗在 DOM 中排在确认弹窗之后，先收起再确认，避免确认框被盖住
  $('#settingsModal').classList.add('hidden');
  const ok = await confirmBox('退出程序？',
    '将关闭主界面、悬浮胶囊与托盘图标，完全退出景同志·电脑加速。', true);
  if (!ok) { $('#settingsModal').classList.remove('hidden'); return; }
  await api('app_quit');
};
$('#btnTray').onclick = async () => {
  const r = await api('minimize_to_tray');
  if (!r || r.ok === false) toast('托盘未就绪', 'err');
  else toast('已收进托盘，双击任务栏图标恢复', 'ok');
};
$('#btnWidget').onclick = async () => {
  const r = await api('widget_toggle');
  if (r && r.ok) {
    $('#btnWidget').classList.toggle('active', !!r.enabled);
    toast(r.enabled ? '悬浮胶囊已显示' : '悬浮胶囊已隐藏', 'ok', 1600);
  } else toast('操作失败', 'err');
};

/* ---------------- 启动 ---------------- */
(async function boot() {
  await waitApi();

  // 主题：优先后端设置（跨会话持久），其次本地缓存
  try {
    const st = await api('get_settings');
    const saved = st && st.settings && st.settings.theme;
    if (saved) applyTheme(saved);
  } catch (e) { /* ignore */ }
  $('#btnTheme').onclick = async () => {
    const next = window.__theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    await api('set_ui_option', 'theme', next);
    toast(next === 'dark' ? '已切换到深色主题' : '已切换到浅色主题', 'info', 1500);
  };

  const env = await api('get_env');
  // 版本号一律以后端为准：HTML 里那三处只是首屏兜底，
  // 升级时忘记同步也不会出现「标题栏 1.4.1、侧边栏 1.4.0」这种自相矛盾。
  if (env && env.version) {
    const v = String(env.version);
    const sv = $('#sideVer'); if (sv) sv.textContent = 'JingBoost v' + v;
    const av = $('#aboutVer'); if (av) av.textContent = `Version ${v} · 完全免费 · 无广告 · 无捆绑`;
    const an = $('#aboutVerNum'); if (an) an.textContent = v;
  }
  if (env && env.admin !== undefined) {
    $('#adminPill').textContent = env.admin ? '已获得管理员权限' : '普通权限 · 点击提权';
    $('#adminPill').className = 'admin-pill' + (env.admin ? ' ok' : ' clickable');
    if (!env.admin) {
      $('#adminPill').onclick = async () => {
        const ok = await confirmBox('以管理员身份重启？',
          '清理系统临时文件、优化服务、系统修复、hosts 修改等深度功能需要管理员权限。\n请在随后弹出的系统窗口点击「是」，程序会自动完成重启交接。', false);
        if (!ok) return;
        overlay('正在以管理员身份重启…', '请在系统弹窗中点击「是」，程序会自动回来');
        const r = await api('elevate');
        hideOverlay();
        if (!r || !r.ok) toast((r && r.msg) || '提权被取消', 'err', 3200);
        else toast('提权成功，正在重新启动…', 'ok', 2000);
      };
    }
  }
  // 同步悬浮胶囊按钮的初始状态
  try {
    const st = await api('widget_state');
    if (st) $('#btnWidget').classList.toggle('active', !!st.enabled);
  } catch (e) { /* ignore */ }

  inited.dashboard = true;
  await refreshDashboard();
  tickTop();
})();
