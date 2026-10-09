# AWS Reverse Tunnel

Expose a home-network service at `<name>.<domain>` — no inbound ports opened at home.

```
Browser -> DNS (Cloudflare) -> EC2 (Caddy + frps) -> tunnel -> home PC (frpc) -> your app
```

## Setup & Usage

**1. Install the CLI:**

```sh
# anywhere else — install frpc yourself first:
# https://github.com/fatedier/frp/releases
uv tool install 'aws-reverse-tunnel[infra]'
```

**2. Deploy the AWS side**

AWS stack: EC2 + frps/Caddy, Elastic IP, IAM role

```sh
aws-reverse-tunnel infra config --domain dasbd72.com   # region auto-detected
aws-reverse-tunnel infra deploy
```

- `infra config`
  - `--domain` is the base domain for your tunnel subdomains (e.g. `dasbd72.com`).
  - `--region` is the AWS region to deploy the stack in (auto-detected from your AWS CLI config).
  - `--extra-port-range START-END` opens that TCP+UDP port range on the EC2 host and in
    frps — needed before `frpc add --proto tcp|udp --remote-port` can use a port in that
    range. Not opened at all unless given. Requires `infra deploy` to apply.
  - `--yes` skips confirmation prompts.

Note the `ElasticIp` output (or run `aws-reverse-tunnel infra status` anytime).

**2b. Point DNS at the Elastic IP.** DNS is managed outside this stack (e.g.
Cloudflare). Create a wildcard record `*.dasbd72.com` (type `A`) pointing at the
`ElasticIp` from the previous step. Keep it **DNS-only** (not proxied / "grey
cloud" in Cloudflare) and make sure port 80 is reachable, so Caddy can obtain
per-host TLS certs on demand via Let's Encrypt HTTP-01.

**3.1. Configure the tunnel client, then expose a service:**

```sh
aws-reverse-tunnel frpc config   # server address + domain + region + token auto-detected from step 2
aws-reverse-tunnel frpc add openwebui 192.168.50.10:8080
# -> https://openwebui.dasbd72.com
```

- `frpc config`
  - `--server-addr` is the public IP of the EC2 instance (auto-detected from step 2).
  - `--base-domain` is the base domain for your tunnel subdomains (auto-detected from step 2).
  - `--region` is the AWS region to deploy the stack in (auto-detected from your AWS CLI config).
  - `--token` is the frp auth token (fetched from SSM if this is the same machine that ran step 2).
- `frpc add <name> <local-ip>:<local-port>` adds a new tunnel for the given local service.
  - By default this exposes an HTTP(S) service at `<name>.<base-domain>`.
  - For a non-HTTP service (e.g. WireGuard), pass `--proto tcp|udp --remote-port <port>`
    to bind an explicit port on the frps server instead, e.g.
    `frpc add wireguard 127.0.0.1:51820 --proto udp --remote-port 51820`. The port must
    fall within the range set by `infra config --extra-port-range`.
- `frpc list` lists all configured tunnels.
- `frpc del <name>` removes a configured tunnel.


**3.2. Run frpc client in other machines:**

```sh
# Get the frpc config
aws-reverse-tunnel frpc render
```

## Development

```sh
uv sync --all-extras
uv run pytest
uv run mypy src tests
uv run pre-commit run --all-files
```

**Layout:**
- `src/aws_reverse_tunnel/` — the CLI, including `infra/` (the CDK stack,
  bundled into the package so `infra deploy` works without cloning this repo)

**Known limitation:** the frp auth token is generated in CDK app code, so its
plaintext ends up in the synthesized CloudFormation template (stored as a
real SecureString at rest, but visible to anyone who can read the stack).
Fine for this personal, single-account setup.

**License:** MIT — see [LICENSE](LICENSE).
