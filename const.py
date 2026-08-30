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

# ISO 3166-1 alpha-2 country codes supported by Tamara checkout.
SUPPORTED_COUNTRIES = {
    'SA',
    'AE',
    'BH',
    'KW',
    'OM',
}

# ISO 4217 currency codes supported by Tamara checkout.
SUPPORTED_CURRENCIES = {
    'SAR',
    'AED',
    'BHD',
    'KWD',
    'OMR',
}

# Tamara checkout locales (RFC 1766).
SUPPORTED_LOCALES = {
    'ar_SA',
    'en_US',
}

# Order webhook events registered with Tamara (docs enum + examples).
WEBHOOK_EVENTS = [
    'order_approved',
    'order_declined',
    'order_authorised',
    'order_canceled',
    'order_updated',
    'order_captured',
    'order_refunded',
    'order_expired',
]

# Error code returned when the webhook URL is already registered.
WEBHOOK_ALREADY_REGISTERED = 'webhook_already_registered'

# Pre-checkout eligibility timeout (seconds). Docs recommend 200ms.
ELIGIBILITY_TIMEOUT = 0.2

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
