-- Naija Tax Guide quiz seed: initial web/channel quiz bank
-- Safe to run more than once. It creates/patches quiz tables and upserts by question_code.
-- 2026-06-14: distractors revised to be plausible tax/business options instead of obvious joke answers.

create extension if not exists pgcrypto;

create table if not exists public.tax_quiz_questions (
  id uuid primary key default gen_random_uuid(),
  question_code text,
  category text,
  difficulty text default 'basic',
  question text not null,
  short_explanation text,
  premium_explanation text,
  source_reference text,
  is_active boolean default true,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists public.tax_quiz_options (
  id uuid primary key default gen_random_uuid(),
  question_id uuid references public.tax_quiz_questions(id) on delete cascade,
  option_code text,
  option_text text not null,
  is_correct boolean default false,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists public.tax_quiz_attempts (
  id uuid primary key default gen_random_uuid(),
  account_id text,
  question_id uuid,
  question_code text,
  category text,
  status text,
  channel text,
  selected_answer text,
  selected_option_id text,
  correct_option_id text,
  is_correct boolean,
  answered_at timestamptz,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

alter table public.tax_quiz_questions add column if not exists question_code text;
alter table public.tax_quiz_questions add column if not exists category text;
alter table public.tax_quiz_questions add column if not exists difficulty text;
alter table public.tax_quiz_questions add column if not exists short_explanation text;
alter table public.tax_quiz_questions add column if not exists premium_explanation text;
alter table public.tax_quiz_questions add column if not exists source_reference text;
alter table public.tax_quiz_questions add column if not exists is_active boolean default true;
alter table public.tax_quiz_questions add column if not exists updated_at timestamptz default now();
alter table public.tax_quiz_options add column if not exists option_code text;
alter table public.tax_quiz_options add column if not exists is_correct boolean default false;
alter table public.tax_quiz_options add column if not exists updated_at timestamptz default now();
alter table public.tax_quiz_attempts add column if not exists channel text;
alter table public.tax_quiz_attempts add column if not exists selected_answer text;
alter table public.tax_quiz_attempts add column if not exists selected_option_id text;
alter table public.tax_quiz_attempts add column if not exists correct_option_id text;
alter table public.tax_quiz_attempts add column if not exists is_correct boolean;
alter table public.tax_quiz_attempts add column if not exists answered_at timestamptz;
alter table public.tax_quiz_attempts add column if not exists metadata jsonb default '{}'::jsonb;
alter table public.tax_quiz_attempts add column if not exists updated_at timestamptz default now();

create unique index if not exists uq_tax_quiz_questions_code on public.tax_quiz_questions(question_code);
create unique index if not exists uq_tax_quiz_options_question_code on public.tax_quiz_options(question_id, option_code);
create index if not exists idx_tax_quiz_questions_active_category on public.tax_quiz_questions(is_active, category);
create index if not exists idx_tax_quiz_attempts_account_created on public.tax_quiz_attempts(account_id, created_at);

with q(question_code, category, difficulty, question, short_explanation, premium_explanation) as (
  values
  ('NTG-PAYE-001','PAYE','basic','For PAYE purposes in Nigeria, who is normally responsible for deducting tax from employee salaries?','PAYE is normally deducted by the employer from payroll and remitted to the relevant State Internal Revenue Service.','PAYE means Pay-As-You-Earn. The employer deducts the tax from employment income and remits it to the relevant State IRS.'),
  ('NTG-PAYE-002','PAYE','medium','Which record best supports PAYE remittance during a tax review?','PAYE compliance is supported by payroll schedules, deduction workings, and payment evidence.','A proper PAYE file should include payroll schedules, employee pay details, relief/deduction workings, and payment receipts.'),
  ('NTG-VAT-001','VAT','basic','VAT in Nigeria is best described as what?','VAT is a consumption tax charged on many taxable supplies of goods and services.','VAT is charged on taxable supplies. Businesses may charge output VAT and claim allowable input VAT before remitting net VAT.'),
  ('NTG-VAT-002','VAT','medium','What is output VAT?','Output VAT is VAT a business charges on taxable supplies it makes.','Output VAT is charged to customers on taxable sales and is reported in VAT returns.'),
  ('NTG-CIT-001','Company Tax','basic','Company Income Tax is generally charged on what?','CIT is generally charged on taxable profit after relevant adjustments.','Company Income Tax generally applies to taxable company profit, not simply all cash received.'),
  ('NTG-CIT-002','Company Tax','medium','Why is turnover important for Nigerian company tax classification?','Company turnover may affect tax classification and applicable company income tax treatment.','Turnover can affect whether a company is treated as small, medium, or large for company tax purposes.'),
  ('NTG-WHT-001','WHT','basic','Withholding Tax is best described as what?','WHT is deducted at source from certain payments and remitted to the relevant tax authority.','Withholding Tax is a deduction at source that may become a tax credit for the recipient, depending on the transaction.'),
  ('NTG-WHT-002','WHT','medium','What is unutilized withholding tax credit?','Unutilized WHT credit is withholding tax credit available but not yet applied against tax liability.','A taxpayer may have WHT credit that remains unused where it has not yet been applied to offset final tax liability.'),
  ('NTG-REC-001','Records','basic','Why should a Nigerian SME keep proper tax records?','Proper records help support tax positions, filings, payments, and audit responses.','Good records reduce disputes and help the business prove income, expenses, deductions, and payments.'),
  ('NTG-REC-002','Records','medium','If tax authority records differ from business records, what should the business do first?','The first practical step is to reconcile records and identify the source of the difference.','A business should compare its filings, receipts, bank records, and authority portal records before responding.'),
  ('NTG-DEAD-001','Deadlines','basic','Why are tax filing deadlines important?','Deadlines help taxpayers file and pay on time and avoid avoidable penalties.','Missing deadlines can create penalties, interest, and avoidable compliance pressure.'),
  ('NTG-PEN-001','Penalties','medium','Late filing penalty generally arises when what happens?','Late filing penalties generally relate to filing required returns after the due date.','A late filing penalty is connected to submitting a required return after the filing deadline.'),
  ('NTG-PEN-002','Penalties','medium','Late payment penalty generally relates to what?','Late payment penalty is connected with paying tax after the required time.','Late payment is different from late filing because it focuses on when tax due is paid.'),
  ('NTG-AUD-001','Audit','medium','What is a tax audit mainly intended to do?','A tax audit reviews records, filings, and tax positions for accuracy and compliance.','Tax audits test whether filings and supporting records agree with the taxpayer’s obligations.'),
  ('NTG-AUD-002','Audit','medium','What should a business do after receiving a tax audit invitation?','A business should prepare records, understand the request, and respond properly.','A tax audit invitation should be handled by organizing records and responding within the stated scope and timeline.'),
  ('NTG-ASS-001','Assessment','medium','What is a tax assessment notice?','A tax assessment notice states the tax authority’s position on tax payable.','An assessment notice should be reviewed against the taxpayer’s records and filings.'),
  ('NTG-ASS-002','Assessment','medium','What should a taxpayer do if an assessment appears wrong?','A taxpayer should review the basis and respond through the appropriate process.','Where an assessment appears wrong, the taxpayer should check records and use the proper clarification or objection process.'),
  ('NTG-SME-001','SME Basics','basic','Which tax habit best reduces risk for small businesses?','Good records and timely compliance reduce avoidable tax risk for SMEs.','Filing on time, keeping records, and reconciling payments are practical SME risk controls.'),
  ('NTG-GEN-001','General','basic','TIN is mainly used for what purpose?','TIN is used to identify taxpayers in tax administration and compliance processes.','A Tax Identification Number helps identify taxpayers when filing, paying, and communicating with tax authorities.'),
  ('NTG-GEN-002','General','medium','Why should a business confirm the relevant tax authority?','Different taxes can involve different authorities, so confirming the right authority prevents wrong filing or payment.','Some obligations may involve FIRS while others may involve State IRS or another authority; the taxpayer should confirm before acting.')
)
insert into public.tax_quiz_questions (question_code, category, difficulty, question, short_explanation, premium_explanation, source_reference, is_active, updated_at)
select question_code, category, difficulty, question, short_explanation, premium_explanation, 'Naija Tax Guide reviewed quiz bank', true, now()
from q
on conflict (question_code) do update set
  category = excluded.category,
  difficulty = excluded.difficulty,
  question = excluded.question,
  short_explanation = excluded.short_explanation,
  premium_explanation = excluded.premium_explanation,
  source_reference = excluded.source_reference,
  is_active = true,
  updated_at = now();

with options(question_code, option_code, option_text, is_correct) as (
  values
  ('NTG-PAYE-001','A','The employer operating payroll',true),('NTG-PAYE-001','B','The employee when filing an annual personal return',false),('NTG-PAYE-001','C','The customer who pays the sales invoice',false),('NTG-PAYE-001','D','The bank processing the salary transfer',false),
  ('NTG-PAYE-002','A','Payroll schedules, payslips, deduction workings, and payment receipts',true),('NTG-PAYE-002','B','Only the CAC certificate and company profile',false),('NTG-PAYE-002','C','Sales invoices and VAT output schedules only',false),('NTG-PAYE-002','D','Bank statements without payroll breakdown',false),
  ('NTG-VAT-001','A','A consumption tax on taxable supplies of goods and services',true),('NTG-VAT-001','B','A tax on company taxable profit after adjustments',false),('NTG-VAT-001','C','A payroll tax deducted from employment income',false),('NTG-VAT-001','D','A deduction at source from qualifying payments',false),
  ('NTG-VAT-002','A','VAT charged by a business on its taxable sales',true),('NTG-VAT-002','B','VAT a business paid on eligible purchases',false),('NTG-VAT-002','C','Company income tax computed on taxable profit',false),('NTG-VAT-002','D','Withholding tax deducted from supplier payments',false),
  ('NTG-CIT-001','A','A company’s taxable profit after relevant adjustments',true),('NTG-CIT-001','B','Total revenue before deducting allowable costs',false),('NTG-CIT-001','C','VAT collected from customers during the period',false),('NTG-CIT-001','D','Employee salaries before PAYE deductions',false),
  ('NTG-CIT-002','A','It can affect whether a company is treated as small, medium, or large',true),('NTG-CIT-002','B','It automatically determines every available tax deduction',false),('NTG-CIT-002','C','It replaces the need to prepare accounting records',false),('NTG-CIT-002','D','It makes all VAT obligations disappear',false),
  ('NTG-WHT-001','A','A deduction at source from certain qualifying payments',true),('NTG-WHT-001','B','VAT charged to a customer on taxable sales',false),('NTG-WHT-001','C','Company income tax paid after annual assessment',false),('NTG-WHT-001','D','PAYE withheld from employee salary only',false),
  ('NTG-WHT-002','A','WHT credit not yet used to offset final tax liability',true),('NTG-WHT-002','B','Output VAT not yet included in a VAT return',false),('NTG-WHT-002','C','PAYE deducted from staff but not remitted',false),('NTG-WHT-002','D','A late filing penalty waiting to be paid',false),
  ('NTG-REC-001','A','To support filings, payments, deductions, and audit responses',true),('NTG-REC-001','B','To make tax registration unnecessary',false),('NTG-REC-001','C','To replace formal invoices with verbal explanations',false),('NTG-REC-001','D','To remove all future tax authority reviews',false),
  ('NTG-REC-002','A','Review and reconcile both records before responding',true),('NTG-REC-002','B','Submit a new return without checking the difference',false),('NTG-REC-002','C','Assume every authority figure is automatically final',false),('NTG-REC-002','D','Ignore the discrepancy until the next financial year',false),
  ('NTG-DEAD-001','A','Missing deadlines can create penalties and compliance risk',true),('NTG-DEAD-001','B','Deadlines only matter after an audit invitation',false),('NTG-DEAD-001','C','Deadlines are optional if records are complete',false),('NTG-DEAD-001','D','Deadlines apply only to companies with employees',false),
  ('NTG-PEN-001','A','A required return is submitted after the due date',true),('NTG-PEN-001','B','A tax return is filed before the statutory deadline',false),('NTG-PEN-001','C','A taxpayer attaches supporting schedules to a return',false),('NTG-PEN-001','D','A business keeps payroll evidence for employees',false),
  ('NTG-PEN-002','A','Failure to pay tax due by the required payment date',true),('NTG-PEN-002','B','Filing a nil return before the deadline',false),('NTG-PEN-002','C','Keeping WHT credit notes for offset claims',false),('NTG-PEN-002','D','Charging VAT correctly on a taxable invoice',false),
  ('NTG-AUD-001','A','Review whether tax records and returns are accurate',true),('NTG-AUD-001','B','Replace statutory filing with an informal discussion',false),('NTG-AUD-001','C','Approve all expenses without checking evidence',false),('NTG-AUD-001','D','Automatically cancel every previous assessment',false),
  ('NTG-AUD-002','A','Organize records and respond professionally within the stated timeline',true),('NTG-AUD-002','B','Wait until enforcement before reviewing documents',false),('NTG-AUD-002','C','File unrelated new returns without reading the request',false),('NTG-AUD-002','D','Assume the invitation has no compliance consequence',false),
  ('NTG-ASS-001','A','A notice showing the tax authority’s determination of tax payable',true),('NTG-ASS-001','B','A taxpayer’s internal estimate before filing returns',false),('NTG-ASS-001','C','A payment receipt issued after remittance only',false),('NTG-ASS-001','D','A payroll schedule prepared by an employer',false),
  ('NTG-ASS-002','A','Review the basis and respond through the proper process',true),('NTG-ASS-002','B','Ignore it until it disappears from the portal',false),('NTG-ASS-002','C','Pay immediately without checking records or basis',false),('NTG-ASS-002','D','Replace all accounts with a fresh spreadsheet',false),
  ('NTG-SME-001','A','Keeping records and filing returns on time',true),('NTG-SME-001','B','Waiting for a tax notice before keeping records',false),('NTG-SME-001','C','Mixing business and personal records permanently',false),('NTG-SME-001','D','Relying only on memory for income and expenses',false),
  ('NTG-GEN-001','A','Identifying a taxpayer in tax administration',true),('NTG-GEN-001','B','Replacing a company’s CAC registration number',false),('NTG-GEN-001','C','Calculating PAYE bands automatically',false),('NTG-GEN-001','D','Approving import duty refunds automatically',false),
  ('NTG-GEN-002','A','Different taxes may be handled by FIRS, State IRS, or other authorities',true),('NTG-GEN-002','B','All taxes are always handled by one authority only',false),('NTG-GEN-002','C','The relevant authority does not affect filing or payment',false),('NTG-GEN-002','D','Only foreign companies need to confirm tax authorities',false)
)
insert into public.tax_quiz_options (question_id, option_code, option_text, is_correct, updated_at)
select q.id, o.option_code, o.option_text, o.is_correct, now()
from options o
join public.tax_quiz_questions q on q.question_code = o.question_code
on conflict (question_id, option_code) do update set
  option_text = excluded.option_text,
  is_correct = excluded.is_correct,
  updated_at = now();
