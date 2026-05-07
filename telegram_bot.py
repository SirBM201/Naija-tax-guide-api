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
    logging.info("✅ Supabase connected - Language preferences will be saved permanently!")
else:
    logging.warning("⚠️ Supabase not configured - Add SUPABASE_URL and SUPABASE_KEY to persist preferences")

# ============ TELEGRAM CONFIGURATION ============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
TELEGRAM_ENABLED = bool(TELEGRAM_TOKEN)

# ============ WHATSAPP CONFIGURATION ============
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "your_verify_token_here")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"
WHATSAPP_ENABLED = bool(WHATSAPP_ACCESS_TOKEN and PHONE_NUMBER_ID)

# ============ CRON JOB TEST USERS ============
TEST_TELEGRAM_CHAT_ID = os.getenv("TEST_TELEGRAM_CHAT_ID")
TEST_WHATSAPP_NUMBER = os.getenv("TEST_WHATSAPP_NUMBER")

# ============ USER SESSIONS ============
user_comparison_sessions = {}
user_quiz_sessions = {}
user_filing_sessions = {}

# ============ LANGUAGE PERSISTENCE WITH SUPABASE ============
def get_user_language(platform, user_id):
    """Get user's language preference from Supabase"""
    # Try to get from Supabase
    if supabase:
        try:
            response = supabase.table("user_preferences").select("preference_value").eq("user_id", str(user_id)).eq("platform", platform).eq("preference_key", "language").execute()
            if response.data:
                return response.data[0]["preference_value"]
        except Exception as e:
            logging.error(f"Failed to get language from Supabase: {e}")
    return "en"

def set_user_language(platform, user_id, lang):
    """Save user's language preference to Supabase"""
    if not supabase:
        return False
    
    try:
        # Check if exists
        existing = supabase.table("user_preferences").select("id").eq("user_id", str(user_id)).eq("platform", platform).eq("preference_key", "language").execute()
        
        if existing.data:
            # Update existing
            supabase.table("user_preferences").update({"preference_value": lang, "updated_at": datetime.now().isoformat()}).eq("id", existing.data[0]["id"]).execute()
        else:
            # Insert new
            supabase.table("user_preferences").insert({
                "user_id": str(user_id),
                "platform": platform,
                "preference_key": "language",
                "preference_value": lang,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }).execute()
        logging.info(f"✅ Saved language for {platform}/{user_id}: {lang}")
        return True
    except Exception as e:
        logging.error(f"Failed to save language: {e}")
        return False

