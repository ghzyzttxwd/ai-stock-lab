const LIVE_SUMMARY = 'https://raw.githubusercontent.com/ghzyzttxwd/ai-stock-lab/v2-shadow/shadow_state/v2/summary.json';
const FUND_ORDER = ['A', 'B', 'C', 'D', 'L'];
const FUND_SHORT = { A: '保守稳健', B: '趋势追强', C: '短线快攻', D: '综合判断', L: '长线价值' };
const FLAG_LABELS = {
  effective_industries_below_2: '有效行业不足 2 个',
  single_industry_at_least_60pct_of_invested: '单一行业超过已投资仓位 60%',
  top2_industries_at_least_85pct_of_invested: '前两大行业超过已投资仓位 85%',
};
const REJECTION_LABELS = {
  limit_up_locked: '涨停无法买入',
  limit_down_locked: '跌停无法卖出',
  t_plus_one_locked: 'T+1 当日不可卖',
  insufficient_cash: '现金不足',
  below_board_lot: '不足一手',
  missing_execution_bar: '缺少执行行情',
  invalid_open_price: '开盘价无效',
  suspended: '停牌',
  stale_pending_decision: '待执行目标已过期',
};

const esc = value => String(value ?? '')
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
const money = value => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY', maximumFractionDigits: 0 }).format(Number(value) || 0);
const pct = value => value == null ? '样本不足' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
const number = value => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(Number(value) || 0);
const tone = value => Number(value) >= 0 ? 'positive' : 'negative';
const regime = value => ({ bullish: '偏强', neutral: '中性', defensive: '防守', bearish: '偏弱' })[value] || value || '未知';
const marketSource = summary => summary?.source_ref?.data_quality?.snapshot_source || '未知';

function assertSummary(data) {
  if (!data || data.summary_version !== 'v2-shadow-summary-1.1') throw new Error('V2 网页汇总版本不匹配');
  if (data.mode !== 'FORWARD_SHADOW_ONLY') throw new Error('V2 模式标记不正确');
  if (FUND_ORDER.some(id => !data.funds?.[id])) throw new Error('五只 V2 基金数据不完整');
  const safety = data.safety || {};
  if (safety.calls_sol || safety.reads_v1_ledger || safety.writes_v1_ledger) throw new Error('V2 隔离标记未通过');
  return data;
}

