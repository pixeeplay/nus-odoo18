"""Wing GraphQL API v3 client.

Pure Python helper — no Odoo ORM dependency.
Supports two authentication modes:
  1. Static API token (recommended) — obtained from my.wing.eu portal
  2. Email/password — attempts createAccessToken mutation (if available)
"""
import logging

import requests

_logger = logging.getLogger(__name__)

WING_API_URL = 'https://api-developer.wing.eu/v3'


class WingAPIError(Exception):
    """Raised when the Wing API returns an error."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or []


class WingAPI:
    """GraphQL client for Wing logistics API v3."""

    def __init__(self, token=None, email=None, password=None):
        """Initialize with either a static token or email/password.

        Args:
            token: Static Bearer API token from Wing portal (recommended)
            email: Wing account email (fallback auth)
            password: Wing account password (fallback auth)
        """
        self.token = token
        self.email = email
        self.password = password

    # ------------------------------------------------------------------
    # GraphQL execution
    # ------------------------------------------------------------------

    def _get_headers(self):
        """Build request headers with authentication."""
        if not self.token and self.email and self.password:
            self._authenticate_email()
        if not self.token:
            raise WingAPIError(
                "No Wing API token available. "
                "Please set a token in the carrier configuration.")
        return {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }

    def _authenticate_email(self):
        """Try to authenticate via email/password (mutation or REST)."""
        # Attempt 1: GraphQL mutation (as documented)
        query = """
        mutation {
            createAccessToken(input: {
                email: "%s"
                password: "%s"
            }) {
                accessToken
                refreshToken
                expiresAt
            }
        }
        """ % (self.email.replace('"', '\\"'),
               self.password.replace('"', '\\"'))
        try:
            resp = requests.post(
                WING_API_URL, json={'query': query}, timeout=30)
            body = resp.json()
            data = (body.get('data') or {}).get('createAccessToken') or {}
            if data.get('accessToken'):
                self.token = data['accessToken']
                _logger.info("Wing: authenticated via GraphQL mutation")
                return
        except Exception as exc:
            _logger.debug("Wing: GraphQL auth failed: %s", exc)

        # Attempt 2: try as a plain Bearer token (email field might be token)
        _logger.warning(
            "Wing: createAccessToken mutation not available. "
            "The email/password might be incorrect, or you may need "
            "a static API token from my.wing.eu.")
        raise WingAPIError(
            "Wing authentication failed. The createAccessToken mutation "
            "is not available on this API version.\n\n"
            "Please obtain a static API token from your Wing portal "
            "(my.wing.eu) and paste it in the 'Wing API Token' field.")

    def _execute(self, query, variables=None):
        """Execute a GraphQL query/mutation."""
        headers = self._get_headers()
        payload = {'query': query}
        if variables:
            payload['variables'] = variables

        try:
            resp = requests.post(WING_API_URL, json=payload, headers=headers,
                                 timeout=60)
        except requests.RequestException as exc:
            raise WingAPIError(f"Wing API request failed: {exc}")

        # Read body first — GraphQL may return 400 with useful errors
        try:
            body = resp.json()
        except Exception:
            if resp.status_code >= 400:
                raise WingAPIError(
                    f"Wing API HTTP {resp.status_code}: {resp.text[:500]}")
            raise WingAPIError("Wing API returned non-JSON response")

        if 'errors' in body:
            msgs = '; '.join(e.get('message', '') for e in body['errors'])
            raise WingAPIError(f"Wing GraphQL error: {msgs}", body['errors'])

        if resp.status_code == 401:
            raise WingAPIError(
                "Wing API: Unauthorized (401). "
                "Your API token may be invalid or expired.")

        if resp.status_code >= 400 and 'data' not in body:
            raise WingAPIError(
                f"Wing API HTTP {resp.status_code}: {resp.text[:500]}")

        return body.get('data', {})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_expeditors(self, limit=50):
        """List available carriers for the organization."""
        query = """
        query GetExpeditors($limit: Int) {
            organizationExpeditors(limit: $limit) {
                id
                name
                code
                isActive
                carriersAccounts {
                    id
                    code
                }
            }
        }
        """
        data = self._execute(query, {'limit': limit})
        return data.get('organizationExpeditors', [])

    def get_pickups(self, limit=50):
        """List configured pickup locations."""
        query = """
        query GetPickups($limit: Int) {
            organizationPickups(limit: $limit) {
                id
                name
                address {
                    street
                    city
                    zipCode
                    country
                }
                isActive
            }
        }
        """
        data = self._execute(query, {'limit': limit})
        return data.get('organizationPickups', [])

    def get_order(self, order_id):
        """Retrieve a single order by ID."""
        query = """
        query GetOrder($input: OrderInput!) {
            order(input: $input) {
                id
                ref
                status
                createdAt
                recipient {
                    firstName
                    lastName
                    email
                }
                fulfillmentOrders {
                    id
                    status
                    wingRef
                    parcels {
                        id
                        trackingNumber
                        status
                        weight
                    }
                }
            }
        }
        """
        data = self._execute(query, {'input': {'orderId': order_id}})
        return data.get('order', {})

    def get_fulfillment_order(self, fulfillment_order_id):
        """Retrieve fulfillment order with parcels and tracking info."""
        query = """
        query GetFulfillmentOrder($input: FulfillmentOrderInput!) {
            fulfillmentOrder(input: $input) {
                id
                orderId
                wingRef
                status
                createdAt
                isReturn
                parcels {
                    id
                    trackingNumber
                    status
                    weight
                }
                invoiceUrl
            }
        }
        """
        data = self._execute(
            query,
            {'input': {'fulfillmentOrderId': fulfillment_order_id}})
        return data.get('fulfillmentOrder', {})

    def get_orders(self, limit=25, offset=0):
        """List orders with pagination."""
        query = """
        query GetOrders($limit: Int, $offset: Int) {
            orders(limit: $limit, offset: $offset) {
                id
                ref
                status
                createdAt
                fulfillmentOrders {
                    id
                    status
                }
            }
        }
        """
        data = self._execute(query, {'limit': limit, 'offset': offset})
        return data.get('orders', [])

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def create_order(self, reference, customer, shipping_address, products):
        """Create a new shipping order in Wing.

        Args:
            reference: Unique order reference (e.g. picking name)
            customer: dict with firstName, lastName, email, phone
            shipping_address: dict with street, city, zipCode, country
            products: list of dicts with id, sku, quantity, price

        Returns:
            dict with id, reference, status, fulfillmentOrders
        """
        query = """
        mutation CreateOrder($input: OrderToCreateInput!) {
            createOrder(input: $input) {
                id
                reference
                status
                fulfillmentOrders {
                    id
                    status
                    wingRef
                }
            }
        }
        """
        variables = {
            'input': {
                'reference': reference,
                'customer': customer,
                'shippingAddress': shipping_address,
                'products': products,
            }
        }
        data = self._execute(query, variables)
        return data.get('createOrder', {})

    def create_parcel(self, fulfillment_order_id, weight_grams,
                      length_cm=None, width_cm=None, height_cm=None):
        """Add a parcel to a fulfillment order.

        Args:
            fulfillment_order_id: ID of the fulfillment order
            weight_grams: Parcel weight in grams
            length_cm, width_cm, height_cm: Optional dimensions in cm

        Returns:
            Updated fulfillment order with parcels
        """
        query = """
        mutation CreateParcel($input: CreateFulfillmentParcelInput!) {
            createFulfillmentParcel(input: $input) {
                id
                status
                wingRef
                parcels {
                    id
                    trackingNumber
                    status
                    weight
                }
            }
        }
        """
        parcel_input = {
            'fulfillmentOrderId': fulfillment_order_id,
            'weight': weight_grams,
        }
        if length_cm and width_cm and height_cm:
            parcel_input['dimensions'] = {
                'length': length_cm,
                'width': width_cm,
                'height': height_cm,
            }
        data = self._execute(query, {'input': parcel_input})
        return data.get('createFulfillmentParcel', {})

    def cancel_parcels(self, fulfillment_order_ids):
        """Cancel parcel labels for given fulfillment orders."""
        query = """
        mutation CancelParcels($input: CancelFulfillmentOrderParcelsInput!) {
            cancelFulfillmentOrderParcels(input: $input) {
                fulfillmentOrders {
                    id
                    status
                }
            }
        }
        """
        data = self._execute(
            query,
            {'input': {'fulfillmentOrderIds': fulfillment_order_ids}})
        return data.get('cancelFulfillmentOrderParcels', {})

    def create_return_parcel(self, fulfillment_order_id,
                             reason='CUSTOMER_REQUEST', quantity=1):
        """Create a return parcel for a fulfillment order."""
        query = """
        mutation CreateReturn($input: CreateFulfillmentReturnParcelInput!) {
            createFulfillmentReturnParcel(input: $input) {
                id
                status
            }
        }
        """
        data = self._execute(query, {
            'input': {
                'fulfillmentOrderId': fulfillment_order_id,
                'reason': reason,
                'quantity': quantity,
            }
        })
        return data.get('createFulfillmentReturnParcel', {})

    def add_to_collect(self, fulfillment_order_ids):
        """Add fulfillment orders to the next collection batch."""
        query = """
        mutation AddToCollect($input: AddFulfillmentOrdersToCollectInput!) {
            addFulfillmentOrdersToCollect(input: $input) {
                id
                status
            }
        }
        """
        data = self._execute(
            query,
            {'input': {'fulfillmentOrderIds': fulfillment_order_ids}})
        return data.get('addFulfillmentOrdersToCollect', {})

    def schedule_next_collect(self):
        """Schedule the next carrier collection/pickup."""
        query = """
        mutation ScheduleCollect {
            upsertNextCollect {
                id
                status
                scheduledAt
            }
        }
        """
        data = self._execute(query)
        return data.get('upsertNextCollect', {})
