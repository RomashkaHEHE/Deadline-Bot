# Deployment

## Overview

The repository already contains everything needed for Ubuntu deployment:

- `.github/workflows/deploy.yml` — GitHub Actions auto-deploy
- `deploy/install_service.sh` — first-time server bootstrap
- `deploy/deploy.sh` — code rollout on the server
- `deploy/deadline-bot.service` — `systemd` unit

The recommended production layout is:

- app code: `/opt/deadline-bot/app`
- virtualenv: `/opt/deadline-bot/.venv`
- env file: `/etc/deadline-bot/deadline-bot.env`
- persistent state: `/var/lib/deadline-bot/deadlines.json`

## 1. Prepare The Server

Connect to the server as a user with `sudo` access and make sure SSH is already configured.

Copy the `deploy` directory to the server:

```bash
scp -r deploy your_user@your_server:/tmp/deadline-bot-deploy
```

Run the bootstrap script:

```bash
ssh your_user@your_server
sudo bash /tmp/deadline-bot-deploy/install_service.sh
```

The script will:

- install `python3`, `python3-venv`, and `rsync`
- create the system user `deadlinebot`
- prepare `/opt/deadline-bot`, `/var/lib/deadline-bot`, and `/etc/deadline-bot`
- install the `systemd` unit
- grant limited `sudo` rights to restart the service

## 2. Fill The Environment File

Edit:

```text
/etc/deadline-bot/deadline-bot.env
```

Example:

```env
TOKEN=your_bot_token
CHANNEL_ID=@your_channel_or_-1001234567890
WHITELIST_USER_IDS=123456789,987654321
DEADLINES_STORAGE_PATH=/var/lib/deadline-bot/deadlines.json
```

## 3. Configure SSH Deploy Access

Create a dedicated deploy key on your local machine:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/deadline-bot-deploy
```

Copy the public key to the server:

```bash
scp ~/.ssh/deadline-bot-deploy.pub your_user@your_server:/tmp/deadline-bot-deploy.pub
```

Install it for `deadlinebot`:

```bash
sudo sh -c 'cat /tmp/deadline-bot-deploy.pub >> /home/deadlinebot/.ssh/authorized_keys'
sudo chown deadlinebot:deadlinebot /home/deadlinebot/.ssh/authorized_keys
sudo chmod 600 /home/deadlinebot/.ssh/authorized_keys
rm /tmp/deadline-bot-deploy.pub
```

## 4. Configure GitHub Secrets

In the GitHub repository, add these Actions secrets:

- `DEPLOY_HOST`
- `DEPLOY_PORT`
- `DEPLOY_USER`
- `DEPLOY_SSH_KEY` or `DEPLOY_SSH_KEY_B64`
- `DEPLOY_KNOWN_HOSTS`

Example `known_hosts` generation:

```bash
ssh-keyscan -H your_server
```

## 5. First Deploy

After secrets are configured, you can:

- run the `Deploy` workflow manually in GitHub Actions
- or push to `main`

The workflow:

- checks out the repo
- installs Python dependencies
- compile-checks `app.py`, `bot_messages.py`, and `tools.py`
- builds a release archive
- uploads it via SSH
- runs `deploy/deploy.sh` remotely
- restarts `deadline-bot.service`

## 6. Verify The Service

On the server:

```bash
sudo systemctl status deadline-bot
sudo journalctl -u deadline-bot -n 100 --no-pager
```

## Manual Deploy Without GitHub Actions

```bash
tar \
  --exclude='.git' \
  --exclude='.github' \
  --exclude='.env' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='deadlines.json' \
  -czf deadline-bot-release.tar.gz .

scp deadline-bot-release.tar.gz deadlinebot@your_server:/tmp/deadline-bot-release.tar.gz

ssh deadlinebot@your_server "RELEASE_ARCHIVE=/tmp/deadline-bot-release.tar.gz bash -s" < deploy/deploy.sh
```

## Production Data Note

Production deadlines should live outside the repository:

```env
DEADLINES_STORAGE_PATH=/var/lib/deadline-bot/deadlines.json
```

That way deploys update code without overwriting live data.
