/** @odoo-module **/

// Intercept Enter key on the PM search wizard to trigger search instead of form save
document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    const target = ev.target;
    if (!target || !target.closest) return;

    // Only intercept if we're in the search_query field of the PM wizard
    const field = target.closest('.o_field_widget[name="search_query"]');
    if (!field) return;

    ev.preventDefault();
    ev.stopPropagation();

    // Find and click the Search button in the form header
    const form = target.closest('.o_form_view') || document;
    const btn = form.querySelector('button[name="action_search"]');
    if (btn) btn.click();
}, true);
