import base64
import hashlib
import hmac
from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment_tamara import const
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
        self.assertEqual(payload['merchant_url']['notification'], '')
        self.assertTrue(payload['items'])

    def test_checkout_response_metadata_is_stored(self):
        tx = self._create_transaction(flow='redirect')
        checkout_data = {
            'order_id': self.order_data['order_id'],
            'checkout_url': 'https://checkout.tamara.co/example',
        }
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=checkout_data,
        ):
            rendering_values = tx._get_specific_rendering_values({})

        self.assertEqual(rendering_values['api_url'], checkout_data['checkout_url'])
        self.assertEqual(tx.provider_reference, checkout_data['order_id'])
        self.assertEqual(tx.tamara_order_id, checkout_data['order_id'])
        self.assertEqual(tx.tamara_checkout_url, checkout_data['checkout_url'])

    def test_checkout_creation_failure_shows_generic_error_and_logs_detail(self):
        tx = self._create_transaction(flow='redirect')
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            side_effect=ValidationError(
                "The payment provider rejected the request.\ninvalid phone"
            ),
        ):
            rendering_values = tx._get_specific_rendering_values({})
            processing_values = tx._get_processing_values()

        self.assertEqual(rendering_values, {})
        self.assertEqual(tx.state, 'error')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('Cannot create the checkout session', tx.state_message)
        self.assertIn('invalid phone', tx.state_message)
        self.assertEqual(
            processing_values['state_message'],
            "Tamara payment is unavailable at this time, please choose another payment option",
        )

    def test_checkout_missing_order_id_uses_generic_customer_error(self):
        tx = self._create_transaction(flow='redirect')
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value={'checkout_url': 'https://checkout.tamara.co/example'},
        ):
            tx._get_specific_rendering_values({})
            processing_values = tx._get_processing_values()

        self.assertEqual(tx.state, 'error')
        self.assertIn('Cannot create the checkout session', tx.state_message)
        self.assertIn('Tamara did not return an order ID.', tx.state_message)
        self.assertEqual(
            processing_values['state_message'],
            "Tamara payment is unavailable at this time, please choose another payment option",
        )

    def test_fetch_order_uses_stored_tamara_order_id(self):
        tx = self._create_transaction(
            flow='redirect',
            provider_reference='legacy-provider-reference',
            tamara_order_id=self.order_data['order_id'],
        )
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=self.order_data,
        ) as send_request:
            self.assertEqual(tx._tamara_fetch_order(), self.order_data)

        send_request.assert_called_once_with(
            'GET',
            f"/orders/{self.order_data['order_id']}",
            params=None,
            data=None,
            json=None,
            reference=tx.reference,
        )

    def test_return_fetches_stored_order_and_marks_success(self):
        tx = self._create_transaction(
            flow='redirect',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        self.provider.with_context(tamara_skip_webhook_register=True).write({
            'is_published': True,
        })
        url = self._build_url(f'{TamaraController._return_url}?ref={tx.reference}')
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=self.order_data,
        ) as send_request:
            self.url_open(url)

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'authorized')
        self.assertEqual(tx.tamara_order_id, self.order_data['order_id'])
        self.assertEqual(tx.tamara_order_status, 'authorised')
        self.assertEqual(tx.tamara_payment_type, 'PAY_BY_INSTALMENTS')
        send_request.assert_called_once_with(
            'GET',
            f"/orders/{self.order_data['order_id']}",
            params=None,
            data=None,
            json=None,
            reference=tx.reference,
        )

    def test_return_does_not_fetch_order_when_provider_is_unpublished(self):
        tx = self._create_transaction(
            flow='redirect',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        self.provider.with_context(tamara_skip_webhook_register=True).write({
            'is_published': False,
        })
        url = self._build_url(f'{TamaraController._return_url}?ref={tx.reference}')
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
        ) as send_request:
            self.url_open(url)

        send_request.assert_not_called()
        tx.invalidate_recordset()
        self.assertFalse(tx.tamara_order_status)
        self.assertFalse(tx.tamara_payment_type)

    def test_return_metadata_uses_provider_reference_as_order_id_fallback(self):
        tx = self._create_transaction(
            flow='redirect',
            provider_reference=self.order_data['order_id'],
        )
        tx._tamara_process_return(self.order_data)

        self.assertEqual(tx.tamara_order_id, self.order_data['order_id'])
        self.assertEqual(tx.tamara_order_status, 'authorised')
        self.assertEqual(tx.tamara_payment_type, 'PAY_BY_INSTALMENTS')

    def test_return_status_authorised_is_authorized(self):
        tx = self._create_transaction(flow='redirect')
        tx._tamara_process_return({'status': 'authorised'})
        self.assertEqual(tx.state, 'authorized')
        self.assertFalse(tx.state_message)

    def test_return_status_captured_is_confirmed(self):
        tx = self._create_transaction(flow='redirect')
        tx._tamara_process_return({'status': 'fully_captured'})
        self.assertEqual(tx.state, 'done')
        self.assertFalse(tx.state_message)

    def test_return_status_declined_is_canceled(self):
        tx = self._create_transaction(flow='redirect')
        tx._tamara_process_return({'status': 'declined'})
        self.assertEqual(tx.state, 'cancel')

    def test_return_status_expired_is_canceled(self):
        tx = self._create_transaction(flow='redirect')
        tx._tamara_process_return({'status': 'expired'})
        self.assertEqual(tx.state, 'cancel')

    def test_return_status_canceled_is_canceled(self):
        tx = self._create_transaction(flow='redirect')
        tx._tamara_process_return({'status': 'canceled'})
        self.assertEqual(tx.state, 'cancel')

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

    def test_eligibility_hides_without_phone_or_email(self):
        self.assertFalse(self.provider._tamara_is_customer_eligible(
            amount=100, currency=self.currency, phone='', email='buyer@example.com',
        ))
        self.assertFalse(self.provider._tamara_is_customer_eligible(
            amount=100, currency=self.currency, phone='966501234567', email='',
        ))

    def test_eligibility_fail_open_on_timeout(self):
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            side_effect=requests.exceptions.Timeout(),
        ):
            self.assertTrue(self.provider._tamara_is_customer_eligible(
                amount=100,
                currency=self.currency,
                phone='966501234567',
                email='buyer@example.com',
            ))

    def test_webhook_registration_saves_id(self):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
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
        mock_response.status_code = 400
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

    @mute_logger('odoo.addons.payment_tamara.models.payment_provider')
    def test_webhook_4xx_raises_wrong_api_token(self):
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 401
        mock_response.text = 'Unauthorized'
        mock_response.json.return_value = {'message': 'Unauthorized'}
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            return_value=mock_response,
        ):
            with self.assertRaises(ValidationError) as error:
                self.provider._tamara_register_webhook()
        self.assertIn('Wrong API Token', str(error.exception))

    @mute_logger('odoo.addons.payment_tamara.models.payment_provider')
    def test_webhook_4xx_blocks_settings_save(self):
        original_token = self.provider.tamara_sandbox_api_token
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 401
        mock_response.text = 'Unauthorized'
        mock_response.json.return_value = {'message': 'Unauthorized'}
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            return_value=mock_response,
        ):
            with self.assertRaises(ValidationError) as error:
                self.provider.write({'tamara_sandbox_api_token': 'bad-token'})
        self.assertIn('Wrong API Token', str(error.exception))
        self.provider.invalidate_recordset()
        self.assertEqual(self.provider.tamara_sandbox_api_token, original_token)

    def test_settings_save_registers_webhook_once(self):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.text = ''
        mock_response.json.return_value = {
            'webhook_id': 'wh-saved',
            'url': 'https://shop.example/payment/tamara/webhook',
        }
        # `tamara_state` is a computed field with an inverse, so saving it triggers nested
        # writes; the webhook must still be registered exactly once per save.
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            return_value=mock_response,
        ) as mock_post:
            self.provider.write({'tamara_state': 'sandbox'})
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(self.provider.tamara_sandbox_webhook_id, 'wh-saved')

    def test_settings_save_registers_webhook_without_credential_change(self):
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.text = ''
        mock_response.json.return_value = {
            'webhook_id': 'wh-plain-save',
            'url': 'https://shop.example/payment/tamara/webhook',
        }
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            return_value=mock_response,
        ) as mock_post:
            self.provider.write({'maximum_amount': 1000.0})
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(self.provider.tamara_sandbox_webhook_id, 'wh-plain-save')

    @mute_logger('odoo.addons.payment_tamara.models.payment_provider')
    def test_settings_save_blocks_any_4xx(self):
        """Any 4xx aborts the save and reports the Tamara error detail."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 400
        mock_response.text = ''
        mock_response.json.return_value = {'message': 'Invalid registered event'}
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.requests.post',
            return_value=mock_response,
        ):
            with self.assertRaises(ValidationError) as error:
                self.provider.write({'maximum_amount': 1000.0})
        self.assertIn('Wrong API Token', str(error.exception))
        self.assertIn('Invalid registered event', str(error.exception))

    def test_webhook_events_exclude_order_updated(self):
        """Tamara rejects `order_updated`: "Invalid registered event order_updated"."""
        self.assertNotIn('order_updated', const.WEBHOOK_EVENTS)

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
        ) as send_request:
            self._make_json_request(
                f'{url}?tamaraToken={token}',
                data=self.payment_data,
            )
        send_request.assert_called_once_with(
            'GET',
            f"/orders/{self.order_data['order_id']}",
            params=None,
            data=None,
            json=None,
            reference=tx.reference,
        )
        self.assertEqual(tx.state, 'done')

    def _post_webhook_with_order(self, tx, order_data, event_type='order_updated'):
        """POST a Tamara webhook for `tx` and return after fetching `order_data`."""
        payload = {
            **self.payment_data,
            'event_type': event_type,
            'order_id': self.order_data['order_id'],
        }
        url = self._build_url(TamaraController._webhook_url)
        token = _sign_tamara_jwt(self.provider._tamara_get_notification_key())
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=order_data,
        ) as send_request, patch.object(
            type(tx), '_log_message_on_linked_documents'
        ) as log_message:
            self._make_json_request(f'{url}?tamaraToken={token}', data=payload)
        return send_request, log_message

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
        'odoo.addons.payment.models.payment_transaction',
    )
    def test_webhook_canceled_cancels_authorized_payment(self):
        tx = self._create_transaction(
            'redirect',
            state='authorized',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        canceled_order = {
            **self.order_data,
            'status': 'canceled',
        }
        send_request, log_message = self._post_webhook_with_order(
            tx, canceled_order, event_type='order_canceled',
        )

        send_request.assert_called_once_with(
            'GET',
            f"/orders/{self.order_data['order_id']}",
            params=None,
            data=None,
            json=None,
            reference=tx.reference,
        )
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'cancel')
        self.assertEqual(tx.tamara_order_status, 'canceled')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('Tamara payment for the order is cancelled', tx.state_message)
        log_message.assert_called()

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
        'odoo.addons.payment.models.payment_transaction',
    )
    def test_webhook_declined_cancels_pending_payment(self):
        tx = self._create_transaction(
            'redirect',
            state='pending',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        declined_order = {**self.order_data, 'status': 'declined'}
        self._post_webhook_with_order(tx, declined_order, event_type='order_declined')

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'cancel')
        self.assertIn('Tamara payment for the order is declined', tx.state_message)

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
        'odoo.addons.payment.models.payment_transaction',
    )
    def test_webhook_expired_cancels_pending_payment(self):
        tx = self._create_transaction(
            'redirect',
            state='pending',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        expired_order = {**self.order_data, 'status': 'expired'}
        self._post_webhook_with_order(tx, expired_order, event_type='order_expired')

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'cancel')
        self.assertIn('Tamara payment for the order is expired', tx.state_message)

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
        'odoo.addons.payment.models.payment_transaction',
    )
    def test_webhook_canceled_on_done_payment_logs_note_without_cancel(self):
        tx = self._create_transaction(
            'redirect',
            state='done',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        canceled_order = {**self.order_data, 'status': 'canceled'}
        self._post_webhook_with_order(tx, canceled_order, event_type='order_canceled')

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('Tamara payment for the order is cancelled', tx.state_message)

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
    )
    def test_webhook_partially_canceled_logs_note(self):
        tx = self._create_transaction(
            'redirect',
            state='authorized',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        partial_order = {
            **self.order_data,
            'status': 'updated',
            'canceled_amount': {'amount': 25.5, 'currency': 'SAR'},
        }
        self._post_webhook_with_order(tx, partial_order, event_type='order_canceled')

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'authorized')
        self.assertEqual(tx.tamara_order_status, 'updated')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('partially canceled', tx.state_message)
        self.assertIn('Canceled amount: 25.50 SAR', tx.state_message)

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
    )
    def test_webhook_captured_logs_note_without_changing_state(self):
        tx = self._create_transaction(
            'redirect',
            state='authorized',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        captured_order = {
            **self.order_data,
            'status': 'fully_captured',
            'captured_amount': {'amount': 111.11, 'currency': 'SAR'},
        }
        _, log_message = self._post_webhook_with_order(
            tx, captured_order, event_type='order_captured',
        )

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'authorized')
        self.assertEqual(tx.tamara_order_status, 'fully_captured')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('fully captured', tx.state_message)
        self.assertIn('Captured amount: 111.11 SAR', tx.state_message)
        log_message.assert_called_once()

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
    )
    def test_webhook_partially_captured_logs_note(self):
        tx = self._create_transaction(
            'redirect',
            state='authorized',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        captured_order = {
            **self.order_data,
            'status': 'partially_captured',
            'captured_amount': {'amount': 40.0, 'currency': 'SAR'},
        }
        self._post_webhook_with_order(tx, captured_order, event_type='order_captured')

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'authorized')
        self.assertIn('partially captured', tx.state_message)
        self.assertIn('Captured amount: 40.00 SAR', tx.state_message)

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
    )
    def test_webhook_refunded_logs_note_without_changing_state(self):
        tx = self._create_transaction(
            'redirect',
            state='done',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        refunded_order = {
            **self.order_data,
            'status': 'fully_refunded',
            'refunded_amount': {'amount': 111.11, 'currency': 'SAR'},
        }
        _, log_message = self._post_webhook_with_order(
            tx, refunded_order, event_type='order_refunded',
        )

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.tamara_order_status, 'fully_refunded')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('fully refunded', tx.state_message)
        self.assertIn('Refunded amount: 111.11 SAR', tx.state_message)
        log_message.assert_called_once()

    @mute_logger(
        'odoo.addons.payment_tamara.controllers.main',
        'odoo.addons.payment_tamara.models.payment_transaction',
    )
    def test_webhook_partially_refunded_logs_note(self):
        tx = self._create_transaction(
            'redirect',
            state='done',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
        )
        refunded_order = {
            **self.order_data,
            'status': 'partially_refunded',
            'refunded_amount': {'amount': 15.0, 'currency': 'SAR'},
        }
        self._post_webhook_with_order(tx, refunded_order, event_type='order_refunded')

        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done')
        self.assertIn('partially refunded', tx.state_message)
        self.assertIn('Refunded amount: 15.00 SAR', tx.state_message)

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

    def test_website_provider_requires_published(self):
        website = self.env['website'].search([], limit=1)
        self.provider.company_id = website.company_id
        with patch(
            'odoo.addons.payment_tamara.models.payment_provider.PaymentProvider'
            '._tamara_register_webhook_on_save',
            return_value=None,
        ):
            self.provider.is_published = True
            self.assertEqual(
                self.env['payment.provider']._tamara_get_website_provider(website),
                self.provider,
            )
            self.provider.is_published = False
        self.assertFalse(self.env['payment.provider']._tamara_get_website_provider(website))

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

    def _create_sale_order_with_tamara_tx(self, product_type='service', **tx_values):
        """Create a confirmed sale order linked to a Tamara payment transaction."""
        product_vals = {
            'name': 'Tamara Capture Product',
            'list_price': self.amount,
            'type': product_type,
            'invoice_policy': 'order',
        }
        if product_type == 'consu' and 'is_storable' in self.env['product.product']._fields:
            product_vals['is_storable'] = True
        product = self.env['product.product'].create(product_vals)
        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'currency_id': self.currency.id,
            'order_line': [Command.create({
                'product_id': product.id,
                'product_uom_qty': 1,
                'price_unit': self.amount,
            })],
        })
        sale_order.action_confirm()
        tx = self._create_transaction(
            'redirect',
            state='authorized',
            provider_reference=self.order_data['order_id'],
            tamara_order_id=self.order_data['order_id'],
            sale_order_ids=[Command.set(sale_order.ids)],
            amount=sale_order.amount_total,
            **tx_values,
        )
        return sale_order, tx

    def test_sale_order_cancel_cancels_tamara_and_logs_success(self):
        sale_order, tx = self._create_sale_order_with_tamara_tx()
        cancel_amount = float(sale_order.amount_total)
        currency_name = sale_order.currency_id.name
        cancel_response = {
            'order_id': self.order_data['order_id'],
            'status': 'canceled',
            'canceled_amount': {
                'amount': cancel_amount,
                'currency': currency_name,
            },
        }
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=cancel_response,
        ) as send_request:
            sale_order.action_cancel()

        send_request.assert_called_once_with(
            'POST',
            f"/orders/{self.order_data['order_id']}/cancel",
            params=None,
            data=None,
            json={
                'total_amount': {
                    'amount': cancel_amount,
                    'currency': currency_name,
                },
            },
            reference=tx.reference,
        )
        self.assertEqual(sale_order.state, 'cancel')
        self.assertEqual(tx.state, 'authorized')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('Payment is Canceled successfully on Tamara side', tx.state_message)
        self.assertIn(f'{cancel_amount:.2f} {currency_name}', tx.state_message)

    def test_sale_order_cancel_logs_tamara_failure(self):
        sale_order, tx = self._create_sale_order_with_tamara_tx()
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            side_effect=ValidationError("order already captured"),
        ):
            sale_order.action_cancel()

        self.assertEqual(sale_order.state, 'cancel')
        self.assertEqual(tx.state, 'authorized')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('Cancel action on Tamara side failed', tx.state_message)
        self.assertIn('order already captured', tx.state_message)

    def test_fully_invoice_triggers_tamara_capture(self):
        self.provider.tamara_capture_trigger = 'fully_invoice'
        sale_order, tx = self._create_sale_order_with_tamara_tx()
        capture_amount = float(tx.amount)
        currency_name = tx.currency_id.name
        capture_response = {
            'capture_id': 'cap_invoice_123',
            'order_id': self.order_data['order_id'],
            'status': 'fully_captured',
            'captured_amount': {
                'amount': capture_amount,
                'currency': currency_name,
            },
        }
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=capture_response,
        ) as send_request:
            sale_order._create_invoices()
            self.assertEqual(sale_order.invoice_status, 'invoiced')

        send_request.assert_called_once()
        self.assertEqual(send_request.call_args.args[0], 'POST')
        self.assertEqual(send_request.call_args.args[1], '/payments/capture')
        self.assertEqual(send_request.call_args.kwargs['json']['order_id'], self.order_data['order_id'])
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.tamara_order_status, 'fully_captured')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('Order captured successfully', tx.state_message)
        self.assertIn(f'{capture_amount:.2f} {currency_name}', tx.state_message)
        self.assertIn('cap_invoice_123', tx.state_message)

    def test_fully_invoice_capture_failure_logs_api_response(self):
        self.provider.tamara_capture_trigger = 'fully_invoice'
        sale_order, tx = self._create_sale_order_with_tamara_tx()
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            side_effect=ValidationError("order not authorised"),
        ):
            sale_order._create_invoices()
            self.assertEqual(sale_order.invoice_status, 'invoiced')

        self.assertEqual(tx.state, 'authorized')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('Capture action on Tamara side failed', tx.state_message)
        self.assertIn('order not authorised', tx.state_message)

    def test_fully_delivered_triggers_tamara_capture(self):
        self.provider.tamara_capture_trigger = 'fully_delivered'
        sale_order, tx = self._create_sale_order_with_tamara_tx(product_type='consu')
        self.assertTrue(sale_order.picking_ids)
        capture_amount = float(tx.amount)
        currency_name = tx.currency_id.name
        capture_response = {
            'capture_id': 'cap_delivery_456',
            'order_id': self.order_data['order_id'],
            'status': 'fully_captured',
            'captured_amount': {
                'amount': capture_amount,
                'currency': currency_name,
            },
        }
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=capture_response,
        ) as send_request:
            picking = sale_order.picking_ids
            picking.move_ids.write({'quantity': 1, 'picked': True})
            picking.button_validate()
            self.assertEqual(sale_order.delivery_status, 'full')

        send_request.assert_called_once()
        self.assertEqual(send_request.call_args.args[1], '/payments/capture')
        self.assertEqual(tx.state, 'done')
        self.assertTrue(tx.state_message.startswith('Tamara:'))
        self.assertIn('Order captured successfully', tx.state_message)
        self.assertIn('cap_delivery_456', tx.state_message)

    def test_capture_not_triggered_when_action_not_selected(self):
        self.provider.tamara_capture_trigger = 'none'
        sale_order, tx = self._create_sale_order_with_tamara_tx()
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
        ) as send_request:
            sale_order._create_invoices()
            self.assertEqual(sale_order.invoice_status, 'invoiced')

        send_request.assert_not_called()
        self.assertEqual(tx.state, 'authorized')
        self.assertFalse(tx.state_message)

    def test_tamara_note_prefix_is_translated_separately(self):
        prefix = self.env['payment.transaction']._tamara_note_prefix()
        note = self.env['payment.transaction']._tamara_format_note("Payment was fully canceled.")
        self.assertEqual(prefix, "Tamara:")
        self.assertEqual(note, "Tamara: Payment was fully canceled.")
