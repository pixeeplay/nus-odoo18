/** @odoo-module **/
import paymentForm from '@payment/js/payment_form';

paymentForm.include({
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
