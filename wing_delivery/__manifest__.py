{
    'name': 'Wing Shipping',
    'summary': 'Ship with Wing logistics carrier aggregator',
    'description': """
Wing Shipping Integration
=========================
Connect Odoo with Wing (wing.eu) logistics platform.

Features:
---------
* Create shipping orders via Wing GraphQL API
* Generate shipping labels automatically
* Track parcels with real-time status updates
* Support multiple carriers through Wing aggregation
* Automatic tracking number assignment
* Cron-based status polling (every 30 minutes)
    """,
    'version': '18.0.1.0.0',
    'category': 'Inventory/Delivery',
    'author': 'Antigravity',
    'website': 'https://antigravity.fr',
    'support': 'support@antigravity.fr',
    'license': 'OPL-1',
    'depends': ['stock_delivery', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/delivery_wing_data.xml',
        'data/ir_cron_data.xml',
        'views/delivery_carrier_views.xml',
        'views/stock_picking_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