async function fetchJson(url, timeoutMs = 6000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`, { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

async function loadSummary() {
  try {
    return { data: assertSummary(await fetchJson(LIVE_SUMMARY)), source: 'V2 分支实时汇总' };
  } catch (liveError) {
    const fallback = assertSummary(await fetchJson('data.json', 3000));
    return { data: fallback, source: `同源备用快照（实时源暂不可用：${liveError.message}）` };
  }
}

function holdingRows(fund) {
  const holdings = fund.holdings || [];
  if (!holdings.length) return '<div class="card empty">当前为空仓；影子盘允许不买股票。</div>';
  return holdings.map(item => `
    <div class="card">
      <div class="row">
        <div class="row-main"><b>${esc(item.name)}</b><div class="mini">${esc(item.symbol)} · ${number(item.qty)} 股 · ${esc(item.industry)}</div></div>
        <div class="row-side"><b>${Number(item.weight_pct || 0).toFixed(1)}%</b><div class="mini ${tone(item.pnl_pct)}">${pct(item.pnl_pct)}</div></div>
      </div>
      <div class="progress"><i style="width:${Math.min(100, Number(item.weight_pct || 0))}%"></i></div>
      <div class="mini">市值 ${money(item.market_value)} · 成本 ${number(item.avg_cost)} · 现价 ${number(item.last_price)}</div>
    </div>`).join('');
}

function pendingRows(fund) {
  const pending = fund.pending_decision || {};
  const targets = pending.targets || [];
  if (!targets.length) return '<div class="card empty">下一交易日暂无待执行目标。</div>';
  return targets.map(item => `
    <div class="card">
      <div class="row">
        <div class="row-main"><b>${esc(item.name || item.symbol)}</b><div class="mini">${esc(item.symbol)} · ${esc(item.industry || '未分类')}</div></div>
        <div class="row-side"><span class="tag">目标 ${(Number(item.target_weight) * 100).toFixed(1)}%</span><div class="mini">评分 ${number(item.v2_score)}</div></div>
      </div>
      <div class="reason">${esc(item.thesis || '按 V2 规则生成的目标仓位')}</div>
      ${item.invalidation ? `<div class="mini">失效条件：${esc(item.invalidation)}</div>` : ''}
    </div>`).join('');
}

function eventRows(items, kind) {
  if (!items?.length) return '<div class="empty">暂无记录。</div>';
  return [...items].reverse().map(item => {
    const isFill = kind === 'fill';
    const title = isFill ? `${item.side === 'BUY' ? '买入' : '卖出'} ${item.name || item.symbol}` : `${item.side || '拒单'} ${item.name || item.symbol}`;
    const detail = isFill
      ? `${number(item.qty)} 股 · ${number(item.price)} · 手续费 ${money(item.fees)}`
      : `${REJECTION_LABELS[item.reason] || item.reason || '规则拒绝'}${item.target_weight == null ? '' : ` · 目标 ${(Number(item.target_weight) * 100).toFixed(1)}%`}`;
    return `<div class="detail-row"><div class="row"><div class="row-main"><b>${esc(title)}</b><div class="mini">${esc(item.trade_date || item.decision_date || '')}</div></div>${isFill ? `<b>${money(item.gross)}</b>` : '<span class="tag">未成交</span>'}</div><div class="mini">${esc(detail)}</div></div>`;
  }).join('');
}

function concentrationCard(fund) {
  const flags = fund.concentration_flags || [];
  const pendingStats = fund.pending_decision?.portfolio_stats || {};
  const industries = Object.entries(pendingStats.industry_weights || {}).sort((a, b) => b[1] - a[1]);
  const top = industries.slice(0, 3).map(([name, weight]) => `${name} ${(Number(weight) * 100).toFixed(1)}%`).join(' · ');
  if (!flags.length) return `<div class="card alert ok"><div class="alert-title">未触发行业集中告警</div><div class="mini">待执行组合${top ? `：${esc(top)}` : '暂无行业暴露'}</div></div>`;
  return `<div class="card alert"><div class="alert-title">行业集中告警</div><div class="reason">${flags.map(flag => esc(FLAG_LABELS[flag] || flag)).join('；')}</div><div class="mini">待执行组合：${esc(top || '暂无行业明细')}</div></div>`;
}

function rankCards(data, activeId) {
  return FUND_ORDER.map(id => data.funds[id]).sort((a, b) => Number(b.metrics.return_pct) - Number(a.metrics.return_pct)).map((fund, index) => `
    <div class="rank-card ${fund.fund_id === activeId ? 'active' : ''}"><button data-fund="${fund.fund_id}"><small>#${index + 1} · ${fund.fund_id}</small><b>${esc(FUND_SHORT[fund.fund_id])}</b><span class="${tone(fund.metrics.return_pct)}">${pct(fund.metrics.return_pct)}</span><div class="mini">${money(fund.metrics.equity)}</div></button></div>`).join('');
}

