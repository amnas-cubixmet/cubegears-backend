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

  // Prevent an invalid Weekly/Daily/etc. + Monthly Postpaid combination
  // before POST. Server validation remains authoritative.
  for (const card of cards) {
    const period = card.querySelector('select[name="billing_type"]');
    const postpaid = card.querySelector('input[type="checkbox"][name="monthly_postpaid"]');
    if (!period || !postpaid) continue; // Purchased/metered fields are read-only.
    const validatePeriod = () => {
      const incompatible = postpaid.checked && period.value !== 'monthly';
      period.setCustomValidity(
        incompatible ? 'Pay next month requires Monthly billing. Choose Monthly or turn off Pay next month.' : ''
      );
    };
    period.addEventListener('change', validatePeriod);
    postpaid.addEventListener('change', validatePeriod);
    card.querySelector('form')?.addEventListener('submit', validatePeriod);
    validatePeriod();
  }
})();
