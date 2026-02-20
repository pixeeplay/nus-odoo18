/** @odoo-module **/

import publicWidget from '@web/legacy/js/public/public_widget';

/**
 * Sticky Add-to-Cart bar — appears when the main add-to-cart button scrolls out of view
 */
publicWidget.registry.NovaStickyCart = publicWidget.Widget.extend({
    selector: '#nova_sticky_cart',

    start: function () {
        this._super.apply(this, arguments);
        this.addToCartBtn = document.getElementById('add_to_cart');
        if (!this.addToCartBtn) return;

        // Sync price from the product page
        this._syncPrice();

        // Observe when main add-to-cart goes out of view
        this.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    this.el.classList.add('d-none');
                    this.el.classList.remove('nova-sticky-visible');
                } else {
                    this.el.classList.remove('d-none');
                    this.el.classList.add('nova-sticky-visible');
                }
            });
        }, { threshold: 0 });

        this.observer.observe(this.addToCartBtn);
    },

    _syncPrice: function () {
        const priceEl = document.querySelector('.product_price .oe_price .oe_currency_value');
        const stickyPriceEl = document.getElementById('nova_sticky_price');
        if (priceEl && stickyPriceEl) {
            stickyPriceEl.textContent = priceEl.textContent;

            // Watch for price changes (variant switches)
            this.priceObserver = new MutationObserver(() => {
                stickyPriceEl.textContent = priceEl.textContent;
            });
            this.priceObserver.observe(priceEl, { childList: true, characterData: true, subtree: true });
        }
    },

    destroy: function () {
        if (this.observer) this.observer.disconnect();
        if (this.priceObserver) this.priceObserver.disconnect();
        this._super.apply(this, arguments);
    },
});

export default publicWidget.registry.NovaStickyCart;
