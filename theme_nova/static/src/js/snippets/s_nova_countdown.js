/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.NovaCountdown = publicWidget.Widget.extend({
    selector: '.s_nova_countdown',

    start() {
        this._super(...arguments);
        this.timerEl = this.el.querySelector('.nova-countdown-timer');
        if (!this.timerEl) return;
        this.targetDate = new Date(this.timerEl.dataset.novaCountdownTarget || '2025-12-31T23:59:59');
        this._updateCountdown();
        this.interval = setInterval(() => this._updateCountdown(), 1000);
    },

    destroy() {
        if (this.interval) clearInterval(this.interval);
        this._super(...arguments);
    },

    _updateCountdown() {
        const now = new Date();
        const diff = Math.max(0, this.targetDate - now);

        const days = Math.floor(diff / (1000 * 60 * 60 * 24));
        const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
        const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((diff % (1000 * 60)) / 1000);

        this._setVal('days', String(days).padStart(2, '0'));
        this._setVal('hours', String(hours).padStart(2, '0'));
        this._setVal('minutes', String(minutes).padStart(2, '0'));
        this._setVal('seconds', String(seconds).padStart(2, '0'));

        if (diff <= 0 && this.interval) {
            clearInterval(this.interval);
        }
    },

    _setVal(key, val) {
        const el = this.timerEl.querySelector(`[data-nova-cd="${key}"]`);
        if (el) el.textContent = val;
    },
});
