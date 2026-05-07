import os
import re
import logging
import json
import random
import calendar
import datetime
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ============ SUPABASE CONFIGURATION ============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info("✅ Supabase connected successfully")

# ============ TELEGRAM CONFIGURATION ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"

# ============ CRON JOB TEST USERS ============
TEST_TELEGRAM_CHAT_ID = os.getenv("TEST_TELEGRAM_CHAT_ID")
TEST_WHATSAPP_NUMBER = os.getenv("TEST_WHATSAPP_NUMBER")

# ============ LANGUAGE SUPPORT ============
LANGUAGES = {
    "en": "English",
    "pidgin": "Pidgin English",
    "yoruba": "Yorùbá",
    "hausa": "Hausa",
    "igbo": "Igbo"
}

user_language = {}

# ============ TRANSLATIONS ============
TRANSLATIONS = {
    "en": {
        "welcome": "🇳🇬 *Nigerian Tax Bot*\n\nYour complete tax assistant!\n\n*Commands:*\n/paye [amount] - Calculate PAYE\n/cit [turnover] - Company tax\n/vat [amount] - VAT calculation\n/wht [amount] [type] - Withholding tax\n/calculate [salary] - Tax calculation\n/calendar - Tax deadlines\n/help - Show all commands\n/language - Change language",
        "paye_summary": "🇳🇬 *PAYE SUMMARY*\n\nGross: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nTax: ₦{tax}\nNet: *₦{net}*\nRate: {rate}%",
        "enter_amount": "Please enter a valid amount",
        "enter_salary": "Send your monthly salary (e.g., 500000):",
        "calculation_saved": "✅ Calculation saved to your history",
        "loading": "⏳ Calculating...",
        "deadlines": "📅 *UPCOMING TAX DEADLINES*\n\n",
        "today": "⚠️ *TODAY:* ",
        "tomorrow": "🔔 *TOMORROW:* ",
        "days_left": "📌 {name} - {days} days left",
        "no_deadlines": "✅ No tax deadlines in the next 30 days",
        "wht_rates": "📊 *WHT RATES*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": """📚 *TAX BOT HELP*

*🇳🇬 Language Support*
/language - Change language (English, Pidgin, Yoruba, Hausa, Igbo)

*📊 Calculations*
• Send amount - Calculate PAYE tax
• /paye 500000 - PAYE for ₦500,000
• /cit 50000000 - Company tax
• /vat 100000 - Add 7.5% VAT
• /vatin 107500 - Extract VAT
• /wht 500000 consultancy - Withholding tax

*📅 Calendar*
• /calendar - Monthly tax calendar
• /deadlines - Upcoming deadlines

*📋 Filing*
• /filepaye - PAYE filing guide
• /filecit - CIT filing guide
• /filevat - VAT filing guide
• /checklist - Document checklist

*👤 Account*
• /history - Your calculation history
• /stats - Your usage statistics""",
        "language_changed": "✅ Language changed to English!",
        "select_language": "🌍 *Select your language:*\n\nSend the number:\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo",
        "paye_guide": """📋 *PAYE FILING GUIDE*

1. Calculate PAYE per employee
2. Deduct PAYE, Pension (8%), NHF (2.5%)
3. File Schedule 6 via FIRS e-PAYE
4. Remit by 14th of following month

🔗 https://e-paye.firs.gov.ng""",
        "cit_guide": """🏢 *CIT FILING GUIDE*

• Small (< ₦25M): File nil returns
• Medium (₦25M-₦100M): 20% CIT
• Large (> ₦100M): 30% CIT

Deadlines: Q1 Apr 30, Q2 Jul 31, Q3 Oct 31, Annual Mar 31""",
        "vat_guide": """🧾 *VAT FILING GUIDE*

1. Track Output VAT and Input VAT
2. Calculate: Output - Input = Payable
3. File Form 002 by 21st of following month""",
        "wht_guide": """📊 *WHT FILING GUIDE*

1. Deduct WHT from eligible payments
2. File Form 1 by 21st of following month
3. Issue credit notes to vendors"""
    },
    "pidgin": {
        "welcome": "🇳🇬 *Nigerian Tax Bot (Pidgin)*\n\nYour complete tax assistant for Nigeria!\n\n*Commands:*\n/paye [amount] - Calculate PAYE tax\n/cit [turnover] - Company tax\n/vat [amount] - VAT calculation\n/calendar - Tax deadlines\n/help - Show all commands\n/language - Change language",
        "paye_summary": "🇳🇬 *PAYE SUMMARY (Pidgin)*\n\nMoney wey you collect: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nTax wey you go pay: ₦{tax}\nYour take home: *₦{net}*\nTax rate: {rate}%",
        "enter_amount": "Abeg send correct amount",
        "enter_salary": "Send your monthly salary (e.g., 500000):",
        "calculation_saved": "✅ We don save your calculation",
        "loading": "⏳ Small time...",
        "deadlines": "📅 *TAX DEADLINES WEY DEY COME*\n\n",
        "today": "⚠️ *TODAY:* ",
        "tomorrow": "🔔 *TOMORROW:* ",
        "days_left": "📌 {name} - {days} days left",
        "no_deadlines": "✅ No tax deadlines for next 30 days",
        "wht_rates": "📊 *WHT RATES (Pidgin)*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": """📚 *TAX BOT HELP (Pidgin)*

*🇳🇬 Language Support*
/language - Change language (English, Pidgin, Yoruba, Hausa, Igbo)

*📊 Calculations*
• Send amount - Calculate PAYE tax
• /paye 500000 - PAYE for ₦500,000
• /cit 50000000 - Company tax
• /vat 100000 - Add 7.5% VAT
• /vatin 107500 - Extract VAT

*📅 Calendar*
• /calendar - Monthly tax calendar
• /deadlines - Upcoming deadlines

*📋 Filing*
• /filepaye - PAYE filing guide
• /filecit - CIT filing guide

*👤 Account*
• /history - Your calculation history""",
        "language_changed": "✅ We don change language to Pidgin English!",
        "select_language": "🌍 *Select your language:*\n\nSend the number:\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo",
        "paye_guide": """📋 *PAYE FILING GUIDE (Pidgin)*

1. Calculate PAYE for each worker
2. Remove PAYE, Pension (8%), NHF (2.5%)
3. File Schedule 6 for FIRS
4. Pay by 14th of next month

🔗 https://e-paye.firs.gov.ng""",
        "cit_guide": """🏢 *CIT FILING GUIDE (Pidgin)*

• Small (< ₦25M): Just file zero
• Medium (₦25M-₦100M): 20% CIT
• Large (> ₦100M): 30% CIT

Deadlines: Q1 Apr 30, Q2 Jul 31, Q3 Oct 31, Annual Mar 31""",
        "vat_guide": """🧾 *VAT FILING GUIDE (Pidgin)*

1. Track VAT wey you collect and pay
2. Calculate money to pay
3. File Form 002 by 21st of next month""",
        "wht_guide": """📊 *WHT FILING GUIDE (Pidgin)*

1. Remove WHT from payments
2. File Form 1 by 21st of next month
3. Give credit notes to vendors"""
    },
    "yoruba": {
        "welcome": "🇳🇬 *Nigerian Tax Bot (Yorùbá)*\n\nOluṣe iranlọwọ orí-ori rẹ!\n\n*Awọn aṣẹ:*\n/paye [owó] - Ṣiṣiro owo-ori PAYE\n/cit [owo-iye] - Owo-ori ile-iṣẹ\n/vat [owo] - Ṣiṣiro VAT\n/calendar - Awọn ọjọ-ipari\n/help - Gbogbo aṣẹ\n/language - Yipada ede",
        "paye_summary": "🇳🇬 *PAYE SUMMARY (Yorùbá)*\n\nOwo-oṣooṣu: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nOwo-ori: ₦{tax}\nOwo ti o gba: *₦{net}*\nOṣuwọn: {rate}%",
        "enter_amount": "Jọwọ tẹ iye to pe",
        "enter_salary": "Fi owo-oṣooṣu rẹ ranṣẹ (fun apẹẹrẹ, 500000):",
        "calculation_saved": "✅ A ti fi iṣiro rẹ pamọ",
        "loading": "⏳ Nṣiṣiro...",
        "deadlines": "📅 *AWỌN ỌJỌ-IPARI OWO-ORI TI NMỌ SỌDỌ*\n\n",
        "today": "⚠️ *ÒNÍ:* ",
        "tomorrow": "🔔 *ỌLA:* ",
        "days_left": "📌 {name} - ọjọ {days} le",
        "no_deadlines": "✅ Ko si ọjọ-ipari owo-ori ninu ọjọ 30 to nbọ",
        "wht_rates": "📊 *WHT RATES (Yorùbá)*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": "📚 *IRANLỌWỌ BOT OWO-ORI*\n\n/language - Yipada ede\n/paye [owó] - Owo-ori PAYE\n/cit [owo-iye] - Owo-ori ile-iṣẹ\n/vat [owo] - VAT\n/calendar - Awọn ọjọ-ipari\n/filepaye - Itọsọna filing\n/history - Itan iṣiro rẹ",
        "language_changed": "✅ A ti yipada ede si Yorùbá!",
        "select_language": "🌍 *Yan ede rẹ:*\n\nFi nọ́ńbà ranṣẹ:\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo",
        "paye_guide": "📋 *ITỌSỌNA PAYE FILING*\n\n1. Ṣe iṣiro owo-ori fun oṣiṣẹ kọọkan\n2. Yọkuro owo-ori, Pension (8%), NHF (2.5%)\n3. Fọọmu Schedule 6 si cyberspace FIRS\n4. San ni ọjọ 14th oṣu to nbọ",
        "cit_guide": "🏢 *ITỌSỌNA CIT FILING*\n\n• Kekere (< ₦25M): Fi iwe-ofo ranṣẹ\n• Alabọde (₦25M-₦100M): 20% CIT\n• Nla (> ₦100M): 30% CIT",
        "vat_guide": "🧾 *ITỌSỌNA VAT FILING*\n\n1. Tọju akọsilẹ Output VAT ati Input VAT\n2. Ṣe iṣiro: Output - Input = Owo lati san\n3. Fi Fọọmu 002 silẹ ni ọjọ 21st oṣu to nbọ",
        "wht_guide": "📊 *ITỌSỌNA WHT FILING*\n\n1. Yọkuro WHT lati awọn isanwo\n2. Fi Fọọmu 1 silẹ ni ọjọ 21st oṣu to nbọ\n3. Fun awọn iwe-ẹri credit si awọn olutaja"
    },
    "hausa": {
        "welcome": "🇳🇬 *Nigerian Tax Bot (Hausa)*\n\nCikakken mataimakin haraji!\n\n*Umarni:*\n/paye [adadin] - Lissafin harajin PAYE\n/cit [juyawa] - Harajin kamfani\n/vat [adadin] - Lissafin VAT\n/calendar - Kwanakin ƙarshe\n/help - Duk umarni\n/language - Canza yare",
        "paye_summary": "🇳🇬 *PAYE SUMMARY (Hausa)*\n\nAlbashin wata: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nHaraji: ₦{tax}\nAbin da zaka karba: *₦{net}*\nAdadin haraji: {rate}%",
        "enter_amount": "Don Allah shigar da adadi mai inganci",
        "enter_salary": "Aika albashin watanka (misali, 500000):",
        "calculation_saved": "✅ An ajiye lissafin ka",
        "loading": "⏳ Ana lissafin...",
        "deadlines": "📅 *KUNAKIN ƘARSHE HARAJI MASU ZUWA*\n\n",
        "today": "⚠️ *YAU:* ",
        "tomorrow": "🔔 *GOBE:* ",
        "days_left": "📌 {name} - {days} days left",
        "no_deadlines": "✅ Babu kwanakin ƙarshe na haraji a cikin kwanaki 30 masu zuwa",
        "wht_rates": "📊 *RATES NA WHT*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": "📚 *TAIMAKON BOT HARAJI*\n\n/language - Canza yare\n/paye [adadin] - Harajin PAYE\n/cit [juyawa] - Harajin kamfani\n/vat [adadin] - VAT\n/calendar - Kwanakin ƙarshe\n/filepaye - Jagororin shigar da haraji\n/history - Tarihin lissafinka",
        "language_changed": "✅ An canza yare zuwa Hausa!",
        "select_language": "🌍 *Zaɓi yarenka:*\n\nAika lambar:\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo",
        "paye_guide": "📋 *JAGORORIN SHIGAR DA PAYE*\n\n1. Lissafi harajin kowane ma'aikaci\n2. Cire haraji, Pension (8%), NHF (2.5%)\n3. Aika Schedule 6 zuwa FIRS\n4. Biya kafin ranar 14th ga wata mai zuwa",
        "cit_guide": "🏢 *JAGORORIN SHIGAR DA CIT*\n\n• Karami (< ₦25M): Aika sifili\n• Matsakaici (₦25M-₦100M): 20% CIT\n• Babba (> ₦100M): 30% CIT",
        "vat_guide": "🧾 *JAGORORIN SHIGAR DA VAT*\n\n1. Rike Output VAT da Input VAT\n2. Lissafi: Output - Input = Abin da za'a biya\n3. Aika Form 002 kafin ranar 21st ga wata mai zuwa",
        "wht_guide": "📊 *JAGORORIN SHIGAR DA WHT*\n\n1. Cire WHT daga biyan kuɗi\n2. Aika Form 1 kafin ranar 21st ga wata mai zuwa\n3. Ba da takardun shaidar kiredit ga dillalai"
    },
    "igbo": {
        "welcome": "🇳🇬 *Nigerian Tax Bot (Igbo)*\n\nOnye na-enyere gị aka n'ụtụ isi!\n\n*Iwu:*\n/paye [ego] - Gbakọọ ụtụ PAYE\n/cit [ntughari] - Ụtụ ụlọ ọrụ\n/vat [ego] - Gbakọọ VAT\n/calendar - Ụbọchị njedebe\n/help - Iwu niile\n/language - Gbanwee asụsụ",
        "paye_summary": "🇳🇬 *PAYE SUMMARY (Igbo)*\n\nEgo ọnwa: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nỤtụ: ₦{tax}\nEgo ị ga-enweta: *₦{net}*\nỌnụ ego: {rate}%",
        "enter_amount": "Biko tinye ego ziri ezi",
        "enter_salary": "Ziga ọnwa ọnwa gị (dịka, 500000):",
        "calculation_saved": "✅ Echekwabara ngụkọ gị",
        "loading": "⏳ Na-agbakọ...",
        "deadlines": "📅 *ỤBỌCHỊ NJEDEBE ỤTỤ ISI NA-ABỊA*\n\n",
        "today": "⚠️ *TAA:* ",
        "tomorrow": "🔔 *ECHI:* ",
        "days_left": "📌 {name} - ụbọchị {days} fọdụrụ",
        "no_deadlines": "✅ Ọ nweghị ụbọchị njedebe ụtụ n'ime ụbọchị 30 na-abịa",
        "wht_rates": "📊 *ỌNỤ EGO WHT*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": "📚 *ENYEMAKA BOT ỤTỤ*\n\n/language - Gbanwee asụsụ\n/paye [ego] - Ụtụ PAYE\n/cit [ntughari] - Ụtụ ụlọ ọrụ\n/vat [ego] - VAT\n/calendar - Ụbọchị njedebe\n/filepaye - Ntuzi maka ịgbanye ụtụ\n/history - Akụkọ ngụkọ gị",
        "language_changed": "✅ Agbanweela asụsụ gaa na Igbo!",
        "select_language": "🌍 *Họrọ asụsụ gị:*\n\nZiga nọmba:\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo",
        "paye_guide": "📋 *NTUZI ỊGBANYE PAYE*\n\n1. Gbakọọ ụtụ maka onye ọrụ nke ọ bụla\n2. Wepụ ụtụ, Pension (8%), NHF (2.5%)\n3. Debe Schedule 6 na FIRS portal\n4. Kwụọ ụgwọ tupu ụbọchị 14th nke ọnwa na-abịa",
        "cit_guide": "🏢 *NTUZI ỊGBANYE CIT*\n\n• Obere (< ₦25M): Debe efu\n• Ọkara (₦25M-₦100M): 20% CIT\n• Nnukwu (> ₦100M): 30% CIT",
        "vat_guide": "🧾 *NTUZI ỊGBANYE VAT*\n\n1. Chekọta Output VAT na Input VAT\n2. Gbakọọ: Output - Input = Ego a ga-akwụ\n3. Debe Form 002 tupu ụbọchị 21st nke ọnwa na-abịa",
        "wht_guide": "📊 *NTUZI ỊGBANYE WHT*\n\n1. Wepụ WHT site na ịkwụ ụgwọ\n2. Debe Form 1 tupu ụbọchị 21st nke ọnwa na-abịa\n3. Nye asambodo kredit ndị na-ere ahịa"
    }
}

