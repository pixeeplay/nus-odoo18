from . import controllers
from . import models


def post_init_hook(env):
    """Ensure nova_label_id column exists in the database."""
    env.cr.execute(
        "ALTER TABLE product_template ADD COLUMN IF NOT EXISTS nova_label_id integer"
    )