# ============ TRANSLATIONS ============
TRANSLATIONS = {
    "en": {
        "welcome": "🇳🇬 *NIGERIA TAX BOT*\n\nComplete tax assistant!\n\n*Commands:*\n/paye [amount] - PAYE tax\n/cit [turnover] - Company tax\n/vat [amount] - VAT\n/wht [amount] [type] - WHT\n/compare - Compare salaries\n/quiz - Tax quiz\n/calendar - Tax calendar\n/deadlines - Due dates\n/filepaye - PAYE filing guide\n/language - Change language\n\nSend your salary to calculate PAYE!",
        "paye_summary": "*PAYE SUMMARY*\n\nGross: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nTax: ₦{tax}\nNet: *₦{net}*\nRate: {rate}%",
        "cit_summary": "*CIT SUMMARY*\n\nTurnover: ₦{turnover}\nProfit: ₦{profit}\nSize: {size}\nCIT Rate: {rate}%\nTotal Tax: *₦{total}*",
        "vat_summary": "*VAT (7.5%)*\n\nAmount: ₦{amount}\nVAT: ₦{vat}\nTotal: ₦{total}",
        "wht_summary": "*WITHHOLDING TAX*\n\nAmount: ₦{amount}\nRate: {rate}%\nWHT: *₦{wht}*\nNet Payment: ₦{net}",
        "wht_rates": "*WHT RATES*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": "*TAX BOT HELP*\n\n/paye [amount] - Calculate PAYE\n/cit [turnover] - Company tax\n/vat [amount] - Add 7.5% VAT\n/vatin [amount] - Extract VAT\n/wht [amount] [type] - WHT\n/compare - Compare salaries\n/quiz - Tax quiz\n/calendar - Tax calendar\n/deadlines - Due dates\n/filepaye - PAYE filing guide\n/filecit - CIT filing guide\n/filevat - VAT filing guide\n/filewht - WHT filing guide\n/language - Change language",
        "language_changed": "✅ Language changed to English! Your preference has been saved.",
        "select_language": "🌍 *Select your language:*\n\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo\n\nSend the number:",
        "compare_start": "*SALARY COMPARISON*\n\nSend up to 5 salary amounts.\n\nSend first salary (e.g., 500000):",
        "quiz_start": "*TAX QUIZ*\n\n{q}\n\n{opts}\n\nSend answer (1-4):",
        "quiz_correct": "✅ *Correct!* {exp}\n\nScore: {score}/{total}",
        "quiz_wrong": "❌ *Incorrect!* Answer: {correct}\n{exp}\n\nScore: {score}/{total}",
        "quiz_complete": "*QUIZ COMPLETE!*\n\nScore: {score}/{total}\nPercentage: {percent}%\n\nSend /quiz for new questions!",
        "deadlines": "*TAX DEADLINES*\n\n",
        "today": "⚠️ *TODAY:* ",
        "tomorrow": "🔔 *TOMORROW:* ",
        "days_left": "📌 {name} - {days} days left",
        "no_deadlines": "✅ No tax deadlines in the next 30 days",
        "calendar_view": "*{month} {year} - Tax Calendar*\n\nMon Tue Wed Thu Fri Sat Sun\n",
        "filing_question": "*{tax_type} FILING ASSISTANT*\n\n{question}",
        "filing_done": "*FILING CHECKLIST - {tax_type}*\n\nData collected: {count} items ✓\nReady for filing!\n\nUse FIRS e-Filing portal to submit.",
        "added": "✅ Added ₦{salary:,.0f}\n",
        "compare_done": "Need at least 2 salaries. Send more or /cancel",
        "enter_amount": "Please enter a positive amount",
        "invalid": "Invalid command. Send /help for available commands"
    },
    "pidgin": {
        "welcome": "🇳🇬 *NIGERIA TAX BOT (Pidgin)*\n\nYour complete tax assistant!\n\n*Commands:*\n/paye [amount] - PAYE tax\n/cit [turnover] - Company tax\n/vat [amount] - VAT\n/compare - Compare salaries\n/quiz - Tax quiz\n/calendar - Tax calendar\n/language - Change language\n\nSend your salary to calculate PAYE!",
        "paye_summary": "*PAYE SUMMARY (Pidgin)*\n\nMoney wey you collect: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nTax wey you go pay: ₦{tax}\nYour take home: *₦{net}*\nTax rate: {rate}%",
        "cit_summary": "*CIT SUMMARY (Pidgin)*\n\nTurnover: ₦{turnover}\nProfit: ₦{profit}\nCompany size: {size}\nCIT Rate: {rate}%\nTotal Tax: *₦{total}*",
        "vat_summary": "*VAT (7.5%) (Pidgin)*\n\nAmount: ₦{amount}\nVAT: ₦{vat}\nTotal: ₦{total}",
        "wht_summary": "*WITHHOLDING TAX (Pidgin)*\n\nAmount: ₦{amount}\nRate: {rate}%\nWHT: *₦{wht}*\nNet Payment: ₦{net}",
        "wht_rates": "*WHT RATES (Pidgin)*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": "*TAX BOT HELP (Pidgin)*\n\n/paye [amount] - Calculate PAYE\n/cit [turnover] - Company tax\n/vat [amount] - Add VAT\n/wht [amount] [type] - WHT\n/compare - Compare salaries\n/quiz - Tax quiz\n/calendar - Tax calendar\n/deadlines - Due dates\n/language - Change language",
        "language_changed": "✅ We don change language to Pidgin English! Your preference don save.",
        "select_language": "🌍 *Select your language:*\n\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo\n\nSend the number:",
        "compare_start": "*SALARY COMPARISON (Pidgin)*\n\nSend up to 5 salary amounts.\n\nSend first salary (e.g., 500000):",
        "quiz_start": "*TAX QUIZ (Pidgin)*\n\n{q}\n\n{opts}\n\nSend answer (1-4):",
        "quiz_correct": "✅ *Correct!* {exp}\n\nScore: {score}/{total}",
        "quiz_wrong": "❌ *Incorrect!* Answer: {correct}\n{exp}\n\nScore: {score}/{total}",
        "quiz_complete": "*QUIZ COMPLETE!*\n\nScore: {score}/{total}\nPercentage: {percent}%\n\nSend /quiz for new questions!",
        "deadlines": "*TAX DEADLINES WEY DEY COME*\n\n",
        "today": "⚠️ *TODAY:* ",
        "tomorrow": "🔔 *TOMORROW:* ",
        "days_left": "📌 {name} - {days} days left",
        "no_deadlines": "✅ No tax deadlines for next 30 days",
        "calendar_view": "*{month} {year} - Tax Calendar (Pidgin)*\n\nMon Tue Wed Thu Fri Sat Sun\n",
        "filing_question": "*{tax_type} FILING ASSISTANT (Pidgin)*\n\n{question}",
        "filing_done": "*FILING CHECKLIST - {tax_type} (Pidgin)*\n\nData collected ✓\nReady for filing!\n\nUse FIRS e-Filing portal to submit.",
        "added": "✅ Added ₦{salary:,.0f}\n",
        "compare_done": "Need at least 2 salaries. Send more or /cancel",
        "enter_amount": "Please enter positive amount",
        "invalid": "Invalid command. Send /help"
    },
    "yoruba": {
        "welcome": "🇳🇬 *NIGERIA TAX BOT (Yorùbá)*\n\nOluṣe iranlọwọ orí-ori rẹ!\n\n*Commands:*\n/paye [owó] - Owo-ori PAYE\n/cit [owo-iye] - Owo-ori ile-iṣẹ\n/vat [owo] - VAT\n/compare - Fi owo we\n/quiz - Idanwo owo-ori\n/calendar - Kalẹnda\n/language - Yipada ede\n\nFi owo-oṣooṣu rẹ ranṣẹ!",
        "paye_summary": "*PAYE SUMMARY (Yorùbá)*\n\nOwo-oṣooṣu: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nOwo-ori: ₦{tax}\nOwo ti o gba: *₦{net}*\nOṣuwọn: {rate}%",
        "cit_summary": "*CIT SUMMARY (Yorùbá)*\n\nTurnover: ₦{turnover}\nProfit: ₦{profit}\nIwọn: {size}\nOṣuwọn CIT: {rate}%\nOwo-ori lapapọ: *₦{total}*",
        "vat_summary": "*VAT (7.5%) (Yorùbá)*\n\nIye owo: ₦{amount}\nVAT: ₦{vat}\nLapapọ: ₦{total}",
        "wht_summary": "*WITHHOLDING TAX (Yorùbá)*\n\nIye owo: ₦{amount}\nOṣuwọn: {rate}%\nWHT: *₦{wht}*\nIsanwo net: ₦{net}",
        "wht_rates": "*WHT RATES (Yorùbá)*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": "*IRANLỌWỌ BOT OWO-ORI (Yorùbá)*\n\n/paye [owó] - Owo-ori PAYE\n/cit [owo-iye] - Owo-ori ile-iṣẹ\n/vat [owo] - VAT\n/wht [owo] [irú] - WHT\n/compare - Fi owo we\n/quiz - Idanwo\n/calendar - Kalẹnda\n/deadlines - Awọn ọjọ-ipari\n/language - Yipada ede",
        "language_changed": "✅ A ti yipada ede si Yorùbá! A ti fipamọ ayanfẹ rẹ.",
        "select_language": "🌍 *Yan ede rẹ:*\n\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo\n\nFi nọ́ńbà ranṣẹ:",
        "compare_start": "*ÌFỌ̀RỌ̀ OWO-OṢOOṢU*\n\nFiranṣẹ owo-oṣooṣu to fi 5.\n\nFiranṣẹ akọkọ (fun apẹẹrẹ, 500000):",
        "quiz_start": "*ÌDÁNWÓ OWO-ORI*\n\n{q}\n\n{opts}\n\nFiranṣẹ nọ́ńbà (1-4):",
        "quiz_correct": "✅ *Ó tọ!* {exp}\n\nDimegilio: {score}/{total}",
        "quiz_wrong": "❌ *Aṣiṣe!* Ìdáhùn: {correct}\n{exp}\n\nDimegilio: {score}/{total}",
        "quiz_complete": "*ÌDÁNWÓ PARÍ!*\n\nDimegilio: {score}/{total}\nÌpín: {percent}%\n\nFi /quiz ranṣẹ fun awọn ibeere titun!",
        "deadlines": "*AWỌN ỌJỌ-IPARI TI NMỌ SỌDỌ*\n\n",
        "today": "⚠️ *ÒNÍ:* ",
        "tomorrow": "🔔 *ỌLA:* ",
        "days_left": "📌 {name} - ọjọ {days} le",
        "no_deadlines": "✅ Ko si awọn ọjọ-ipari ni awọn ọjọ 30 to nbọ",
        "calendar_view": "*{month} {year} - Kalẹnda Owo-ori*\n\nMon Tue Wed Thu Fri Sat Sun\n",
        "filing_question": "*{tax_type} ÌRANLỌWỌ IFILE*\n\n{question}",
        "filing_done": "*AYẸWÒ IFILE - {tax_type}*\n\nData ti a gba ✓\nṢetan fun filing!\n\nLo oju opo wẹẹbu FIRS e-Filing.",
        "added": "✅ A fi kun ₦{salary:,.0f}\n",
        "compare_done": "O nilo o kere ju owo-oṣooṣu meji. Fi awọn miiran ranṣẹ tabi /cancel",
        "enter_amount": "Jọwọ tẹ iye to pe",
        "invalid": "Aṣẹ ti ko tọ. Fi /help ranṣẹ"
    },
    "hausa": {
        "welcome": "🇳🇬 *NIGERIA TAX BOT (Hausa)*\n\nCikakken mataimakin haraji!\n\n*Umarni:*\n/paye [adadin] - Harajin PAYE\n/cit [juyawa] - Harajin kamfani\n/vat [adadin] - VAT\n/compare - Kwatanta albashi\n/quiz - Tambayoyin haraji\n/calendar - Kalandar haraji\n/language - Canza yare\n\nAika albashin ka don lissafin PAYE!",
        "paye_summary": "*PAYE SUMMARY (Hausa)*\n\nAlbashin wata: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nHaraji: ₦{tax}\nAbin da zaka karba: *₦{net}*\nAdadin haraji: {rate}%",
        "cit_summary": "*CIT SUMMARY (Hausa)*\n\nJuyawa: ₦{turnover}\nRiba: ₦{profit}\nGirman kamfani: {size}\nAdadin CIT: {rate}%\nJimlar Haraji: *₦{total}*",
        "vat_summary": "*VAT (7.5%) (Hausa)*\n\nAdadin: ₦{amount}\nVAT: ₦{vat}\nJimlar: ₦{total}",
        "wht_summary": "*WITHHOLDING TAX (Hausa)*\n\nAdadin: ₦{amount}\nAdadin: {rate}%\nWHT: *₦{wht}*\nBiyan net: ₦{net}",
        "wht_rates": "*WHT RATES (Hausa)*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": "*TAIMAKON BOT HARAJI (Hausa)*\n\n/paye [adadin] - Harajin PAYE\n/cit [juyawa] - Harajin kamfani\n/vat [adadin] - VAT\n/wht [adadin] [nau'i] - WHT\n/compare - Kwatanta albashi\n/quiz - Tambayoyi\n/calendar - Kalandar\n/deadlines - Kwanakin ƙarshe\n/language - Canza yare",
        "language_changed": "✅ An canza yare zuwa Hausa! An ajiye zaɓinka.",
        "select_language": "🌍 *Zaɓi yarenka:*\n\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo\n\nAika lambar:",
        "compare_start": "*KWATANTA ALBASHI*\n\nAika albashin har guda 5.\n\nAika albashin farko (misali, 500000):",
        "quiz_start": "*TAMBAYOYIN HARAJI*\n\n{q}\n\n{opts}\n\nAika lambar (1-4):",
        "quiz_correct": "✅ *Daidai!* {exp}\n\nMaki: {score}/{total}",
        "quiz_wrong": "❌ *Kuskure!* Amsa: {correct}\n{exp}\n\nMaki: {score}/{total}",
        "quiz_complete": "*TAMBAYOYIN SUN ƘARE!*\n\nMaki: {score}/{total}\nKashi: {percent}%\n\nAika /quiz don sababbin tambayoyi!",
        "deadlines": "*KUNAKIN ƘARSHE MASU ZUWA*\n\n",
        "today": "⚠️ *YAU:* ",
        "tomorrow": "🔔 *GOBE:* ",
        "days_left": "📌 {name} - {days} days left",
        "no_deadlines": "✅ Babu kwanakin ƙarshe a cikin kwanaki 30 masu zuwa",
        "calendar_view": "*{month} {year} - Kalandar Haraji*\n\nMon Tue Wed Thu Fri Sat Sun\n",
        "filing_question": "*{tax_type} JAGORORIN SHIGAR DA HARAJI*\n\n{question}",
        "filing_done": "*JERIN SHIGAR DA HARAJI - {tax_type}*\n\nAn tattara bayanai ✓\nA shirye don shigarwa!\n\nYi amfani da tashar yanar gizo ta FIRS e-Filing.",
        "added": "✅ An ƙara ₦{salary:,.0f}\n",
        "compare_done": "Kana buƙatar aƙalla albashi 2. Aika ƙarin ko /cancel",
        "enter_amount": "Don Allah shigar da adadi mai inganci",
        "invalid": "Umarni mara inganci. Aika /help"
    },
    "igbo": {
        "welcome": "🇳🇬 *NIGERIA TAX BOT (Igbo)*\n\nOnye na-enyere gị aka n'ụtụ isi!\n\n*Iwu:*\n/paye [ego] - Ụtụ PAYE\n/cit [ntughari] - Ụtụ ụlọ ọrụ\n/vat [ego] - VAT\n/compare - Tụnyere ụgwọ ọnwa\n/quiz - Ajụjụ ụtụ isi\n/calendar - Kalenda ụtụ isi\n/language - Gbanwee asụsụ\n\nZiga ọnwa ọnwa gị maka ngụkọ PAYE!",
        "paye_summary": "*PAYE SUMMARY (Igbo)*\n\nEgo ọnwa: ₦{gross}\nPension: ₦{pension}\nNHF: ₦{nhf}\nỤtụ: ₦{tax}\nEgo ị ga-enweta: *₦{net}*\nỌnụ ego: {rate}%",
        "cit_summary": "*CIT SUMMARY (Igbo)*\n\nNtughari: ₦{turnover}\nUru: ₦{profit}\nNha ụlọ ọrụ: {size}\nỌnụ CIT: {rate}%\nNgụkọta Ụtụ: *₦{total}*",
        "vat_summary": "*VAT (7.5%) (Igbo)*\n\nEgo: ₦{amount}\nVAT: ₦{vat}\nNgụkọta: ₦{total}",
        "wht_summary": "*WITHHOLDING TAX (Igbo)*\n\nEgo: ₦{amount}\nỌnụ ego: {rate}%\nWHT: *₦{wht}*\nỊkwụ ụgwọ net: ₦{net}",
        "wht_rates": "*WHT RATES (Igbo)*\n\n10%: Consultancy, Rent, Interest, Dividend\n5%: Construction, Contracts\n3%: Transportation",
        "help": "*ENYEMAKA BOT ỤTỤ ISI (Igbo)*\n\n/paye [ego] - Ụtụ PAYE\n/cit [ntughari] - Ụtụ ụlọ ọrụ\n/vat [ego] - VAT\n/wht [ego] [ụdị] - WHT\n/compare - Tụnyere ụgwọ ọnwa\n/quiz - Ajụjụ\n/calendar - Kalenda\n/deadlines - Ụbọchị njedebe\n/language - Gbanwee asụsụ",
        "language_changed": "✅ Agbanweela asụsụ gaa na Igbo! Echekwabara mmasị gị.",
        "select_language": "🌍 *Họrọ asụsụ gị:*\n\n1. English\n2. Pidgin English\n3. Yorùbá\n4. Hausa\n5. Igbo\n\nZiga nọmba:",
        "compare_start": "*ỊTỤNYERE ỤGWỌ ỌNWA*\n\nZiga ụgwọ ọnwa ruru 5.\n\nZiga nke mbụ (dịka, 500000):",
        "quiz_start": "*AJỤJỤ ỤTỤ ISI*\n\n{q}\n\n{opts}\n\nZiga nọmba (1-4):",
        "quiz_correct": "✅ *Ọ ziri ezi!* {exp}\n\nAkara: {score}/{total}",
        "quiz_wrong": "❌ *Ọ ezighi ezi!* Azịza: {correct}\n{exp}\n\nAkara: {score}/{total}",
        "quiz_complete": "*AJỤJỤ GỤCHARA!*\n\nAkara: {score}/{total}\nPasentị: {percent}%\n\nZiga /quiz maka ajụjụ ọhụrụ!",
        "deadlines": "*ỤBỌCHỊ NJEDEBE NA-ABỊA*\n\n",
        "today": "⚠️ *TAA:* ",
        "tomorrow": "🔔 *ECHI:* ",
        "days_left": "📌 {name} - ụbọchị {days} fọdụrụ",
        "no_deadlines": "✅ Ọ nweghị ụbọchị njedebe n'ime ụbọchị 30 na-abịa",
        "calendar_view": "*{month} {year} - Kalenda Ụtụ Isi*\n\nMon Tue Wed Thu Fri Sat Sun\n",
        "filing_question": "*{tax_type} NTUZI ỊGBANYE ỤTỤ*\n\n{question}",
        "filing_done": "*NDỊRỊ ỊGBANYE ỤTỤ - {tax_type}*\n\nEchịkọtala data ✓\nDị njikere maka ịgbanye!\n\nJiri webụsaịtị FIRS e-Filing.",
        "added": "✅ Agbakwunyere ₦{salary:,.0f}\n",
        "compare_done": "Ị chọrọ opekata mpe ụgwọ ọnwa abụọ. Ziga ndị ọzọ ma ọ bụ /cancel",
        "enter_amount": "Biko tinye ego ziri ezi",
        "invalid": "Iwu na-ezighi ezi. Ziga /help"
    }
}

