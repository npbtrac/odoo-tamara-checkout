import { patch } from '@web/core/utils/patch';

import { WebsiteSale } from '@website_sale/interactions/website_sale';

patch(WebsiteSale.prototype, {
    /**
     * Keep Tamara product promo widgets in sync with the selected variant price.
     *
     * @override
     */
    _onChangeCombination(ev, parent, combination) {
        super._onChangeCombination(...arguments);
        const amount = combination?.price;
        if (amount === undefined || amount === null) {
            return;
        }
        parent.querySelectorAll('.o_tamara_product_widget tamara-widget').forEach((widget) => {
            widget.setAttribute('amount', amount);
        });
    },
});
