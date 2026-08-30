import base64
import hashlib
import hmac
from unittest.mock import MagicMock, patch

import requests

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment.tests.http_common import PaymentHttpCommon
from odoo.addons.payment_tamara.controllers.main import TamaraController
from odoo.addons.payment_tamara.tests.common import TamaraCommon


def _sign_tamara_jwt(secret):
    """Return a minimal HS256 JWT signed with the given secret."""
    def b64url(data):
        return base64.urlsafe_b64encode(data).rstrip(b'=')

    signing_input = b64url(b'{"alg":"HS256","typ":"JWT"}') + b'.' + b64url(b'{"iss":"Tamara"}')
    signature = b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return (signing_input + b'.' + signature).decode()


@tagged('post_install', '-at_install')
class TamaraTest(TamaraCommon, PaymentHttpCommon):

    def test_checkout_payload_values(self):
        tx = self._create_transaction(flow='redirect')
        payload = tx._tamara_prepare_checkout_payload()

        self.assertEqual(payload['order_reference_id'], tx.reference)
        self.assertEqual(payload['total_amount'], {'amount': self.amount, 'currency': 'SAR'})
        self.assertEqual(payload['payment_type'], 'PAY_BY_INSTALMENTS')
        self.assertEqual(payload['instalments'], 3)
        self.assertIn('success', payload['merchant_url'])
        self.assertTrue(payload['items'])

    def test_payment_labels_ksa_english(self):
        labels = self.provider._tamara_get_payment_labels(country_code='SA', lang='en_US')
        self.assertEqual(labels['title'], 'Tamara')
        self.assertEqual(labels['description'], 'Monthly Payments. Sharia Compliant.')

    def test_payment_labels_ksa_arabic(self):
        labels = self.provider._tamara_get_payment_labels(country_code='SA', lang='ar_001')
        self.assertEqual(labels['title'], 'تمارا')
        self.assertEqual(labels['description'], 'دفعات شهرية. متوافقة مع الشريعة')

    def test_payment_labels_other_country_english(self):
        labels = self.provider._tamara_get_payment_labels(country_code='AE', lang='en_US')
        self.assertEqual(labels['title'], 'Tamara')
        self.assertEqual(labels['description'], 'Monthly Payments.')

    def test_eligibility_defaults_true_without_phone(self):
        self.assertTrue(self.provider._tamara_is_customer_eligible(
            amount=100, currency=self.currency, phone='',
        ))

    def test_eligibility_fail_open_on_timeout(self):
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            side_effect=requests.exceptions.Timeout(),
        ):
            self.assertTrue(self.provider._tamara_is_customer_eligible(
                amount=100, currency=self.currency, phone='966501234567',
            ))

    def test_webhook_registration_saves_id(self):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.text = ''
        mock_response.json.return_value = {
            'webhook_id': 'wh-123',
            'url': 'https://shop.example/payment/tamara/webhook',
        }
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            return_value=mock_response,
        ):
            webhook_id = self.provider._tamara_register_webhook()
        self.assertEqual(webhook_id, 'wh-123')
        self.assertEqual(self.provider.tamara_sandbox_webhook_id, 'wh-123')
        self.assertEqual(
            self.provider.tamara_sandbox_webhook_url,
            'https://shop.example/payment/tamara/webhook',
        )

    def test_webhook_already_registered_still_saves_id(self):
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.text = ''
        mock_response.json.return_value = {
            'errors': [{
                'error_code': 'webhook_already_registered',
                'data': {
                    'webhook_id': 'wh-existing',
                    'url': 'https://shop.example/payment/tamara/webhook',
                },
            }],
        }
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            return_value=mock_response,
        ):
            webhook_id = self.provider._tamara_register_webhook()
        self.assertEqual(webhook_id, 'wh-existing')
        self.assertEqual(self.provider.tamara_sandbox_webhook_id, 'wh-existing')
        self.assertEqual(
            self.provider.tamara_sandbox_webhook_url,
            'https://shop.example/payment/tamara/webhook',
        )

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
    )
    def test_webhook_notification_confirms_transaction(self):
        tx = self._create_transaction('redirect', provider_reference=self.order_data['order_id'])
        url = self._build_url(TamaraController._webhook_url)
        token = _sign_tamara_jwt(self.provider._tamara_get_notification_key())
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=self.order_data,
        ):
            self._make_json_request(
                f'{url}?tamaraToken={token}',
                data=self.payment_data,
            )
        self.assertEqual(tx.state, 'done')

    @mute_logger('odoo.addons.payment_tamara.controllers.main')
    def test_webhook_rejects_invalid_token(self):
        self._create_transaction('redirect', provider_reference=self.order_data['order_id'])
        url = self._build_url(TamaraController._webhook_url)
        response = self._make_json_request(url, data=self.payment_data)
        self.assertEqual(response.status_code, 403)

    def test_apply_updates_sets_authorized_when_manual_capture(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('redirect', provider_reference=self.order_data['order_id'])
        tx._apply_updates(self.order_data)
        self.assertEqual(tx.state, 'authorized')

    def test_sandbox_credentials_are_used_in_sandbox_mode(self):
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.PaymentProvider'
            '._tamara_register_webhook_on_save',
            return_value=None,
        ):
            self.provider.tamara_state = 'sandbox'
        self.assertTrue(self.provider._tamara_is_sandbox())
        self.assertEqual(self.provider._tamara_get_api_token(), 'dummy_sandbox_api_token')
        self.assertEqual(
            self.provider._tamara_get_notification_key(), 'dummy_sandbox_notification_key'
        )
        self.assertIn('api-sandbox.tamara.co', self.provider._build_request_url('checkout'))

    def test_live_credentials_are_used_in_live_mode(self):
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.PaymentProvider'
            '._tamara_register_webhook_on_save',
            return_value=None,
        ):
            self.provider.tamara_state = 'enabled'
        self.assertFalse(self.provider._tamara_is_sandbox())
        self.assertEqual(self.provider._tamara_get_api_token(), 'dummy_live_api_token')
        self.assertEqual(self.provider._tamara_get_notification_key(), 'dummy_live_notification_key')
        self.assertIn('api.tamara.co', self.provider._build_request_url('checkout'))
