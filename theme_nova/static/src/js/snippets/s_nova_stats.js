/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.NovaStats = publicWidget.Widget.extend({
    selector: '.s_nova_stats',

    start() {
        this._super(...arguments);
        this._animated = false;
        this._setupObserver();
    },

    _setupObserver() {
        if (!('IntersectionObserver' in window)) {
            this._animateCounters();
            return;
        }

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting && !this._animated) {
                    this._animated = true;
                    this._animateCounters();
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.3 });

        observer.observe(this.el);
    },

    _animateCounters() {
        const counters = this.el.querySelectorAll('.nova-stat-value[data-target]');
        const duration = 2000; // 2 seconds

        counters.forEach(counter => {
            const target = parseInt(counter.dataset.target, 10);
            const start = 0;
            const startTime = performance.now();

            const update = (currentTime) => {
                const elapsed = currentTime - startTime;
                const progress = Math.min(elapsed / duration, 1);

                // Ease-out cubic
                const eased = 1 - Math.pow(1 - progress, 3);
                const current = Math.floor(start + (target - start) * eased);

                counter.textContent = current.toLocaleString();

                if (progress < 1) {
                    requestAnimationFrame(update);
                } else {
                    counter.textContent = target.toLocaleString();
                }
            };

            requestAnimationFrame(update);
        });
    },
});
