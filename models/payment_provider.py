import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import urls

from odoo.addons.payment_tamara import const
from odoo.addons.payment_tamara.controllers.main import TamaraController


_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('tamara', "Tamara")], ondelete={'tamara': 'set default'}
    )
    tamara_state = fields.Selection(
        string="Tamara Mode",
        selection=[
            ('disabled', "Disabled"),
            ('sandbox', "Sandbox"),
            ('enabled', "Live"),
        ],
        compute='_compute_tamara_state',
        inverse='_inverse_tamara_state',
        help="Sandbox uses Tamara sandbox APIs and credentials. Live uses production.",
    )
    tamara_sandbox_mode = fields.Boolean(
        string="Use Sandbox Mode",
        default=True,
        help="When enabled, Tamara sandbox credentials and API host are used.",
    )
    tamara_sandbox_api_token = fields.Text(
        string="Sandbox API Token (Merchant Token)",
        copy=False,
        groups='base.group_system',
    )
    tamara_sandbox_notification_key = fields.Char(
        string="Sandbox Notification Token",
        copy=False,
        groups='base.group_system',
    )
    tamara_sandbox_public_key = fields.Char(
        string="Sandbox Public Key",
        copy=False,
        groups='base.group_system',
    )
    tamara_sandbox_webhook_id = fields.Char(
        string="Sandbox Webhook ID",
        copy=False,
        readonly=True,
        groups='base.group_system',
    )
    tamara_sandbox_webhook_url = fields.Char(
        string="Sandbox Webhook URL",
        copy=False,
        readonly=True,
        groups='base.group_system',
    )
    tamara_live_api_token = fields.Text(
        string="Live API Token (Merchant Token)",
        copy=False,
        groups='base.group_system',
    )
    tamara_live_notification_key = fields.Char(
        string="Live Notification Token",
        copy=False,
        groups='base.group_system',
    )
    tamara_live_public_key = fields.Char(
        string="Live Public Key",
        copy=False,
        groups='base.group_system',
    )
    tamara_live_webhook_id = fields.Char(
        string="Live Webhook ID",
        copy=False,
        readonly=True,
        groups='base.group_system',
    )
    tamara_live_webhook_url = fields.Char(
        string="Live Webhook URL",
        copy=False,
        readonly=True,
        groups='base.group_system',
    )
    tamara_capture_trigger = fields.Selection(
        string="Action to trigger Order Capture",
        selection=[
            ('none', "Select an action"),
            ('fully_invoice', "Fully invoice"),
            ('fully_delivered', "Fully Delivered"),
        ],
        default='none',
        help="When the selected sale-order event happens, automatically fully capture the "
             "linked Tamara order.",
    )

    # === COMPUTE METHODS === #

    @api.depends('code', 'state', 'tamara_sandbox_mode')
    def _compute_tamara_state(self):
        for provider in self:
            if provider.code != 'tamara':
                provider.tamara_state = False
            elif provider.state == 'disabled':
                provider.tamara_state = 'disabled'
            elif provider.tamara_sandbox_mode or provider.state == 'test':
                provider.tamara_state = 'sandbox'
            else:
                provider.tamara_state = 'enabled'

    def _inverse_tamara_state(self):
        for provider in self.filtered(lambda p: p.code == 'tamara'):
            if provider.tamara_state == 'disabled':
                provider.state = 'disabled'
            elif provider.tamara_state == 'sandbox':
                provider.state = 'test'
                provider.tamara_sandbox_mode = True
            else:
                provider.state = 'enabled'
                provider.tamara_sandbox_mode = False

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'tamara').update({
            'support_manual_capture': 'full_only',
            'support_refund': 'partial',
        })

    def _get_supported_currencies(self):
        """Override of `payment` to return the supported currencies."""
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'tamara':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    # === CONSTRAINT METHODS === #

    @api.constrains(
        'code', 'state', 'tamara_sandbox_mode',
        'tamara_sandbox_api_token', 'tamara_sandbox_notification_key', 'tamara_sandbox_public_key',
        'tamara_live_api_token', 'tamara_live_notification_key', 'tamara_live_public_key',
    )
    def _check_tamara_credentials(self):
        for provider in self.filtered(lambda p: p.code == 'tamara' and p.state != 'disabled'):
            missing = [
                provider._fields[name].string
                for name in provider._tamara_credential_field_names()
                if not provider[name]
            ]
            if missing:
                raise ValidationError(_(
                    "The following Tamara fields must be filled for the selected mode: %s",
                    ", ".join(missing),
                ))

    # === CRUD METHODS === #

    @api.model_create_multi
    def create(self, vals_list):
        if self._tamara_skip_webhook_register() or not any(
            vals.get('code') == 'tamara' for vals in vals_list
        ):
            return super().create(vals_list)
        # Roll back the new records if webhook registration rejects the API token.
        with self.env.cr.savepoint():
            providers = super(
                PaymentProvider, self.with_context(tamara_skip_webhook_register=True)
            ).create(vals_list)
            providers.filtered(
                lambda p: p.code == 'tamara'
            ).with_env(self.env)._tamara_register_webhook_on_save()
            return providers.with_env(self.env)

    def write(self, vals):
        tamara_providers = self.filtered(lambda p: p.code == 'tamara')
        if not tamara_providers or self._tamara_skip_webhook_register():
            return super().write(vals)
        # Register once per save: nested writes (field inverses, webhook fields) are skipped.
        # The savepoint rolls the save back when Tamara rejects the API token.
        with self.env.cr.savepoint():
            result = super(
                PaymentProvider, self.with_context(tamara_skip_webhook_register=True)
            ).write(vals)
            tamara_providers._tamara_register_webhook_on_save()
            return result

    def _tamara_skip_webhook_register(self):
        """Return whether webhook registration must be skipped for the current write.

        Registration is skipped for nested writes and while module data is being loaded,
        so installing or upgrading the module never calls the Tamara API.

        :return: Whether to skip webhook registration.
        :rtype: bool
        """
        return bool(
            self.env.context.get('tamara_skip_webhook_register')
            or self.env.context.get('install_mode')
            or self.env.context.get('module')
        )

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        self.ensure_one()
        if self.code != 'tamara':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    @api.model
    def _tamara_refresh_logos(self):
        """Reload Tamara logos from the addon icon file onto provider, method, and app menu."""
        import base64
        from pathlib import Path

        icon_path = Path(__file__).resolve().parents[1] / 'static' / 'description' / 'icon.png'
        if not icon_path.is_file():
            _logger.warning("Tamara icon not found at %s", icon_path)
            return True

        logo_b64 = base64.b64encode(icon_path.read_bytes())
        providers = self.with_context(active_test=False).search([('code', '=', 'tamara')])
        if providers:
            # Clear first so Image attachments are replaced (XML file= updates are often skipped).
            providers.write({'image_128': False})
            providers.write({'image_128': logo_b64})

        methods = self.env['payment.method'].with_context(active_test=False).search([
            ('code', '=', 'tamara'),
        ])
        if methods:
            methods.write({'image': False})
            methods.write({'image': logo_b64})

        menu = self.env.ref('payment_tamara.menu_tamara_root', raise_if_not_found=False)
        if menu:
            menu.write({'web_icon': False, 'web_icon_data': False})
            menu.write({
                'web_icon': 'payment_tamara,static/description/icon.png',
                'web_icon_data': logo_b64,
            })
        return True

    def _get_compatible_providers(
        self, company_id, partner_id, amount, currency_id=None, force_tokenization=False,
        is_express_checkout=False, is_validation=False, report=None, **kwargs
    ):
        """Override of `payment` to hide Tamara when pre-checkout eligibility fails."""
        providers = super()._get_compatible_providers(
            company_id, partner_id, amount, currency_id=currency_id,
            force_tokenization=force_tokenization, is_express_checkout=is_express_checkout,
            is_validation=is_validation, report=report, **kwargs
        )
        partner = self.env['res.partner'].browse(partner_id)
        # Prefer the invoice address from the website order when available.
        sale_order = self.env['sale.order'].browse(kwargs.get('sale_order_id')).exists()
        if sale_order:
            partner = sale_order.partner_invoice_id or sale_order.partner_id or partner
        currency = self.env['res.currency'].browse(currency_id).exists()
        tamara_providers = providers.filtered(lambda p: p.code == 'tamara')
        if not tamara_providers:
            return providers

        ineligible = self.env['payment.provider']
        for provider in tamara_providers:
            if not provider._tamara_is_customer_eligible(
                amount=amount,
                currency=currency,
                partner=partner,
            ):
                ineligible |= provider
        if ineligible:
            from odoo.addons.payment import utils as payment_utils
            from odoo.addons.payment.const import REPORT_REASONS_MAPPING
            payment_utils.add_to_report(
                report,
                ineligible,
                available=False,
                reason=REPORT_REASONS_MAPPING['provider_not_available'],
            )
            providers -= ineligible
        return providers

    # === BUSINESS METHODS === #

    @api.model
    def _tamara_get_website_provider(self, website=None):
        """Return the Tamara provider usable for website widgets/checkout.

        Prefers the current website company. Widgets are only shown for published sandbox
        (`test`) or live (`enabled`) providers that have a public key. Sandbox providers stay
        unpublished by default in core payment, so Tamara must be published to appear on the
        product and cart pages.

        :param website website: Optional website; defaults to the current website.
        :return: The matching provider, or an empty recordset.
        :rtype: payment.provider
        """
        website = website or self.env['website'].get_current_website()
        company = website.company_id if website else self.env.company
        providers = self.sudo().search([
            ('code', '=', 'tamara'),
            ('state', 'in', ['enabled', 'test']),
            ('is_published', '=', True),
            ('company_id', '=', company.id),
        ], order='id')
        for provider in providers:
            if provider._tamara_get_public_key():
                return provider
        return self.browse()

    def _tamara_is_sandbox(self):
        """Return whether Tamara sandbox APIs and credentials should be used.

        :return: Whether sandbox mode is active.
        :rtype: bool
        """
        self.ensure_one()
        return bool(self.tamara_sandbox_mode or self.state == 'test')

    def _tamara_credential_field_names(self):
        """Return the credential field names for the active Tamara mode.

        :return: The field names.
        :rtype: list[str]
        """
        self.ensure_one()
        if self._tamara_is_sandbox():
            return [
                'tamara_sandbox_api_token',
                'tamara_sandbox_notification_key',
                'tamara_sandbox_public_key',
            ]
        return [
            'tamara_live_api_token',
            'tamara_live_notification_key',
            'tamara_live_public_key',
        ]

    def _tamara_get_api_token(self):
        """Return the merchant API token for the active Tamara mode.

        :return: The API token.
        :rtype: str
        """
        self.ensure_one()
        token = (
            self.tamara_sandbox_api_token
            if self._tamara_is_sandbox()
            else self.tamara_live_api_token
        )
        return (token or '').strip()

    def _tamara_get_notification_key(self):
        """Return the notification key for the active Tamara mode.

        :return: The notification key.
        :rtype: str
        """
        self.ensure_one()
        if self._tamara_is_sandbox():
            return self.tamara_sandbox_notification_key
        return self.tamara_live_notification_key

    def _tamara_get_public_key(self):
        """Return the public key for the active Tamara mode.

        :return: The public key.
        :rtype: str
        """
        self.ensure_one()
        if self._tamara_is_sandbox():
            return self.tamara_sandbox_public_key
        return self.tamara_live_public_key

    def _tamara_get_webhook_id_field(self):
        """Return the webhook id field name for the active Tamara mode.

        :return: The field name.
        :rtype: str
        """
        self.ensure_one()
        return (
            'tamara_sandbox_webhook_id' if self._tamara_is_sandbox() else 'tamara_live_webhook_id'
        )

    def _tamara_get_webhook_url_field(self):
        """Return the webhook URL field name for the active Tamara mode.

        :return: The field name.
        :rtype: str
        """
        self.ensure_one()
        return (
            'tamara_sandbox_webhook_url'
            if self._tamara_is_sandbox() else 'tamara_live_webhook_url'
        )

    def _tamara_get_widget_url(self):
        """Return the Tamara widget script URL for the active mode.

        :return: The CDN URL.
        :rtype: str
        """
        self.ensure_one()
        return const.WIDGET_URLS['test' if self._tamara_is_sandbox() else 'prod']

    def _tamara_get_country_code(self, partner=None):
        """Return the ISO country code used for Tamara labels and widgets.

        :param res.partner partner: Optional partner used to resolve the country.
        :return: The country code (e.g. SA).
        :rtype: str
        """
        self.ensure_one()
        partner = partner or self.env.user.partner_id
        return (
            (partner.country_id.code if partner else None)
            or self.company_id.country_id.code
            or 'SA'
        )

    def _tamara_get_payment_labels(self, country_code=None, lang=None):
        """Return the checkout title and description for the given country/language.

        :param str country_code: ISO country code.
        :param str lang: Language code (e.g. ar_001, en_US).
        :return: Dict with `title` and `description`.
        :rtype: dict
        """
        self.ensure_one()
        country_code = (country_code or self._tamara_get_country_code() or 'SA').upper()
        lang = lang or self.env.context.get('lang') or 'en_US'
        lang_key = 'ar' if lang.startswith('ar') else 'en'
        description_map = const.PAYMENT_LABELS['description'].get(
            country_code, const.PAYMENT_LABELS['description']['default']
        )
        return {
            'title': const.PAYMENT_LABELS['title'][lang_key],
            'description': description_map[lang_key],
        }

    def _tamara_get_webhook_url(self):
        """Return the absolute webhook URL Tamara should call.

        :return: The webhook URL.
        :rtype: str
        """
        self.ensure_one()
        return urls.urljoin(self.get_base_url(), TamaraController._webhook_url)

    def _tamara_register_webhook_on_save(self):
        """Register the Tamara webhook for enabled providers after settings are saved.

        Providers without an API token are skipped. A 4xx response from Tamara is treated
        as an invalid API token and raised as `ValidationError` so the settings save is
        aborted.
        """
        for provider in self.filtered(lambda p: p.state != 'disabled' and p._tamara_get_api_token()):
            provider._tamara_register_webhook()

    def _tamara_register_webhook(self):
        """Register the order webhook with Tamara and store the webhook id + URL.

        Calls `POST /webhooks` (https://docs.tamara.co/reference/registerwebhookurl).
        A response of `webhook_already_registered` is treated as success and the existing
        webhook id from the error payload is saved. Any other 4xx response means the API
        token is invalid: a `ValidationError` is raised and the settings save is stopped.

        :return: The webhook id, if any.
        :rtype: str|None
        :raise ValidationError: If Tamara returns a 4xx status (invalid API token).
        """
        self.ensure_one()
        webhook_url = self._tamara_get_webhook_url()
        payload = {
            'type': 'order',
            'url': webhook_url,
            'events': list(const.WEBHOOK_EVENTS),
        }
        url = self._build_request_url('/webhooks')
        headers = self._build_request_headers('POST', '/webhooks', payload)
        _logger.info(
            "Registering Tamara webhook for provider %s: POST %s (url=%s)",
            self.id, url, webhook_url,
        )
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            _logger.warning("Could not reach Tamara to register webhook for provider %s.", self.id)
            return None

        webhook_id = None
        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.ok:
            webhook_id = data.get('webhook_id')
            # Prefer the URL Tamara acknowledged when present.
            webhook_url = data.get('url') or webhook_url
            _logger.info(
                "Tamara webhook registered for provider %s (id=%s, url=%s).",
                self.id, webhook_id, webhook_url,
            )
        else:
            errors = data.get('errors') or []
            first_error = errors[0] if errors else {}
            if first_error.get('error_code') == const.WEBHOOK_ALREADY_REGISTERED:
                error_data = first_error.get('data') or {}
                webhook_id = error_data.get('webhook_id') or data.get('webhook_id')
                webhook_url = error_data.get('url') or data.get('url') or webhook_url
                _logger.info(
                    "Tamara webhook already registered for provider %s (id=%s).",
                    self.id, webhook_id,
                )
            elif 400 <= (response.status_code or 0) < 500:
                _logger.warning(
                    "Tamara webhook registration rejected for provider %s (HTTP %s): %s",
                    self.id, response.status_code, data or response.text,
                )
                details = self._parse_response_error(response)
                message = _("Wrong API Token.")
                if details:
                    message = f'{message}\n\n{details}'
                raise ValidationError(message)
            else:
                _logger.warning(
                    "Tamara webhook registration failed for provider %s: %s",
                    self.id, data or response.text,
                )
                return None

        if webhook_id or webhook_url:
            # Avoid recursive write() webhook registration.
            self.with_context(tamara_skip_webhook_register=True).sudo().write({
                self._tamara_get_webhook_id_field(): webhook_id,
                self._tamara_get_webhook_url_field(): webhook_url,
            })
        return webhook_id

    def _tamara_normalize_phone(self, phone):
        """Return digits-only phone for Tamara eligibility (e.g. 966501234567).

        :param str phone: Raw phone value.
        :return: Normalized phone, or empty string.
        :rtype: str
        """
        if not phone:
            return ''
        return ''.join(ch for ch in str(phone) if ch.isdigit())

    def _tamara_partner_phone(self, partner):
        """Return the best phone number available on the partner.

        :param res.partner partner: Customer partner.
        :return: Normalized phone, or empty string.
        :rtype: str
        """
        if not partner:
            return ''
        return self._tamara_normalize_phone(partner.phone or partner.mobile)

    def _tamara_is_customer_eligible(
        self, amount, currency, partner=None, phone=None, email=None, timeout=None,
    ):
        """Return whether Tamara should be shown for the customer (pre-checkout eligibility).

        Hides Tamara when phone or email is missing (no API call). When both are present,
        calls Tamara's eligibility API. Timeouts / errors fail open (show Tamara):
        https://docs.tamara.co/reference/pre-checkout-eligibility

        :param float amount: Order amount.
        :param res.currency currency: Order currency.
        :param res.partner partner: Customer partner, if any.
        :param str phone: Optional phone override.
        :param str email: Optional email override.
        :param float timeout: Optional request timeout override (seconds).
        :return: Whether Tamara is eligible.
        :rtype: bool
        """
        self.ensure_one()
        partner = partner or self.env['res.partner']
        phone = self._tamara_normalize_phone(phone) or self._tamara_partner_phone(partner)
        email = (email or (partner.email if partner else '') or '').strip()
        # Require phone + email before showing Tamara; skip the API when either is missing.
        if not phone or not email:
            return False
        if not currency or currency.name not in const.SUPPORTED_CURRENCIES:
            return True

        payload = {
            'order': {
                'amount': float(amount or 0),
                'currency': currency.name,
            },
            'customer': {
                'phone_number': phone,
                'email': email,
            },
        }
        url = self._build_request_url('/pre-checkout/v1/eligibility')
        headers = self._build_request_headers('POST', '/pre-checkout/v1/eligibility', payload)
        request_timeout = const.ELIGIBILITY_TIMEOUT if timeout is None else timeout
        try:
            response = requests.post(
                url, json=payload, headers=headers, timeout=request_timeout,
            )
            if not response.ok:
                _logger.info(
                    "Tamara eligibility non-OK for provider %s phone=%s: %s",
                    self.id, phone, response.status_code,
                )
                return True
            data = response.json()
            is_eligible = bool(data.get('is_eligible', True))
            _logger.info(
                "Tamara eligibility for provider %s phone=%s -> %s",
                self.id, phone, is_eligible,
            )
            return is_eligible
        except (requests.exceptions.RequestException, ValueError) as error:
            _logger.info(
                "Tamara eligibility fallback (show) for provider %s phone=%s: %s",
                self.id, phone, error,
            )
            return True

    def _tamara_get_widget_config(self, country_code=None, lang=None):
        """Return values needed to render Tamara widgets on the website.

        Country is resolved from (in order): explicit override, website/pricelist currency,
        partner/company country, then SA.

        :param str country_code: Optional country override.
        :param str lang: Optional language override.
        :return: Widget configuration.
        :rtype: dict
        """
        self.ensure_one()
        lang = lang or self.env.context.get('lang') or 'en_US'
        lang_code = 'ar' if lang.startswith('ar') else 'en'

        if not country_code:
            currency = None
            website = self.env['website'].get_current_website()
            if website:
                currency = website.currency_id
            currency_code = currency.name if currency else None
            country_code = const.CURRENCY_COUNTRY_MAP.get(currency_code)

        return {
            'public_key': self._tamara_get_public_key() or '',
            'country': (country_code or self._tamara_get_country_code() or 'SA').upper(),
            'lang': lang_code,
            'widget_url': self._tamara_get_widget_url(),
            'is_sandbox': self._tamara_is_sandbox(),
        }

    # === REQUEST HELPERS === #

    def _build_request_url(self, endpoint, **kwargs):
        """Override of `payment` to build the request URL."""
        if self.code != 'tamara':
            return super()._build_request_url(endpoint, **kwargs)
        base_url = const.API_URLS['test' if self._tamara_is_sandbox() else 'prod']
        return urls.urljoin(base_url, endpoint.strip('/'))

    def _build_request_headers(self, *args, **kwargs):
        """Override of `payment` to build the request headers."""
        if self.code != 'tamara':
            return super()._build_request_headers(*args, **kwargs)
        return {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self._tamara_get_api_token()}',
            'Content-Type': 'application/json',
        }

    def _parse_response_error(self, response):
        """Override of `payment` to parse the error message."""
        if self.code != 'tamara':
            return super()._parse_response_error(response)

        try:
            error_data = response.json()
        except ValueError:
            return response.text

        message = error_data.get('message') or error_data.get('error')
        if isinstance(error_data.get('errors'), list):
            details = ', '.join(
                err.get('error_code') or err.get('message') or str(err)
                for err in error_data['errors']
            )
            message = f'{message}: {details}' if message else details
        return message or response.text
