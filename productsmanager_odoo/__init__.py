from . import models
from . import wizard
from . import controllers


def post_init_hook(env):
    """Create missing DB columns for fields registered by other modules.

    theme_nova adds nova_label_id to product.template in Python, but if
    the column doesn't exist in the DB, ANY read of product.template crashes.
    """
    env.cr.execute(
        "ALTER TABLE product_template ADD COLUMN IF NOT EXISTS nova_label_id integer"
    )
