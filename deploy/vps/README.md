# JobScout on an existing VPS behind Caddy

Put JobScout next to services you already run, on a host you already pay for.
Marginal cost is zero: one small container, a Docker volume, one Caddy site
block. Idle footprint measured on a 2 vCPU / 3.8 GB box: **~65-125 MB RSS**
(`--memory=1500m` cap so it can never starve the neighbours).

This guide assumes Docker and a Caddy container that is already serving HTTPS
for other domains (`metavocab-caddy-1` in the example). Substitute your own
container, network and domain names.

## 1. DNS

Create a plain `A` record (DNS-only, no proxy) pointing the subdomain at the
VPS. Proxying through Cloudflare works too, but DNS-only keeps certificate
issuance and SSE streaming simple:

```
A  jobs.example.com  ->  <VPS public IP>      TTL auto
```

## 2. Build and start

```bash
git clone --depth 1 -b main https://github.com/<you>/matching-jobs-finder.git /opt/jobscout
cd /opt/jobscout
docker build -t jobscout:local .

docker volume create jobscout_data
docker run -d --name jobscout --restart unless-stopped \
  --network caddy_network \
  -p 127.0.0.1:8080:8000 \
  --memory=1500m --memory-swap=2500m \
  -v jobscout_data:/app/data \
  -e JOBSCOUT_PASSWORD="<a long password>" \
  jobscout:local
```

`--network` must be the network Caddy is attached to, so the proxy can reach
`jobscout:8000` by name. The published port is bound to loopback only; it is
handy for `curl` and never exposed to the internet.

Find the network with:

```bash
docker inspect <caddy-container> --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

If Caddy runs on the default bridge, attach the existing container instead of
recreating it:

```bash
docker network connect bridge jobscout
```

## 3. Caddy

Back up the Caddyfile, then append:

```caddyfile
jobs.example.com {
    encode zstd gzip
    reverse_proxy jobscout:8000
}
```

Validate and reload without downtime:

```bash
docker exec <caddy-container> caddy validate --config /etc/caddy/Caddyfile
docker exec <caddy-container> caddy reload   --config /etc/caddy/Caddyfile
```

Caddy obtains a Let's Encrypt certificate on its own (ports 80/443 must be open
and the DNS record live). SSE needs no extra directives - Caddy streams
`text/event-stream` responses as they are.

## 4. Verify

```bash
curl -s https://jobs.example.com/api/health            # {"ok":true,...}
curl -o /dev/null -w '%{http_code}\n' https://jobs.example.com/            # 401
curl -o /dev/null -w '%{http_code}\n' -u jobscout:<password> \
  https://jobs.example.com/api/settings                                    # 200
```

Then open the site, sign in as `jobscout`, and fill the **SETUP** tab once
(NVIDIA/OpenAI key, Google Sheet + service account). Everything is written to
`/app/data` on the `jobscout_data` volume, so redeploys keep it.

## 5. Operations

```bash
docker logs -f jobscout                    # logs
docker restart jobscout                    # restart, data kept
docker stats --no-stream jobscout          # memory / CPU
```

Update:

```bash
cd /opt/jobscout && git pull
docker build -t jobscout:local .
docker rm -f jobscout
docker run -d ... jobscout:local           # same flags as step 2
```

Change the password:

```bash
docker rm -f jobscout && docker run -d ... -e JOBSCOUT_PASSWORD="<new>" jobscout:local
```

Back up settings, service account and profiles:

```bash
docker run --rm -v jobscout_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/jobscout-data-$(date +%F).tgz -C /data .
```

## Notes

- **Memory**: idle 65-125 MB; a Chromium page peaks around 500 MB, a busy run
  with PDF rendering stays under ~1 GB. The `--memory=1500m` cap turns an
  overrun into a container restart instead of host pressure.
- **Firewall**: with the port bound to `127.0.0.1` you do not need to open
  anything beyond 80/443. If you bind `0.0.0.0` for direct access, restrict it
  with `ufw` or a VPN instead of exposing it to the world.
- **Reboots**: `--restart unless-stopped` plus `systemctl enable docker` is
  enough; Caddy re-resolves `jobscout` when the container comes back.
- **Host size**: 1 vCPU / 1 GB works with swap; 2 GB is comfortable.
