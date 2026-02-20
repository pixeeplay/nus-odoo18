/** @odoo-module **/

import publicWidget from '@web/legacy/js/public/public_widget';

/**
 * Product Zoom — CSS-based zoom on hover (no external library needed)
 * Hover over product image → zoomed view follows cursor
 */
publicWidget.registry.NovaProductZoom = publicWidget.Widget.extend({
    selector: '#product_detail .o_carousel_product_outer',

    start: function () {
        this._super.apply(this, arguments);
        this._setupZoom();
    },

    _setupZoom: function () {
        const images = this.el.querySelectorAll('.carousel-item img, .product_detail_img');
        images.forEach(img => {
            const container = img.closest('.carousel-item') || img.parentElement;
            container.classList.add('nova-zoom-container');

            container.addEventListener('mousemove', (e) => {
                const rect = container.getBoundingClientRect();
                const x = ((e.clientX - rect.left) / rect.width) * 100;
                const y = ((e.clientY - rect.top) / rect.height) * 100;
                img.style.transformOrigin = `${x}% ${y}%`;
                img.style.transform = 'scale(2)';
            });

            container.addEventListener('mouseleave', () => {
                img.style.transformOrigin = 'center center';
                img.style.transform = 'scale(1)';
            });
        });
    },
});

export default publicWidget.registry.NovaProductZoom;
