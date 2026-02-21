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
        """POST /auth/login → returns access_token string.

        Uses form-encoded data (OAuth2PasswordRequestForm pattern).
        """
        url = f'{self.base_url}/auth/login'
        try:
            resp = self._session.post(url, data={
                'username': email,
                'password': password,
            }, headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=30)
            if resp.status_code >= 400:
                raise ProductsManagerAPIError(
                    f'API error {resp.status_code}: {resp.text[:500]}', resp.status_code)
            data = resp.json()
        except requests.ConnectionError:
            raise ProductsManagerAPIError(f'Connection failed: cannot reach {self.base_url}')
        except requests.Timeout:
            raise ProductsManagerAPIError('Login request timed out')
        except requests.RequestException as exc:
            raise ProductsManagerAPIError(f'Login request error: {exc}')
        token = data.get('access_token') or data.get('token')
        if not token:
            raise ProductsManagerAPIError('Login succeeded but no token in response')
        self.token = token
        self._session.headers['Authorization'] = f'Bearer {token}'
        return token

    # ── Products ────────────────────────────────────────────────────────

    def search_products(self, query, page=1, per_page=20):
        """GET /products?search=&page=&per_page= → (items, meta).

        Returns a tuple of (list of products, pagination meta dict).
        Meta contains: total, page, per_page, total_pages, has_next, has_previous.
        """
        params = {'search': query, 'page': page, 'per_page': per_page}
        result = self._request('GET', '/products', params=params)
        if isinstance(result, list):
            return result, {}
        items = result.get('items') or result.get('data') or result.get('results') or []
        meta = result.get('meta') or {}
        return items, meta

    def get_product(self, product_id):
        """GET /products/{id} → ProductDetailResponse."""
        return self._request('GET', f'/products/{product_id}')

    def get_product_suppliers(self, product_id):
        """GET /products/{id}/suppliers → list of supplier objects."""
        return self._request('GET', f'/products/{product_id}/suppliers')

    def get_price_comparison(self, product_id):
        """GET /products/{id}/price-comparison → PriceComparisonResponse.

        Returns dict with keys: product_id, product_name, prices (list),
        min_price, max_price, avg_price, price_trend, trend_percentage.
        Each price entry: supplier_name, supplier_code, supplier_id,
        current_price, stock_quantity, currency, is_available.
        """
        return self._request('GET', f'/products/{product_id}/price-comparison')

    # ── Suppliers ───────────────────────────────────────────────────────

    def get_suppliers(self):
        """GET /suppliers → list of all suppliers."""
        result = self._request('GET', '/suppliers')
        if isinstance(result, list):
            return result
        return result.get('items') or result.get('data') or []

    def search_suppliers(self, search='', page=1, per_page=50, is_active=True):
        """GET /suppliers?search=&page=&per_page=&is_active= → (items, meta)."""
        params = {'page': page, 'per_page': per_page, 'is_active': is_active}
        if search:
            params['search'] = search
        result = self._request('GET', '/suppliers', params=params)
        if isinstance(result, list):
            return result, {}
        items = result.get('items') or result.get('data') or []
        meta = result.get('meta') or {}
        return items, meta

    def get_supplier_products(self, supplier_id, page=1, per_page=20):
        """GET /products?supplier_ids={id}&page=&per_page= → (items, meta)."""
        params = {
            'supplier_ids': str(supplier_id),
            'page': page,
            'per_page': per_page,
        }
        result = self._request('GET', '/products', params=params)
        if isinstance(result, list):
            return result, {}
        items = result.get('items') or result.get('data') or result.get('results') or []
        meta = result.get('meta') or {}
        return items, meta

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

    # ── Enrichment ─────────────────────────────────────────────────────

    def get_enrichment(self, product_id):
        """GET /enrichment/products/{id} → enrichment data."""
        return self._request('GET', f'/enrichment/products/{product_id}')

    # ── Utility ─────────────────────────────────────────────────────────

    def test_connection(self):
        """Quick connectivity check — tries to list products with per_page=1."""
        try:
            self._request('GET', '/products', params={'per_page': 1})
            return True, 'Connection successful'
        except ProductsManagerAPIError as exc:
            return False, str(exc)