def get_translation(lang, key, **kwargs):
    """Get translated text for a given key"""
    translation = TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"][key])
    if kwargs:
        return translation.format(**kwargs)
    return translation

def get_user_language(user_id):
    """Get user's preferred language preference"""
    if user_id in user_language:
        return user_language[user_id]
    
    # Check database for saved preference
    if supabase:
        try:
            response = supabase.table("user_preferences").select("preference_value").eq("user_id", str(user_id)).eq("preference_key", "language").execute()
            if response.data:
                lang = response.data[0]["preference_value"]
                user_language[user_id] = lang
                return lang
        except:
            pass
    
    return "en"

def set_user_language(user_id, lang):
    """Set user's language preference"""
    if lang in LANGUAGES:
        user_language[user_id] = lang
        
        # Save to database
        if supabase:
            try:
                existing = supabase.table("user_preferences").select("*").eq("user_id", str(user_id)).eq("preference_key", "language").execute()
                if existing.data:
                    supabase.table("user_preferences").update({"preference_value": lang}).eq("id", existing.data[0]["id"]).execute()
                else:
                    supabase.table("user_preferences").insert({"user_id": str(user_id), "preference_key": "language", "preference_value": lang}).execute()
            except:
                pass
        return True
    return False

# ============ TAX CALCULATION FUNCTIONS ============
def calculate_nigerian_paye(monthly_gross):
    annual_gross = monthly_gross * 12
    pension = monthly_gross * 0.08
    nhf = monthly_gross * 0.025
    
    cra_fixed = 200000
    cra_one_percent = annual_gross * 0.01
    cra_base = max(cra_fixed, cra_one_percent)
    cra_percentage = annual_gross * 0.20
    cra_total = cra_base + cra_percentage
    
    total_deductions = (pension * 12) + (nhf * 12) + cra_total
    chargeable = max(0, annual_gross - total_deductions)
    
    if chargeable <= 300000:
        annual_tax = chargeable * 0.07
    elif chargeable <= 600000:
        annual_tax = 21000 + (chargeable - 300000) * 0.11
    elif chargeable <= 1100000:
        annual_tax = 54000 + (chargeable - 600000) * 0.15
    elif chargeable <= 1600000:
        annual_tax = 129000 + (chargeable - 1100000) * 0.19
    elif chargeable <= 3200000:
        annual_tax = 224000 + (chargeable - 1600000) * 0.21
    else:
        annual_tax = 560000 + (chargeable - 3200000) * 0.24
    
    if annual_tax < annual_gross * 0.01:
        annual_tax = annual_gross * 0.01
    
    monthly_tax = annual_tax / 12
    effective_rate = (annual_tax / annual_gross) * 100 if annual_gross > 0 else 0
    
    return {
        "gross": monthly_gross,
        "pension": round(pension, 2),
        "nhf": round(nhf, 2),
        "tax": round(monthly_tax, 2),
        "net": round(monthly_gross - pension - nhf - monthly_tax, 2),
        "rate": round(effective_rate, 2)
    }

