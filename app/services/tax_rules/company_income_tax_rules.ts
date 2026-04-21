// app/services/tax_rules/company_income_tax_rules.ts
export function can_handle_cit_rule(question: string, topic: string, intent: string): boolean {
  return topic === 'company_income_tax' && intent === 'calculation';
}
export function resolve_cit_rule(question: string, intent: string): string {
  // parse question and calculate
  return "Company Income Tax is 20% of taxable profit.";
}