# ============ WHT RATES ============
WHT_RATES = {
    "consultancy": 10, "rent": 10, "interest": 10, "dividend": 10,
    "construction": 5, "contracts": 5, "transport": 3
}

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

# ============ QUIZ QUESTIONS ============
QUIZ_QUESTIONS = [
    {"q": "What is the current VAT rate in Nigeria?", "opt": ["5%", "7.5%", "10%", "12.5%"], "correct": 1, "exp": "VAT rate in Nigeria is 7.5%"},
    {"q": "By which date must PAYE be remitted monthly?", "opt": ["7th", "14th", "21st", "30th"], "correct": 1, "exp": "PAYE must be remitted by the 14th of each month"},
    {"q": "What is the CIT rate for large companies?", "opt": ["20%", "25%", "30%", "35%"], "correct": 2, "exp": "Large companies pay 30% CIT + 3% Education Tax"},
    {"q": "When must VAT returns be filed monthly?", "opt": ["7th", "14th", "21st", "30th"], "correct": 2, "exp": "VAT returns are due by the 21st of each month"},
    {"q": "What is the WHT rate for consultancy services?", "opt": ["5%", "7.5%", "10%", "12.5%"], "correct": 2, "exp": "Consultancy services attract 10% Withholding Tax"},
    {"q": "What is the penalty for late CIT filing?", "opt": ["₦100k", "₦250k", "₦500k", "₦1M"], "correct": 2, "exp": "Late CIT penalty is ₦500,000 + 10% of tax due"},
]