def calculate_cit(turnover, profit=None):
    if profit is None:
        profit = turnover * 0.20
    if turnover < 25000000:
        size = "Small (Exempt)"
        rate = 0
    elif turnover <= 100000000:
        size = "Medium"
        rate = 0.20
    else:
        size = "Large"
        rate = 0.30
    
    cit = profit * rate
    education = profit * 0.03
    total = cit + education
    
    return {"turnover": turnover, "profit": profit, "size": size, "total": round(total, 2)}

def calculate_vat(amount, inclusive=False):
    if inclusive:
        vat = amount * 0.075 / 1.075
        exclusive = amount - vat
        total = amount
    else:
        vat = amount * 0.075
        exclusive = amount
        total = amount + vat
    return {"amount": amount, "vat": round(vat, 2), "exclusive": round(exclusive, 2), "total": round(total, 2)}

WHT_RATES = {"consultancy": 10, "rent": 10, "interest": 10, "dividend": 10, "construction": 5, "contracts": 5, "transport": 3}

def calculate_wht(amount, trans_type):
    rate = WHT_RATES.get(trans_type, 10)
    wht = amount * rate / 100
    return {"amount": amount, "rate": rate, "wht": round(wht, 2), "net": round(amount - wht, 2)}

# ============ TAX CALENDAR ============
TAX_CALENDAR = {
    1: {14: "PAYE Remittance (Dec)", 21: "VAT Filing (Dec)"},
    2: {14: "PAYE Remittance (Jan)", 21: "VAT Filing (Jan)"},
    3: {14: "PAYE Remittance (Feb)", 21: "VAT Filing (Feb)", 31: "Annual CIT Filing"},
    4: {14: "PAYE Remittance (Mar)", 21: "VAT Filing (Mar)", 30: "Q1 CIT Filing"},
    5: {14: "PAYE Remittance (Apr)", 21: "VAT Filing (Apr)"},
    6: {14: "PAYE Remittance (May)", 21: "VAT Filing (May)"},
    7: {14: "PAYE Remittance (Jun)", 21: "VAT Filing (Jun)", 31: "Q2 CIT Filing"},
    8: {14: "PAYE Remittance (Jul)", 21: "VAT Filing (Jul)"},
    9: {14: "PAYE Remittance (Aug)", 21: "VAT Filing (Aug)"},
    10: {14: "PAYE Remittance (Sep)", 21: "VAT Filing (Sep)", 31: "Q3 CIT Filing"},
    11: {14: "PAYE Remittance (Oct)", 21: "VAT Filing (Oct)"},
    12: {14: "PAYE Remittance (Nov)", 21: "VAT Filing (Nov)", 31: "Year-end Planning"},
}

