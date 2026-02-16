# -*- coding: utf-8 -*-
import time
import logging
import requests

_logger = logging.getLogger(__name__)


class SearXNGClient:
    """Client for querying a self-hosted SearXNG instance."""

    def __init__(self, base_url, engines='google,duckduckgo', language='fr-FR',
                 max_results=8, timeout=15, delay_between_requests=3.0):
        self.base_url = base_url.rstrip('/')
        self.engines = engines
        self.language = language
        self.max_results = max_results
        self.timeout = timeout
        self.delay = delay_between_requests

    def search(self, query, categories='general'):
        """Execute a SearXNG search and return a list of result dicts.

        Each result: {'title': str, 'content': str, 'url': str, 'engine': str}
        """
        url = f"{self.base_url}/search"
        params = {
            'q': query,
            'format': 'json',
            'categories': categories,
            'language': self.language,
            'engines': self.engines,
        }
        _logger.info("SearXNG search: %s (engines=%s)", query[:80], self.engines)
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 200:
                _logger.error("SearXNG HTTP %s: %s", resp.status_code, resp.text[:300])
                return []
            data = resp.json()
            results = data.get('results', [])[:self.max_results]
            _logger.info("SearXNG returned %d results for '%s'", len(results), query[:60])
            return results
        except requests.exceptions.ConnectionError as e:
            _logger.error("SearXNG connection error (%s): %s", self.base_url, e)
            return []
        except requests.exceptions.Timeout:
            _logger.error("SearXNG timeout after %ss", self.timeout)
            return []
        except ValueError:
            _logger.error("SearXNG returned invalid JSON")
            return []

    def search_product(self, product_name, ean='', brand=''):
        """Multi-angle product search: technical + commercial queries.

        Returns {'query_tech': str, 'query_commercial': str, 'results': list}
        """
        # Build technical query
        parts = [product_name]
        if ean:
            parts.append(ean)
        query_tech = ' '.join(parts) + ' fiche technique caractéristiques'

        # Build commercial query
        parts_com = [product_name]
        if brand:
            parts_com.append(brand)
        query_commercial = ' '.join(parts_com) + ' avis prix comparatif'

        # Run both searches with delay between them
        results_tech = self.search(query_tech)
        time.sleep(self.delay)
        results_commercial = self.search(query_commercial, categories='general')

        # Merge and deduplicate by URL
        seen_urls = set()
        merged = []
        for r in results_tech + results_commercial:
            url = r.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                merged.append({
                    'title': r.get('title', ''),
                    'content': r.get('content', ''),
                    'url': url,
                    'engine': r.get('engine', ''),
                })

        return {
            'query_tech': query_tech,
            'query_commercial': query_commercial,
            'results': merged,
        }

    def test_connection(self):
        """Test SearXNG connectivity.

        Returns (success: bool, message: str)
        """
        url = f"{self.base_url}/search"
        params = {'q': 'test', 'format': 'json'}
        try:
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                result_count = len(data.get('results', []))
                return True, f"OK — {result_count} results returned"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.exceptions.ConnectionError as e:
            return False, f"Connection failed: {e}"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Error: {e}"
