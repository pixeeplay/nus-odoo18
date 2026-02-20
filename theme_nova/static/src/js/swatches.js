/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { jsonrpc } from '@web/core/network/rpc';

publicWidget.registry.NovaSwatches = publicWidget.Widget.extend({
    selector: '.oe_product',
    events: {
        'mouseenter .nova-swatch': '_onSwatchHover',
        'mouseleave .nova-swatch': '_onSwatchLeave',
    },

    start() {
        this._super(...arguments);
        this._originalImage = null;
        this._loadSwatches();
    },

    async _loadSwatches() {
        const productLink = this.el.querySelector('.oe_product_image a[href]');
        if (!productLink) return;

        // Extract product ID from URL like /shop/product-slug-42
        const href = productLink.getAttribute('href');
        const match = href.match(/-(\d+)(?:$|[?#])/);
        if (!match) return;

        const productId = parseInt(match[1]);
        try {
            const swatches = await jsonrpc('/theme_nova/get_swatches', {
                product_id: productId,
            });

            if (swatches && swatches.length > 1) {
                this._renderSwatches(swatches);
            }
        } catch (e) {
            // Silently fail
        }
    },

    _renderSwatches(swatches) {
        const details = this.el.querySelector('.oe_product_details');
        if (!details) return;

        const container = document.createElement('div');
        container.className = 'nova-swatches d-flex gap-1 mt-1';

        swatches.slice(0, 6).forEach(swatch => {
            const dot = document.createElement('span');
            dot.className = 'nova-swatch';
            dot.title = swatch.name;

            if (swatch.html_color) {
                dot.style.backgroundColor = swatch.html_color;
            } else if (swatch.image_url) {
                dot.style.backgroundImage = `url(${swatch.image_url})`;
                dot.style.backgroundSize = 'cover';
            } else {
                dot.textContent = swatch.name.charAt(0);
                dot.classList.add('nova-swatch-text');
            }

            if (swatch.product_image_url) {
                dot.dataset.imageUrl = swatch.product_image_url;
            }
            container.appendChild(dot);
        });

        if (swatches.length > 6) {
            const more = document.createElement('span');
            more.className = 'nova-swatch nova-swatch-more';
            more.textContent = `+${swatches.length - 6}`;
            container.appendChild(more);
        }

        // Insert before product details (after name/price area)
        details.appendChild(container);
    },

    _onSwatchHover(ev) {
        const imageUrl = ev.target.dataset.imageUrl;
        if (!imageUrl) return;

        const img = this.el.querySelector('.oe_product_image img');
        if (!img) return;

        if (!this._originalImage) {
            this._originalImage = img.src;
        }
        img.src = imageUrl;

        // Mark active swatch
        this.el.querySelectorAll('.nova-swatch').forEach(s => s.classList.remove('active'));
        ev.target.classList.add('active');
    },

    _onSwatchLeave(ev) {
        if (!this._originalImage) return;

        const img = this.el.querySelector('.oe_product_image img');
        if (img) {
            img.src = this._originalImage;
        }

        this.el.querySelectorAll('.nova-swatch').forEach(s => s.classList.remove('active'));
    },
});
