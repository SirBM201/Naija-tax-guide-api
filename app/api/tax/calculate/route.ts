// app/api/tax/calculate/route.ts
import { NextRequest, NextResponse } from 'next/server';
import { resolve_paye_rule, can_handle_paye_rule } from '@/app/services/tax_rules/paye_rules';
import { resolve_vat_rule, can_handle_vat_rule } from '@/app/services/tax_rules/vat_rules';
import { resolve_pit_rule, can_handle_pit_rule } from '@/app/services/tax_rules/personal_income_tax_rules';
import { resolve_cit_rule, can_handle_cit_rule } from '@/app/services/tax_rules/company_income_tax_rules'; // you may need to create/export these

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { type, inputs } = body; // type: 'paye', 'vat', 'cit'

    let result: any = {};

    switch (type) {
      case 'paye':
        // inputs: monthly_gross_income, pension_contribution, nhf_contribution, etc.
        const payeQuestion = `Calculate PAYE for monthly gross income of ${inputs.monthly_gross_income} with pension contribution ${inputs.pension_contribution || 0} and NHF ${inputs.nhf || 0}`;
        if (can_handle_paye_rule(payeQuestion, 'paye', 'calculation')) {
          const answer = resolve_paye_rule(payeQuestion, 'calculation');
          result = { ok: true, answer, raw: { tax_due: extractNumber(answer), ...inputs } };
        } else {
          result = { ok: false, error: 'PAYE calculation rule not available' };
        }
        break;

      case 'vat':
        // inputs: taxable_supplies, exempt_supplies, input_vat
        const vatQuestion = `Calculate VAT for taxable supplies ${inputs.taxable_supplies} with input VAT ${inputs.input_vat || 0}`;
        if (can_handle_vat_rule(vatQuestion, 'vat', 'calculation')) {
          const answer = resolve_vat_rule(vatQuestion, 'calculation');
          result = { ok: true, answer, raw: { vat_payable: (inputs.taxable_supplies * 0.075) - (inputs.input_vat || 0), ...inputs } };
        } else {
          result = { ok: false, error: 'VAT calculation rule not available' };
        }
        break;

      case 'cit':
        // inputs: gross_profit, allowable_expenses, tax_reliefs
        const citQuestion = `Calculate CIT for gross profit ${inputs.gross_profit} with expenses ${inputs.allowable_expenses}`;
        if (can_handle_cit_rule(citQuestion, 'company_income_tax', 'calculation')) {
          const answer = resolve_cit_rule(citQuestion, 'calculation');
          result = { ok: true, answer, raw: { tax_due: (inputs.gross_profit - inputs.allowable_expenses) * 0.2, ...inputs } };
        } else {
          result = { ok: false, error: 'CIT calculation rule not available' };
        }
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

function extractNumber(str: string): number {
  const match = str.match(/[\d,]+(?:\.\d+)?/);
  return match ? parseFloat(match[0].replace(/,/g, '')) : 0;
}