def get_upcoming_deadlines(days_ahead=30):
    today = datetime.now()
    upcoming = []
    for month in range(today.month, today.month + 2):
        current_month = ((month - 1) % 12) + 1
        year = today.year + (month - 1) // 12
        deadlines = TAX_CALENDAR.get(current_month, {})
        for day, name in deadlines.items():
            deadline_date = datetime(year, current_month, day)
            if deadline_date >= today:
                days = (deadline_date - today).days
                if days <= days_ahead:
                    upcoming.append({"name": name, "days": days, "date": deadline_date})
    return sorted(upcoming, key=lambda x: x["days"])

# ============ DATABASE FUNCTIONS ============
def get_or_create_user(platform, user_id, name=None):
    if not supabase:
        return None
    try:
        response = supabase.table("users").select("*").eq("platform", platform).eq("user_id", str(user_id)).execute()
        if response.data:
            return response.data[0]
        else:
            new_user = {"platform": platform, "user_id": str(user_id), "name": name, "created_at": datetime.now().isoformat(), "total_calculations": 0, "is_active": True}
            result = supabase.table("users").insert(new_user).execute()
            return result.data[0] if result.data else None
    except:
        return None

def log_calculation(user_id, calc_type, input_data, result_data):
    if not supabase:
        return False
    try:
        supabase.table("calculations").insert({
            "user_id": str(user_id), "calculation_type": calc_type,
            "input_data": json.dumps(input_data), "result_data": json.dumps(result_data),
            "created_at": datetime.now().isoformat()
        }).execute()
        supabase.table("users").update({"total_calculations": supabase.raw("total_calculations + 1"), "last_active": datetime.now().isoformat()}).eq("user_id", str(user_id)).execute()
        return True
    except:
        return False

