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
* **16 premium snippets** — Hero, categories, products, brands, testimonials,
  promo banner, newsletter, countdown, features, Instagram, pricing, team,
  stats counters, gallery, CTA, blog posts
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
        'security/ir.model.access.csv',
        'views/headers.xml',
        'views/footers.xml',
        'views/quick_view.xml',
        'views/product_label_views.xml',
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
        'views/snippets/s_nova_pricing.xml',
        'views/snippets/s_nova_team.xml',
        'views/snippets/s_nova_stats.xml',
        'views/snippets/s_nova_gallery.xml',
        'views/snippets/s_nova_cta.xml',
        'views/snippets/s_nova_blog.xml',
        'views/snippets.xml',
        'views/shop_customizations.xml',
        'views/layout.xml',
    ],
    'assets': {
        'web._assets_primary_variables': [
            'theme_nova/static/src/scss/primary_variables.scss',
        ],
        'web._assets_frontend_helpers': [
            ('prepend', 'theme_nova/static/src/scss/bootstrap_overridden.scss'),
        ],
        'web.assets_frontend': [
            'theme_nova/static/src/scss/_globals.scss',
            'theme_nova/static/src/scss/_headers.scss',
            'theme_nova/static/src/scss/_footers.scss',
            'theme_nova/static/src/scss/_product_cards.scss',
            'theme_nova/static/src/scss/_product_page.scss',
            'theme_nova/static/src/scss/_shop.scss',
            'theme_nova/static/src/scss/_cart.scss',
            'theme_nova/static/src/scss/_quick_view.scss',
            'theme_nova/static/src/scss/_side_cart.scss',
            'theme_nova/static/src/scss/_mobile.scss',
            'theme_nova/static/src/scss/_search.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_hero.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_categories.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_products.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_brands.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_testimonials.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_promo_banner.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_newsletter.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_countdown.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_features.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_instagram.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_pricing.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_team.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_stats.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_gallery.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_cta.scss',
            'theme_nova/static/src/scss/snippets/_s_nova_blog.scss',
            'theme_nova/static/src/js/quick_view.js',
            'theme_nova/static/src/js/sticky_cart.js',
            'theme_nova/static/src/js/product_nav.js',
            'theme_nova/static/src/js/product_zoom.js',
            'theme_nova/static/src/js/side_cart.js',
            'theme_nova/static/src/js/bottom_bar.js',
            'theme_nova/static/src/js/search.js',
            'theme_nova/static/src/js/swatches.js',
            'theme_nova/static/src/js/snippets/s_nova_countdown.js',
            'theme_nova/static/src/js/snippets/s_nova_products.js',
            'theme_nova/static/src/js/snippets/s_nova_stats.js',
        ],
    },
    'configurator_snippets': {
        'homepage': [
            's_nova_hero', 's_nova_categories', 's_nova_products',
            's_nova_features', 's_nova_testimonials', 's_nova_newsletter',
        ],
    },
    'images': [
        'static/description/icon.png',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
