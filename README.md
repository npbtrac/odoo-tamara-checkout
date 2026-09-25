# Tamara

Odoo payment provider for [Tamara](https://tamara.co) buy-now-pay-later (instalments), aimed at Saudi Arabia, the UAE, and the GCC.

API: [Checkout API](https://docs.tamara.co/docs/direct-online-checkout)

Depends on `payment`, `website_sale`, and `sale_stock`.

## User Guides

### 1. Enable the Tamara addon

1. Log in to Odoo as an **Administrator** (Settings access).
2. Open **Apps** via **Home menu (⚙ / app switcher) → Apps**.
3. Remove the **Apps** filter on the search bar if needed so you can see installed and available modules.
4. Search for **Tamara** (technical name: `payment_tamara`).
5. Click **Activate** / **Install**.
6. Wait until installation finishes. Odoo will also load its dependencies (`payment`, `website_sale`, `sale_stock`).

After install, Tamara appears as a payment provider and as the **Payment Gateway - Tamara** app in the main menu.

### 2. Access Tamara settings

Open the Tamara provider form via any of these paths:

- **Home menu → Payment Gateway - Tamara → Settings**
- **Home menu → Website → Configuration → eCommerce → Tamara**
- **Home menu → Accounting → Configuration → Payment Providers → Tamara**
- **Home menu → Website → Configuration → Payment Providers → Tamara**

All of these open the same Tamara payment provider settings form.

### 3. Configure Tamara

Open settings first (**Home menu → Payment Gateway - Tamara → Settings**), then on the form:

1. **Mode**
   - Choose **Sandbox** for testing, or **Live** (Enabled) for production.
   - Sandbox and Live keep separate credentials.

2. **Credentials** (from the Tamara merchant portal)
   - Fill the Sandbox or Live group under **Configuration** on the provider form:
     - **API Token (Merchant Token)**
     - **Notification Token** (used to verify webhooks)
     - **Public Key** (used for frontend widgets)

3. **State / availability**
   - On the same form, set the provider so it is available on the website (published / enabled as required for your Odoo payment setup).
   - Under **Configuration → Availability**, restrict **countries** / **currencies** if needed. Tamara supports **SA / AE** and **SAR / AED**.

4. **Order Capture** (optional)
   - On the form, under **Order Capture**:
     - **Action to trigger Order Capture**:
       - **Select an action** — no automatic capture (use manual capture, or leave authorised until you capture later)
       - **Fully invoice** — fully capture on Tamara when the sale order is fully invoiced
       - **Fully Delivered** — fully capture on Tamara when the sale order is fully delivered

5. **Save**
   - Click **Save** on the provider form.
   - Saving registers the webhook URL (`/payment/tamara/webhook`) with Tamara and stores the webhook id/URL on the form.
   - For real webhook delivery, Odoo must be reachable over **HTTPS** (public URL / ngrok / reverse proxy). After changing the public URL, open **Payment Gateway - Tamara → Settings** again and **Save** so the webhook is re-registered.

If save fails with **Wrong API Token**, check the API token for the selected mode (Sandbox vs Live).

### 4. Checkout with Tamara on the website

1. Open the shop (`/shop`) and add products to the cart.
2. Go to **Checkout** and fill in the customer details.
   - A valid **phone** and **email** are required; otherwise Tamara is hidden.
   - Billing country/currency should match Tamara (e.g. Saudi Arabia + SAR, or UAE + AED).
3. On the payment step, select **Tamara** (instalments). Promo widgets may also appear on the product and cart pages when the provider is published.
4. Confirm / pay. The customer is redirected to Tamara’s checkout page.
5. Complete the Tamara payment flow (approve the instalment plan).
6. After success, Tamara redirects back to Odoo (`/payment/tamara/return`). Odoo re-fetches the order from Tamara and updates the payment (typically **Authorized** after approval/authorisation).
7. The linked sale order is confirmed according to Odoo’s normal payment post-processing.
8. Capture happens later either:
   - automatically (if you configured **Fully invoice** or **Fully Delivered**), or
   - manually via **Capture Transaction** on the authorized payment.

If checkout creation fails, the customer sees a generic unavailable message; admins see a detailed `Tamara:` note on the payment / sale order.
## Features

### Checkout & payment

- Redirect payment flow: `POST /checkout` → customer redirected to Tamara `checkout_url`
- Stores Tamara `order_id` and checkout URL on the payment transaction
- Customer return route `/payment/tamara/return` re-fetches the order from Tamara (does not trust query status)
- Checkout uses `PAY_BY_INSTALMENTS` (3 instalments)
- Checkout creation failures:
  - Customer sees a generic message: *Tamara payment is unavailable at this time, please choose another payment option*
  - Payment / linked sale order get a detailed note: `Tamara: Cannot create the checkout session, error from Tamara: …`

### Webhooks

- Registers `/payment/tamara/webhook` with Tamara on provider settings save (`POST /webhooks`)
- Verifies Tamara notification JWT (HS256) via `tamaraToken` or `Authorization: Bearer`
- Ignores `event_type`; always re-fetches the order by payload `order_id`
- On `approved`: auto-authorises the order on Tamara, then updates the Odoo payment
- On `declined` / `expired` / `canceled`: cancels the Odoo payment (if not already canceled) and logs `Tamara: Tamara payment for the order is declined/expired/cancelled` on the payment and sale order
- For capture / refund / partial cancel statuses: **logs a `Tamara:` note only** — does not change Odoo payment or sale order state
- Registered events: `order_approved`, `order_declined`, `order_authorised`, `order_canceled`, `order_captured`, `order_refunded`, `order_expired`

### Capture, void & refund

- Manual capture (full amount) via **Capture Transaction** on an authorized payment / sale order → `POST /payments/capture`
- Automatic full capture when the provider setting **Action to trigger Order Capture** matches:
  - **Fully invoice** — when the sale order becomes fully invoiced
  - **Fully Delivered** — when the sale order becomes fully delivered
  - Success note: `Tamara: Order captured successfully. Capture amount: …. Capture Id: …`
  - Failure note: `Tamara: Capture action on Tamara side failed, error from Tamara: …`
- Void authorised payments → `POST /orders/{id}/cancel`
- Refunds (including partial) → `POST /payments/simplified-refund/{id}`

### Sale order cancel

- When a sale order linked to a Tamara payment is cancelled in Odoo:
  - Cancels the Tamara order for the sale order amount (`POST /orders/{id}/cancel`)
  - Logs success or failure as a `Tamara:` note on the payment and sale order
  - Does not change the Odoo payment transaction state

### Website widgets & eligibility

- Product page promo widget (above Add to Cart)
- Cart page promo widget (above Checkout)
- Checkout payment option labels (EN/AR; KSA Sharia copy vs other countries) + inline widget
- Widgets only render when the Tamara provider is **published**
- Pre-checkout eligibility (`POST /pre-checkout/v1/eligibility`, 2s timeout, fail-open)
- Hides Tamara when billing phone or email is missing

### Provider settings

- Sandbox / Live modes with separate API token, notification token, and public key
- Automatic webhook registration on save (stores webhook id + URL)
- **Action to trigger Order Capture**: Select an action / Fully invoice / Fully Delivered
- Admin menu: **Payment Gateway - Tamara → Settings**

### Scope & limits

| Supported | Not implemented |
|-----------|-----------------|
| Countries: SA, AE | Tokenization |
| Currencies: SAR, AED | Express checkout |
| Locales: `en_US`, `ar_SA` | |

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

For Tamara webhooks, use a public HTTPS URL (Cloudflare, ngrok, etc.):

```bash
# Cloudflare / reverse-proxy testing server: set the public HTTPS origin in .env
# PUBLIC_BASE_URL=https://shop.example.com
./scripts/run-website.sh

# Or start ngrok from the script (needs the ngrok CLI)
./scripts/run-website.sh --ngrok

# Or set NGROK_URL in .env to your HTTPS tunnel and start ngrok yourself
# NGROK_URL=https://your-subdomain.ngrok-free.app
# NGROK_ENABLED=1
```

The script sets `web.base.url` (and the website domain) to that HTTPS URL and enables Odoo `proxy_mode` so Cloudflare's `X-Forwarded-Proto` is trusted. Non-local `http://` public URLs are upgraded to `https://`. Re-save Tamara settings so the webhook is registered against the public URL.

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
