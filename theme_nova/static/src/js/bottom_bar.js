/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.NovaBottomBar = publicWidget.Widget.extend({
    selector: '.nova-bottom-bar',

    start() {
        this._super(...arguments);
        this._highlightActive();
        this._setupScrollHide();
        this._setupSearch();
    },

    /**
     * Highlight the current page's tab.
     */
    _highlightActive() {
        const path = window.location.pathname;
        let active = 'home';
        if (path.startsWith('/shop/cart') || path.startsWith('/shop/checkout')) {
            active = 'cart';
        } else if (path.startsWith('/shop')) {
            active = 'shop';
        } else if (path.startsWith('/my')) {
            active = 'account';
        } else if (path === '/') {
            active = 'home';
        }
        const item = this.el.querySelector(`[data-nova-bar="${active}"]`);
        if (item) {
            item.classList.add('active');
        }
    },

    /**
     * Hide the bar when scrolling down, show on scroll up.
     */
    _setupScrollHide() {
        let lastScroll = 0;
        const bar = this.el;
        this._scrollHandler = () => {
            const currentScroll = window.scrollY;
            if (currentScroll > lastScroll && currentScroll > 100) {
                bar.classList.add('nova-bar-hidden');
            } else {
                bar.classList.remove('nova-bar-hidden');
            }
            lastScroll = currentScroll;
        };
        window.addEventListener('scroll', this._scrollHandler, { passive: true });
    },

    /**
     * Open a search overlay when tapping the search button.
     */
    _setupSearch() {
        const searchBtn = this.el.querySelector('.nova-bottom-bar-search');
        if (!searchBtn) return;

        searchBtn.addEventListener('click', () => {
            let overlay = document.querySelector('.nova-mobile-search-overlay');
            if (!overlay) {
                overlay = document.createElement('div');
                overlay.className = 'nova-mobile-search-overlay';
                overlay.innerHTML = `
                    <div class="nova-mobile-search-header">
                        <input type="text" placeholder="Search products..." autocomplete="off"/>
                        <button type="button" class="nova-close-search"><i class="fa fa-times"></i></button>
                    </div>
                    <div class="nova-mobile-search-results"></div>
                `;
                document.body.appendChild(overlay);

                // Close button
                overlay.querySelector('.nova-close-search').addEventListener('click', () => {
                    overlay.classList.remove('show');
                });

                // Search input → redirect to /shop with search param
                const input = overlay.querySelector('input');
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' && input.value.trim()) {
                        window.location.href = `/shop?search=${encodeURIComponent(input.value.trim())}`;
                    }
                });
            }

            overlay.classList.add('show');
            setTimeout(() => overlay.querySelector('input').focus(), 100);
        });
    },

    destroy() {
        if (this._scrollHandler) {
            window.removeEventListener('scroll', this._scrollHandler);
        }
        this._super(...arguments);
    },
});
