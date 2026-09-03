{
    'name': 'Payment Provider: Tamara',
    'version': '1.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': "Buy now, pay later for Saudi Arabia, the UAE, and the GCC.",
    'description': " ",  # Non-empty string to avoid loading the README file.
    'author': 'Tamara',
    'website': 'https://tamara.co',
    'depends': ['payment', 'website_sale'],
    'data': [
        # The provider record references the form templates, and the menus
        # reference the provider record, so keep this load order.
        'views/payment_tamara_templates.xml',
        'views/payment_form_templates.xml',
        'views/payment_provider_views.xml',
        'views/payment_transaction_views.xml',
        'views/website_sale_templates.xml',
        'data/payment_method_data.xml',
        'data/payment_provider_data.xml',
        'views/payment_tamara_menus.xml',
        'data/payment_provider_logo_data.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_tamara/static/src/interactions/payment_form.js',
            'payment_tamara/static/src/interactions/product_widget.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}
