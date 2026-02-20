/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { jsonrpc } from '@web/core/network/rpc';

publicWidget.registry.NovaProducts = publicWidget.Widget.extend({
    selector: '.s_nova_products',

    start: async function () {
        await this._super(...arguments);
        const container = this.el.querySelector('.nova-products-grid');
        if (!container) return;

        // Read configuration from data attributes
        const categoryId = this.el.dataset.categoryId || null;
        const limit = parseInt(this.el.dataset.limit || 8);
        const order = this.el.dataset.order || 'website_sequence';

        try {
            const products = await jsonrpc('/theme_nova/get_products', {
                category_id: categoryId,
                limit: limit,
                order: order,
            });

            if (products && products.length) {
                container.innerHTML = products.map((p, i) => this._renderCard(p, i)).join('');
                this._animateCards(container);
            } else {
                container.innerHTML = `
                    <div class="col-12 text-center py-4 text-muted">
                        <i class="fa fa-shopping-bag fa-2x mb-2 d-block"></i>
                        <p>No products available</p>
                    </div>
                `;
            }
        } catch (e) {
            // Keep static content as fallback
        }
    },

    _renderCard: function (product, index) {
        const priceHtml = this._formatPrice(product);
        const labelHtml = product.label
            ? `<span class="nova-product-label nova-label-badge" style="background-color: ${product.label_bg}; color: ${product.label_color};">${product.label}</span>`
            : '';

        return `
            <div class="col-6 col-md-4 col-lg-3 mb-4">
                <div class="nova-product-card card h-100 border-0 shadow-sm">
                    <div class="position-relative overflow-hidden">
                        ${labelHtml}
                        <a href="${product.url}">
                            <img src="${product.image_url}" class="card-img-top" alt="${product.name}"
                                 style="aspect-ratio: 1; object-fit: cover;" loading="lazy"/>
                        </a>
                        <button type="button" class="nova-quick-view-btn" data-product-id="${product.id}" title="Quick View">
                            <i class="fa fa-eye"></i>
                        </button>
                    </div>
                    <div class="card-body p-3">
                        <h6 class="card-title mb-1">
                            <a href="${product.url}" class="text-decoration-none" style="color: var(--o-color-5);">${product.name}</a>
                        </h6>
                        <div class="fw-bold" style="color: var(--o-color-1);">${priceHtml}</div>
                    </div>
                </div>
            </div>
        `;
    },

    _formatPrice: function (product) {
        const format = (val) => {
            const num = parseFloat(val).toFixed(2);
            return product.currency_position === 'before'
                ? `${product.currency_symbol}${num}`
                : `${num}${product.currency_symbol}`;
        };

        if (product.has_discount) {
            return `<span>${format(product.price)}</span> <del class="text-muted small ms-1">${format(product.list_price)}</del>`;
        }
        return format(product.price);
    },

    _animateCards: function (container) {
        if (!('IntersectionObserver' in window)) return;

        const cards = container.querySelectorAll('.nova-product-card');
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry, idx) => {
                if (entry.isIntersecting) {
                    setTimeout(() => {
                        entry.target.style.opacity = '1';
                        entry.target.style.transform = 'translateY(0)';
                    }, idx * 80);
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });

        cards.forEach(card => {
            card.style.opacity = '0';
            card.style.transform = 'translateY(20px)';
            card.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
            observer.observe(card);
        });
    },
});
