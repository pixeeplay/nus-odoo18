{
    'name': 'Theme Nova',
    'summary': 'Premium e-commerce theme with 20 stunning looks',
    'description': """
Theme Nova — Premium E-commerce Theme for Odoo 18
==================================================

A beautiful, modern, and highly customizable e-commerce theme featuring:

* **20 unique looks** — From luxury minimalist to vibrant startup
* **Google Fonts** — Carefully paired heading & body fonts for each look
* **5 header variants** — Centered, transparent, minimal, mega-menu, topbar
* **4 footer variants** — Columns, dark, minimal, CTA
* **10 e-commerce snippets** — Hero, categories, products, brands, testimonials,
  promo banner, newsletter, countdown, features, Instagram grid
* **6 product card styles** — Classic, overlay, minimal, bordered, rounded, dark
* **Enhanced shop pages** — Improved product page, cart, checkout
* **Fully responsive** — Perfect on desktop, tablet, and mobile

Built for Odoo 18 Apps Store.
    """,
    'category': 'Theme/Retail',
    'version': '18.0.1.0.0',
    'author': 'Antigravity',
    'website': 'https://antigravity.dev',
    'license': 'OPL-1',
    'depends': ['website', 'website_sale'],
    'data': [
        'views/headers.xml',
        'views/footers.xml',
        'views/snippets/s_nova_hero.xml',
        'views/snippets/s_nova_categories.xml',
        'views/snippets/s_nova_products.xml',
        'views/snippets/s_nova_brands.xml',
        'views/snippets/s_nova_testimonials.xml',
        'views/snippets/s_nova_promo_banner.xml',
        'views/snippets/s_nova_newsletter.xml',
        'views/snippets/s_nova_countdown.xml',
        'views/snippets/s_nova_features.xml',
        'views/snippets/s_nova_instagram.xml',
        'views/snippets.xml',
        'views/shop_customizations.xml',
    ],
    'assets': {
        'web._assets_primary_variables': [
            'theme_nova/static/src/scss/primary_variables.scss',
        ],
        'web._assets_frontend_helpers': [
            ('prepend', 'theme_nova/static/src/scss/bootstrap_overridden.scss'),
        ],
        'web.assets_frontend': [
            'theme_nova/static/src/scss/theme.scss',
            'theme_nova/static/src/js/snippets/s_nova_countdown.js',
            'theme_nova/static/src/js/snippets/s_nova_products.js',
        ],
    },
    'configurator_snippets': {
        'homepage': [
            's_nova_hero', 's_nova_categories', 's_nova_products',
            's_nova_features', 's_nova_testimonials', 's_nova_newsletter',
        ],
    },
    'images': [
        'static/description/icon.svg',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
