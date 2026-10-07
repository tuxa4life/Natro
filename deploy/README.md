# Putting Natro's brain on the VPS

The VPS runs the voice pipeline and the agent (`voice/` + `agent/`); the PC and
phone connect to it over Tailscale. Other services share the VPS, so Natro gets
its own user, folder and venv, listens only on its Tailscale address, and has a
memory limit. Done on Ubuntu 26.04 with root (the sudo steps below); the no-sudo
steps are untested.

## 1. Look first

```bash
id; sudo -v            # root or sudo?
cat /etc/os-release    # which Linux
free -h; nproc         # 2 vCPU, 4 GB expected
ss -tlnp               # what already listens (don't break it)
```

## 2. Tailscale

- **With sudo:** `curl -fsSL https://tailscale.com/install.sh | sh`, then
  `sudo tailscale up` and log in. `tailscale ip -4` shows the address
  (100.x.y.z): that goes into `NATRO_LISTEN` (with `:8700`).
- **Without sudo:** run `tailscaled --tun=userspace-networking` (static binaries
  from pkgs.tailscale.com), then `tailscale up`. In this mode there is no
  network interface: connections to the Tailscale address are passed to
  localhost, so set `NATRO_LISTEN=127.0.0.1:8700` (still not public). Check
  this against Tailscale's current docs when doing it.

Install Tailscale on the PC too (already installed) and the phone, same account.
On Windows the client connects only once its tray app has started: if
`tailscale status` says "NoState", start the Tailscale app.

## 3. Code and Python

```bash
sudo useradd -m natro && sudo -iu natro   # with sudo; otherwise use your own user and ~/natro
git clone <the repo> ~/natro
cd ~/natro
curl -LsSf https://astral.sh/uv/install.sh | sh    # uv, no root needed; it also installs Python 3.13
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e voice -e agent
```

To deploy by pushing from the PC instead of pulling on the VPS: run
`git config receive.denyCurrentBranch updateInstead` in `~/natro`, add it as a
remote on the PC (`git remote add vps natro@<vps>:natro`), and `git push vps dev`
updates the VPS's files; then restart the service.

## 4. Secrets

Copy `.env` (see `.env.example`, server part) and `google-key.json` into
`~/natro` with `scp`, then `chmod 600 .env google-key.json`. Make a device token
for `NATRO_DEVICE_TOKEN` and put the same one in the PC's `.env`.

Check it works: `cd agent && ../.venv/bin/python -m natro_agent.chat --once "hi"`.

## 5. Run it as a service

- With sudo: `deploy/natro.service` (instructions inside).
- Without sudo: `deploy/natro-user.service`. If `loginctl enable-linger` isn't
  allowed, start it at boot from cron instead: `crontab -e`, then
  `@reboot cd ~/natro/agent && ../.venv/bin/python -m natro_agent.server >> ~/natro/logs/server.log 2>&1`.

Check from the PC: `curl http://<tailscale ip>:8700/health` answers `ok`.

## 6. Firewall

The agent listens only on the Tailscale address, so it isn't reachable from
the internet either way. Closing everything except SSH (`ufw default deny
incoming; ufw allow OpenSSH; ufw enable`) is still good, but only after checking
with `ss -tlnp` that the other services on the VPS don't need public ports.
Also: SSH keys only, automatic security updates (`unattended-upgrades`).

## 7. Connect the PC

In the PC's `.env`: `NATRO_SERVER=ws://<tailscale ip>:8700` and the token. Then
`python -m natro_pc.app --wake`. Each reply shows the time from the end of your
request to the English and to the reply, and its cost.
