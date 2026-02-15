UPDATE payment_provider
   SET sherlocks_merchant_id = NULL,
       sherlocks_secret_key = NULL,
       sherlocks_key_version = NULL
 WHERE code = 'sherlocks';
