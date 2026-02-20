import logging
import requests

_logger = logging.getLogger(__name__)


class ProductsManagerAPIError(Exception):
    """Raised when Products Manager API returns an error."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class ProductsManagerAPI:
    """Pure Python client for Products Manager API (no ORM dependency).

    Usage:
        api = ProductsManagerAPI('https://api.productsmanager.app/api/v1')
        token = api.login('user@example.com', 'password')
        products = api.search_products('bluesound')
    """

    def __init__(self, base_url, token=None):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        if token:
            self._session.headers['Authorization'] = f'Bearer {token}'

    def _request(self, method, endpoint, data=None, params=None, timeout=30):
        """Execute an HTTP request against the PM API."""
        url = f'{self.base_url}{endpoint}'
        try:
            resp = self._session.request(
                method=method,
                url=url,
                json=data,
                params=params,
                timeout=timeout,
            )
            if resp.status_code == 401:
                raise ProductsManagerAPIError('Authentication failed — invalid or expired token', 401)
            if resp.status_code == 403:
                raise ProductsManagerAPIError('Permission denied', 403)
            if resp.status_code == 404:
                raise ProductsManagerAPIError(f'Not found: {endpoint}', 404)
            if resp.status_code == 429:
                raise ProductsManagerAPIError('Rate limit exceeded — try again later', 429)
            if resp.status_code >= 400:
                body = resp.text[:500]
                raise ProductsManagerAPIError(f'API error {resp.status_code}: {body}', resp.status_code)
            return resp.json()
        except requests.ConnectionError:
            raise ProductsManagerAPIError(f'Connection failed: cannot reach {self.base_url}')
        except requests.Timeout:
            raise ProductsManagerAPIError(f'Request timed out after {timeout}s')
        except requests.RequestException as exc:
            raise ProductsManagerAPIError(f'Request error: {exc}')

    # ── Authentication ──────────────────────────────────────────────────

    def login(self, email, password):
        """POST /auth/login → returns access_token string."""
        data = self._request('POST', '/auth/login', data={
            'username': email,
            'password': password,
        })
        token = data.get('access_token') or data.get('token')
        if not token:
            raise ProductsManagerAPIError('Login succeeded but no token in response')
        self.token = token
        self._session.headers['Authorization'] = f'Bearer {token}'
        return token

    # ── Products ────────────────────────────────────────────────────────

    def search_products(self, query, limit=50, offset=0):
        """GET /products?search=&limit=&offset= → list of products."""
        params = {'search': query, 'limit': limit, 'offset': offset}
        result = self._request('GET', '/products', params=params)
        if isinstance(result, list):
            return result
        return result.get('items') or result.get('data') or result.get('results') or []

    def get_product(self, product_id):
        """GET /products/{id} → product detail."""
        return self._request('GET', f'/products/{product_id}')

    def get_product_suppliers(self, product_id):
        """GET /products/{id}/suppliers → list of supplier offers with prices."""
        return self._request('GET', f'/products/{product_id}/suppliers')

    # ── Suppliers ───────────────────────────────────────────────────────

    def get_suppliers(self):
        """GET /suppliers → list of all suppliers."""
        result = self._request('GET', '/suppliers')
        if isinstance(result, list):
            return result
        return result.get('items') or result.get('data') or []

    def get_supplier_products(self, supplier_id, limit=50, offset=0):
        """GET /suppliers/{id}/products → products for a supplier."""
        params = {'limit': limit, 'offset': offset}
        result = self._request('GET', f'/suppliers/{supplier_id}/products', params=params)
        if isinstance(result, list):
            return result
        return result.get('items') or result.get('data') or []

    # ── Categories ──────────────────────────────────────────────────────

    def get_categories(self):
        """GET /categories → list of categories."""
        result = self._request('GET', '/categories')
        if isinstance(result, list):
            return result
        return result.get('items') or result.get('data') or []

    # ── Search (Meilisearch) ────────────────────────────────────────────

    def global_search(self, query, limit=50):
        """GET /search?q=&limit= → global search results."""
        params = {'q': query, 'limit': limit}
        result = self._request('GET', '/search', params=params)
        if isinstance(result, list):
            return result
        return result.get('items') or result.get('hits') or result.get('data') or []

    # ── Utility ─────────────────────────────────────────────────────────

    def test_connection(self):
        """Quick connectivity check — tries to list products with limit=1."""
        try:
            result = self._request('GET', '/products', params={'limit': 1})
            return True, 'Connection successful'
        except ProductsManagerAPIError as exc:
            return False, str(exc)
