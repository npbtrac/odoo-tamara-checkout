from odoo.http import request

from odoo.addons.payment import utils as payment_utils
from odoo.addons.website_sale.controllers.main import WebsiteSale


class WebsiteSaleTamara(WebsiteSale):
    """Ensure Tamara eligibility uses the billing (invoice) partner on /shop/payment."""

    def _get_shop_payment_values(self, order, **kwargs):
        values = super()._get_shop_payment_values(order, **kwargs)
        invoice_partner = order.partner_invoice_id
        if invoice_partner and values.get('partner_id') != invoice_partner.id:
            values['partner_id'] = invoice_partner.id
            # Access token is partner-scoped; regenerate when we switch to the invoice partner.
            amount = values.get('amount')
            currency = values.get('currency')
            currency_id = currency.id if currency else values.get('currency_id')
            if amount is not None and currency_id:
                values['access_token'] = payment_utils.generate_access_token(
                    invoice_partner.id, amount, currency_id,
                )
        values['sale_order_id'] = order.id
        return values
