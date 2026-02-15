/** @odoo-module **/
import { paymentExpressCheckoutForm } from '@payment/js/express_checkout_form';

paymentExpressCheckoutForm.include({
    _prepareTransactionRouteParams() {
        if (this.paymentContext.providerCode !== 'alma') {
            return this._super(...arguments);
        } else {
            const values = this._super(...arguments);
            values.alma_options = document.getElementById('alma_options').value;
            return values;
        }
    }
})