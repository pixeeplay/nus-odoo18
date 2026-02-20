from . import controllers
from . import models


def _ensure_nova_columns(env):
    """Create missing DB columns for theme_nova fields.

    When the module is in the addons path but not yet upgraded,
    the ORM registers fields in Python but the DB columns may not exist,
    causing crashes in any module that reads product.template.
    """
    env.cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'product_template' AND column_name = 'nova_label_id'
    """)
    if not env.cr.fetchone():
        env.cr.execute("""
            ALTER TABLE product_template
            ADD COLUMN nova_label_id integer
        """)


def post_init_hook(env):
    _ensure_nova_columns(env)
