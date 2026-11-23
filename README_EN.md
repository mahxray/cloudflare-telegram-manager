<p align="center"><img src="assets/banner.svg" alt="Cloudflare Telegram Manager" width="100%"></p>

# Cloudflare Telegram Manager — Installation & Operations

**Version 2.8.1** · Self-hosted · Docker Compose · Administrator-only

[🇮🇷 راهنمای فارسی](README_FA.md) · [🏠 Project README](README.md) · [Changelog](CHANGELOG.md)

> **Unofficial project.** This bot is not endorsed by or affiliated with Cloudflare or Telegram. Use scoped API tokens and only manage accounts and domains you own or are authorized to administer.

## Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Creating your Telegram bot](#creating-your-telegram-bot)
4. [Installation on Ubuntu](#installation-on-ubuntu)
5. [Connecting a Cloudflare account](#connecting-a-cloudflare-account)
6. [Bot commands and service operations](#bot-commands-and-service-operations)
7. [Upgrading without losing data](#upgrading-without-losing-data)
8. [Troubleshooting](#troubleshooting)
9. [Security best practices](#security-best-practices)
10. [Offline tests](#offline-tests)

## Features

- Manage multiple Cloudflare accounts, activate an account, test connectivity, rotate tokens, and disconnect an account.
- Browse zones; search, create, edit, delete, import, and export DNS records; browse long lists with pagination.
- Manage selected SSL/TLS, HTTP/network settings, caching, and DNSSEC depending on your token permissions.
- Encrypted Cloudflare API token storage (Fernet), numeric Telegram admin allowlist, and private-chat-only onboarding.
- Confirmation prompts for destructive operations, an in-bot connection guide, and a Persian user interface.
- Docker Compose deployment with persistent SQLite storage and a backup-aware upgrade script.

## Requirements

- A Linux VPS with root or sudo privileges; **Ubuntu is recommended** for the included installer.
- Outbound HTTPS access to GitHub, Docker image registries, Telegram (<code>api.telegram.org</code>), and Cloudflare (<code>api.cloudflare.com</code>).
- Git, Python 3, and a working Docker Engine/Compose installation (the installer attempts Docker installation if needed).
- A bot token issued by [@BotFather](https://t.me/BotFather).
- Your **numeric** Telegram user ID (not your @username).
- A Cloudflare account with access to the zones you want to manage.

The Telegram bot uses **long polling**, so no domain, public webhook URL, inbound port forwarding, or TLS certificate is required for normal operation. Outbound HTTPS is required.

## Creating your Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram and send <code>/newbot</code>.
2. Pick a display name and a unique username ending in <code>bot</code>.
3. Keep the **Telegram Bot Token** private. Do not paste it into GitHub issues, screenshots, or chat groups.
4. Identify your numeric **Telegram User ID** using a trusted method. Optionally configure multiple administrators by separating IDs with commas (e.g. <code>123456789,987654321</code>).
5. You will enter both values into the installer. The bot responds only to configured administrator IDs.

## Installation on Ubuntu

Connect to your VPS via SSH, then run:

~~~bash
sudo apt-get update
sudo apt-get install -y git python3 ca-certificates

sudo git clone https://github.com/mahxray/cloudflare-telegram-manager.git /opt/cloudflare-telegram-manager
cd /opt/cloudflare-telegram-manager
sudo bash install.sh
~~~

### First-run prompts

The installer will request:

| Input | Description |
|---|---|
| <code>Telegram Bot Token</code> | Token from BotFather; entered without terminal echo |
| <code>Telegram Admin numeric ID(s)</code> | One or more numeric IDs, comma-separated |

On first run the script creates <code>.env</code> with restrictive permissions, generates a Fernet encryption key, prepares persistent <code>data/</code> storage, builds the Docker image, and starts the service.

**Verify the deployment:**

~~~bash
cd /opt/cloudflare-telegram-manager
sudo docker compose ps
sudo bash diagnose.sh
~~~

You should see the <code>cloudflare_telegram_manager</code> container running. The diagnostic reports configuration presence, SQLite health, encrypted-token readability, Telegram API reachability, and categorized errors without printing token values. Use <code>docker-compose</code> instead of <code>docker compose</code> if your host only has the legacy Compose command.

Open a **private chat** with your new Telegram bot and send <code>/start</code>.

### Environment variables

The installer manages <code>.env</code>. A non-secret template is included at [<code>.env.example</code>](.env.example):

~~~dotenv
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_ADMIN_IDS=123456789
ENCRYPTION_KEY=YOUR_EXISTING_OR_GENERATED_FERNET_KEY
DATA_DIR=/app/data
~~~

**Never commit your actual <code>.env</code>**, rotate an encryption key while encrypted tokens are still stored, or put secret values in public command-line examples.

## Connecting a Cloudflare account

### Step 1 — Generate a scoped API Token

1. Sign in to [Cloudflare](https://dash.cloudflare.com/).
2. Go to **My Profile → API Tokens** ([direct link](https://dash.cloudflare.com/profile/api-tokens)).
3. Select **Create Token**. Use **Edit zone DNS** as a starting template, or choose a **Custom Token** for tighter control.
4. For zone discovery, grant the appropriate **Zone → Zone → Read** permission. To modify DNS records, grant **Zone → DNS → Edit**.
5. Features such as zone settings, cache purging, and DNSSEC may need additional, feature-specific permissions. Grant only those you intend to use.
6. Restrict **Zone Resources** to the specific zones you administer whenever possible.
7. Create and copy the **raw API Token**. Do **not** use a Global API Key.

Official references: [Create API tokens](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/) · [API token permissions](https://developers.cloudflare.com/fundamentals/api/reference/permissions/).

### Step 2 — Find your Cloudflare Account ID

Open the Cloudflare dashboard, choose the account containing your zones, and copy its **Account ID**. This is **not** the Zone ID. The bot validates a 32-character hexadecimal Account ID.

### Step 3 — Add it inside Telegram

1. Open the bot's private chat and run <code>/start</code>.
2. Select **👤 Cloudflare Accounts → ➕ Add new account** (button labels appear in Persian).
3. Enter a friendly account label, then the Account ID, then the raw API Token.
4. **Do not** prepend <code>Bearer</code>, quotes, or extra text: the bot constructs the HTTP Authorization header itself.
5. Use **🧪 Test connection** on the account detail screen, then open the zones list.

A <code>403</code> response generally indicates missing or incorrectly scoped permissions, not necessarily an invalid token. A DNS-only token might not support other zone-management features.

## Language preferences

The interface supports **Persian 🇮🇷, English 🇬🇧 and Russian 🇷🇺**. Run `/language` or select the language button in the bot's home menu. Language is stored separately for each authorized administrator in SQLite; it persists across container restarts and does not affect Cloudflare credentials or DNS data. Interactive keyboards, menus, prompts, help and confirmation messages follow the selected language. The Telegram slash-command descriptions are also synchronized.

## Bot commands and service operations

### Bot commands

| Command | Purpose |
|---|---|
| <code>/start</code> | Display the main menu |
| <code>/menu</code> | Return to the main menu |
| <code>/language</code> | Choose Persian, English or Russian with buttons |
| <code>/accounts</code> | Open Cloudflare account management |
| <code>/about</code> | Developer, GitHub repository and Telegram channel links |
| <code>/help</code> | Display help |
| <code>/cancel</code> | Cancel the current input flow |

The slash-command list is synchronized with Telegram on bot startup. An obsolete `/id` entry configured previously through BotFather is replaced in the default and private-chat menus. This release does not implement `/id`.

### Service operations

~~~bash
cd /opt/cloudflare-telegram-manager

# Check service state
sudo docker compose ps

# Restart the bot
sudo docker compose restart cloudflare-bot

# Stop the service
sudo docker compose stop

# Start the service
sudo docker compose up -d

# Run sanitized diagnostics
sudo bash diagnose.sh
~~~

Avoid sharing raw container logs publicly; they may contain user or account metadata.

## Upgrading without losing data

### Git-based installation

From your **existing installation directory**, run:

~~~bash
cd /opt/cloudflare-telegram-manager
sudo bash install.sh --update
sudo docker compose ps
~~~

The installer preserves the existing <code>.env</code>, **<code>ENCRYPTION_KEY</code>**, and <code>data/bot.db</code>. If present, it backs up the environment file and the SQLite database under <code>backups/</code> before rebuilding the service.

**Do not regenerate the encryption key.** Saved Cloudflare tokens cannot be decrypted without the original key.

### Migrating from an earlier ZIP-based installation

1. Record the path of the **current** installation, which might be <code>/root/cloudflare-telegram-manager</code>.
2. Make secure copies of the existing <code>.env</code> and <code>data/</code> before changing any files.
3. Replace the **application source** in the same installation, preserving <code>.env</code> and <code>data/</code> intact.
4. Run <code>sudo bash install.sh</code> inside that existing directory.
5. Check <code>sudo docker compose ps</code>, run <code>sudo bash diagnose.sh</code>, and test an existing account in Telegram.

**Do not** clone into a new directory and assume the previous database will appear there automatically. Store backups securely off-repository. To recover, restore the matching environment file (including the original encryption key) and SQLite database, then re-run the installer to correct permissions.

## Troubleshooting

| Symptom | Suggested checks |
|---|---|
| Bot does not respond | Check the container status, outbound Telegram access, Bot Token, and numeric admin ID. Run <code>sudo bash diagnose.sh</code>. |
| Cloudflare <code>6003</code> / <code>6111</code> | Authorization header/token formatting failed. Supply only the raw API Token; create a replacement if needed. |
| Cloudflare <code>403</code> | Check token permissions, Zone Resources scope, expiration, and IP restrictions. |
| Cloudflare <code>404</code> or missing zones | Verify Account ID, Zone Read permission, and whether the zone belongs to the selected account. |
| Telegram <code>Button_data_invalid</code> | Use the current build; long callback IDs are encoded to fit Telegram's limit. |
| Telegram <code>Message is not modified</code> | Repeated taps should be treated as no-ops. Verify your installed version if the error persists. |
| Encrypted tokens cannot be read | Restore the **original** <code>ENCRYPTION_KEY</code> from a trusted environment backup. Do not delete your database. |
| SQLite permission errors | Re-run the installer; the bind-mounted data directory must be writable by container UID <code>10001</code>. |
| Docker / Compose error | Verify Docker Engine and Compose. Consult the [official Docker Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/). |

Before editing production records or toggling DNSSEC, export current DNS settings and understand the impact. Inconsistent registrar-side DS records may cause DNS resolution failures.

## Security best practices

- Keep <code>.env</code>, <code>data/</code>, <code>backups/</code>, API tokens, private keys, and unredacted logs **out of GitHub**.
- Always use **least-privilege, zone-scoped** Cloudflare API tokens rather than Global API Keys.
- Enter credentials only in the bot's **private chat**. Revoke any exposed token immediately.
- Restrict the admin allowlist to known numeric Telegram IDs.
- Maintain secure backups of both <code>.env</code> and SQLite storage; they must be restored together.
- Validate risky DNS or DNSSEC changes on a non-production zone first.
- Offline tests do **not** certify live Cloudflare or Telegram functionality.

## Offline tests

~~~bash
python3 test_auth.py
python3 test_account_connection.py
python3 test_callback_encoding.py
python3 test_interface.py
python3 test_account_commands.py
python3 test_command_menu.py
python3 test_i18n.py
~~~

These tests validate local behavior without requiring real Telegram or Cloudflare credentials. For operational confidence, perform a low-risk connection and zone-list test against an account you control.

---

**Documentation:** [🇮🇷 Persian](README_FA.md) · [🏠 README](README.md) · [📋 Changelog](CHANGELOG.md)