# ============ CALCULATION FUNCTIONS ============
def calculate_paye(monthly_gross):
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
    rate = (annual_tax / annual_gross) * 100 if annual_gross > 0 else 0
    
    return {
        "gross": monthly_gross,
        "pension": round(pension),
        "nhf": round(nhf),
        "tax": round(monthly_tax),
        "net": round(monthly_gross - pension - nhf - monthly_tax),
        "rate": round(rate, 1)
    }

def calculate_cit(turnover, profit=None):
    if profit is None:
        profit = turnover * 0.20
    if turnover < 25000000:
        size = "Small (Exempt)"
        rate = 0
    elif turnover <= 100000000:
        size = "Medium"
        rate = 20
    else:
        size = "Large"
        rate = 30
    cit = profit * rate / 100
    education = profit * 0.03
    total = cit + education
    return {"turnover": turnover, "profit": profit, "size": size, "rate": rate, "total": round(total)}

def calculate_vat(amount, inclusive=False):
    if inclusive:
        vat = amount * 0.075 / 1.075
        exclusive = amount - vat
        total = amount
    else:
        vat = amount * 0.075
        exclusive = amount
        total = amount + vat
    return {"amount": amount, "vat": round(vat), "exclusive": round(exclusive), "total": round(total)}

def calculate_wht(amount, trans_type):
    rate = WHT_RATES.get(trans_type, 10)
    wht = amount * rate / 100
    return {"amount": amount, "rate": rate, "wht": round(wht), "net": round(amount - wht)}

