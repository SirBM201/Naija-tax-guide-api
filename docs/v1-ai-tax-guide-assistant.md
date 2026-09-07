# V1 — AI Tax Guide Assistant

Status: **Required V1 guided-assistance capability, subject to plan/credit policy**

Naija Tax Guide must provide an in-product guided tax-assistance path so users are not left to interpret tax questions, calculators, deadlines and product workflows alone.

## V1 objective

Users should be able to ask what they are trying to do in ordinary language and receive clear Nigeria-tax-oriented guidance, navigation and next steps within the product's approved plan and credit rules.

## V1 minimum capabilities

- Guide users to the appropriate NTG question, calculator, deadline, quiz/help or account workflow.
- Explain calculator inputs/outputs and general tax concepts in accessible language.
- Use available NTG context where appropriate rather than behaving as an unrelated generic chatbot.
- Clearly distinguish database/non-AI guidance from AI-assisted responses where plan/credit policy requires it.
- Preserve the approved Free Forever and paid-plan/credit boundaries; adding the assistant must not silently convert free features into paid AI usage.
- Surface source/effective-date context where available and communicate uncertainty.
- Never fabricate filing/payment status, tax authority acceptance or rules.
- Distinguish educational guidance from professional tax/legal advice.

## Architecture principle

**User ↔ Guided Tax Assistant ↔ NTG knowledge/calculator/account engines ↔ result/next step**

## V1 AI cost guardrail

NTG must use the least expensive reliable answer path:

**Database/rules → cached/approved knowledge → low-cost AI → advanced AI**

- Free calculators, database answers, navigation/help and other deterministic free functionality must not trigger unnecessary paid AI calls.
- Preserve the approved Free Forever and paid credit/top-up rules.
- Prefer approved tax knowledge/rules before generative reasoning.
- Cache safe reusable explanations where freshness and privacy permit.
- Keep AI context focused and use economical models for simple guidance; reserve advanced models for genuinely complex paid AI tasks.
- Enforce plan/credit-aware quotas, rate limits and abuse controls before inference.
- Record model/task/token/credit/cost telemetry and support configurable cost ceilings.
- Gracefully return deterministic/database guidance when AI entitlement or budget is unavailable rather than breaking the user journey.
- Cost optimization must never weaken tax-source integrity or user safety.

This cost-routing layer is a **V1 requirement** and must be implemented without changing the approved free feature policy.

## V1 acceptance

NTG V1 should provide a clear guided path for users who do not already know which feature or tax workflow they need, while retaining the approved calculator/free-tier/credit model. V1 also requires AI cost routing, entitlement controls and measurable inference-cost telemetry.