from unittest.mock import patch

import requests

from odoo.addons.payment.tests.common import PaymentCommon


class TamaraCommon(PaymentCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.PaymentProvider'
            '._tamara_register_webhook_on_save',
            return_value=None,
        ):
            cls.tamara = cls._prepare_provider('tamara', update_values={
                'tamara_sandbox_mode': True,
                'tamara_sandbox_api_token': 'dummy_sandbox_api_token',
                'tamara_sandbox_notification_key': 'dummy_sandbox_notification_key',
                'tamara_sandbox_public_key': 'dummy_sandbox_public_key',
                'tamara_live_api_token': 'dummy_live_api_token',
                'tamara_live_notification_key': 'dummy_live_notification_key',
                'tamara_live_public_key': 'dummy_live_public_key',
            })
        cls.provider = cls.tamara
        cls.currency = cls._enable_currency('SAR')

        cls.payment_data = {
            'ref': cls.reference,
            'order_reference_id': cls.reference,
            'order_id': '11111111-1111-1111-1111-111111111111',
            'event_type': 'order_authorised',
        }
        cls._patch_webhook_url('http://localhost:8069/payment/tamara/webhook')
        # Never reach the real Tamara API from tests: saving a provider registers the
        # webhook, and an unreachable API is handled without blocking the save. Tests that
        # exercise registration patch `requests.post` themselves.
        cls.startClassPatcher(patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            side_effect=requests.exceptions.ConnectionError(),
        ))

        cls.order_data = {
            'order_id': '11111111-1111-1111-1111-111111111111',
            'order_reference_id': cls.reference,
            'status': 'authorised',
            'total_amount': {'amount': cls.amount, 'currency': 'SAR'},
        }

    _WEBHOOK_URL_PATH = (
        'odoo.addons.payment_tamara.models.payment_provider.PaymentProvider'
        '._tamara_get_webhook_url'
    )

    @classmethod
    def _patch_webhook_url(cls, url):
        """Force the webhook URL Tamara would be pointed at, for the whole test class."""
        cls.startClassPatcher(patch(cls._WEBHOOK_URL_PATH, return_value=url))
