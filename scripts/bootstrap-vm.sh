#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 VM (Linode, or any cloud). Run as root:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/bootstrap-vm.sh -o bootstrap.sh
#   bash bootstrap.sh git@github.com:<owner>/<repo>.git range.example.com you@example.com
set -euo pipefail
REPO="${1:?usage: bootstrap-vm.sh <git-ssh-url> <domain> <admin-email>}"
DOMAIN="${2:?domain}"; EMAIL="${3:?admin email}"
APP_DIR=/opt/airange

echo "==> Packages, Docker, firewall"
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq
apt-get install -y -qq git curl openssl ufw fail2ban unattended-upgrades
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

echo "==> Swap (helps 4-8 GB VMs running LLM Guard and Langfuse)"
if ! swapon --show | grep -q swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> 'deploy' user for GitHub Actions"
id deploy >/dev/null 2>&1 || useradd -m -s /bin/bash deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
[ -f /home/deploy/.ssh/id_ed25519 ] || sudo -u deploy ssh-keygen -q -t ed25519 -N "" -f /home/deploy/.ssh/id_ed25519 -C "airange-vm-deploy-key"
[ -f /home/deploy/.ssh/actions_ed25519 ] || sudo -u deploy ssh-keygen -q -t ed25519 -N "" -f /home/deploy/.ssh/actions_ed25519 -C "github-actions"
grep -qf /home/deploy/.ssh/actions_ed25519.pub /home/deploy/.ssh/authorized_keys 2>/dev/null || \
  cat /home/deploy/.ssh/actions_ed25519.pub >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys
sudo -u deploy sh -c 'ssh-keyscan -q github.com >> ~/.ssh/known_hosts 2>/dev/null'

cat <<MSG

------------------------------------------------------------------------------
1) Add this READ-ONLY deploy key to GitHub: repo > Settings > Deploy keys > Add
   (title "VM", leave "Allow write access" unticked):

$(cat /home/deploy/.ssh/id_ed25519.pub)

Press Enter when done...
------------------------------------------------------------------------------
MSG
read -r _

echo "==> Cloning $REPO"
install -d -o deploy -g deploy "$APP_DIR"
[ -d "$APP_DIR/.git" ] || sudo -u deploy git clone "$REPO" "$APP_DIR"
cd "$APP_DIR"
[ -f .env ] || sudo -u deploy bash scripts/gen-env.sh "$DOMAIN" "$EMAIL"

cat <<MSG

------------------------------------------------------------------------------
2) Edit $APP_DIR/.env and set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
   (sudo -u deploy nano $APP_DIR/.env), then start everything:
     sudo -u deploy bash -c 'cd $APP_DIR && APP_DIR=$APP_DIR scripts/deploy.sh'

3) DNS: point these A records at this VM's IP:
     $DOMAIN   gateway.$DOMAIN   langfuse.$DOMAIN   minio.$DOMAIN

4) GitHub repo > Settings > Secrets and variables > Actions > New repository secret:
     VM_HOST         = $(curl -fs4 ifconfig.me || hostname -I | awk '{print $1}')
     VM_USER         = deploy
     VM_SSH_KEY      = (the private key below, including the BEGIN/END lines)
     VM_KNOWN_HOSTS  = (the line below)

$(cat /home/deploy/.ssh/actions_ed25519)

$(ssh-keyscan -q localhost 2>/dev/null | sed "s/^localhost/$(curl -fs4 ifconfig.me || echo YOUR_VM_IP)/" | head -1)

   Every push to main then tests, builds and deploys automatically.
------------------------------------------------------------------------------
MSG
