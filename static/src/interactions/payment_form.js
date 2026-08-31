import { rpc } from '@web/core/network/rpc';
import { patch } from '@web/core/utils/patch';

import { PaymentForm } from '@payment/interactions/payment_form';

patch(PaymentForm.prototype, {

    async willStart() {
        await super.willStart(...arguments);
        await this.waitFor(this._tamaraRefreshEligibility());
    },

    /**
     * Re-check Tamara pre-checkout eligibility and hide/show the payment option.
     *
     * Calls Tamara eligibility with the configured timeout so the payment method
     * visibility reflects the API when phone + email are present.
     * See https://docs.tamara.co/reference/pre-checkout-eligibility
     *
     * @private
     * @return {Promise<void>}
     */
    async _tamaraRefreshEligibility() {
        const tamaraRadio = this.el.querySelector(
            'input[name="o_payment_radio"][data-provider-code="tamara"]'
        );
        if (!tamaraRadio) {
            return;
        }
        const providerId = parseInt(tamaraRadio.dataset.providerId);
        const amount = parseFloat(this.paymentContext['amount'] || 0);
        const currencyId = parseInt(this.paymentContext['currencyId'] || 0);
        const partnerId = parseInt(this.paymentContext['partnerId'] || 0);
        const saleOrderId = parseInt(
            this.paymentContext['saleOrderId'] || this.el.dataset.saleOrderId || 0
        );
        if (!providerId || !currencyId) {
            return;
        }

        let isEligible = true;
        try {
            const result = await this.waitFor(rpc('/payment/tamara/eligibility', {
                provider_id: providerId,
                amount: amount,
                currency_id: currencyId,
                partner_id: partnerId || null,
                sale_order_id: saleOrderId || null,
            }));
            isEligible = Boolean(result && result.is_eligible);
        } catch {
            isEligible = true; // Fail open per Tamara guidance.
        }

        const optionEl = tamaraRadio.closest('[name="o_payment_option"]');
        if (!optionEl) {
            return;
        }
        optionEl.classList.toggle('d-none', !isEligible);
        if (!isEligible && tamaraRadio.checked) {
            tamaraRadio.checked = false;
            const fallback = this.el.querySelector(
                'input[name="o_payment_radio"]:not([data-provider-code="tamara"])'
            );
            if (fallback) {
                fallback.checked = true;
                fallback.dispatchEvent(new Event('change'));
            }
        }
    },
});