# ============ COMPARISON SESSION ============
class ComparisonSession:
    def __init__(self):
        self.salaries = []
    def add(self, salary):
        self.salaries.append(calculate_paye(salary))
        return len(self.salaries)
    def is_full(self):
        return len(self.salaries) >= 5
    def get_result(self, lang, t):
        if not self.salaries:
            return "No salaries to compare."
        msg = ""
        for i, s in enumerate(self.salaries, 1):
            msg += f"{i}. ₦{s['gross']:,.0f} → ₦{s['net']:,.0f} net (Tax: ₦{s['tax']:,.0f})\n"
        best = max(self.salaries, key=lambda x: x['net'])
        msg += f"\n*Best net:* ₦{best['gross']:,.0f} → ₦{best['net']:,.0f}"
        return msg

# ============ QUIZ SESSION ============
class QuizSession:
    def __init__(self):
        self.questions = random.sample(QUIZ_QUESTIONS, min(5, len(QUIZ_QUESTIONS)))
        self.index = 0
        self.score = 0
    def current(self):
        if self.index < len(self.questions):
            return self.questions[self.index]
        return None
    def answer(self, choice):
        q = self.current()
        if not q:
            return None
        correct = (choice == q['correct'])
        if correct:
            self.score += 1
        result = {"correct": correct, "exp": q['exp'], "correct_answer": q['opt'][q['correct']]}
        self.index += 1
        return result
    def is_done(self):
        return self.index >= len(self.questions)
    def get_score(self):
        return f"{self.score}/{len(self.questions)}"

