"""Wing GraphQL API v3 client.

Pure Python helper — no Odoo ORM dependency.
Handles authentication, token refresh, and all GraphQL operations.
"""
import json
import logging
import time
from datetime import datetime, timedelta

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

    def __init__(self, email, password, access_token=None, refresh_token=None,
                 token_expires_at=None):
        self.email = email
        self.password = password
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expires_at = token_expires_at
        self._token_changed = False

    @property
    def token_changed(self):
        """True if tokens were refreshed during this session."""
        return self._token_changed

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _ensure_token(self):
        """Authenticate or refresh token if needed."""
        if self.access_token and self.token_expires_at:
            # Refresh 5 minutes before expiry
            if isinstance(self.token_expires_at, str):
                try:
                    expires = datetime.fromisoformat(
                        self.token_expires_at.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    expires = datetime.min
            else:
                expires = self.token_expires_at
            if expires > datetime.now(expires.tzinfo) + timedelta(minutes=5):
                return  # Token still valid
        self._authenticate()

    def _authenticate(self):
        """Obtain a fresh access token via createAccessToken mutation."""
        query = """
        mutation CreateAccessToken($input: CreateAccessTokenInput!) {
            createAccessToken(input: $input) {
                accessToken
                refreshToken
                expiresAt
            }
        }
        """
        variables = {
            'input': {
                'email': self.email,
                'password': self.password,
            }
        }
        payload = {'query': query, 'variables': variables}
        _logger.info("Wing: authenticating as %s", self.email)
        try:
            resp = requests.post(WING_API_URL, json=payload, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise WingAPIError(f"Wing authentication request failed: {exc}")

        body = resp.json()
        if 'errors' in body:
            msgs = '; '.join(e.get('message', '') for e in body['errors'])
            raise WingAPIError(f"Wing authentication failed: {msgs}",
                               body['errors'])

        data = body.get('data', {}).get('createAccessToken', {})
        if not data.get('accessToken'):
            raise WingAPIError("Wing authentication returned no token")

        self.access_token = data['accessToken']
        self.refresh_token = data.get('refreshToken', self.refresh_token)
        self.token_expires_at = data.get('expiresAt', '')
        self._token_changed = True
        _logger.info("Wing: authenticated OK, token expires %s",
                      self.token_expires_at)

    # ------------------------------------------------------------------
    # GraphQL execution
    # ------------------------------------------------------------------

    def _execute(self, query, variables=None):
        """Execute a GraphQL query/mutation with automatic token handling."""
        self._ensure_token()
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
        }
        payload = {'query': query}
        if variables:
            payload['variables'] = variables

        try:
            resp = requests.post(WING_API_URL, json=payload, headers=headers,
                                 timeout=60)
        except requests.RequestException as exc:
            raise WingAPIError(f"Wing API request failed: {exc}")

        if resp.status_code == 401:
            # Token expired — re-auth and retry once
            _logger.warning("Wing: 401 received, re-authenticating")
            self._authenticate()
            headers['Authorization'] = f'Bearer {self.access_token}'
            try:
                resp = requests.post(WING_API_URL, json=payload,
                                     headers=headers, timeout=60)
            except requests.RequestException as exc:
                raise WingAPIError(
                    f"Wing API request failed after re-auth: {exc}")

        if resp.status_code >= 400:
            raise WingAPIError(
                f"Wing API HTTP {resp.status_code}: {resp.text[:500]}")

        body = resp.json()
        if 'errors' in body:
            msgs = '; '.join(e.get('message', '') for e in body['errors'])
            raise WingAPIError(f"Wing GraphQL error: {msgs}", body['errors'])

        return body.get('data', {})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_expeditors(self, limit=50):
        """List available carriers for the organization."""
        query = """
        query GetExpeditors($input: ExpeditorFilterInput, $limit: Int) {
            organizationExpeditors(input: $input, limit: $limit) {
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
        query GetPickups($input: PickupFilterInput, $limit: Int) {
            organizationPickups(input: $input, limit: $limit) {
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
        query GetOrders($input: OrdersInput, $limit: Int, $offset: Int) {
            orders(input: $input, limit: $limit, offset: $offset) {
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
        """Cancel parcel labels for given fulfillment orders.

        Args:
            fulfillment_order_ids: list of fulfillment order IDs

        Returns:
            Cancellation result
        """
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
