# app/__init__.py - Only show the blueprint sections
# (Keep all your existing code, just update these sections)

    required_modules = [
        "app.routes.health",
        "app.routes.accounts",
        "app.routes.subscriptions",
        "app.routes.ask",
        "app.routes.web",
        "app.routes.webhooks",
        "app.routes.plans",
        "app.routes.billing",
        "app.routes.link_tokens",
        "app.routes.admin_link_tokens",
        "app.routes.accounts_admin",
        "app.routes.meta",
        "app.routes.email_link",
        "app.routes.web_auth",
        "app.routes.web_session",
        "app.routes.tax",
        "app.routes.workspace",
        "app.routes.link",
        "app.routes.referrals",
        "app.routes.entry",
        "app.routes.history",
        "app.routes.support",
    ]

    optional_modules = [
        "app.routes.cron",
        "app.routes.whatsapp",
        "app.routes.telegram",
        "app.routes.web_ask",
        "app.routes.web_chat",
        "app.routes.paystack_webhook",
        "app.routes.channel",
        "app.routes.channel_admin",
        "app.routes.support_admin",
    ]
