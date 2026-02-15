# -*- coding: utf-8 -*-

# Mapping of transaction states to Alma payment statuses.
PAYMENT_STATUS_MAPPING = {
    'pending': ['scored_yes', 'scored_maybe'],
    'done': ['paid', 'in_progress'],
    'cancel': ['not_started'],
    'error': ['scored_no'],
}

# The codes of the payment methods to activate when Alma is activated.
DEFAULT_PAYMENT_METHODS_CODES = [
    'alma',
]