function render(data, sourceLabel, activeId) {
  const fund = data.funds[activeId] || data.funds.A;
  const metrics = fund.metrics || {};
  const positionPct = metrics.equity > 0 ? Number(metrics.position_market_value || 0) / Number(metrics.equity) * 100 : 0;
  const flags = fund.concentration_flags || [];
  document.querySelector('#app').innerHTML = `
    <div class="shell">
      <header class="top"><div><div class="eyebrow">EXPERIMENTAL PORTFOLIO</div><div class="brand">V2 影子基金竞技场</div><div class="updated">更新 ${esc(data.updated_at)} · 行情 ${esc(marketSource(data))} · 每只初始 ${money(data.initial_cash_per_fund)}</div></div><span class="live-dot" aria-label="数据可用"></span></header>
      <div class="safety-strip" aria-label="V2 状态"><span>V2 影子盘</span><span>非实盘</span><span>当前未替代 V1</span></div>
      <section class="rank-strip" aria-label="五只基金收益排名">${rankCards(data, fund.fund_id)}</section>
      <nav class="fund-tabs" aria-label="切换 V2 基金">${FUND_ORDER.map(id => `<button class="fund-tab ${id === fund.fund_id ? 'active' : ''}" data-fund="${id}">${id}<br>${FUND_SHORT[id]}</button>`).join('')}</nav>
      <section class="hero">
        <div class="fund-title"><div><span class="fund-id">${fund.fund_id}</span><div class="updated">${esc(fund.name)}</div></div><span class="tag">只读</span></div>
        <div class="equity">${money(metrics.equity)}</div>
        <div class="${tone(metrics.return_pct)}"><b>${pct(metrics.return_pct)}</b> 累计收益</div>
        <div class="stats">
          <div class="stat"><span class="label">现金</span><b>${money(metrics.cash)}</b></div>
          <div class="stat"><span class="label">股票仓位</span><b>${positionPct.toFixed(1)}%</b></div>
          <div class="stat"><span class="label">最大回撤</span><b class="${tone(metrics.max_drawdown_pct)}">${pct(metrics.max_drawdown_pct)}</b></div>
          <div class="stat"><span class="label">交易日</span><b>${number(metrics.trading_days)}</b></div>
        </div>
      </section>
      <section class="section"><h2>市场与风控</h2><div class="market-grid"><div class="card"><span class="label">市场状态</span><div class="market-score">${esc(regime(data.regime?.label))}</div><div class="mini">强度 ${number(data.regime?.score)} · 置信 ${number(data.regime?.confidence)}</div></div><div class="card"><span class="label">执行统计</span><div class="market-score">${number(metrics.fills)} / ${number(metrics.rejected_orders)}</div><div class="mini">成交 / 拒单 · 费用 ${money(metrics.fees)}</div></div></div></section>
      <section class="section"><h2>行业集中</h2>${concentrationCard(fund)}</section>
      <section class="section"><h2>当前持仓 · ${number(metrics.positions)} 只</h2>${holdingRows(fund)}</section>
      <section class="section"><h2>待执行目标 · ${(fund.pending_decision?.targets || []).length} 只</h2><div class="card mini">决策日 ${esc(fund.pending_decision?.decision_date || '暂无')} · 仅在下一交易日开盘按规则模拟执行，不代表已经成交。</div>${pendingRows(fund)}</section>
      <section class="section"><h2>成交与拒单</h2><details class="card" ${metrics.fills ? 'open' : ''}><summary>最近成交 · ${number(metrics.fills)} 笔</summary><div class="detail-body">${eventRows(fund.recent_fills, 'fill')}</div></details><details class="card" ${flags.length || metrics.rejected_orders ? 'open' : ''}><summary>最近拒单 · ${number(metrics.rejected_orders)} 笔</summary><div class="detail-body">${eventRows(fund.recent_rejected_orders, 'reject')}</div></details></section>
      <div class="source-note">数据源：<b>${esc(sourceLabel)}</b>，固定读取 <code>v2-shadow/shadow_state/v2/summary.json</code>；备用文件为本目录 <code>data.json</code>。页面没有写入接口。</div>
    </div>
    <footer class="footer">V2 影子盘 · 不连接券商 · 不调用 Sol · 不读取或修改 V1 正式账本</footer>`;
  document.querySelectorAll('[data-fund]').forEach(button => button.addEventListener('click', () => {
    const nextId = button.dataset.fund;
    sessionStorage.setItem('v2-shadow-fund', nextId);
    render(data, sourceLabel, nextId);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }));
}

loadSummary().then(({ data, source }) => {
  const saved = sessionStorage.getItem('v2-shadow-fund');
  render(data, source, FUND_ORDER.includes(saved) ? saved : 'A');
}).catch(error => {
  document.querySelector('#app').innerHTML = `<div class="error"><b>V2 数据加载失败</b><div class="mini">${esc(error.message)}</div><div class="reason">请稍后刷新。现有 V1 页面和正式账本不受影响。</div></div>`;
});

if ('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js');

