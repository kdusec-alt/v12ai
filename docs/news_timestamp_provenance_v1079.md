# V1079 News Timestamp Provenance Truth Guard

Google News RSS `pubDate` is treated as `aggregator_seen_at`, never as proof of
the publisher's original publication time.

## Contract

- `verified`: publisher `datePublished` is available and differs from the
  aggregator observation by no more than 24 hours. The row may enter evidence.
- `stale_reindexed`: publisher `datePublished` and aggregator observation differ
  by more than 24 hours. The row remains displayable as history, but score is
  zero and all model/event gates reject it.
- `unverified`: publisher URL or original publication time could not be
  verified. The UI labels the timestamp as an aggregator observation; score is
  zero and all model/event gates reject it.

`dateModified` is stored separately and never upgrades an article into a new
event without an original `datePublished`.

## Covered paths

- GoogleNewsTW company, daily and policy/geo searches
- GoogleNewsUS daily, industry and company searches
- Global Event Core Google RSS scans
- Causal news selection and event-driven reassessment

Permanent regression fixture:

`RSS 2026-07-28 + publisher 2026-04-22 => stale_reindexed, model impact 0`.