# ============ FILING SESSION ============
class FilingSession:
    def __init__(self, tax_type):
        self.tax_type = tax_type
        self.step = 1
        self.data = {}
    def get_question(self):
        questions = {
            "paye": ["Send your company TIN:", "Number of employees:", "Filing month (e.g., January 2024):", "PAYE computation ready? (yes/no):", "Payment made? (yes/no):"],
            "cit": ["Send your company TIN:", "Annual turnover (₦):", "Assessable profit (₦):", "Audited statements ready? (yes/no):", "Quarterly returns filed? (yes/no):"],
            "vat": ["Send your company TIN:", "Output VAT collected (₦):", "Input VAT paid (₦):", "Sales invoices ready? (yes/no):", "Purchase invoices ready? (yes/no):"],
            "wht": ["Send your company TIN:", "Number of payments made:", "Total amount (₦):", "Credit notes issued? (yes/no):", "WHT certificates ready? (yes/no):"]
        }
        return questions.get(self.tax_type, questions["paye"])[self.step - 1]
    def process(self, answer):
        fields = {"paye": ["tin", "employees", "month", "computation", "payment"], "cit": ["tin", "turnover", "profit", "audited", "quarterly"], "vat": ["tin", "output", "input", "invoices", "purchases"], "wht": ["tin", "payments", "amount", "credit_notes", "certificates"]}
        self.data[fields.get(self.tax_type, [])[self.step - 1]] = answer
        self.step += 1
        return self.step > 5
    def get_summary(self, lang, t):
        return f"Data collected: {len(self.data)} items ✓\nReady for filing!\n\nUse FIRS e-Filing portal to submit."

# ============ HELPER FUNCTIONS ============
def get_upcoming_deadlines(days=30):
    today = datetime.now()
    upcoming = []
    for month in range(today.month, today.month + 2):
        m = ((month - 1) % 12) + 1
        year = today.year + (month - 1) // 12
        for day, name in TAX_CALENDAR.get(m, {}).items():
            d = datetime(year, m, day)
            if d >= today:
                diff = (d - today).days
                if diff <= days:
                    upcoming.append({"name": name, "days": diff, "date": d})
    return sorted(upcoming, key=lambda x: x["days"])[:10]

def format_deadlines(upcoming, t):
    if not upcoming:
        return t("no_deadlines")
    msg = t("deadlines")
    for d in upcoming:
        if d['days'] == 0:
            msg += f"{t('today')}{d['name']}\n"
        elif d['days'] == 1:
            msg += f"{t('tomorrow')}{d['name']}\n"
        else:
            msg += f"{t('days_left', name=d['name'], days=d['days'])}\n"
    return msg

def get_calendar_view(lang, t):
    today = datetime.now()
    cal = calendar.monthcalendar(today.year, today.month)
    month = today.strftime("%B")
    msg = t("calendar_view", month=month, year=today.year)
    for week in cal:
        for day in week:
            if day == 0:
                msg += "    "
            else:
                if day in TAX_CALENDAR.get(today.month, {}):
                    msg += f"*{day:2d}* "
                else:
                    msg += f"{day:2d} "
        msg += "\n"
    return msg

