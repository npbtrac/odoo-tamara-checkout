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
- Product and cart widgets are only rendered when the Tamara provider is published (Published toggle on the provider); unpublishing hides them and skips the widget script
- Checkout payment labels (KSA Sharia copy vs other countries, EN/AR) + checkout widget (inline-type `6`)
- Pre-checkout eligibility ([docs](https://docs.tamara.co/reference/pre-checkout-eligibility)): 2s timeout, fail-open on API errors; Tamara is hidden when billing phone or email is missing (no API call)
- Pre-checkout eligibility gating (`POST /pre-checkout/v1/eligibility`, 2s timeout, fail-open)

## Not implemented features

- Tokenization
- Express checkout

## Demo store setup (macOS / Ubuntu)

From the repository root:

```bash
chmod +x scripts/setup-website.sh scripts/run-website.sh

# Copy .env.example to .env and set HTTP_EXPOSING_PORT / DB_* if needed.
# Set DB_USER (+ DB_PASSWORD) to use an existing PostgreSQL; leave DB_USER
# empty to install PostgreSQL locally.
cp -n .env.example .env

# Setup (idempotent — already-done steps are skipped)
./scripts/setup-website.sh

# Start in the background (default)
./scripts/run-website.sh

# Start with ngrok (HTTPS public URL for Tamara webhooks)
./scripts/run-website.sh --ngrok

# Start in this terminal (stream logs)
./scripts/run-website.sh --foreground

# Stop all demo Odoo processes
./scripts/run-website.sh --stop

# Stop then start again (add --foreground to stream logs)
./scripts/run-website.sh --restart
```

Open `http://localhost:<HTTP_EXPOSING_PORT>/shop` (default `8069`). Backend login is `admin` / `admin`.

For Tamara webhooks, expose the shop with ngrok:

```bash
# Either start ngrok from the script (needs the ngrok CLI)
./scripts/run-website.sh --ngrok

# Or set NGROK_URL in .env to your HTTPS tunnel and start ngrok yourself
# NGROK_URL=https://your-subdomain.ngrok-free.app
# NGROK_ENABLED=1
```

The script sets `web.base.url` (and the website domain) to that HTTPS URL and enables Odoo `proxy_mode`. Re-save Tamara settings so the webhook is registered against the public URL.

## Testing instructions

An HTTPS connection is required for webhooks.

Use Sandbox mode on the Tamara provider and the sandbox credentials from the Tamara merchant
portal. Sandbox API host: `https://api-sandbox.tamara.co`.

Saving the Tamara provider registers the webhook URL `/payment/tamara/webhook` via
[Register Webhook URL](https://docs.tamara.co/reference/registerwebhookurl) for all documented
order events and stores the returned `webhook_id` plus the registered webhook URL.
Registration runs once per save, for any change on the provider.
If Tamara responds with `webhook_already_registered`, the existing id/URL are still saved.
A 4xx response is treated as an invalid API token: the save is aborted and **Wrong API Token**
is shown.

The webhook URL is built from the company website domain when set, otherwise from the
`web.base.url` system parameter. The Tamara sandbox accepts an `http://localhost` URL, so
registration works locally, but Tamara can only deliver notifications to a URL it can reach:
use ngrok for an actual end-to-end test.
