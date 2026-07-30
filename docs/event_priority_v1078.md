# V1078 Event Attribution / Impact Contract

## Arbitration order

An event is ranked by:

`event priority × freshness × subject relevance × measured magnitude`

The final explanation follows this order:

1. verified event owned by the analysed company;
2. verified customer / supplier event;
3. industry event;
4. global macro / policy / geopolitical context;
5. price and flow when no current event is verified.

Price, volume, VWAP and chip/flow evidence retain final veto.  A keyword is
evidence, not permission to overwrite formal T0/T1/High/Low or model weights.

## Priority database

| Tier | Meaning | Typical examples |
|---|---|---|
| P5 | Survival / systemic crisis | bankruptcy, default, full-scale war, Taiwan/Hormuz blockade |
| P4 | Structural repricing | GDS/GDR/new-share dilution, direct military attack, embargo/export ban |
| P3 | Material event | guidance cut, investigation, tariff, conflict escalation, 3–6% oil move |
| P2 | Normal catalyst | earnings result, order/product update, scheduled release, 1.5–3% oil move |
| P1 | Context | analyst/commentary, indirect industry narrative, unverified attribution |

Company-owned P4/P5 events cannot be displaced by a customer earnings headline.
An old high-priority event decays with freshness and cannot remain today's cause
forever.

## Oil magnitude

The scanner reads WTI/Brent percentage changes and records
`magnitude_pct`, `priority_tier`, `impact_score` and `magnitude_basis`.

| Absolute move | Tier | Base interpretation |
|---|---|---|
| below 1.5% | P1 | background |
| 1.5–3% | P2 | sector impact |
| 3–6% | P3 | cross-market impact |
| 6–8% | P4 | systemic pressure |
| 8% or more | P5 | extreme shock |

Ticker exposure is then applied: airlines receive stronger negative fuel-cost
impact, energy may receive positive direction but still carries market-risk
uncertainty, and semiconductors receive indirect inflation/yield/Beta pressure.

## War magnitude

| Language / fact | Tier |
|---|---|
| tension, drills, troop buildup, conflict escalation | P3 |
| airstrike, missile attack, direct military action, resumed war | P4 |
| formal/full-scale war, Taiwan or Hormuz blockade/closure | P5 |
| ceasefire / de-escalation | P3 relief event |

War news and an independently verified oil move form one bounded causal chain.
Their scores are not added twice; the chain is upgraded once and remains subject
to cross-market and ticker-price confirmation.

## Subject truth guard

For company-level earnings, financing and legal events, the event owner is the
named entity closest to the event phrase.  Example:

- `Quanta issues GDS; Microsoft earnings beat` → Quanta financing is the P4
  company event; Microsoft earnings is secondary customer evidence.
- `Quanta benefits from Microsoft earnings` → customer/industry evidence, not
  Quanta earnings.