# ============ TRANSLATION HELPER ============
def get_text(lang, key, **kwargs):
    translation = TRANSLATIONS.get(lang, TRANSLATIONS["en"])
    text = translation.get(key, TRANSLATIONS["en"].get(key, key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except:
            return text
    return text

# ============ MESSAGE SENDING ============
def send_message(platform, recipient, text):
    if platform == "telegram" and TELEGRAM_ENABLED:
        try:
            url = f"{TELEGRAM_API_URL}/sendMessage"
            requests.post(url, json={"chat_id": recipient, "text": text, "parse_mode": "Markdown"}, timeout=10)
            return True
        except:
            return False
    elif platform == "whatsapp" and WHATSAPP_ENABLED:
        try:
            url = f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages"
            headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
            payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient, "type": "text", "text": {"body": text}}
            requests.post(url, json=payload, headers=headers, timeout=10)
            return True
        except:
            return False
    return False

def process_command(platform, user_id, text, user_name="User"):
    lang = get_user_language(platform, user_id)
    t = lambda key, **kwargs: get_text(lang, key, **kwargs)
    
    # Language selection menu
    if text == '/language':
        send_message(platform, user_id, t("select_language"))
        return True
    
    if text in ['1', '2', '3', '4', '5']:
        lang_map = {"1": "en", "2": "pidgin", "3": "yoruba", "4": "hausa", "5": "igbo"}
        set_user_language(platform, user_id, lang_map[text])
        send_message(platform, user_id, t("language_changed"))
        send_message(platform, user_id, t("welcome"))
        return True
    
    # Comparison session
    session_key = f"{platform}_{user_id}_compare"
    if session_key in user_comparison_sessions:
        session = user_comparison_sessions[session_key]
        salary_match = re.search(r'[\d,]+', text.replace(',', ''))
        if salary_match:
            salary = float(salary_match.group())
            if salary > 0:
                count = session.add(salary)
                if session.is_full():
                    send_message(platform, user_id, session.get_result(lang, t))
                    del user_comparison_sessions[session_key]
                else:
                    send_message(platform, user_id, t("added", salary=salary) + f"Send {5-count} more or 'done' to finish:")
            else:
                send_message(platform, user_id, t("enter_amount"))
        elif text.lower() == 'done':
            if len(session.salaries) >= 2:
                send_message(platform, user_id, session.get_result(lang, t))
            else:
                send_message(platform, user_id, t("compare_done"))
            del user_comparison_sessions[session_key]
        return True
    
    # Quiz session
    quiz_key = f"{platform}_{user_id}_quiz"
    if quiz_key in user_quiz_sessions:
        session = user_quiz_sessions[quiz_key]
        if text in ['1', '2', '3', '4']:
            result = session.answer(int(text) - 1)
            if result:
                if result['correct']:
                    msg = t("quiz_correct", exp=result['exp'], score=session.score, total=session.index)
                else:
                    msg = t("quiz_wrong", correct=result['correct_answer'], exp=result['exp'], score=session.score, total=session.index)
                
                if session.is_done():
                    percent = int((session.score / len(session.questions)) * 100)
                    msg += f"\n\n{t('quiz_complete', score=session.score, total=len(session.questions), percent=percent)}"
                    del user_quiz_sessions[quiz_key]
                else:
                    q = session.current()
                    opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q['opt'])])
                    msg += f"\n\n*Next:* {q['q']}\n\n{opts}\n\nSend number (1-4):"
                send_message(platform, user_id, msg)
        else:
            send_message(platform, user_id, "Send number (1-4) for your answer, or /quiz to start over")
        return True
    
    # Filing session
    filing_key = f"{platform}_{user_id}_filing"
    if filing_key in user_filing_sessions:
        session = user_filing_sessions[filing_key]
        is_done = session.process(text)
        if is_done:
            send_message(platform, user_id, f"*FILING CHECKLIST - {session.tax_type.upper()}*\n\n{session.get_summary(lang, t)}")
            del user_filing_sessions[filing_key]
        else:
            send_message(platform, user_id, t("filing_question", tax_type=session.tax_type.upper(), question=session.get_question()))
        return True
    
    # Commands
    if text == '/start' or text == 'start':
        send_message(platform, user_id, t("welcome"))
        return True
    
    if text == '/help' or text == 'help':
        send_message(platform, user_id, t("help"))
        return True
    
    if text.startswith('/paye '):
        try:
            salary = float(text.split()[1].replace(',', ''))
            if salary > 0:
                d = calculate_paye(salary)
                send_message(platform, user_id, t("paye_summary", gross=f"{d['gross']:,.0f}", pension=f"{d['pension']:,.0f}", nhf=f"{d['nhf']:,.0f}", tax=f"{d['tax']:,.0f}", net=f"{d['net']:,.0f}", rate=d['rate']))
            else:
                send_message(platform, user_id, t("enter_amount"))
        except:
            send_message(platform, user_id, "Example: /paye 500000")
        return True
    
    if text.startswith('/cit '):
        try:
            turnover = float(text.split()[1].replace(',', ''))
            d = calculate_cit(turnover)
            send_message(platform, user_id, t("cit_summary", turnover=f"{d['turnover']:,.0f}", profit=f"{d['profit']:,.0f}", size=d['size'], rate=d['rate'], total=f"{d['total']:,.0f}"))
        except:
            send_message(platform, user_id, "Example: /cit 50000000")
        return True
    
    if text.startswith('/vat '):
        try:
            amount = float(text.split()[1].replace(',', ''))
            d = calculate_vat(amount, False)
            send_message(platform, user_id, t("vat_summary", amount=f"{d['amount']:,.0f}", vat=f"{d['vat']:,.0f}", total=f"{d['total']:,.0f}"))
        except:
            send_message(platform, user_id, "Example: /vat 100000")
        return True
    
    if text.startswith('/vatin '):
        try:
            amount = float(text.split()[1].replace(',', ''))
            d = calculate_vat(amount, True)
            send_message(platform, user_id, f"*VAT (7.5%)*\n\nAmount (incl): ₦{d['amount']:,.0f}\nVAT: ₦{d['vat']:,.0f}\nExclusive: ₦{d['exclusive']:,.0f}")
        except:
            send_message(platform, user_id, "Example: /vatin 107500")
        return True
    
    if text.startswith('/wht '):
        parts = text.split()
        try:
            amount = float(parts[1].replace(',', ''))
            ttype = parts[2].lower() if len(parts) > 2 else "consultancy"
            d = calculate_wht(amount, ttype)
            send_message(platform, user_id, t("wht_summary", amount=f"{d['amount']:,.0f}", rate=d['rate'], wht=f"{d['wht']:,.0f}", net=f"{d['net']:,.0f}"))
        except:
            send_message(platform, user_id, "Example: /wht 500000 consultancy\nTypes: consultancy, rent, interest, construction, transport")
        return True
    
    if text == '/whtrates':
        send_message(platform, user_id, t("wht_rates"))
        return True
    
    if text == '/compare':
        user_comparison_sessions[f"{platform}_{user_id}_compare"] = ComparisonSession()
        send_message(platform, user_id, t("compare_start"))
        return True
    
    if text == '/quiz':
        user_quiz_sessions[f"{platform}_{user_id}_quiz"] = QuizSession()
        q = user_quiz_sessions[f"{platform}_{user_id}_quiz"].current()
        opts = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q['opt'])])
        send_message(platform, user_id, t("quiz_start", q=q['q'], opts=opts))
        return True
    
    if text == '/calendar':
        send_message(platform, user_id, get_calendar_view(lang, t))
        return True
    
    if text == '/deadlines':
        send_message(platform, user_id, format_deadlines(get_upcoming_deadlines(30), t))
        return True
    
    if text == '/filepaye':
        user_filing_sessions[f"{platform}_{user_id}_filing"] = FilingSession("paye")
        send_message(platform, user_id, t("filing_question", tax_type="PAYE", question=user_filing_sessions[f"{platform}_{user_id}_filing"].get_question()))
        return True
    
    if text == '/filecit':
        user_filing_sessions[f"{platform}_{user_id}_filing"] = FilingSession("cit")
        send_message(platform, user_id, t("filing_question", tax_type="CIT", question=user_filing_sessions[f"{platform}_{user_id}_filing"].get_question()))
        return True
    
    if text == '/filevat':
        user_filing_sessions[f"{platform}_{user_id}_filing"] = FilingSession("vat")
        send_message(platform, user_id, t("filing_question", tax_type="VAT", question=user_filing_sessions[f"{platform}_{user_id}_filing"].get_question()))
        return True
    
    if text == '/filewht':
        user_filing_sessions[f"{platform}_{user_id}_filing"] = FilingSession("wht")
        send_message(platform, user_id, t("filing_question", tax_type="WHT", question=user_filing_sessions[f"{platform}_{user_id}_filing"].get_question()))
        return True
    
    # Default: PAYE calculation from number
    salary_match = re.search(r'[\d,]+', text.replace(',', ''))
    if salary_match:
        salary = float(salary_match.group())
        if salary > 0:
            d = calculate_paye(salary)
            send_message(platform, user_id, t("paye_summary", gross=f"{d['gross']:,.0f}", pension=f"{d['pension']:,.0f}", nhf=f"{d['nhf']:,.0f}", tax=f"{d['tax']:,.0f}", net=f"{d['net']:,.0f}", rate=d['rate']))
        else:
            send_message(platform, user_id, t("enter_amount"))
    else:
        send_message(platform, user_id, t("invalid"))
    
    return True

