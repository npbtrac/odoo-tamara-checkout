import { patch } from '@web/core/utils/patch';

import { WebsiteSale } from '@website_sale/interactions/website_sale';

/**
 * Tamara's web component only re-renders when `uuid` changes (see TamaraWidgetV2.refresh).
 * Updating `amount` alone has no effect, so we set the new amount then refresh.
 */
function _tamaraRefreshProductWidgets(root, amount) {
    const widgets = root.querySelectorAll('.o_tamara_product_widget tamara-widget');
    if (!widgets.length) {
        return;
    }
    const amountStr = String(amount);
    widgets.forEach((widget) => {
        widget.setAttribute('amount', amountStr);
    });
    if (window.TamaraWidgetV2?.refresh) {
        window.TamaraWidgetV2.refresh();
    } else {
        // Fallback before the CDN script finishes loading: bump uuid to force a re-render.
        widgets.forEach((widget) => {
            widget.setAttribute('uuid', `${Date.now()}-${Math.random().toString(16).slice(2)}`);
        });
    }
}

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
        // `parent` is `.js_product`; also search the product details column as a fallback.
        const root = parent.closest('#product_details') || parent;
        _tamaraRefreshProductWidgets(root, amount);
    },
});
