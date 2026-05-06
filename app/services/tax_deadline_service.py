# app/services/tax_deadline_service.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

TAX_DEADLINES = {
    "paye": {
        "name": "PAYE (Pay As You Earn)",
        "description": "Monthly salary tax remittance",
        "deadline_day": 10,
        "frequency": "monthly",
        "reminder_days": [14, 7, 3, 1]
    },
    "vat": {
        "name": "VAT (Value Added Tax)",
        "description": "Monthly sales tax filing",
        "deadline_day": 21,
        "frequency": "monthly",
        "reminder_days": [14, 7, 3, 1]
    },
    "cit_annual": {
        "name": "CIT (Company Income Tax) - Annual",
        "description": "Annual company tax return",
        "deadline_month": 6,
        "deadline_day": 30,
        "frequency": "annual",
        "reminder_days": [30, 14, 7, 3]
    },
    "wht": {
        "name": "WHT (Withholding Tax)",
        "description": "Tax deducted at source",
        "deadline_day": 21,
        "frequency": "monthly",
        "reminder_days": [14, 7, 3, 1]
    },
    "annual_returns": {
        "name": "Annual Returns (CAC)",
        "description": "Company annual return filing",
        "deadline_day": 30,
        "deadline_month": 6,
        "frequency": "annual",
        "reminder_days": [30, 14, 7]
    }
}


def get_upcoming_deadlines(days_ahead: int = 30) -> List[Dict[str, Any]]:
    """Get tax deadlines in the next X days"""
    today = datetime.now().date()
    end_date = today + timedelta(days=days_ahead)
    deadlines = []
    
    for tax_type, config in TAX_DEADLINES.items():
        if config["frequency"] == "monthly":
            deadlines.extend(_get_monthly_deadlines(tax_type, config, today, end_date))
        elif config["frequency"] == "annual":
            deadlines.extend(_get_annual_deadlines(tax_type, config, today, end_date))
    
    deadlines.sort(key=lambda x: x["deadline_date"])
    return deadlines


def _get_monthly_deadlines(tax_type: str, config: dict, start_date: date, end_date: date) -> List[Dict[str, Any]]:
    deadlines = []
    current = start_date.replace(day=1)
    
    while current <= end_date:
        try:
            deadline_date = current.replace(day=config["deadline_day"])
        except ValueError:
            deadline_date = current.replace(day=28)
        
        if deadline_date >= start_date and deadline_date <= end_date:
            deadlines.append({
                "tax_type": tax_type,
                "tax_name": config["name"],
                "description": config["description"],
                "deadline_date": deadline_date.isoformat(),
                "reminder_days": config["reminder_days"]
            })
        
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    
    return deadlines


def _get_annual_deadlines(tax_type: str, config: dict, start_date: date, end_date: date) -> List[Dict[str, Any]]:
    deadlines = []
    year = start_date.year
    
    while year <= end_date.year:
        try:
            deadline_date = date(year, config["deadline_month"], config["deadline_day"])
        except ValueError:
            deadline_date = date(year, config["deadline_month"], 28)
        
        if deadline_date >= start_date and deadline_date <= end_date:
            deadlines.append({
                "tax_type": tax_type,
                "tax_name": config["name"],
                "description": config["description"],
                "deadline_date": deadline_date.isoformat(),
                "reminder_days": config["reminder_days"]
            })
        year += 1
    
    return deadlines


def format_deadline_message(deadline: Dict[str, Any]) -> Optional[str]:
    """Format a deadline for WhatsApp message"""
    deadline_date = datetime.fromisoformat(deadline["deadline_date"]).date()
    today = datetime.now().date()
    days_until = (deadline_date - today).days
    
    if days_until < 0:
        return None
    
    if days_until == 0:
        urgency = "⚠️ *TODAY!* ⚠️"
    elif days_until <= 3:
        urgency = f"🔴 *URGENT: {days_until} days left* 🔴"
    elif days_until <= 7:
        urgency = f"🟠 *{days_until} days left* 🟠"
    else:
        urgency = f"🟡 *{days_until} days remaining* 🟡"
    
    return (
        f"📅 *{deadline['tax_name']}*\n"
        f"{urgency}\n\n"
        f"📋 {deadline['description']}\n"
        f"🗓️ Deadline: {deadline_date.strftime('%d %B %Y')}\n\n"
        f"💡 Reply:\n"
        f"• 'FILE {deadline['tax_type'].upper()}' - Start filing now\n"
        f"• 'REMIND ME' - Get another reminder\n"
        f"• 'UNSUBSCRIBE' - Stop reminders"
    )


def get_deadlines_summary(days_ahead: int = 30) -> str:
    """Get a summary of upcoming deadlines for menu display"""
    deadlines = get_upcoming_deadlines(days_ahead)
    
    if not deadlines:
        return "📅 No upcoming tax deadlines in the next 30 days."
    
    summary = "*📅 Upcoming Tax Deadlines*\n\n"
    
    for d in deadlines:
        deadline_date = datetime.fromisoformat(d["deadline_date"]).date()
        days_until = (deadline_date - datetime.now().date()).days
        
        if days_until == 0:
            urgency = "⚠️ TODAY!"
        elif days_until <= 3:
            urgency = f"🔴 {days_until}d"
        elif days_until <= 7:
            urgency = f"🟠 {days_until}d"
        else:
            urgency = f"{days_until}d"
        
        summary += f"• *{d['tax_name']}* - {deadline_date.strftime('%d %b')} ({urgency})\n"
    
    summary += f"\n📌 *Next {days_ahead} days*\n"
    summary += f"Total deadlines: {len(deadlines)}\n\n"
    summary += f"💡 Reply 'REMIND ME' to get alerts for these deadlines!"
    
    return summary
