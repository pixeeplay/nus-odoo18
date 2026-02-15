-- disable alma payment provider
UPDATE payment_provider
   SET alma_api_test_key = NULL,
       alma_api_key = NULL;
