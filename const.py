from odoo.addons.payment.const import SENSITIVE_KEYS as PAYMENT_SENSITIVE_KEYS

SENSITIVE_KEYS = {'tamaraToken', 'Authorization'}
PAYMENT_SENSITIVE_KEYS.update(SENSITIVE_KEYS)

# Sandbox and production API hosts.
API_URLS = {
    'prod': 'https://api.tamara.co',
    'test': 'https://api-sandbox.tamara.co',
}

# Widget CDN hosts (sandbox vs production).
WIDGET_URLS = {
    'prod': 'https://cdn.tamara.co/widget-v2/tamara-widget.js',
    'test': 'https://cdn-sandbox.tamara.co/widget-v2/tamara-widget.js',
}

# ISO 4217 currency → ISO 3166-1 alpha-2 country for Tamara widgets.
CURRENCY_COUNTRY_MAP = {
    'SAR': 'SA',
    'AED': 'AE',
}

# ISO 3166-1 alpha-2 country codes supported by Tamara checkout.
SUPPORTED_COUNTRIES = {
    'SA',
    'AE',
}

# ISO 4217 currency codes supported by Tamara checkout.
SUPPORTED_CURRENCIES = {
    'SAR',
    'AED',
}

# Tamara checkout locales (RFC 1766).
SUPPORTED_LOCALES = {
    'ar_SA',
    'en_US',
}

# Order webhook events registered with Tamara. This matches the request example in
# https://docs.tamara.co/reference/registerwebhookurl; the `order_updated` event listed in
# the schema enum is rejected by the API ("Invalid registered event order_updated").
WEBHOOK_EVENTS = [
    'order_approved',
    'order_declined',
    'order_authorised',
    'order_canceled',
    'order_captured',
    'order_refunded',
    'order_expired',
]

# Error code returned when the webhook URL is already registered.
WEBHOOK_ALREADY_REGISTERED = 'webhook_already_registered'

# Pre-checkout eligibility timeout (seconds).
ELIGIBILITY_TIMEOUT = 2.0

# Payment method codes to activate when Tamara is enabled.
DEFAULT_PAYMENT_METHOD_CODES = {
    'tamara',
}

# Checkout payment option labels (KSA vs other countries, EN/AR).
PAYMENT_LABELS = {
    'title': {
        'en': 'Tamara',
        'ar': 'تمارا',
    },
    'description': {
        'SA': {
            'en': 'Monthly Payments. Sharia Compliant.',
            'ar': 'دفعات شهرية. متوافقة مع الشريعة',
        },
        'default': {
            'en': 'Monthly Payments.',
            'ar': 'دفعات شهرية',
        },
    },
}

# Order statuses treated as a successful payment (authorised or captured).
DONE_STATUSES = {
    'authorised',
    'authorized',  # Defensive: some payloads use US spelling.
    'captured',
    'fully_captured',
    'partially_captured',
}

# Order statuses treated as an authorization that still needs capture.
AUTHORIZED_STATUSES = {
    'authorised',
    'authorized',
}

# Order statuses treated as a captured payment (Odoo Confirmed).
CAPTURED_STATUSES = {
    'captured',
    'fully_captured',
    'partially_captured',
}

# Order statuses treated as a cancellation or decline.
CANCELED_STATUSES = {
    'declined',
    'expired',
    'canceled',
    'cancelled',
}

# Order statuses treated as pending customer action.
PENDING_STATUSES = {
    'new',
    'approved',
}