# ============ FLASK ENDPOINTS ============

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "telegram": TELEGRAM_ENABLED,
        "whatsapp": WHATSAPP_ENABLED,
        "supabase": supabase is not None,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        update = request.get_json()
        if not update or 'message' not in update:
            return jsonify({"status": "ok"}), 200
        
        msg = update['message']
        chat_id = str(msg['chat']['id'])
        user_name = msg.get('from', {}).get('first_name', 'User')
        text = msg.get('text', '').strip()
        
        logging.info(f"Telegram {chat_id}: {text}")
        process_command("telegram", chat_id, text, user_name)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.error(f"Telegram error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode and token and mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
            return challenge, 200
        return "Verification failed", 403
    
    try:
        body = request.get_json()
        entry = body.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        messages = value.get('messages', [])
        
        if not messages:
            return jsonify({"status": "ok"}), 200
        
        for message in messages:
            from_number = message.get('from')
            msg_type = message.get('type')
            
            if msg_type == 'text':
                text = message.get('text', {}).get('body', '').strip()
                logging.info(f"WhatsApp {from_number}: {text}")
                process_command("whatsapp", from_number, text)
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.error(f"WhatsApp error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/cron/send-deadline-reminders', methods=['POST', 'GET'])
def send_deadline_reminders():
    try:
        upcoming = get_upcoming_deadlines(7)
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_ENABLED:
            lang = get_user_language("telegram", TEST_TELEGRAM_CHAT_ID)
            t = lambda key, **kwargs: get_text(lang, key, **kwargs)
            msg = format_deadlines(upcoming, t)
            send_message("telegram", TEST_TELEGRAM_CHAT_ID, msg)
        
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ENABLED:
            lang = get_user_language("whatsapp", TEST_WHATSAPP_NUMBER)
            t = lambda key, **kwargs: get_text(lang, key, **kwargs)
            msg = format_deadlines(upcoming, t)
            send_message("whatsapp", TEST_WHATSAPP_NUMBER, msg)
        
        return jsonify({"status": "success", "deadlines": len(upcoming)}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/cron/daily-tip', methods=['POST', 'GET'])
def daily_tip():
    try:
        tips = [
            "💡 Use /compare to compare multiple salaries and find the best net pay!",
            "💡 Use /quiz to test your tax knowledge and become an expert!",
            "💡 VAT returns are due by 21st of each month - don't be late!",
            "💡 PAYE must be remitted by 14th monthly to avoid penalties!",
            "💡 WHT deducted can be credited against your CIT liability at year end!",
            "💡 Small companies with turnover < ₦25M are CIT exempt!",
            "💡 Keep all tax documents for at least 6 years for audit purposes!",
            "💡 Use /filepaye for guided PAYE filing assistance!",
            "💡 Use /language to switch to Pidgin, Yoruba, Hausa, or Igbo!"
        ]
        tip = random.choice(tips)
        
        if TEST_TELEGRAM_CHAT_ID and TELEGRAM_ENABLED:
            send_message("telegram", TEST_TELEGRAM_CHAT_ID, f"{tip}\n\nSend /help for more features!")
        if TEST_WHATSAPP_NUMBER and WHATSAPP_ENABLED:
            send_message("whatsapp", TEST_WHATSAPP_NUMBER, f"{tip}\n\nSend /help for more features!")
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8000))
    app.run(host='0.0.0.0', port=port)