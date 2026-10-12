-- Which controller the reading came from (omada | unifi), now that the survey reads both
-- (2026-10-12, the AC Lite's View Only admin). The AP name already tells a human; this makes
-- grouping by controller a one-liner in Grafana. Idempotent.
alter table wifi_survey add column if not exists controller text;