def get_user_history(user_id):
    if not supabase:
        return []
    try:
        response = supabase.table("calculations").select("*").eq("user_id", str(user_id)).order("created_at", desc=True).limit(10).execute()
        return response.data
    except:
        return []

# ============ MESSAGE SENDING ============
def send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        return False
    try:
        url = f"{TELEGRAM_API_URL}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
        return True
    except:
        return False

# ============ FLASK ENDPOINTS ============

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy", "telegram": bool(TELEGRAM_TOKEN)})

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        update = request.get_json()
        if not update or 'message' not in update:
            return jsonify({"status": "ok"}), 200
        
        message = update['message']
        chat_id = str(message['chat']['id'])
        user_name = message.get('from', {}).get('first_name', 'User')
        text = message.get('text', '').strip()
        
        logging.info(f"Telegram from {chat_id}: {text}")
        get_or_create_user("telegram", chat_id, user_name)
        
        lang = get_user_language(chat_id)
        
        # Language selection menu
        if text == '/language':
            send_telegram_message(chat_id, get_translation(lang, "select_language"))
            return jsonify({"status": "ok"}), 200
        
        # Language selection handler
        if text in ['1', '2', '3', '4', '5']:
            lang_map = {"1": "en", "2": "pidgin", "3": "yoruba", "4": "hausa", "5": "igbo"}
            set_user_language(chat_id, lang_map[text])
            send_telegram_message(chat_id, get_translation(lang_map[text], "language_changed"))
            send_telegram_message(chat_id, get_translation(lang_map[text], "welcome"))
            return jsonify({"status": "ok"}), 200
        
        # Help command
        if text == '/help':
            send_telegram_message(chat_id, get_translation(lang, "help"))
            return jsonify({"status": "ok"}), 200
        
        # Start command
        if text == '/start':
            send_telegram_message(chat_id, get_translation(lang, "welcome"))
            return jsonify({"status": "ok"}), 200
        
        # PAYE calculation command
        if text.startswith('/paye '):
            parts = text.split()
            try:
                salary = float(parts[1].replace(',', ''))
                if salary > 0:
                    data = calculate_nigerian_paye(salary)
                    result = get_translation(lang, "paye_summary", gross=f"{data['gross']:,.0f}", pension=f"{data['pension']:,.0f}", nhf=f"{data['nhf']:,.0f}", tax=f"{data['tax']:,.0f}", net=f"{data['net']:,.0f}", rate=data['rate'])
                    send_telegram_message(chat_id, result)
                    log_calculation(chat_id, "paye", {"salary": salary}, data)
                else:
                    send_telegram_message(chat_id, get_translation(lang, "enter_amount"))
            except:
                send_telegram_message(chat_id, get_translation(lang, "enter_amount"))
            return jsonify({"status": "ok"}), 200
        
        # CIT calculation
        if text.startswith('/cit '):
            parts = text.split()
            try:
                turnover = float(parts[1].replace(',', ''))
                data = calculate_cit(turnover)
                msg = f"🏢 *CIT SUMMARY*\n\nTurnover: ₦{data['turnover']:,.0f}\nProfit: ₦{data['profit']:,.0f}\nSize: {data['size']}\nTotal Tax: *₦{data['total']:,.0f}*"
                send_telegram_message(chat_id, msg)
                log_calculation(chat_id, "cit", {"turnover": turnover}, data)
            except:
                send_telegram_message(chat_id, "Example: /cit 50000000")
            return jsonify({"status": "ok"}), 200
        
        # VAT calculation
        if text.startswith('/vat '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                data = calculate_vat(amount, False)
                msg = f"🧾 *VAT (7.5%)*\n\nAmount (excl): ₦{data['amount']:,.0f}\nVAT: ₦{data['vat']:,.0f}\nTotal: ₦{data['total']:,.0f}"
                send_telegram_message(chat_id, msg)
                log_calculation(chat_id, "vat", {"amount": amount}, data)
            except:
                send_telegram_message(chat_id, "Example: /vat 100000")
            return jsonify({"status": "ok"}), 200
        
        # VAT inclusive calculation
        if text.startswith('/vatin '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                data = calculate_vat(amount, True)
                msg = f"🧾 *VAT (7.5%)*\n\nAmount (incl): ₦{data['amount']:,.0f}\nVAT: ₦{data['vat']:,.0f}\nExclusive: ₦{data['exclusive']:,.0f}"
                send_telegram_message(chat_id, msg)
                log_calculation(chat_id, "vat", {"amount": amount}, data)
            except:
                send_telegram_message(chat_id, "Example: /vatin 107500")
            return jsonify({"status": "ok"}), 200
        
        # WHT calculation
        if text.startswith('/wht '):
            parts = text.split()
            try:
                amount = float(parts[1].replace(',', ''))
                trans_type = parts[2].lower() if len(parts) > 2 else "consultancy"
                data = calculate_wht(amount, trans_type)
                msg = f"📊 *WITHHOLDING TAX*\n\nAmount: ₦{data['amount']:,.0f}\nRate: {data['rate']}%\nWHT: *₦{data['wht']:,.0f}*\nNet Payment: ₦{data['net']:,.0f}"
                send_telegram_message(chat_id, msg)
                log_calculation(chat_id, "wht", {"amount": amount, "type": trans_type}, data)
            except:
                send_telegram_message(chat_id, "Example: /wht 500000 consultancy")
            return jsonify({"status": "ok"}), 200
        
        # WHT rates
        if text == '/whtrates':
            send_telegram_message(chat_id, get_translation(lang, "wht_rates"))
            return jsonify({"status": "ok"}), 200
        
        # Calendar
        if text == '/calendar':
            today = datetime.now()
            cal = calendar.monthcalendar(today.year, today.month)
            month_name = today.strftime("%B")
            msg = f"📅 *{month_name} {today.year} - Tax Calendar*\n\n"
            msg += "Mon Tue Wed Thu Fri Sat Sun\n"
            for week in cal:
                for day in week:
                    if day == 0:
                        msg += "    "
                    else:
                        msg += f"{day:3d} "
                msg += "\n"
            send_telegram_message(chat_id, msg)
            return jsonify({"status": "ok"}), 200
        
        # Deadlines
        if text == '/deadlines':
            upcoming = get_upcoming_deadlines(30)
            if not upcoming:
                send_telegram_message(chat_id, get_translation(lang, "no_deadlines"))
            else:
                msg = get_translation(lang, "deadlines")
                for d in upcoming:
                    if d['days'] == 0:
                        msg += f"{get_translation(lang, 'today')}{d['name']}\n"
                    elif d['days'] == 1:
                        msg += f"{get_translation(lang, 'tomorrow')}{d['name']}\n"
                    else:
                        msg += f"{get_translation(lang, 'days_left', name=d['name'], days=d['days'])}\n"
                send_telegram_message(chat_id, msg)
            return jsonify({"status": "ok"}), 200
        
        # Filing guides
        if text == '/filepaye':
            send_telegram_message(chat_id, get_translation(lang, "paye_guide"))
            return jsonify({"status": "ok"}), 200
        if text == '/filecit':
            send_telegram_message(chat_id, get_translation(lang, "cit_guide"))
            return jsonify({"status": "ok"}), 200
        if text == '/filevat':
            send_telegram_message(chat_id, get_translation(lang, "vat_guide"))
            return jsonify({"status": "ok"}), 200
        if text == '/filewht':
            send_telegram_message(chat_id, get_translation(lang, "wht_guide"))
            return jsonify({"status": "ok"}), 200
        
        # History
        if text == '/history':
            history = get_user_history(chat_id)
            if not history:
                send_telegram_message(chat_id, "📋 No history yet. Make some calculations!")
            else:
                msg = "📋 *YOUR HISTORY*\n\n"
                for h in history[:5]:
                    date = datetime.fromisoformat(h['created_at']).strftime("%b %d")
                    msg += f"{date}: {h['calculation_type'].upper()}\n"
                send_telegram_message(chat_id, msg)
            return jsonify({"status": "ok"}), 200
        
        # Default: salary calculation
        salary_match = re.search(r'[\d,]+', text.replace(',', ''))
        if salary_match:
            salary = float(salary_match.group())
            if salary > 0:
                data = calculate_nigerian_paye(salary)
                result = get_translation(lang, "paye_summary", gross=f"{data['gross']:,.0f}", pension=f"{data['pension']:,.0f}", nhf=f"{data['nhf']:,.0f}", tax=f"{data['tax']:,.0f}", net=f"{data['net']:,.0f}", rate=data['rate'])
                send_telegram_message(chat_id, result)
                log_calculation(chat_id, "paye", {"salary": salary}, data)
        else:
            send_telegram_message(chat_id, get_translation(lang, "enter_salary"))
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logging.error(f"Error: {e}")
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)