---
name: Billing policy
slug: billing_policy
description: Apply the company billing & disputes policy. Use when a user asks how a charge, refund, dispute, chargeback, dunning, proration, or VIP handling works. Follow the policy exactly; never guess amounts or timelines.
stages: [synthesize]
required_tools: []
injection: on_demand
---
SKILL: Billing policy — operating procedure

Apply the matching rule below before answering. The policy is authoritative over intuition, and every figure stated to a customer must come from this policy or their actual invoice, never from memory. Anything not covered here is escalated to a manager rather than improvised.

## Charges and invoicing
Charges are captured at the start of each billing period, which runs from the first to the last day of the month. An invoice is issued on the second of the month for the period just elapsed. The statement descriptor reads ACME-SUBSCR plus the last four digits of the plan id, and a receipt email is sent within fifteen minutes of capture. Tax is calculated on the net charge after any proration and shown as a separate line.

## Failed charges and dunning
If a charge fails, the dunning sequence begins: a first retry twenty-four hours later, a second retry seventy-two hours after the first, and a third and final retry seven days after the second. If all three retries fail the subscription is suspended, not cancelled, and the customer keeps read-only access for a thirty-day grace window before the account is closed.

## Refunds
A refund requested within fourteen days of the charge is a full refund to the original payment method within five business days. Requested between fifteen and thirty days, it is prorated for the unused portion of the period. Requested after thirty days, it is declined except where required by law or approved by a manager. Refunds are never issued as account credit unless the customer explicitly asks for credit.

## Credits
When a customer asks for credit instead of cash, the credit is worth one hundred and ten percent of the cash refund as a goodwill gesture, and the credit never expires.

## Disputes
When a customer disputes a charge, acknowledge it immediately, freeze any dunning on that subscription, and open a dispute ticket. If the dispute is not resolved within forty-eight hours it is escalated to tier two support automatically, and a tier two analyst then has five business days to reach a decision.

## Chargebacks
A chargeback filed with the customer's bank is more serious than a dispute. On receipt of a chargeback notification the account is immediately flagged, all active subscriptions are paused, and the case is routed to the fraud-and-recovery team rather than ordinary support. Never issue a refund on an account with an open chargeback — it can cause a double refund the company cannot recover.

## Proration and plan changes
An upgrade is charged immediately for the difference between the old and new plan, prorated by the days remaining in the period. A downgrade does not refund the difference; it applies the lower price at the start of the next period.

## Annual plans
Annual plans are billed once up front with a two-month discount baked in. A mid-term cancellation refunds the unused full months only — never partial months, and never the discount.

## VIP customers
VIP customers are those on the specialization tier or with more than twenty-four months of tenure. Their disputes skip the forty-eight-hour clock and go straight to tier two, their refunds are approved up to the full amount without a manager, and their dunning grace window is sixty days instead of thirty.
