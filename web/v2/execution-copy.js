(() => {
  const replacements = [
    ['仅在下一交易日开盘按规则模拟执行，不代表已经成交。', '卖出/减仓在下一交易日09:40模拟执行；买入/加仓按当日收盘价模拟执行，不代表已经真实成交。'],
    ['下一交易日开盘按规则模拟执行', '下一交易日09:40先卖出/减仓，买入/加仓按收盘价模拟执行'],
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
