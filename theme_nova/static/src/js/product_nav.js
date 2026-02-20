/** @odoo-module **/

import publicWidget from '@web/legacy/js/public/public_widget';
import { jsonrpc } from '@web/core/network/rpc';

/**
 * Product Navigation — Prev/Next arrows on product page
 */
publicWidget.registry.NovaProductNav = publicWidget.Widget.extend({
    selector: '.nova-product-nav',

    start: async function () {
        await this._super.apply(this, arguments);

        // Get product ID from the page
        const productDetail = document.getElementById('product_detail');
        if (!productDetail) return;

        const productInput = productDetail.querySelector('input[name="product_id"], .product_template_id');
        let productId = null;

        // Try to get product_template_id from the form
        const tmplInput = document.querySelector('.js_product input[name="csrf_token"]');
        const form = tmplInput && tmplInput.closest('form');
        if (form) {
            const action = form.getAttribute('action');
            // Also try to find it from data attributes
            const jsProduct = document.querySelector('.js_product');
            if (jsProduct) {
                productId = parseInt(jsProduct.dataset.productTemplateId);
            }
        }

        // Fallback: get from URL or meta
        if (!productId) {
            const match = window.location.pathname.match(/\/shop\/([^/]+)-(\d+)/);
            if (match) {
                productId = parseInt(match[2]);
            }
        }

        if (!productId) return;

        try {
            const result = await jsonrpc('/theme_nova/product_nav', {
                product_id: productId,
            });

            const prevBtn = this.el.querySelector('.nova-nav-prev');
            const nextBtn = this.el.querySelector('.nova-nav-next');

            if (result.prev && prevBtn) {
                prevBtn.href = result.prev.url;
                prevBtn.classList.remove('disabled');
                prevBtn.title = result.prev.name;
            }
            if (result.next && nextBtn) {
                nextBtn.href = result.next.url;
                nextBtn.classList.remove('disabled');
                nextBtn.title = result.next.name;
            }
        } catch (e) {
            // Silently fail — navigation is non-critical
        }
    },
});

export default publicWidget.registry.NovaProductNav;
