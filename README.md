# Tamara

## Technical details

API: [Checkout API](https://docs.tamara.co/docs/direct-online-checkout)

This module integrates Tamara using the generic payment with redirection flow provided by the
`payment` module, plus website ecommerce widgets and pre-checkout eligibility.

## Supported features

- Payment with redirection flow (`POST /checkout` → redirect to `checkout_url`)
- Webhook registration on provider settings save (`POST /webhooks`) with stored `webhook_id`
- Webhook notifications (JWT verification)
- Order authorisation after customer approval
- Manual capture
- Full and partial refunds
- Cancellation of authorised orders
- Product page promo widget above Add to Cart (`tamara-summary`, inline-type `2`; amount updates on variant change)
- Cart page promo widget above Checkout (`tamara-summary`, inline-type `5`)
- Checkout payment labels (KSA Sharia copy vs other countries, EN/AR) + checkout widget (inline-type `6`)
- Pre-checkout eligibility ([docs](https://docs.tamara.co/reference/pre-checkout-eligibility)): 2s timeout, fail-open on API errors; Tamara is hidden when billing phone or email is missing (no API call)
- Pre-checkout eligibility gating (`POST /pre-checkout/v1/eligibility`, 2s timeout, fail-open)

## Not implemented features

- Tokenization
- Express checkout

## Demo store setup (macOS / Ubuntu)

From the repository root:

```bash
chmod +x scripts/setup_demo_store.sh scripts/start-website.sh scripts/stop-website.sh

# Setup (idempotent — already-done steps are skipped)
TAMARA_NOTIFICATION_TOKEN='your-partners-portal-notification-token' ./scripts/setup_demo_store.sh

# Start (foreground)
./scripts/start-website.sh

# Or start as a background service
./scripts/start-website.sh --service

# Stop the background service
./scripts/stop-website.sh
```

Open `http://localhost:8069/shop`. Backend login is `admin` / `admin`.

## Testing instructions

An HTTPS connection is required for webhooks.

Use Sandbox mode on the Tamara provider and the sandbox credentials from the Tamara merchant
portal. Sandbox API host: `https://api-sandbox.tamara.co`.

Saving the Tamara provider (with a valid API token) registers the webhook URL
`/payment/tamara/webhook` for all documented order events and stores the returned `webhook_id`
plus the registered webhook URL (shown under Webhook ID in settings).
If Tamara responds with `webhook_already_registered`, the existing id/URL are still saved.
