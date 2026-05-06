# app/services/language_service.py
from __future__ import annotations

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from app.core.supabase_client import supabase

logger = logging.getLogger(__name__)

# Supported languages
LANGUAGES = {
    "en": {"name": "English", "code": "en", "native_name": "English"},
    "pcm": {"name": "Pidgin", "code": "pcm", "native_name": "Naija Pidgin"},
    "yo": {"name": "Yoruba", "code": "yo", "native_name": "Èdè Yorùbá"},
    "ig": {"name": "Igbo", "code": "ig", "native_name":"Asụsụ Igbo"},
    "ha": {"name": "Hausa", "code": "ha", "native_name": "Harshen Hausa"},
}

# Translations for all UI text
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    # Welcome messages
    "welcome": {
        "en": "*Welcome to Naija Tax Guide!* ✅\n\nI'm your AI tax assistant for Nigerian taxes.",
        "pcm": "*Welcome to Naija Tax Guide!* ✅\n\nI be your AI tax padi for Naija tax matters.",
        "yo": "*Kaabọ si Itọsọna Owo-ori Naijiria!* ✅\n\nMo jẹ oluranlọwọ owo-ori AI rẹ fun owo-ori Naijiria.",
        "ig": "*Nnọọ na Ntuzi Ụtụ Naijiria!* ✅\n\nAbụ m onye enyemaka ụtụ AI gị maka ụtụ Naijiria.",
        "ha": "*Barka da Zuwa Jagorancin Harajin Najeriya!* ✅\n\nNi ne mataimakin harajin AI na ku don harajin Najeriya.",
    },
    
    # Main menu
    "main_menu": {
        "en": "Reply with:\n1️⃣ - Ask a tax question\n2️⃣ - Check AI credits balance\n3️⃣ - Check my subscription plan\n4️⃣ - View subscription plans\n5️⃣ - Link to website account\n6️⃣ - Buy AI credits\n7️⃣ - Tax filing & management\n8️⃣ - Help / Menu",
        "pcm": "Reply with:\n1️⃣ - Ask tax question\n2️⃣ - Check your credit\n3️⃣ - Check your plan\n4️⃣ - See all plans\n5️⃣ - Link your account\n6️⃣ - Buy credit\n7️⃣ - File tax\n8️⃣ - Help / Menu",
        "yo": "Fesi pẹlu:\n1️⃣ - Beere ibeere owo-ori\n2️⃣ - Ṣayẹwo iwọntunwọnsi awọn kirẹditi AI\n3️⃣ - Ṣayẹwo ero ṣiṣe alabapin mi\n4️⃣ - Wo awọn ero ṣiṣe alabapin\n5️⃣ - Sopọ si akọọlẹ oju opo wẹẹbu\n6️⃣ - Ra awọn kirẹditi AI\n7️⃣ - Iforukọsilẹ owo-ori & iṣakoso\n8️⃣ - Iranlọwọ / Akojọ aṣayan",
        "ig": "Zaghachi:\n1️⃣ - Jụọ ajụjụ ụtụ\n2️⃣ - Lelee nguzo kredit AI\n3️⃣ - Lelee atụmatụ ndenye aha m\n4️⃣ - Lelee atụmatụ ndenye aha\n5️⃣ - Jikọọ na akaụntụ webụ\n6️⃣ - Zụta kredit AI\n7️⃣ - Ndịbanye ụtụ & njikwa\n8️⃣ - Enyemaka / Nchịkọta",
        "ha": "Amsa da:\n1️⃣ - Tambayi tambayar haraji\n2️⃣ - Duba ma'aunin kiredit AI\n3️⃣ - Duba tsarin biyan kuɗi na\n4️⃣ - Duba tsare-tsaren biyan kuɗi\n5️⃣ - Haɗa zuwa asusun gidan yanar gizo\n6️⃣ - Siyan kiredit AI\n7️⃣ - Shigar da haraji & gudanarwa\n8️⃣ - Taimako / Menu",
    },
    
    # Tax menu
    "tax_menu": {
        "en": "*📋 TAX FILING & MANAGEMENT*\n\nReply with:\nP - File PAYE Tax (Salary tax)\nV - File VAT (Sales tax)\nC - File CIT (Company tax)\nH - View my filing history\nD - View tax deadlines\nB - Back to main menu",
        "pcm": "*📋 FILE TAX*\n\nReply with:\nP - File PAYE (Salary tax)\nV - File VAT (Sales tax)\nC - File CIT (Company tax)\nH - See your filing history\nD - See tax deadlines\nB - Back to menu",
        "yo": "*📋 IFORUKỌLỆ OWO-ORI & IṢAKỌSO*\n\nFesi pẹlu:\nP - Iforukọsilẹ Owo-ori PAYE\nV - Iforukọsilẹ Owo-ori VAT\nC - Iforukọsilẹ Owo-ori CIT\nH - Wo itan iforukọsilẹ mi\nD - Wo awọn ọjọ ipari owo-ori\nB - Pada si akojọ aṣayan",
        "ig": "*📋 NDỊBANYE ỤTỤ & NJIKWA*\n\nZaghachi:\nP - Ndịbanye Ụtụ PAYE\nV - Ndịbanye Ụtụ VAT\nC - Ndịbanye Ụtụ CIT\nH - Lee akụkọ ndịbanye m\nD - Lee ụbọchị njedebe ụtụ\nB - Laghachi na menu",
        "ha": "*📋 SHIGAR HARAJI & GUDANARWA*\n\nAmsa da:\nP - Shigar da Harajin PAYE\nV - Shigar da Harajin VAT\nC - Shigar da Harajin CIT\nH - Duba tarihin shigar da na\nD - Duba kwanakin ƙarshen haraji\nB - Koma zuwa menu",
    },
    
    # Filing step prompts
    "paye_step1": {
        "en": "📋 *PAYE Tax Filing - Step 1 of 3*\n\nWhat is your monthly salary?\n(Example: 750000 or 750k)",
        "pcm": "📋 *PAYE Tax Filing - Step 1 of 3*\n\nHow much be your monthly salary?\n(Example: 750000 or 750k)",
        "yo": "📋 *Iforukọsilẹ Owo-ori PAYE - Igbesẹ 1 ti 3*\n\nKini owo-osu oṣooṣu rẹ?\n(Apere: 750000 tabi 750k)",
        "ig": "📋 *Ndịbanye Ụtụ PAYE - Nzọụkwụ 1 nke 3*\n\nKedu ụgwọ ọnwa gị kwa ọnwa?\n(Ihe atụ: 750000 ma ọ bụ 750k)",
        "ha": "📋 *Shigar da Harajin PAYE - Mataki 1 na 3*\n\nMenene albashin ku na kowane wata?\n(Misali: 750000 ko 750k)",
    },
    
    "paye_step2": {
        "en": "📋 Step 2 of 3: Pension Contribution\nEnter your monthly pension contribution (0 if none):",
        "pcm": "📋 Step 2 of 3: Pension Contribution\nHow much you dey pay for pension every month? (0 if none):",
        "yo": "📋 Igbesẹ 2 ti 3: Ilowosi Ifẹhinti\nTẹ ilowosi ifẹhinti oṣooṣu rẹ sii (0 ti o ba ko si):",
        "ig": "📋 Nzọụkwụ 2 nke 3: Ntinye Ụgwọ Ezumike Nka\nTinye ntinye ụgwọ ezumike nka gị kwa ọnwa (0 ma ọ bụrụ na ọ dịghị):",
        "ha": "📋 Mataki 2 na 3: Gudunmawar Fansho\nShigar da gudunmawar fansho na kowane wata (0 idan babu):",
    },
    
    "paye_step3": {
        "en": "📋 Step 3 of 3: NHF Contribution\nEnter your NHF contribution (0 if none):",
        "pcm": "📋 Step 3 of 3: NHF Contribution\nHow much you dey pay for NHF? (0 if none):",
        "yo": "📋 Igbesẹ 3 ti 3: Ilowosi NHF\nTẹ ilowosi NHF rẹ sii (0 ti o ba ko si):",
        "ig": "📋 Nzọụkwụ 3 nke 3: Ntinye NHF\nTinye ntinye NHF gị (0 ma ọ bụrụ na ọ dịghị):",
        "ha": "📋 Mataki 3 na 3: Gudunmawar NHF\nShigar da gudunmawar NHF (0 idan babu):",
    },
    
    "vat_step1": {
        "en": "📋 *VAT Filing - Step 1 of 3*\n\nWhat is your total sales for the period?\n(Example: 25000000 or 25M)",
        "pcm": "📋 *VAT Filing - Step 1 of 3*\n\nHow much be your total sales for this period?\n(Example: 25000000 or 25M)",
        "yo": "📋 *Iforukọsilẹ VAT - Igbesẹ 1 ti 3*\n\nKini apapọ awọn tita rẹ fun asiko naa?\n(Apere: 25000000 tabi 25M)",
        "ig": "📋 *Ndịbanye VAT - Nzọụkwụ 1 nke 3*\n\nKedu ngụkọta ahịa gị maka oge a?\n(Ihe atụ: 25000000 ma ọ bụ 25M)",
        "ha": "📋 *Shigar da VAT - Mataki 1 na 3*\n\nMenene jimlar tallace-tallacen ku na lokacin?\n(Misali: 25000000 ko 25M)",
    },
    
    "vat_step2": {
        "en": "📋 Step 2 of 3: Total Purchases\nEnter your total purchases (excluding VAT):",
        "pcm": "📋 Step 2 of 3: Total Purchases\nHow much be your total purchases (without VAT):",
        "yo": "📋 Igbesẹ 2 ti 3: Apapọ Awọn rira\nTẹ apapọ awọn rira rẹ sii (laisi VAT):",
        "ig": "📋 Nzọụkwụ 2 nke 3: Ngụkọta Ịzụ Ahịa\nTinye ngụkọta ịzụ ahịa gị (eweghị VAT):",
        "ha": "📋 Mataki 2 na 3: Jimlar Saye\nShigar da jimlar sayen ku (ba tare da VAT ba):",
    },
    
    "cit_step1": {
        "en": "📋 *CIT Filing - Step 1 of 3*\n\nWhat is your company's total revenue for the period?\n(Example: 50000000 or 50M)",
        "pcm": "📋 *CIT Filing - Step 1 of 3*\n\nHow much be your company total revenue for this period?\n(Example: 50000000 or 50M)",
        "yo": "📋 *Iforukọsilẹ CIT - Igbesẹ 1 ti 3*\n\nKini apapọ owo-wiwọle ile-iṣẹ rẹ fun asiko naa?\n(Apere: 50000000 tabi 50M)",
        "ig": "📋 *Ndịbanye CIT - Nzọụkwụ 1 nke 3*\n\nKedu ngụkọta ego ụlọ ọrụ gị maka oge a?\n(Ihe atụ: 50000000 ma ọ bụ 50M)",
        "ha": "📋 *Shigar da CIT - Mataki 1 na 3*\n\nMenene jimlar kudaden shiga na kamfanin ku na lokacin?\n(Misali: 50000000 ko 50M)",
    },
    
    "cit_step2": {
        "en": "📋 Step 2 of 3: Total Expenses\nEnter your total allowable expenses:",
        "pcm": "📋 Step 2 of 3: Total Expenses\nHow much be your total expenses wey allowed:",
        "yo": "📋 Igbesẹ 2 ti 3: Apapọ Awọn inawo\nTẹ apapọ awọn inawo rẹ ti o yọọda sii:",
        "ig": "📋 Nzọụkwụ 2 nke 3: Ngụkọta Mmefu\nTinye ngụkọta mmefu gị enyere ikike:",
        "ha": "📋 Mataki 2 na 3: Jimlar Kuɗi\nShigar da jimlar kuɗin da aka halatta:",
    },
    
    # Global commands
    "global_commands": {
        "en": "💡 Global commands:\n# - Save & Menu | * - Back | 0 - Cancel | 9 - Resume",
        "pcm": "💡 Global commands:\n# - Save & Menu | * - Go back | 0 - Cancel | 9 - Resume",
        "yo": "💡 Awọn aṣẹ agbaye:\n# - Fi pamọ & Akojọ aṣayan | * - Pada | 0 - Fagilee | 9 - Tun bẹrẹ",
        "ig": "💡 Iwu zuru ụwa ọnụ:\n# - Chekwa & Menu | * - Laa azụ | 0 - Kagbuo | 9 - Malitegharịa",
        "ha": "💡 Umurnin duniya:\n# - Ajiye & Menu | * - Komawa | 0 - Soke | 9 - Ci gaba",
    },
    
    # Error messages
    "invalid_amount": {
        "en": "❌ Please enter a valid amount (e.g., 750000 or 750k)",
        "pcm": "❌ Please enter correct amount (e.g., 750000 or 750k)",
        "yo": "❌ Jọwọ tẹ iye ti o wulo sii (Apere: 750000 tabi 750k)",
        "ig": "❌ Biko tinye ego ziri ezi (Ihe atụ: 750000 ma ọ bụ 750k)",
        "ha": "❌ Da fatan za a shigar da adadi mai inganci (Misali: 750000 ko 750k)",
    },
    
    "filing_submitted": {
        "en": "✅ *{} Filing Submitted!*\n\n📋 Reference: {}\n💰 {} Payable: ₦{:.2f}\n\nReply 8 for main menu.",
        "pcm": "✅ *{} Filing don!*\n\n📋 Reference: {}\n💰 {} Wey you go pay: ₦{:.2f}\n\nReply 8 go back to menu.",
        "yo": "✅ *Iforukọsilẹ {} ti fi silẹ!*\n\n📋 Itọkasi: {}\n💰 {} Isanwo: ₦{:.2f}\n\nFesi 8 fun akojọ aṣayan akọkọ.",
        "ig": "✅ *Ndịbanye {} Edebere!*\n\n📋 Ntụaka: {}\n💰 {} Kwụ Ụgwọ: ₦{:.2f}\n\nZaghachi 8 maka menu.",
        "ha": "✅ *Shigar da {}!*\n\n📋 Misali: {}\n💰 {} Abin da ake biya: ₦{:.2f}\n\nAmsa 8 don menu na farko.",
    },
    
    "no_filing": {
        "en": "No filing to confirm. Reply 7 to start a new filing.",
        "pcm": "No filing wey you fit confirm. Reply 7 start new filing.",
        "yo": "Ko si iforukọsilẹ lati jẹrisi. Fesi 7 lati bẹrẹ iforukọsilẹ tuntun.",
        "ig": "Enweghị ndịbanye iji kwado. Zaghachi 7 ịmalite ndịbanye ọhụrụ.",
        "ha": "Babu shigar da za a tabbatar. Amsa 7 don fara sabon shigar.",
    },
}


