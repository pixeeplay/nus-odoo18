/** @odoo-module **/

import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";
import { onWillUnmount } from "@odoo/owl";

/**
 * Custom list controller for prestashop.product.preview that auto-refreshes
 * every 4 seconds so users see products arriving progressively during import.
 */
class ProductPreviewListController extends ListController {
    setup() {
        super.setup();

        // Auto-refresh interval (4 seconds)
        this._refreshInterval = setInterval(async () => {
            try {
                await this.model.load();
            } catch {
                // Silently ignore refresh errors (e.g. view was destroyed)
            }
        }, 4000);

        onWillUnmount(() => {
            if (this._refreshInterval) {
                clearInterval(this._refreshInterval);
                this._refreshInterval = null;
            }
        });
    }
}

const productPreviewListView = {
    ...listView,
    Controller: ProductPreviewListController,
};

registry
    .category("views")
    .add("prestashop_product_preview_list", productPreviewListView);
