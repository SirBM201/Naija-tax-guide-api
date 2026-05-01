# app/routes/whatsapp.py - Add credit purchase functionality

# Add these imports at the top
from app.services.channel_credit_service import (
    get_credit_balance,
    get_credit_packages_menu,
    validate_package_number,
    create_credit_payment,
    format_balance_message
)

# Update the menu to include option 6 for buying credits
def _send_main_menu(phone: str):
    menu = (
        "*Naija Tax Guide* ✅\n\n"
        "Reply with:\n"
        "1️⃣ – Ask a tax question\n"
        "2️⃣ – Check AI credits balance\n"
        "3️⃣ – Check current plan\n"
        "4️⃣ – Upgrade subscription\n"
        "5️⃣ – Link website account\n"
        "6️⃣ – Buy AI credits\n"
        "7️⃣ – Help / how to use\n\n"
        "You can also type your tax question directly at any time."
    )
    send_whatsapp_text(phone, menu)


# In the webhook handler, add these options in the numbered menu section:

    # Handle numbered menu options
    if MENU_NUMBER_RE.match(text):
        option = int(text)
        
        if option == 1:
            send_whatsapp_text(from_phone, "Please type your tax question and I'll answer it.")
            return jsonify({"ok": True})
        
        elif option == 2:
            # Check AI credits balance
            account_id = lk.get("account_id") or from_phone
            balance = get_credit_balance(account_id)
            send_whatsapp_text(from_phone, format_balance_message(balance))
            return jsonify({"ok": True})
        
        elif option == 3:
            send_whatsapp_text(from_phone, "📋 *Your Current Plan*\n\nPlan: Free\nAI Credits: 10/month\nDaily Questions: Unlimited\n\nUpgrade for more features!")
            return jsonify({"ok": True})
        
        elif option == 4:
            send_whatsapp_text(
                from_phone,
                "💎 *Upgrade Your Plan*\n\n"
                "Visit our website to upgrade:\n"
                "https://www.naijataxguides.com/plans\n\n"
                "Or reply with 6 to buy credits."
            )
            return jsonify({"ok": True})
        
        elif option == 5:
            send_whatsapp_text(
                from_phone,
                "🔗 *Link to Website*\n\n"
                "1. Login to your account on our website\n"
                "2. Go to Settings → WhatsApp Linking\n"
                "3. Generate an 8-character code\n"
                "4. Send that code here\n\n"
                "Once linked, your WhatsApp will be connected to your web account!"
            )
            return jsonify({"ok": True})
        
        elif option == 6:
            # Buy AI Credits
            credit_menu = get_credit_packages_menu()
            send_whatsapp_text(from_phone, credit_menu)
            # Store state - for now, we'll just let the next response be a number
            return jsonify({"ok": True, "awaiting_credit_package": True})
        
        elif option == 7:
            _send_main_menu(from_phone)
            return jsonify({"ok": True})

    # Handle credit package selection (if user responded with 1-4 after seeing menu)
    if text in ["1", "2", "3", "4"]:
        package_num = int(text)
        account_id = lk.get("account_id") or from_phone
        
        result = create_credit_payment(account_id, package_num, "whatsapp", from_phone)
        
        if result.get("ok"):
            send_whatsapp_text(from_phone, result["message"])
        else:
            send_whatsapp_text(from_phone, f"❌ {result.get('message', 'Please try again.')}")
        
        return jsonify({"ok": True})
