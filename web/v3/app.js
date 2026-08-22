const LIVE_DATA = 'https://raw.githubusercontent.com/ghzyzttxwd/ai-stock-lab/v3-agent-paper/web/v3/data.json';
const FUND_ORDER = ['A', 'B', 'C', 'D', 'L'];
const FUND_LABEL = { A: '保守稳健', B: '趋势进攻', C: '短线机会', D: '综合判断', L: '长线价值' };
const REJECTION = {
  limit_up_locked: '涨停无法买入', limit_down_locked: '跌停无法卖出', t_plus_one_locked: 'T+1 当日不可卖',
  insufficient_cash: '现金不足', below_board_lot: '不足一手', missing_execution_bar: '缺少执行行情',
  invalid_open_price: '开盘价无效', suspended: '停牌'
};

const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
const n = value => Number(value) || 0;
const money = value => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', maximumFractionDigits: 0 }).format(n(value));
const num = value => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(n(value));
const pct = value => `${n(value) >= 0 ? '+' : ''}${n(value).toFixed(2)}%`;
const tone = value => n(value) > 0 ? 'positive' : n(value) < 0 ? 'negative' : 'neutral';
const shortDate = value => String(value || '尚未运行').replaceAll('-', '.');

function validate(data) {
  if (!data || data.summary_version !== 'v3-agent-paper-summary-1.0') throw new Error('V3 数据版本不匹配');
  if (data.mode !== 'AUTONOMOUS_AI_PAPER' || data.requires_user_approval !== false) throw new Error('V3 虚拟盘模式标记异常');
  if (FUND_ORDER.some(id => !data.funds?.[id])) throw new Error('五个 V3 组合数据不完整');
  return data;
}

