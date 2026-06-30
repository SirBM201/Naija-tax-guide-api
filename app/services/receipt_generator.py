# app/services/receipt_generator.py
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io

logger = logging.getLogger(__name__)


def generate_tax_receipt(filing: Dict[str, Any], calculation: Optional[Dict[str, Any]] = None) -> bytes:
    """
    Generate a PDF receipt for a tax filing
    
    Args:
        filing: Tax filing record
        calculation: Tax calculation details
    
    Returns:
        PDF file as bytes
    """
    buffer = io.BytesIO()
    
    # Create document
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=18,
        alignment=1,  # Center
        spaceAfter=20
    )
    
    header_style = ParagraphStyle(
        'Header',
        parent=styles['Heading2'],
        fontSize=14,
        spaceAfter=10
    )
    
    normal_style = ParagraphStyle(
        'Normal',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6
    )
    
    story = []
    
    # Header
    story.append(Paragraph("Naija Tax Guide - Tax Receipt", title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Filing details
    story.append(Paragraph("Filing Details", header_style))
    filing_data = [
        ["Reference:", filing.get("reference", "N/A")],
        ["Tax Type:", filing.get("tax_type", "N/A").upper()],
        ["Status:", filing.get("status", "N/A").upper()],
        ["Date:", datetime.fromisoformat(filing.get("submitted_at", datetime.now(timezone.utc).isoformat())).strftime("%d %B %Y, %H:%M")],
    ]
    
    filing_table = Table(filing_data, colWidths=[2*inch, 4*inch])
    filing_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(filing_table)
    story.append(Spacer(1, 0.2*inch))
    
    # Input details
    story.append(Paragraph("Filing Inputs", header_style))
    inputs = filing.get("inputs", {})
    input_data = []
    for key, value in inputs.items():
        formatted_key = key.replace("_", " ").title()
        # Format currency values
        if isinstance(value, (int, float)) and any(x in key for x in ["income", "profit", "supplies", "contribution"]):
            input_data.append([formatted_key + ":", f"₦{value:,.2f}"])
        else:
            input_data.append([formatted_key + ":", str(value)])
    
    if input_data:
        input_table = Table(input_data, colWidths=[2.5*inch, 3.5*inch])
        input_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(input_table)
    story.append(Spacer(1, 0.2*inch))
    
    # Calculation details
    if calculation:
        story.append(Paragraph("Tax Calculation", header_style))
        calc_data = []
        
        if filing.get("tax_type") == "paye":
            calc_data = [
                ["Annual Gross Income:", f"₦{calculation.get('annual_gross', 0):,.2f}"],
                ["CRA Deduction:", f"₦{calculation.get('cra_deduction', 0):,.2f}"],
                ["Pension Deduction:", f"₦{calculation.get('pension_deduction', 0):,.2f}"],
                ["NHF Deduction:", f"₦{calculation.get('nhf_deduction', 0):,.2f}"],
                ["Chargeable Income:", f"₦{calculation.get('chargeable_income', 0):,.2f}"],
                ["Annual Tax Payable:", f"₦{calculation.get('annual_tax_payable', 0):,.2f}"],
                ["Monthly Tax Payable:", f"₦{calculation.get('monthly_tax_payable', 0):,.2f}"],
                ["Effective Tax Rate:", f"{calculation.get('effective_rate', 0):.2f}%"],
            ]
        elif filing.get("tax_type") == "vat":
            calc_data = [
                ["Taxable Supplies:", f"₦{calculation.get('taxable_supplies', 0):,.2f}"],
                ["Output VAT (7.5%):", f"₦{calculation.get('output_vat', 0):,.2f}"],
                ["Input VAT:", f"₦{calculation.get('input_vat', 0):,.2f}"],
                ["VAT Payable:", f"₦{calculation.get('vat_payable', 0):,.2f}"],
            ]
        elif filing.get("tax_type") == "cit":
            calc_data = [
                ["Gross Profit:", f"₦{calculation.get('gross_profit', 0):,.2f}"],
                ["Allowable Expenses:", f"₦{calculation.get('allowable_expenses', 0):,.2f}"],
                ["Assessable Profit:", f"₦{calculation.get('assessable_profit', 0):,.2f}"],
                ["Applicable Rate:", f"{calculation.get('applicable_rate', 0)}%"],
                ["CIT Payable:", f"₦{calculation.get('cit_payable', 0):,.2f}"],
                ["Company Size:", calculation.get('company_size', 'N/A').title()],
            ]
        
        if calc_data:
            calc_table = Table(calc_data, colWidths=[2.5*inch, 3.5*inch])
            calc_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(calc_table)
    
    story.append(Spacer(1, 0.3*inch))
    
    # Footer
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        alignment=1,
        textColor=colors.grey
    )
    story.append(Paragraph("This is a computer-generated receipt. No signature is required.", footer_style))
    story.append(Paragraph("Guidance note: Naija Tax Guide provides general Nigerian tax information and does not replace a qualified tax professional for sensitive filing, audit, dispute, or penalty matters.", footer_style))
    story.append(Paragraph("Naija Tax Guide - Powered by BMS Creative Concept", footer_style))
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
