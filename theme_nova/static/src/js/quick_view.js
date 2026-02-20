/** @odoo-module **/

import publicWidget from '@web/legacy/js/public/public_widget';
import { jsonrpc } from '@web/core/network/rpc';
import { Markup } from 'web.utils';

/**
 * Quick View — Click eye icon on product card → opens modal with product info
 */
publicWidget.registry.NovaQuickView = publicWidget.Widget.extend({
    selector: '.nova-quick-view-btn',
    events: {
        'click': '_onClick',
    },

    _onClick: async function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const productId = parseInt(ev.currentTarget.dataset.productId);
        if (!productId) return;

        // Fetch quick view HTML
        const html = await jsonrpc('/theme_nova/quick_view', {
            product_id: productId,
        });
        if (!html) return;

        // Create and show Bootstrap modal
        const modalEl = document.createElement('div');
        modalEl.className = 'modal fade nova-quick-view-modal';
        modalEl.setAttribute('tabindex', '-1');
        modalEl.innerHTML = `
            <div class="modal-dialog modal-lg modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header border-0 pb-0">
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body pt-0 px-4 pb-4">
                        ${html}
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modalEl);

        // Use Bootstrap's Modal from the global scope (loaded by Odoo)
        const BsModal = window.bootstrap && window.bootstrap.Modal
            ? window.bootstrap.Modal
            : Modal;
        const modal = new BsModal(modalEl, { focus: true });
        modal.show();

        // Cleanup on close
        modalEl.addEventListener('hidden.bs.modal', () => {
            modal.dispose();
            modalEl.remove();
        });
    },
});

export default publicWidget.registry.NovaQuickView;
