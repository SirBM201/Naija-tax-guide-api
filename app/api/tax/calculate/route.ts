import { NextRequest, NextResponse } from 'next/server';

// Simple calculation functions (fallback if rule engine not yet integrated)
function calculatePAYE(grossMonthly: number, pension: number = 0, nhf: number = 0): number {
  const taxableIncome = grossMonthly - pension - nhf;
  // Simplified PAYE bands (2024)
  let tax = 0;
  if (taxableIncome <= 300000) tax = 0;
  else if (taxableIncome <= 600000) tax = (taxableIncome - 300000) * 0.07;
  else if (taxableIncome <= 1100000) tax = 21000 + (taxableIncome - 600000) * 0.11;
  else if (taxableIncome <= 1600000) tax = 76000 + (taxableIncome - 1100000) * 0.15;
  else if (taxableIncome <= 3200000) tax = 151000 + (taxableIncome - 1600000) * 0.19;
  else tax = 455000 + (taxableIncome - 3200000) * 0.24;
  return Math.max(0, tax);
}

function calculateVAT(taxableSupplies: number, inputVAT: number = 0): number {
  const outputVAT = taxableSupplies * 0.075;
  return Math.max(0, outputVAT - inputVAT);
}

function calculateCIT(grossProfit: number, allowableExpenses: number): number {
  const taxableProfit = Math.max(0, grossProfit - allowableExpenses);
  // CIT rate is 20% for small companies, 30% for large; using 20% for MVP
  return taxableProfit * 0.2;
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { type, inputs } = body;

    let result: any = {};

    switch (type) {
      case 'paye':
        const payeTax = calculatePAYE(
          inputs.monthly_gross_income || 0,
          inputs.pension_contribution || 0,
          inputs.nhf || 0
        );
        result = {
          ok: true,
          answer: `Your estimated PAYE tax per month is ₦${payeTax.toLocaleString()}. Annual: ₦${(payeTax * 12).toLocaleString()}.`,
          raw: { tax_due: payeTax, ...inputs }
        };
        break;

      case 'vat':
        const vatDue = calculateVAT(inputs.taxable_supplies || 0, inputs.input_vat || 0);
        result = {
          ok: true,
          answer: `VAT payable is ₦${vatDue.toLocaleString()}.`,
          raw: { vat_payable: vatDue, ...inputs }
        };
        break;

      case 'cit':
        const citDue = calculateCIT(inputs.gross_profit || 0, inputs.allowable_expenses || 0);
        result = {
          ok: true,
          answer: `Company Income Tax (CIT) payable is ₦${citDue.toLocaleString()}.`,
          raw: { tax_due: citDue, ...inputs }
        };
        break;

      default:
        return NextResponse.json({ ok: false, error: 'Invalid tax type' }, { status: 400 });
    }

    return NextResponse.json(result);
  } catch (error) {
    console.error(error);
    return NextResponse.json({ ok: false, error: 'Server error' }, { status: 500 });
  }
}
