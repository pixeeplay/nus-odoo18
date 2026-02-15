# Sips 2.0 Paypage POST API URLs
PRODUCTION_URL = 'https://payment-webinit.sips-services.com/paymentInit'
TEST_URL = 'https://payment-webinit.simu.sips-services.com/paymentInit'

# Sips Paypage POST interface version
INTERFACE_VERSION = 'HP_2.31'

# Mapping of Odoo payment states to Sips response codes
RESPONSE_CODES_MAPPING = {
    'done': ['00'],
    'pending': ['60'],
    'cancel': ['17', '97'],
}

# Default payment method codes for this provider
DEFAULT_PAYMENT_METHODS_CODES = ['card']

# ISO 4217 numeric currency codes
CURRENCY_CODES = {
    'EUR': '978',
    'USD': '840',
    'GBP': '826',
    'CHF': '756',
    'CAD': '124',
    'SEK': '752',
    'NOK': '578',
    'DKK': '208',
}