async function fetchJson(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 9000);
  try {
    const response = await fetch(`${url}?t=${Date.now()}`, { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally { clearTimeout(timer); }
}

async function loadData() {
  try { return validate(await fetchJson(LIVE_DATA)); }
  catch (liveError) {
    try { return validate(await fetchJson('data.json')); }
    catch (fallbackError) { throw new Error(`实时账本：${liveError.message}；备用快照：${fallbackError.message}`); }
  }
}

function positionValue(position) {
  return n(position.market_value ?? (n(position.qty) * n(position.last_price ?? position.close ?? position.avg_cost)));
}

function positionPnl(position) {
  if (position.pnl_pct != null) return n(position.pnl_pct);
  const last = n(position.last_price ?? position.close);
  const cost = n(position.avg_cost);
  return cost > 0 && last > 0 ? (last / cost - 1) * 100 : 0;
}

function alignActiveFund(activeId) {
  const strip = document.querySelector('.fund-strip');
  const active = strip?.querySelector(`[data-fund="${activeId}"]`);
  if (!strip || !active) return;
  const target = active.offsetLeft - (strip.clientWidth - active.clientWidth) / 2;
  strip.scrollLeft = Math.max(0, target);
}

function equityChart(fund) {
  let values = (fund.equity_curve || []).map(x => n(x.equity)).filter(x => x > 0);
  if (!values.length) values = [n(fund.initial_cash), n(fund.equity)];
  if (values.length === 1) values.push(values[0]);
  const width = 640, height = 150, pad = 12;
  const min = Math.min(...values), max = Math.max(...values);
  const span = Math.max(max - min, max * .004, 1);
  const points = values.map((value, index) => {
    const x = pad + index * (width - pad * 2) / Math.max(values.length - 1, 1);
    const y = height - pad - (value - min) / span * (height - pad * 2);
    return [x, y];
  });
  const line = points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `${line} L${points.at(-1)[0]},${height} L${points[0][0]},${height} Z`;
  const last = points.at(-1);
  return `<div class="chart"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="累计总资产曲线">
    <defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#20d49a" stop-opacity=".28"/><stop offset="1" stop-color="#20d49a" stop-opacity="0"/></linearGradient></defs>
    <path class="gridline" d="M0,38 H640 M0,75 H640 M0,112 H640"/><path class="area" d="${area}"/><path class="curve" d="${line}"/><circle class="latest-dot" cx="${last[0]}" cy="${last[1]}" r="4"/>
  </svg></div>`;
}

function fundCards(data, activeId) {
  return FUND_ORDER.map(id => {
    const fund = data.funds[id];
    return `<button class="fund-card ${id === activeId ? 'active' : ''}" data-fund="${id}">
      <div class="fund-top"><span class="fund-letter">${id}</span><small>${esc(FUND_LABEL[id])}</small></div>
      <strong>${money(fund.equity)}</strong><div class="return ${tone(fund.return_pct)}">${pct(fund.return_pct)}</div>
    </button>`;
  }).join('');
}

function positionRows(fund) {
  const positions = fund.positions || [];
  if (!positions.length) return '<div class="empty">当前为空仓<br>AI 可以主动选择保留全部现金</div>';
  const total = Math.max(n(fund.equity), 1);
  return positions.sort((a, b) => positionValue(b) - positionValue(a)).map(item => {
    const value = positionValue(item), weight = value / total * 100, pnl = positionPnl(item);
    return `<div class="position"><div class="row"><div class="row-main"><b>${esc(item.name || item.symbol)}</b><div class="meta">${esc(item.symbol)} · ${num(item.qty)} 股 · ${esc(item.industry || '主板')}</div></div><div class="position-price"><b>${money(value)}</b><span class="meta ${tone(pnl)}">${pct(pnl)}</span></div></div><div class="bar"><i style="width:${Math.min(100, weight).toFixed(1)}%"></i></div><div class="meta">仓位 ${weight.toFixed(1)}% · 成本 ${num(item.avg_cost)} · 现价 ${num(item.last_price ?? item.close)}</div></div>`;
  }).join('');
}

function targetRows(decision, activeId) {
  const targets = decision?.portfolios?.[activeId] || [];
  if (!targets.length) return '<div class="empty">该组合暂无待执行目标<br>可能是 AI 决定空仓，或首个交易日尚未分析</div>';
  return targets.map(item => `<div class="target"><div class="row"><div class="row-main"><b>${esc(item.name)}</b><div class="meta">${esc(item.symbol)} · ${esc(item.industry || '主板')}</div></div><span class="weight">${(n(item.target_weight) * 100).toFixed(1)}%</span></div><div class="reason">${esc(item.thesis)}</div><div class="meta">失效条件：${esc(item.invalidation)}</div></div>`).join('');
}

function activityRows(fund) {
  const fills = (fund.recent_fills || []).map(x => ({ ...x, kind: 'fill' }));
  const rejects = (fund.recent_rejections || []).map(x => ({ ...x, kind: 'reject' }));
  const rows = [...fills, ...rejects].sort((a, b) => String(b.trade_date || '').localeCompare(String(a.trade_date || ''))).slice(0, 12);
  if (!rows.length) return '<div class="empty">暂无成交记录<br>第一笔交易将在 AI 决策后的下一交易日按开盘价模拟执行</div>';
  return rows.map(item => {
    const rejected = item.kind === 'reject';
    const side = rejected ? 'reject' : String(item.side || '').toLowerCase();
    const label = rejected ? '拒' : item.side === 'BUY' ? '买' : '卖';
    const title = rejected ? (REJECTION[item.reason] || item.reason || '未成交') : `${item.side === 'BUY' ? '买入' : '卖出'} ${item.name || item.symbol}`;
    const amount = n(item.gross ?? (n(item.qty) * n(item.price)));
    return `<div class="activity"><div class="row"><span class="side ${side}">${label}</span><div class="activity-main"><b>${esc(title)}</b><div class="meta">${shortDate(item.trade_date || '')} · 开盘价模拟 · ${num(item.qty)} 股${rejected ? '' : ` · ¥${num(item.price)}`}</div></div><div class="activity-amount">${rejected ? '<span class="neutral">未成交</span>' : `<b>${money(amount)}</b><div class="meta">费 ${money(item.fees)}</div>`}</div></div></div>`;
  }).join('');
}

function render(data, activeId = 'A') {
  const fund = data.funds[activeId] || data.funds.A;
  const funds = FUND_ORDER.map(id => data.funds[id]);
  const totalEquity = funds.reduce((sum, x) => sum + n(x.equity), 0);
  const totalInitial = funds.reduce((sum, x) => sum + n(x.initial_cash), 0);
  const totalReturn = totalInitial ? (totalEquity / totalInitial - 1) * 100 : 0;
  const totalCash = funds.reduce((sum, x) => sum + n(x.cash), 0);
  const positionsValue = Math.max(0, totalEquity - totalCash);
  const decision = data.latest_decision;
  const updated = data.updated_at;

  document.querySelector('#app').innerHTML = `<div class="shell">
    <header class="appbar"><div class="brand"><span class="logo">AI</span><div><b>AI Trade V3</b><small>全自动主板虚拟盘</small></div></div><span class="status"><i></i>系统运行中</span></header>
    <section class="hero" id="overview"><div class="kicker">五组合总资产</div><div class="hero-value">${money(totalEquity)}</div><div class="pnl-line"><b class="${tone(totalReturn)}">${pct(totalReturn)}</b><span class="subtle">累计盈亏 ${money(totalEquity - totalInitial)}</span></div><div class="hero-stats"><div class="hero-stat"><span>可用现金</span><b>${money(totalCash)}</b></div><div class="hero-stat"><span>股票市值</span><b>${money(positionsValue)}</b></div><div class="hero-stat"><span>数据交易日</span><b>${shortDate(updated)}</b></div></div></section>
    <section class="section"><div class="section-head"><h2>AI 基金竞技场</h2><span>每只初始 ¥100万</span></div><div class="fund-strip">${fundCards(data, activeId)}</div></section>
    <section class="section panel summary-panel" id="fund"><div class="fund-title"><div><h3>${esc(fund.name)}</h3><div class="meta">最后结算 ${esc(fund.last_processed_date || '等待首笔交易')}</div></div><span class="pill">${activeId} · ${esc(FUND_LABEL[activeId])}</span></div><div class="metrics"><div class="metric"><span>当前总资产</span><b>${money(fund.equity)}</b></div><div class="metric"><span>累计收益</span><b class="${tone(fund.return_pct)}">${pct(fund.return_pct)}</b></div><div class="metric"><span>现金</span><b>${money(fund.cash)}</b></div><div class="metric"><span>持仓数量</span><b>${(fund.positions || []).length} 只</b></div></div>${equityChart(fund)}</section>
    <div class="dashboard-grid">
      <section class="section" id="positions"><div class="section-head"><h2>当前持仓</h2><span>${(fund.positions || []).length} 只股票</span></div><div class="panel">${positionRows(fund)}</div></section>
      <section class="section" id="decision"><div class="section-head"><h2>AI 当天决策</h2><span>${decision ? `${esc(decision.decision_date)} → ${esc(decision.execute_on)}` : '等待首轮分析'}</span></div><div class="panel ai-panel"><div class="ai-head"><span class="ai-mark">AI</span><div><b>市场判断</b><span>无需人工审批 · 下一交易日按开盘价模拟执行</span></div></div><p class="market-view">${esc(decision?.market_view || '首个实际交易日收盘后，我会读取完整行情与当天消息并生成决策。')}</p><div class="target-list">${targetRows(decision, activeId)}</div></div></section>
    </div>
    <section class="section" id="trades"><div class="section-head"><h2>交易流水</h2><span>成交与拒单 · 开盘价模拟</span></div><div class="panel">${activityRows(fund)}</div></section>
    <p class="footnote">纯虚拟交易 · 不连接银河证券 · 仅沪深 A 股主板 · 排除创业板、科创板、北交所、B 股、ST 与退市整理股票<br>买卖按决策指定交易日的官方开盘价并计入滑点、手续费模拟。当前账本只记录交易日，不记录精确分钟；GitHub 工作流实际几点运行也不等于虚拟成交时点。</p>
  </div><nav class="bottom-nav"><a href="#overview"><b>⌂</b>总览</a><a href="#fund"><b>◇</b>基金</a><a href="#decision"><b>✦</b>决策</a><a href="#trades"><b>≡</b>流水</a></nav>`;

  document.querySelectorAll('[data-fund]').forEach(button => button.addEventListener('click', () => {
    sessionStorage.setItem('v3-active-fund', button.dataset.fund);
    render(data, button.dataset.fund);
  }));
  requestAnimationFrame(() => alignActiveFund(activeId));
}

loadData().then(data => {
  const saved = sessionStorage.getItem('v3-active-fund');
  render(data, FUND_ORDER.includes(saved) ? saved : 'A');
}).catch(error => {
  document.querySelector('#app').innerHTML = `<div class="error"><b>V3 虚拟盘正在初始化</b><div class="meta">${esc(error.message)}</div><p>后台首次流水线完成后，这里会自动出现五个组合的资产、AI 决策、持仓和交易记录。请稍后刷新。</p></div>`;
});

if ('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js');