def get_user_language(account_id: str) -> str:
    """Get user's preferred language from database"""
    try:
        result = supabase.table("accounts")\
            .select("language_preference")\
            .eq("account_id", account_id)\
            .maybe_single()\
            .execute()
        
        if result.data and result.data.get("language_preference"):
            lang = result.data.get("language_preference")
            if lang in LANGUAGES:
                return lang
    except Exception as e:
        logger.error(f"Failed to get user language: {e}")
    
    return "en"  # Default to English


def set_user_language(account_id: str, language_code: str) -> Dict[str, Any]:
    """Set user's preferred language"""
    if language_code not in LANGUAGES:
        return {"ok": False, "error": f"Unsupported language. Supported: {', '.join(LANGUAGES.keys())}"}
    
    try:
        supabase.table("accounts")\
            .update({"language_preference": language_code, "updated_at": datetime.utcnow().isoformat()})\
            .eq("account_id", account_id)\
            .execute()
        
        return {"ok": True, "message": f"Language changed to {LANGUAGES[language_code]['name']}"}
    except Exception as e:
        logger.error(f"Failed to set language: {e}")
        return {"ok": False, "error": str(e)}


def get_language_menu() -> str:
    """Get language selection menu"""
    menu = "*🌐 Select Your Language / Yan Harshenku / Gèdùn Èdè Rẹ*\n\n"
    for code, lang in LANGUAGES.items():
        menu += f"{code.upper()} - {lang['native_name']}\n"
    menu += "\nReply with the language code (EN, PCM, YO, IG, HA)\n\n💡 Reply 8 to cancel"
    return menu


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate a key to the specified language"""
    if key not in TRANSLATIONS:
        return key
    
    translation = TRANSLATIONS[key].get(lang, TRANSLATIONS[key].get("en", key))
    
    if kwargs:
        try:
            translation = translation.format(**kwargs)
        except:
            pass
    
    return translation
