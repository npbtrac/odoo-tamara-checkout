import base64
import hashlib
import hmac
import pprint

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_tamara import const


_logger = get_payment_logger(__name__, const.SENSITIVE_KEYS)


class TamaraController(http.Controller):
    _return_url = '/payment/tamara/return'
    _webhook_url = '/payment/tamara/webhook'
    _eligibility_url = '/payment/tamara/eligibility'

    @http.route(
        _return_url, type='http', auth='public', methods=['GET', 'POST'], csrf=False,
        save_session=False,
    )
    def tamara_return_from_checkout(self, **data):
        """Process the payment data sent by Tamara after redirection from checkout.

        The route is flagged with `save_session=False` to prevent Odoo from assigning a new session
        to the user if they are redirected to this route with a POST request.

        :param dict data: The query parameters sent by Tamara, including the transaction `ref`.
        """
        _logger.info("handling redirection from Tamara with data:\n%s", pprint.pformat(data))
        tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference('tamara', data)
        if not tx_sudo:
            return request.redirect('/payment/status')
        if not tx_sudo._tamara_can_process_return():
            _logger.warning(
                "Ignoring Tamara return for transaction %s: the Tamara payment method "
                "or published provider is unavailable.",
                tx_sudo.reference,
            )
            return request.redirect('/payment/status')

        try:
            order_data = tx_sudo._tamara_fetch_order()
        except ValidationError as error:
            _logger.error(
                "Unable to retrieve the Tamara order for transaction %s: %s",
                tx_sudo.reference, error,
            )
            tx_sudo._set_error(
                "Unable to retrieve the Tamara order details. Please contact support."
            )
        else:
            tx_sudo._tamara_process_return(order_data)
        return request.redirect('/payment/status')

    @http.route(_webhook_url, type='http', auth='public', methods=['POST'], csrf=False)
    def tamara_webhook(self, **data):
        """Process the payment data sent by Tamara to the webhook.

        :param dict data: Unused; the payload is read from the JSON body.
        :return: An empty string to acknowledge the notification.
        :rtype: str
        """
        try:
            payload = request.get_json_data()
        except Exception:  # noqa: BLE001 - keep the webhook from 500-ing on empty bodies.
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.update(data)
        _logger.info("notification received from Tamara with data:\n%s", pprint.pformat(payload))
        self._verify_and_process(payload, verify_notification_token=True)
        return ''  # Acknowledge the notification.

    @http.route(_eligibility_url, type='jsonrpc', auth='public', methods=['POST'])
    def tamara_pre_checkout_eligibility(
        self, provider_id, amount, currency_id, partner_id=None, phone=None, email=None,
        sale_order_id=None, **_kwargs
    ):
        """Check Tamara pre-checkout eligibility for the current checkout form values.

        Uses the configured eligibility timeout so the payment form can hide Tamara when
        the API returns `is_eligible: false`.

        :param int provider_id: The Tamara provider id.
        :param float amount: The order amount.
        :param int currency_id: The currency id.
        :param int partner_id: Optional partner id.
        :param str phone: Optional phone number from the checkout form.
        :param str email: Optional email from the checkout form.
        :param int sale_order_id: Optional website sale order id (uses invoice partner phone).
        :return: Whether Tamara should be shown.
        :rtype: dict
        """
        provider_sudo = request.env['payment.provider'].sudo().browse(provider_id).exists()
        if not provider_sudo or provider_sudo.code != 'tamara' or provider_sudo.state == 'disabled':
            return {'is_eligible': False}

        partner = request.env['res.partner'].sudo().browse(partner_id).exists()
        order = request.env['sale.order'].sudo().browse(sale_order_id).exists()
        if order:
            partner = order.partner_invoice_id or order.partner_id or partner
        currency = request.env['res.currency'].sudo().browse(currency_id).exists()
        is_eligible = provider_sudo._tamara_is_customer_eligible(
            amount=amount,
            currency=currency,
            partner=partner,
            phone=phone,
            email=email,
            timeout=const.ELIGIBILITY_TIMEOUT,
        )
        return {'is_eligible': is_eligible}

    @staticmethod
    def _verify_and_process(data, verify_notification_token=False):
        """Find the transaction, optionally verify the notification token, and process the order.

        Tamara webhook bodies are treated as a signal. The order is always re-fetched from the API
        before the transaction is updated.

        :param dict data: The payment data from the return URL or webhook.
        :param bool verify_notification_token: Whether to verify the Tamara JWT.
        :return: None
        """
        tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference('tamara', data)
        if not tx_sudo:
            return

        if verify_notification_token:
            TamaraController._verify_notification_token(tx_sudo)

        try:
            order_data = tx_sudo._tamara_fetch_order()
            order_data = tx_sudo._tamara_authorise_if_needed(order_data)
        except ValidationError:
            _logger.error("Unable to process the payment data for transaction %s.", tx_sudo.reference)
        else:
            tx_sudo._process('tamara', order_data)

    @staticmethod
    def _verify_notification_token(tx_sudo):
        """Check that the Tamara notification JWT is signed with the configured notification token.

        Tamara sends the JWT both as the `tamaraToken` query parameter and as a Bearer token.

        :param payment.transaction tx_sudo: The sudoed transaction referenced by the payment data.
        :return: None
        :raise Forbidden: If the token is missing or invalid.
        """
        token = request.httprequest.args.get('tamaraToken')
        if not token:
            auth_header = request.httprequest.headers.get('Authorization', '')
            if auth_header.lower().startswith('bearer '):
                token = auth_header.split(' ', 1)[1].strip()
        if not token:
            _logger.warning("Received Tamara notification with missing token.")
            raise Forbidden()

        secret = tx_sudo.provider_id._tamara_get_notification_key()
        if not secret or not TamaraController._is_valid_hs256_jwt(token, secret):
            _logger.warning("Received Tamara notification with invalid token.")
            raise Forbidden()

    @staticmethod
    def _is_valid_hs256_jwt(token, secret):
        """Return whether `token` is an HS256 JWT signed with `secret`.

        :param str token: The JWT to verify.
        :param str secret: The Tamara notification token used as the HMAC secret.
        :return: Whether the signature matches.
        :rtype: bool
        """
        try:
            header_b64, payload_b64, signature_b64 = token.split('.')
        except ValueError:
            return False

        signing_input = f'{header_b64}.{payload_b64}'.encode()
        expected_signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        received_signature = TamaraController._b64url_decode(signature_b64)
        return hmac.compare_digest(expected_signature, received_signature)

    @staticmethod
    def _b64url_decode(value):
        """Decode a URL-safe base64 string, adding missing padding if needed.

        :param str value: The encoded value.
        :return: The decoded bytes.
        :rtype: bytes
        """
        padding = '=' * (-len(value) % 4)
        return base64.urlsafe_b64decode(f'{value}{padding}')
