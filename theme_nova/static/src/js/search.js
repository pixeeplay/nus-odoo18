/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { jsonrpc } from '@web/core/network/rpc';

publicWidget.registry.NovaSearchAutocomplete = publicWidget.Widget.extend({
    selector: 'header .o_searchbar_form, header form[action="/shop"]',
    events: {
        'input input[name="search"]': '_onInput',
        'focus input[name="search"]': '_onFocus',
        'keydown input[name="search"]': '_onKeydown',
    },

    start() {
        this._super(...arguments);
        this._debounceTimer = null;
        this._minChars = 2;
        this._createDropdown();

        // Close dropdown on outside click
        this._outsideClickHandler = (e) => {
            if (!this.el.contains(e.target)) {
                this._hideDropdown();
            }
        };
        document.addEventListener('click', this._outsideClickHandler);
    },

    _createDropdown() {
        this.el.classList.add('nova-search-wrap');
        this.dropdown = document.createElement('div');
        this.dropdown.className = 'nova-search-dropdown';
        this.el.appendChild(this.dropdown);
    },

    _onInput(ev) {
        const query = ev.target.value.trim();
        clearTimeout(this._debounceTimer);

        if (query.length < this._minChars) {
            this._hideDropdown();
            return;
        }

        this._debounceTimer = setTimeout(() => this._search(query), 300);
    },

    _onFocus(ev) {
        const query = ev.target.value.trim();
        if (query.length >= this._minChars && this.dropdown.innerHTML) {
            this._showDropdown();
        }
    },

    _onKeydown(ev) {
        if (ev.key === 'Escape') {
            this._hideDropdown();
        }
    },

    async _search(query) {
        try {
            const data = await jsonrpc('/theme_nova/search_autocomplete', {
                query: query,
                limit: 6,
            });

            if (!data || (!data.products.length && !data.categories.length)) {
                this.dropdown.innerHTML = `
                    <div class="nova-search-empty">
                        No results for "<strong>${query}</strong>"
                    </div>
                `;
                this._showDropdown();
                return;
            }

            let html = '';

            // Categories
            if (data.categories.length) {
                html += '<div class="nova-search-category">Categories</div>';
                data.categories.forEach(cat => {
                    html += `
                        <a href="/shop?category=${cat.id}" class="nova-search-result">
                            <i class="fa fa-folder-o" style="width:44px;text-align:center;font-size:1.2rem;color:#999;"></i>
                            <div class="nova-search-info">
                                <div class="nova-search-name">${cat.name}</div>
                            </div>
                        </a>
                    `;
                });
            }

            // Products
            if (data.products.length) {
                html += '<div class="nova-search-category">Products</div>';
                data.products.forEach(p => {
                    html += `
                        <a href="${p.url}" class="nova-search-result">
                            <img src="${p.image_url}" alt="${p.name}" loading="lazy"/>
                            <div class="nova-search-info">
                                <div class="nova-search-name">${p.name}</div>
                                <div class="nova-search-price">${p.price_formatted}</div>
                            </div>
                        </a>
                    `;
                });
            }

            // Footer
            html += `
                <div class="nova-search-footer">
                    <a href="/shop?search=${encodeURIComponent(query)}">
                        View all results <i class="fa fa-arrow-right ms-1"></i>
                    </a>
                </div>
            `;

            this.dropdown.innerHTML = html;
            this._showDropdown();
        } catch (e) {
            // Silently fail — standard search still works
        }
    },

    _showDropdown() {
        this.dropdown.classList.add('show');
    },

    _hideDropdown() {
        this.dropdown.classList.remove('show');
    },

    destroy() {
        clearTimeout(this._debounceTimer);
        document.removeEventListener('click', this._outsideClickHandler);
        if (this.dropdown) {
            this.dropdown.remove();
        }
        this._super(...arguments);
    },
});
