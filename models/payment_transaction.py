from datetime import datetime, timezone

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import urls

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_tamara import const
from odoo.addons.payment_tamara.controllers.main import TamaraController


_logger = get_payment_logger(__name__, const.SENSITIVE_KEYS)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    tamara_order_id = fields.Char(
        string="Tamara Order ID",
        help="The order ID returned by Tamara when the checkout session is created.",
        readonly=True,
        copy=False,
    )
    tamara_checkout_url = fields.Char(
        string="Tamara Checkout URL",
        help="The checkout URL returned by Tamara for this transaction.",
        readonly=True,
        copy=False,
    )
    tamara_order_status = fields.Char(
        string="Tamara Order Status",
        help="The latest order status returned by the Tamara order details API.",
        readonly=True,
        copy=False,
    )
    tamara_payment_type = fields.Char(
        string="Tamara Payment Type",
        help="The payment type returned by the Tamara order details API.",
        readonly=True,
        copy=False,
    )

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
            return self._tamara_fail_checkout_creation(error)

        if not checkout_data:
            return self._tamara_fail_checkout_creation(_("Empty response"))

        order_id = checkout_data.get('order_id')
        checkout_url = checkout_data.get('checkout_url')
        if not order_id:
            return self._tamara_fail_checkout_creation(_("Tamara did not return an order ID."))
        if not checkout_url:
            return self._tamara_fail_checkout_creation(_("Tamara did not return a checkout URL."))
        # Keep provider_reference populated for Odoo's generic transaction UI and store explicit
        # Tamara metadata so the return route can retrieve the order without trusting query data.
        self.write({
            'provider_reference': order_id,
            'tamara_order_id': order_id,
            'tamara_checkout_url': checkout_url,
        })
        return {'api_url': checkout_url}

    def _get_processing_values(self):
        """Override of `payment` to show a generic checkout error to the customer."""
        values = super()._get_processing_values()
        if (
            self.provider_code == 'tamara'
            and self.state == 'error'
            and self.state_message
            and 'Cannot create the checkout session' in self.state_message
        ):
            values['state_message'] = _(
                "Tamara payment is unavailable at this time, please choose another payment option"
            )
        return values

    def _tamara_fail_checkout_creation(self, tamara_error):
        """Mark checkout creation as failed for the customer and log the Tamara error.

        The payment keeps a detailed Tamara-prefixed note. The customer-facing payment form
        receives a generic unavailable message via `_get_processing_values`.

        :param Exception|str tamara_error: The Tamara API error or reason.
        :return: Empty rendering values so no redirect is attempted.
        :rtype: dict
        """
        self.ensure_one()
        note = self._tamara_format_note(
            _("Cannot create the checkout session, error from Tamara: %s", tamara_error)
        )
        # `_set_error` stores the note on the payment and logs it on linked documents.
        self._set_error(note)
        return {}

    def _tamara_prepare_checkout_payload(self):
        """Create the payload for the checkout session request.

        :return: The request payload.
        :rtype: dict
        """
        self.ensure_one()
        base_url = self.provider_id.get_base_url()
        return_url = urls.urljoin(base_url, f'{TamaraController._return_url}?ref={self.reference}')
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
                'notification': '',
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

    def _tamara_fetch_order(self, order_id=None):
        """Fetch the Tamara order linked to this transaction.

        :param str order_id: Optional Tamara order id. Defaults to the stored order id.
        :return: The order data.
        :rtype: dict
        """
        self.ensure_one()
        order_id = order_id or self.tamara_order_id or self.provider_reference
        if not order_id:
            raise ValidationError(_("The Tamara order id is missing."))
        return self._send_api_request('GET', f'/orders/{order_id}')

    @api.model
    def _tamara_note_prefix(self):
        """Return the translated Tamara note prefix.

        :return: The prefix used before Tamara chatter / payment notes.
        :rtype: str
        """
        return _("Tamara:")

    @api.model
    def _tamara_format_note(self, message):
        """Prefix a Tamara note with the translated `Tamara:` label.

        :param str message: The note body without the Tamara prefix.
        :return: The prefixed note.
        :rtype: str
        """
        return f'{self._tamara_note_prefix()} {message}'

    def _tamara_log_note(self, message, *, sale_orders=None):
        """Log a Tamara-prefixed note on the payment and linked sales orders.

        :param str message: The note body without the Tamara prefix.
        :param sale.order sale_orders: Optional sales orders to notify in addition to linked ones.
        :return: The prefixed note that was logged.
        :rtype: str
        """
        self.ensure_one()
        note = self._tamara_format_note(message)
        self.write({'state_message': note})
        body = Markup(note)
        self.with_context(payment_backend_action=True)._log_message_on_linked_documents(body)
        for order in sale_orders or self.env['sale.order']:
            if order not in (self.sale_order_ids | self.source_transaction_id.sale_order_ids):
                order.sudo().message_post(
                    body=body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
        return note

    def _tamara_handle_webhook_cancel_status(self, order_data):
        """Cancel the Odoo payment and log a sale-order note for terminal Tamara failures.

        Handles `declined`, `expired`, and `canceled` / `cancelled`. Cancels the payment
        when it is not already canceled (Odoo only allows cancel from draft/pending/authorized).
        Always leaves a `Tamara:` note on the payment and linked sales orders.

        :param dict order_data: The latest Tamara order details.
        :return: Whether this status was handled.
        :rtype: bool
        """
        self.ensure_one()
        status = (order_data.get('status') or '').lower()
        if status not in const.WEBHOOK_CANCEL_PAYMENT_STATUSES:
            return False

        if status == 'declined':
            outcome = _("declined")
        elif status == 'expired':
            outcome = _("expired")
        else:
            outcome = _("cancelled")

        self.write({
            'tamara_order_id': (
                order_data.get('order_id') or self.tamara_order_id or self.provider_reference
            ),
            'tamara_order_status': order_data.get('status') or False,
            'tamara_payment_type': (
                order_data.get('payment_type') or self.tamara_payment_type or False
            ),
        })

        message = _("Tamara payment for the order is %(outcome)s.", outcome=outcome)
        note = self._tamara_format_note(message)

        if self.state == 'cancel':
            self._tamara_log_note(message)
        else:
            self._set_canceled(note)
            if self.state != 'cancel':
                # Cancel refused (e.g. payment already Confirmed) — still leave the note.
                self._tamara_log_note(message)
            # On success, `_set_canceled` already stores state_message and logs on linked docs.

        _logger.info(
            "Handled Tamara webhook cancel status %s on transaction %s (payment state=%s).",
            status, self.reference, self.state,
        )
        return True

    def _tamara_log_webhook_status_note(self, order_data):
        """Log a Tamara status note on the transaction and linked sales orders.

        Used for webhook notifications when the live Tamara order status is partially
        canceled, captured, or refunded (fully or partially). Does not change the Odoo
        payment or sales order state.

        :param dict order_data: The latest Tamara order details.
        :return: Whether a note was logged for a note-only status.
        :rtype: bool
        """
        self.ensure_one()
        status = (order_data.get('status') or '').lower()
        if status not in const.WEBHOOK_NOTE_ONLY_STATUSES:
            return False

        if status in const.PARTIALLY_CANCELED_STATUSES:
            action_kind = _("partially")
            action = _("canceled")
            amount_label = _("Canceled amount")
            amount_keys = ('canceled_amount', 'total_amount')
        elif status in const.FULLY_CAPTURED_STATUSES:
            action_kind = _("fully")
            action = _("captured")
            amount_label = _("Captured amount")
            amount_keys = ('captured_amount', 'total_amount')
        elif status in const.PARTIALLY_CAPTURED_STATUSES:
            action_kind = _("partially")
            action = _("captured")
            amount_label = _("Captured amount")
            amount_keys = ('captured_amount', 'total_amount')
        elif status in const.FULLY_REFUNDED_STATUSES:
            action_kind = _("fully")
            action = _("refunded")
            amount_label = _("Refunded amount")
            amount_keys = ('refunded_amount', 'total_amount')
        else:  # partially refunded
            action_kind = _("partially")
            action = _("refunded")
            amount_label = _("Refunded amount")
            amount_keys = ('refunded_amount', 'total_amount')

        amount, currency_code = self._tamara_extract_money(order_data, amount_keys)
        if amount is None:
            amount = self.amount
            currency_code = self.currency_id.name
        formatted_amount = f'{float(amount):.2f} {currency_code}'
        message = _(
            "Payment was %(action_kind)s %(action)s. %(amount_label)s: %(amount)s.",
            action_kind=action_kind,
            action=action,
            amount_label=amount_label,
            amount=formatted_amount,
        )
        self.write({
            'tamara_order_id': (
                order_data.get('order_id') or self.tamara_order_id or self.provider_reference
            ),
            'tamara_order_status': order_data.get('status') or False,
            'tamara_payment_type': (
                order_data.get('payment_type') or self.tamara_payment_type or False
            ),
        })
        self._tamara_log_note(message)
        _logger.info(
            "Logged Tamara %s note on transaction %s (status=%s, amount=%s).",
            action, self.reference, status, formatted_amount,
        )
        return True

    def _tamara_cancel_from_sale_order(self, sale_order):
        """Cancel this Tamara order for the given sales order amount and log the result.

        Does not change the Odoo payment transaction state.

        :param sale.order sale_order: The sales order being canceled.
        :return: None
        """
        self.ensure_one()
        order_id = self.tamara_order_id or self.provider_reference
        if not order_id:
            return

        cancel_amount = sale_order.amount_total
        currency_code = (sale_order.currency_id or self.currency_id).name
        payload = {
            'total_amount': {
                'amount': float(cancel_amount),
                'currency': currency_code,
            },
        }
        try:
            cancel_data = self._send_api_request(
                'POST', f'/orders/{order_id}/cancel', json=payload
            )
        except ValidationError as error:
            self._tamara_log_note(
                _(
                    "Cancel action on Tamara side failed, error from Tamara: %s",
                    error,
                ),
                sale_orders=sale_order,
            )
            return

        canceled_amount, response_currency = self._tamara_extract_money(
            cancel_data, ('canceled_amount', 'total_amount')
        )
        if canceled_amount is None:
            canceled_amount = cancel_amount
            response_currency = currency_code
        formatted_amount = f'{float(canceled_amount):.2f} {response_currency}'
        if cancel_data.get('status'):
            self.tamara_order_status = cancel_data['status']
        self._tamara_log_note(
            _(
                "Payment is Canceled successfully on Tamara side, Canceled amount is %s",
                formatted_amount,
            ),
            sale_orders=sale_order,
        )

    def _tamara_capture_from_sale_order(self, sale_order):
        """Fully capture this Tamara order and log the result on the sales order.

        Does not change the Odoo payment transaction state unless it is still authorized,
        in which case it is marked done after a successful capture.

        :param sale.order sale_order: The sales order that triggered the capture.
        :return: None
        """
        self.ensure_one()
        order_id = self.tamara_order_id or self.provider_reference
        if not order_id:
            return

        payload = {
            'order_id': order_id,
            'total_amount': self._tamara_money(self.amount),
            'shipping_info': {
                'shipped_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'shipping_company': self.company_id.name or 'Odoo',
                'tracking_number': sale_order.name or self.reference,
            },
        }
        try:
            capture_data = self._send_api_request('POST', '/payments/capture', json=payload)
        except ValidationError as error:
            self._tamara_log_note(
                _(
                    "Capture action on Tamara side failed, error from Tamara: %s",
                    error,
                ),
                sale_orders=sale_order,
            )
            return

        capture_id = capture_data.get('capture_id') or ''
        captured_amount, response_currency = self._tamara_extract_money(
            capture_data, ('captured_amount', 'total_amount')
        )
        if captured_amount is None:
            captured_amount = self.amount
            response_currency = self.currency_id.name
        formatted_amount = f'{float(captured_amount):.2f} {response_currency}'
        if capture_data.get('status'):
            self.tamara_order_status = capture_data['status']
        else:
            self.tamara_order_status = 'fully_captured'
        if self.state == 'authorized':
            self._set_done()
        self._tamara_log_note(
            _(
                "Order captured successfully. Capture amount: %(amount)s. Capture Id: %(capture_id)s.",
                amount=formatted_amount,
                capture_id=capture_id,
            ),
            sale_orders=sale_order,
        )

    def _tamara_extract_money(self, order_data, amount_keys):
        """Extract an amount/currency pair from Tamara order data.

        :param dict order_data: The Tamara order details.
        :param tuple[str] amount_keys: Preferred money field names, in order.
        :return: The amount and currency code.
        :rtype: tuple[float|None, str]
        """
        for key in amount_keys:
            money = order_data.get(key) or {}
            if isinstance(money, dict) and money.get('amount') is not None:
                return float(money['amount']), money.get('currency') or self.currency_id.name
        return None, self.currency_id.name

    def _tamara_can_process_return(self):
        """Return whether this transaction can query Tamara after checkout.

        The transaction must use both the Tamara provider and payment method, and the
        provider must remain enabled and published.

        :return: Whether the Tamara return can be processed.
        :rtype: bool
        """
        self.ensure_one()
        return bool(
            self.provider_code == 'tamara'
            and self.payment_method_id.code == 'tamara'
            and self.provider_id.state in ('enabled', 'test')
            and self.provider_id.is_published
        )

    def _tamara_update_order_metadata(self, order_data):
        """Store metadata returned by Tamara's order details API.

        :param dict order_data: The latest Tamara order details.
        :return: None
        """
        self.ensure_one()
        order_id = order_data.get('order_id') or self.tamara_order_id or self.provider_reference
        self.write({
            'tamara_order_id': order_id,
            'tamara_order_status': order_data.get('status') or False,
            'tamara_payment_type': order_data.get('payment_type') or False,
        })

    def _tamara_process_return(self, order_data):
        """Apply Tamara's latest order status after the customer returns from checkout.

        The return URL is only a signal. The caller must pass order details fetched directly from
        Tamara using the order ID stored when the checkout session was created.

        Authorised orders become Odoo Authorized. Captured orders become Confirmed (`done`).
        Canceled, expired, and declined orders become Canceled.

        :param dict order_data: The latest Tamara order details.
        :return: None
        """
        self.ensure_one()
        self._tamara_update_order_metadata(order_data)
        status = (order_data.get('status') or '').lower()
        if status in const.AUTHORIZED_STATUSES:
            self._set_authorized()
        elif status in const.CAPTURED_STATUSES:
            self._set_done()
        elif status in const.CANCELED_STATUSES:
            self._set_canceled()

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
