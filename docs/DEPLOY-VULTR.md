# Deploy Social Video Downloader on Vultr

Step-by-step guide to run the app on a Vultr cloud instance with HTTPS.

---

## Prerequisites

- A [Vultr](https://vultr.com) account
- (Optional but recommended) A domain name and access to set an **A record** to your server’s IP
- Your project in a **Git repo** (e.g. GitHub) so the server can clone it, or you can upload files with `scp`/`rsync`

---

## Step 1: Create a Vultr instance

1. Log in at [vultr.com](https://vultr.com) and go to **Products** → **Deploy New** (or **+**).
2. **Choose server type**: **Cloud Compute** (regular VPS).
3. **Location**: Pick a region close to you or your users.
4. **Image**: **Ubuntu 22.04 LTS**.
5. **Plan**: e.g. **Regular Performance** → **$6/mo** (1 vCPU, 1 GB RAM, 25 GB SSD). Enough for light traffic.
6. **SSH Key** (recommended): Add your public key so you can log in without a password.  
   - On your Mac: `cat ~/.ssh/id_rsa.pub` or `cat ~/.ssh/id_ed25519.pub`. Copy the line and paste into Vultr’s “SSH Key” section and save.  
   - Or skip and use **Password**; Vultr will email or show it in the dashboard.
7. **Hostname**: e.g. `svd` (optional).
8. Click **Deploy Now**. Wait until status is **Running** and note the **IP Address** (e.g. `123.45.67.89`).

---

## Step 2: Connect over SSH

From your computer:

```bash
ssh root@YOUR_IP
```

Replace `YOUR_IP` with the instance IP. If you used a password, enter it when prompted. Accept the host key if asked.

You should see a shell prompt on the Ubuntu server.

---

## Step 3: Update the system and install Python

Run on the server (use **lowercase** `python3` — `Python3` is not a valid package name):

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git
```

Ubuntu 22.04 ships Python 3.10, which is fine for this app. Check:

```bash
python3 --version
```

---

## Step 4: Deploy the application

**Option A – Clone from Git (if the project is in a repo)**

```bash
cd /opt
git clone https://github.com/YOUR_USERNAME/social-video-downloader.git
cd social-video-downloader
```

Use your real repo URL. If the repo is private, set up SSH keys or a deploy token on the server.

**Option B – Upload from your machine**

On your **local** machine (not the server), from the folder that contains `backend` and `frontend`:

```bash
scp -r backend frontend root@YOUR_IP:/opt/social-video-downloader/
```

Then on the **server**:

```bash
mkdir -p /opt/social-video-downloader
# If you only uploaded backend + frontend, create README or leave as-is
cd /opt/social-video-downloader
```

---

## Step 5: Create virtualenv and install dependencies

On the server, create a virtual environment and install into it. Use the venv’s `pip` **by path** so you don’t hit the “externally-managed-environment” error (Ubuntu 24+ / PEP 668):

```bash
cd /opt/social-video-downloader/backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Test that the app starts:

```bash
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

From your computer, open `http://YOUR_IP:8000`. You should see the app. Press `Ctrl+C` on the server to stop it.

---

## Step 6: Run the app with systemd (always on, restart on crash)

On the server, create a systemd unit:

```bash
nano /etc/systemd/system/svd.service
```

Paste this (adjust paths if you used a different directory):

```ini
[Unit]
Description=Social Video Downloader
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/social-video-downloader/backend
Environment="PATH=/opt/social-video-downloader/backend/.venv/bin"
ExecStart=/opt/social-video-downloader/backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Save and exit (`Ctrl+O`, Enter, `Ctrl+X`). Then:

```bash
systemctl daemon-reload
systemctl enable svd
systemctl start svd
systemctl status svd
```

You should see `active (running)`. The app now listens on **127.0.0.1:8000** (localhost only). Next we’ll put Caddy in front so the outside world hits Caddy and Caddy talks to the app.

---

## Step 7: Install Caddy (reverse proxy + HTTPS)

On the server:

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update
apt install -y caddy
```

---

## Step 8a: Use HTTPS with a domain (recommended)

1. **Point your domain to the server**  
   In your domain’s DNS (where you bought the domain), add an **A record**:  
   - Name: `@` (or a subdomain like `svd`)  
   - Value: `YOUR_IP`  
   - TTL: 300 or default  

   Example: if your domain is `example.com` and you use subdomain `svd`, you’ll use `https://svd.example.com`.

2. **Configure Caddy** on the server:

```bash
nano /etc/caddy/Caddyfile
```

Replace `svd.example.com` with your real hostname:

```
svd.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Save and exit. Then:

```bash
systemctl reload caddy
```

Caddy will request a Let’s Encrypt certificate automatically. Open `https://svd.example.com` in a browser; you should see the app over HTTPS.

---

## Step 8b: Use HTTP only (no domain, for testing)

If you don’t have a domain yet, you can serve over HTTP on port 80:

```bash
nano /etc/caddy/Caddyfile
```

Use this (replace with your server’s public IP):

```
:80 {
    reverse_proxy 127.0.0.1:8000
}
```

Then:

```bash
systemctl reload caddy
```

Open `http://YOUR_IP` in a browser. This is **not encrypted**; only use for testing or on a trusted network.

---

## Step 9: Open the firewall

Allow SSH, HTTP, and HTTPS; block everything else:

```bash
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
ufw status
```

Confirm 22, 80, 443 are allowed. You should now reach the app at `https://your-domain` or `http://YOUR_IP` (if you used 8b).

---

## Step 10: Optional – create a non-root user

Running the app as root is not ideal. You can create a dedicated user:

```bash
adduser --disabled-password --gecos "" svd
```

Give them access to the app directory and venv:

```bash
chown -R svd:svd /opt/social-video-downloader
```

Edit the systemd service to use `User=svd` and `Group=svd`:

```bash
nano /etc/systemd/system/svd.service
```

Change `User=root` to `User=svd`, then:

```bash
systemctl daemon-reload
systemctl restart svd
systemctl status svd
```

---

## Useful commands

| Task | Command |
|------|--------|
| View app logs | `journalctl -u svd -f` |
| Restart app | `systemctl restart svd` |
| Restart Caddy | `systemctl reload caddy` |
| Update app (if using Git) | `cd /opt/social-video-downloader && git pull && systemctl restart svd` |

---

## Troubleshooting

- **502 Bad Gateway**: App not running or not on 8000. Run `systemctl status svd` and `journalctl -u svd -n 50`.
- **Can’t connect to site**: Check `ufw status`; ensure 80/443 are allowed. Confirm Caddy is running: `systemctl status caddy`.
- **HTTPS certificate errors**: Ensure the domain’s A record points to this server’s IP and that ports 80 and 443 are open. Wait a few minutes for DNS to propagate.
- **Downloads fail**: Check app logs with `journalctl -u svd -f`. Ensure the server can reach the internet (e.g. `curl -I https://youtube.com`).
