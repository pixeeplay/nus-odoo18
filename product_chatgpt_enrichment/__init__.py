# -*- coding: utf-8 -*-
from . import models
from . import services
from . import wizards
import logging

_logger = logging.getLogger(__name__)


def _post_init_fix_searxng_config(env):
    """Force SearXNG config model to mistral after install/upgrade."""
    config = env['chatgpt.config'].search([
        ('searxng_enabled', '=', True),
        ('active', '=', True),
    ], limit=1)
    if not config:
        _logger.info("post_init: no SearXNG config found, skipping.")
        return
    _logger.info(
        "post_init: SearXNG config id=%s ai_model_name='%s' model_id=%s",
        config.id, config.ai_model_name, config.model_id.id if config.model_id else False,
    )
    if not config.ai_model_name or config.ai_model_name in ('gpt-4o-mini', 'gpt-3.5-turbo', 'gpt-4o', 'mistral'):
        config.write({
            'ai_model_name': 'llama3.1:8b',
            'model_id': False,
        })
        _logger.info("post_init: FIXED → ai_model_name='llama3.1:8b', model_id=False")
