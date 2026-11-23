# Cloudflare Telegram Manager — Current Release

## v1.0.0 · 24 November 2026

- Set the version number for this fork to 1.0.0.
- Add guided Cloudflare token setup, live account details, and account selection before domain management.
- Update the installer to fetch updates from this fork and simplify the main and account menus.

## v2.8.1 · 19 November 2026

**This is the current supported release.** The repository maintains one active release entry; earlier development milestones are available only in Git history.

### Language picker layout
- Show English, Persian and Russian in a single button row, keeping Persian in the center and Main Menu on its own row.
- Retain per-user language persistence and translated screens.

### Multilingual interface
- Added `/language` and a main-menu language selector with **Persian, English and Russian**.
- Localized Telegram menus, DNS workflows, connection help, errors, confirmations and command descriptions.
- Language preferences are stored per Telegram administrator in SQLite and survive restarts.
- Added language-specific command-menu descriptions and offline localization regressions.

### Features
- Self-hosted Telegram management of multiple Cloudflare accounts and zones.
- Create, edit, search, delete, import and export DNS records (21 supported record types).
- Zone inspection, selected SSL/network settings, cache purge and DNSSEC management.
- Persian inline-button interface and a step-by-step Cloudflare account onboarding guide.
- `/start`, `/menu`, `/accounts`, `/about`, `/help` and `/cancel` commands, automatically synchronized with Telegram at bot startup.
- Developer and project links in About: [Net-spoof](https://github.com/Net-spoof), [source repository](https://github.com/Net-spoof/cloudflare-telegram-manager), and [Telegram channel](https://t.me/NET_SPOOF).

### Security & reliability
- Administrator allowlist and private-chat-only API credential entry.
- Encrypted Cloudflare token storage with Fernet; scoped-token guidance.
- Confirmation prompts for destructive actions, Cloudflare API response diagnostics and callback-data validation.
- Docker Compose installer with backups and preservation of the existing `.env`, encryption key and SQLite data.
- Offline regression suite and GitHub Actions CI.

### Install
Refer to the [Persian documentation](README_FA.md) or [English setup guide](README_EN.md).

> This is an independent, unofficial project. Do not publish secrets, credentials, `.env`, `data/` or `backups/`. Validate on a non-production zone before making potentially disruptive DNS changes.
