from . import models
from . import wizard
from . import controllers


def _fix_missing_columns(env):
    """Create DB columns that are defined in Python but missing from the DB.

    theme_nova adds nova_label_id to product.template in Python, but if the
    module was never properly upgraded, the column is missing — causing crashes
    in ANY module that reads product.template (ORM fetches all stored fields).
    """
    env.cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'product_template' AND column_name = 'nova_label_id'
    """)
    if not env.cr.fetchone():
        env.cr.execute(
            "ALTER TABLE product_template ADD COLUMN nova_label_id integer"
        )


def post_init_hook(env):
    _fix_missing_columns(env)
