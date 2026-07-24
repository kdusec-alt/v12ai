# Architecture

The active ticker route and the market-wide route are intentionally separate:

- Ticker news: company and industry entity validation.
- Global Event Core: commodities, macro calendar, trade/policy and geopolitical evidence.
- Merge: deduplicated NewsItem contract consumed by existing V12 modules.

This prevents a market-wide event from being missed only because its headline does not name the active company.
