(() => {
  const replacements = [
    ['下一交易日开盘模拟执行', '09:40先卖出/减仓；买入/加仓收盘模拟执行'],
    ['今天收盘生成的是下一交易日目标仓位；点开基金详情后会直接标明每只股票明天是买入、卖出、加仓、减仓还是持有。', '今天收盘生成下一交易日目标仓位；卖出/减仓在次日09:40模拟执行，买入/加仓按当日收盘价模拟执行。'],
  ];
  const rewrite = root => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      let text = node.nodeValue;
      replacements.forEach(([from, to]) => { text = text.replaceAll(from, to); });
      node.nodeValue = text;
    });
  };
  const app = document.querySelector('#app');
  if (!app) return;
  rewrite(app);
  new MutationObserver(() => rewrite(app)).observe(app, { childList: true, subtree: true });
})();
