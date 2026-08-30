from datetime import datetime, timezone

from odoo import _, api, models
from odoo.exceptions import ValidationError
from odoo.tools import urls

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_tamara import const
from odoo.addons.payment_tamara.controllers.main import TamaraController


_logger = get_payment_logger(__name__, const.SENSITIVE_KEYS)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_rendering_values(self, processing_values):
        """Override of `payment` to return Tamara-specific rendering values.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific rendering values
        :rtype: dict
        """
        if self.provider_code != 'tamara':
            return super()._get_specific_rendering_values(processing_values)

        payload = self._tamara_prepare_checkout_payload()
        try:
            checkout_data = self._send_api_request('POST', '/checkout', json=payload)
        except ValidationError as error:
            self._set_error(str(error))
            return {}

        # Store Tamara's order id so return/webhook handling can fetch the live order status.
        self.provider_reference = checkout_data.get('order_id')

        checkout_url = checkout_data.get('checkout_url')
        if not checkout_url:
            self._set_error(_("Tamara did not return a checkout URL."))
            return {}
        return {'api_url': checkout_url}

    def _tamara_prepare_checkout_payload(self):
        """Create the payload for the checkout session request.

        :return: The request payload.
        :rtype: dict
        """
        self.ensure_one()
        base_url = self.provider_id.get_base_url()
        return_url = urls.urljoin(base_url, f'{TamaraController._return_url}?ref={self.reference}')
        webhook_url = urls.urljoin(base_url, TamaraController._webhook_url)
        first_name, last_name = payment_utils.split_partner_name(self.partner_name)
        phone = (self.partner_phone or '').replace(' ', '')
        country_code = self.partner_country_id.code or self.company_id.country_id.code or 'SA'
        lang = self.env.context.get('lang') or 'en_US'
        locale = lang if lang in const.SUPPORTED_LOCALES else (
            'ar_SA' if lang.startswith('ar') else 'en_US'
        )
        money = self._tamara_money(self.amount)

        payload = {
            'order_reference_id': self.reference,
            'order_number': self.reference,
            'total_amount': money,
            'description': self.reference,
            'country_code': country_code,
            'payment_type': 'PAY_BY_INSTALMENTS',
            'instalments': 3,
            'locale': locale,
            'platform': 'Odoo',
            'items': self._tamara_prepare_items(money),
            'consumer': {
                'first_name': first_name or last_name or '',
                'last_name': last_name or first_name or '',
                'phone_number': phone,
                'email': self.partner_email or '',
            },
            'shipping_address': {
                'first_name': first_name or last_name or '',
                'last_name': last_name or first_name or '',
                'line1': self.partner_address or '',
                'city': self.partner_city or '',
                'country_code': country_code,
                'phone_number': phone,
            },
            'tax_amount': self._tamara_money(0),
            'shipping_amount': self._tamara_money(0),
            'merchant_url': {
                'success': return_url,
                'failure': return_url,
                'cancel': return_url,
                'notification': webhook_url,
            },
        }
        if self.partner_zip:
            payload['shipping_address']['postal_code'] = self.partner_zip
        return payload

    def _tamara_prepare_items(self, fallback_money):
        """Build Tamara line items from the related sales order, or a generic item.

        :param dict fallback_money: Amount/currency used when no order lines are available.
        :return: The items payload.
        :rtype: list[dict]
        """
        order_lines = (
            self.sale_order_ids.order_line.filtered(lambda line: not line.display_type)
            if 'sale_order_ids' in self._fields else self.env['payment.transaction']
        )
        if not order_lines:
            return [{
                'reference_id': self.reference,
                'type': 'Physical',
                'name': self.reference,
                'sku': self.reference,
                'quantity': 1,
                'unit_price': fallback_money,
                'total_amount': fallback_money,
            }]

        items = []
        for line in order_lines:
            quantity = line.product_uom_qty or 1
            unit_price = self._tamara_money(line.price_total / quantity if quantity else line.price_total)
            items.append({
                'reference_id': str(line.id),
                'type': 'Physical',
                'name': line.name or line.product_id.display_name or self.reference,
                'sku': line.product_id.default_code or str(line.id),
                'quantity': int(quantity) or 1,
                'unit_price': unit_price,
                'total_amount': self._tamara_money(line.price_total),
            })
        return items

    def _tamara_money(self, amount):
        """Return a Tamara money object for the transaction currency.

        :param float amount: The amount in major currency units.
        :return: The money payload.
        :rtype: dict
        """
        return {
            'amount': float(amount),
            'currency': self.currency_id.name,
        }

    def _tamara_fetch_order(self):
        """Fetch the Tamara order linked to this transaction.

        :return: The order data.
        :rtype: dict
        """
        self.ensure_one()
        if not self.provider_reference:
            raise ValidationError(_("The Tamara order id is missing."))
        return self._send_api_request('GET', f'/merchants/orders/{self.provider_reference}')

    def _tamara_authorise_if_needed(self, order_data):
        """Authorise the Tamara order when it is in the `approved` state.

        :param dict order_data: The latest Tamara order data.
        :return: The order data after a possible authorisation.
        :rtype: dict
        """
        self.ensure_one()
        if order_data.get('status') != 'approved':
            return order_data
        self._send_api_request('POST', f'/orders/{self.provider_reference}/authorise')
        return self._tamara_fetch_order()

    def _send_capture_request(self):
        """Override of `payment` to send a capture request to Tamara."""
        if self.provider_code != 'tamara':
            return super()._send_capture_request()

        source_tx = self.source_transaction_id
        payload = {
            'order_id': source_tx.provider_reference,
            'total_amount': self._tamara_money(self.amount),
            'shipping_info': {
                'shipped_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'shipping_company': self.company_id.name or 'Odoo',
                'tracking_number': source_tx.reference,
            },
        }
        capture_data = self._send_api_request('POST', '/payments/capture', json=payload)
        self._process('tamara', capture_data)

    def _send_void_request(self):
        """Override of `payment` to cancel an authorised Tamara order."""
        if self.provider_code != 'tamara':
            return super()._send_void_request()

        source_tx = self.source_transaction_id
        payload = {'total_amount': self._tamara_money(self.amount)}
        cancel_data = self._send_api_request(
            'POST', f'/orders/{source_tx.provider_reference}/cancel', json=payload
        )
        self._process('tamara', cancel_data)

    def _send_refund_request(self):
        """Override of `payment` to send a refund request to Tamara."""
        if self.provider_code != 'tamara':
            return super()._send_refund_request()

        source_tx = self.source_transaction_id
        payload = {
            'total_amount': self._tamara_money(-self.amount),  # Refund txs store a negative amount.
            'comment': _("Refund for transaction %s", source_tx.reference),
            'merchant_refund_id': self.reference,
        }
        refund_data = self._send_api_request(
            'POST', f'/payments/simplified-refund/{source_tx.provider_reference}', json=payload
        )
        self._process('tamara', refund_data)

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        """Override of `payment` to extract the merchant reference from the payment data."""
        if provider_code != 'tamara':
            return super()._extract_reference(provider_code, payment_data)
        return (
            payment_data.get('ref')
            or payment_data.get('order_reference_id')
            or payment_data.get('order_number')
        )

    def _extract_amount_data(self, payment_data):
        """Override of `payment` to extract the amount and currency from the payment data."""
        if self.provider_code != 'tamara':
            return super()._extract_amount_data(payment_data)

        amount_data = (
            payment_data.get('total_amount')
            or payment_data.get('captured_amount')
            or payment_data.get('refunded_amount')
            or {}
        )
        amount = amount_data.get('amount')
        currency_code = amount_data.get('currency')
        if amount is None or not currency_code:
            return None
        return {
            'amount': abs(float(amount)),
            'currency_code': currency_code,
        }

    def _apply_updates(self, payment_data):
        """Override of `payment` to update the transaction based on the Tamara order status."""
        if self.provider_code != 'tamara':
            return super()._apply_updates(payment_data)

        payment_status = (payment_data.get('status') or '').lower()
        if payment_status in const.PENDING_STATUSES:
            self._set_pending()
        elif payment_status in const.DONE_STATUSES:
            if self.provider_id.capture_manually and payment_status in const.AUTHORIZED_STATUSES:
                self._set_authorized()
            else:
                self._set_done()
        elif payment_status in const.CANCELED_STATUSES:
            self._set_canceled(_("Cancelled payment with status: %s", payment_status))
        elif payment_status in ('fully_refunded', 'partially_refunded'):
            self._set_done()
        else:
            _logger.info(
                "Received data with invalid payment status (%s) for transaction %s.",
                payment_status, self.reference,
            )
            self._set_error(_("Received data with invalid payment status: %s.", payment_status))
