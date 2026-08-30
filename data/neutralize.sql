-- disable tamara payment provider
UPDATE payment_provider
   SET tamara_sandbox_api_token = NULL,
       tamara_sandbox_notification_key = NULL,
       tamara_sandbox_public_key = NULL,
       tamara_sandbox_webhook_id = NULL,
       tamara_sandbox_webhook_url = NULL,
       tamara_live_api_token = NULL,
       tamara_live_notification_key = NULL,
       tamara_live_public_key = NULL,
       tamara_live_webhook_id = NULL,
       tamara_live_webhook_url = NULL;
