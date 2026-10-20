/* Search currently rendered billable module cards; never invent prices. */
(() => {
  const search = document.getElementById('cg-module-search');
  const empty = document.getElementById('cg-module-no-results');
  const cards = [...document.querySelectorAll('#cg-module-grid .module-pricing-card')];
  if (!search || !empty) return;
  const update = () => {
    const term = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const card of cards) {
      const matched = !term || (card.dataset.moduleSearch || '').includes(term);
      card.hidden = !matched;
      if (matched) visible += 1;
    }
    empty.hidden = visible !== 0;
  };
  search.addEventListener('input', update);
  update();

  // Module billing periods are rate units, not collection dates.
  // All published periods can be used with monthly settlement; the server
  // continues to validate permissions and prevent conflicting billing engines.
})();
