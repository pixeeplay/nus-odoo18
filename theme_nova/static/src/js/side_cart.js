/** @odoo-module **/

import publicWidget from '@web/legacy/js/public/public_widget';

/**
 * Side Cart — Offcanvas cart panel that slides in from the right
 */
publicWidget.registry.NovaSideCart = publicWidget.Widget.extend({
    selector: '#wrapwrap',
    events: {
        'click .nova-side-cart-trigger, a[href="/shop/cart"][data-nova-sidecart]': '_onOpenCart',
    },

    start: function () {
        this._super.apply(this, arguments);
        this._injectSideCart();
        this._hijackCartLinks();
    },

    _hijackCartLinks: function () {
        // Add data attribute to the main cart link in navbar
        const cartLink = document.querySelector('header a[href="/shop/cart"]');
        if (cartLink) {
            cartLink.setAttribute('data-nova-sidecart', '1');
        }
    },

    _injectSideCart: function () {
        if (document.getElementById('nova-side-cart')) return;

        const offcanvas = document.createElement('div');
        offcanvas.id = 'nova-side-cart';
        offcanvas.className = 'offcanvas offcanvas-end nova-side-cart';
        offcanvas.setAttribute('tabindex', '-1');
        offcanvas.innerHTML = `
            <div class="offcanvas-header border-bottom">
                <h5 class="offcanvas-title fw-bold">
                    <i class="fa fa-shopping-bag me-2"></i>Your Cart
                </h5>
                <button type="button" class="btn-close" data-bs-dismiss="offcanvas" aria-label="Close"></button>
            </div>
            <div class="offcanvas-body p-0">
                <div class="nova-side-cart-loading text-center py-5">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">Loading...</span>
                    </div>
                </div>
                <div class="nova-side-cart-content"></div>
                <div class="nova-side-cart-empty text-center py-5 d-none">
                    <i class="fa fa-shopping-cart fa-3x text-muted mb-3 d-block"></i>
                    <p class="text-muted">Your cart is empty</p>
                    <a href="/shop" class="btn btn-primary">Continue Shopping</a>
                </div>
            </div>
            <div class="offcanvas-footer nova-side-cart-footer border-top p-3 d-none">
                <div class="d-flex justify-content-between mb-3">
                    <span class="fw-bold">Subtotal</span>
                    <span class="fw-bold nova-side-cart-total"></span>
                </div>
                <a href="/shop/cart" class="btn btn-outline-secondary w-100 mb-2">View Cart</a>
                <a href="/shop/checkout?express=1" class="btn btn-primary w-100">Checkout</a>
            </div>
        `;
        document.body.appendChild(offcanvas);
    },

    _onOpenCart: function (ev) {
        // Don't hijack on the cart page itself
        if (window.location.pathname === '/shop/cart') return;

        ev.preventDefault();
        ev.stopPropagation();

        const offcanvasEl = document.getElementById('nova-side-cart');
        if (!offcanvasEl) return;

        const BsOffcanvas = window.bootstrap && window.bootstrap.Offcanvas
            ? window.bootstrap.Offcanvas
            : Offcanvas;

        let instance = BsOffcanvas.getInstance(offcanvasEl);
        if (!instance) {
            instance = new BsOffcanvas(offcanvasEl);
        }
        instance.show();

        this._loadCartContent();
    },

    _loadCartContent: async function () {
        const contentEl = document.querySelector('.nova-side-cart-content');
        const loadingEl = document.querySelector('.nova-side-cart-loading');
        const emptyEl = document.querySelector('.nova-side-cart-empty');
        const footerEl = document.querySelector('.nova-side-cart-footer');

        if (!contentEl) return;

        loadingEl.classList.remove('d-none');
        contentEl.innerHTML = '';
        emptyEl.classList.add('d-none');
        footerEl.classList.add('d-none');

        try {
            const response = await fetch('/shop/cart', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            // Extract cart items
            const cartTable = doc.querySelector('.oe_cart table tbody');
            const totalEl = doc.querySelector('.oe_cart .oe_currency_value');

            loadingEl.classList.add('d-none');

            if (cartTable && cartTable.children.length > 0) {
                // Build simplified cart items
                const rows = cartTable.querySelectorAll('tr');
                let itemsHtml = '<div class="nova-side-cart-items">';

                rows.forEach(row => {
                    const img = row.querySelector('img');
                    const nameEl = row.querySelector('.td-product_name a, td a[href*="/shop/"]');
                    const priceEl = row.querySelector('.oe_currency_value');
                    const qtyEl = row.querySelector('.quantity');

                    if (nameEl) {
                        const imgSrc = img ? img.src : '';
                        const name = nameEl.textContent.trim();
                        const price = priceEl ? priceEl.textContent.trim() : '';
                        const qty = qtyEl ? qtyEl.value : '1';

                        itemsHtml += `
                            <div class="nova-side-cart-item d-flex gap-3 p-3 border-bottom">
                                ${imgSrc ? `<img src="${imgSrc}" class="rounded" width="64" height="64" style="object-fit: cover;">` : ''}
                                <div class="flex-grow-1">
                                    <h6 class="mb-1 small fw-semibold">${name}</h6>
                                    <div class="text-muted small">Qty: ${qty}</div>
                                </div>
                                <div class="fw-bold small text-nowrap">${price}</div>
                            </div>
                        `;
                    }
                });
                itemsHtml += '</div>';
                contentEl.innerHTML = itemsHtml;

                // Show footer with total
                if (totalEl) {
                    document.querySelector('.nova-side-cart-total').textContent = totalEl.textContent;
                }
                footerEl.classList.remove('d-none');
            } else {
                emptyEl.classList.remove('d-none');
            }
        } catch (e) {
            loadingEl.classList.add('d-none');
            emptyEl.classList.remove('d-none');
        }
    },
});

export default publicWidget.registry.NovaSideCart;
