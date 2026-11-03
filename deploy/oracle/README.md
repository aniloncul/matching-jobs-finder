# JobScout on Oracle Cloud Always Free

Runs JobScout 24/7 on Oracle's always-free ARM VM for **0 per month** (no card
charge inside the free limits). One instance, one volume, HTTP basic auth.

Oracle sits behind a console and requires a card for identity verification
(a temporary ~1 USD authorisation, refunded). If you do not want to enter a
card at all, run locally instead: `uv run jobscout serve`.

## 1. Account

1. Sign up at <https://cloud.oracle.com> and pick a **home region** near you
   (e.g. `eu-frankfurt-1`, `eu-marseille-1`). Always Free resources only
   exist in the home region.
2. Complete identity verification. Oracle pre-authorises about 1 USD on the
   card and releases it - that is not a charge.
3. **Never click "Upgrade to Pay As You Go".** On an un-upgraded Always Free
   account Oracle does not provision paid resources, so a surprise bill is
   practically impossible; upgrading removes that guardrail.

## 2. SSH key

An ed25519 key pair is already on the machine that prepared this repo:

```bash
cat ~/.ssh/oracle_jobscout.pub
```

Paste that public key into the instance form below. Keep
`~/.ssh/oracle_jobscout` private - it is the only way into the VM.

## 3. Create the instance

Compute → Instances → **Create instance**:

| Field | Value |
|---|---|
| Name | `jobscout` |
| Image | Canonical Ubuntu 24.04 (or 22.04) |
| Shape | **VM.Standard.A1.Flex** (Ampere) |
| OCPUs / memory | 4 / 24 GB (or 2 / 12 GB if capacity is short) |
| Boot volume | default ~47 GB is fine (stay within 200 GB total) |
| SSH keys | paste the public key from step 2 |

ARM capacity in popular regions is often exhausted ("Out of host capacity").
Retry at a different hour, try another availability domain, or create a
smaller A1 shape - the app runs fine on 2 OCPU / 12 GB.

## 4. Open the port

Two firewalls stand between the internet and the app:

1. **VCN security list** (console): Networking → Virtual Cloud Networks →
   your VCN → Security Lists → Default → **Add ingress rule**:
   `Source 0.0.0.0/0`, `IP Protocol TCP`, `Destination port 8000`.
   Narrow the source to your own IP if you have a static one.
2. **The VM firewall**: handled by `setup.sh` (iptables, and ufw if active).

For optional HTTPS (section 7) also add `80` and `443` to the same rule set.

## 5. Install

Copy the VM's public IP from the console, then from this machine:

```bash
ssh -i ~/.ssh/oracle_jobscout ubuntu@<PUBLIC_IP> \
  'curl -fsSL https://raw.githubusercontent.com/aniloncul/matching-jobs-finder/main/deploy/oracle/setup.sh | bash -s -- "<JOBSCOUT_PASSWORD>"'
```

The script installs Docker, clones the branch, builds the image (10-20 min on
first run; Chromium and Tectonic are compiled into it), creates the
`jobscout_data` volume and starts the container with `--restart unless-stopped`.

Open `http://<PUBLIC_IP>:8000`, sign in as `jobscout` with that password, and
configure the model and sheet once in the **SETUP** tab. Everything lives on
the Docker volume, so rebuilding/restarting the container keeps it.

## 6. Staying inside Always Free

The whole tenancy, not per instance:

| Resource | Always Free allowance |
|---|---|
| Ampere A1 compute | **4 OCPU + 24 GB RAM total** (any split across <= 4 VMs) |
| AMD micro compute | 2 x VM.Standard.E2.1.Micro (1 GB each) |
| Block storage | **200 GB total** (boot volumes included), 5 backups |
| Object storage | 20 GB, 50k requests/month |
| Outbound transfer | 10 TB/month |
| Load balancer | 1 flexible, 10 Mbps |

Rules that keep the bill at zero:

- **Do not upgrade** to Pay As You Go.
- **Home region only.** Resources in other regions are billable.
- Keep the **total** A1 allocation <= 4 OCPU / 24 GB. Two instances of
  2/12 fit; a third does not.
- **Delete old boot volumes** when you recreate an instance. Leftovers count
  against the 200 GB block budget and are the most common accidental overage.
- Do not attach extra block volumes or take more than 5 volume backups.
- Do not create paid shapes (E4/E5, GPU), NAT/extra load balancers beyond the
  free one, or Autonomous Databases beyond the free two.
- Set a free **budget alert**: Billing & Cost Management → Budgets → create a
  budget of 1 USD with an alert at 100%. It emails you at the first cent.
- Check **Governance → Limits, Quotas and Usage** now and then; filter on
  "Always Free" to see what is consumed.

Idle policy: Oracle may stop and eventually reclaim Always Free compute that
sits idle (low CPU/network for days). This app is mostly idle between runs.
If it is reclaimed, re-create the instance (step 3) and re-run `setup.sh`;
the data lived on the VM's Docker volume, so keep a copy of your profile/CVs
in the Google Sheet or export them from time to time.

## 7. Optional HTTPS

Basic auth over plain HTTP sends the password in clear text. Without a domain,
use a free wildcard-DNS hostname pointing at the IP:

```bash
# on the VM, after setup.sh
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

IP=$(curl -s https://api.ipify.org | tr '.' '-')
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$IP.nip.io {
    reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl restart caddy
```

Then browse `https://<ip-with-dashes>.nip.io`. Let's Encrypt certificates work
because `nip.io` is on the public suffix list; ports 80/443 must be open.

## 8. Operations

```bash
sudo docker logs -f jobscout          # live logs
sudo docker restart jobscout          # restart without losing data
cd ~/jobscout && git pull && \
  sudo docker build -t jobscout:local . && \
  sudo docker rm -f jobscout && sudo docker run -d --name jobscout \
  --restart unless-stopped -p 8000:8000 -v jobscout_data:/app/data \
  -e JOBSCOUT_PASSWORD="<password>" jobscout:local
```

Back up the volume occasionally:

```bash
sudo docker run --rm -v jobscout_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/jobscout-data-$(date +%F).tgz -C /data .
```
