# Changelog

All notable changes to this project are written here (Keep a Changelog).

## [Unreleased]

## [0.2.1] - 2026-11-02

### Fixed
- Event pushes to the central service send at most 20 events a request and wait up to 30 seconds, so a backlog no
  longer times out against the live service.

## [0.2.0] - 2026-11-02

### Added
- Central service (`auto-runner/central/`): a Flask app with its own accounts, project access, per-project API tokens,
  `event.v1` ingest and the delivery endpoints. Deployment is unverified.
- `HttpTransport` in `autodeliver.py`; the runner uses the central service when `central` is enabled in `.claude/auto.json`
  and `AUTO_RUNNER_CENTRAL_TOKEN` is set.
- Decision record `docs/decisions/0001-central-service.md`.
- The runner sends its event ledger to the central service when `central` is on, and `raised` blocker events carry the
  question and options; the service has blocker pages where owners, managers and admins answer them.
