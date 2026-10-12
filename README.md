# Wildtunnel

[![PyPI](https://img.shields.io/pypi/v/wildtunnel)](https://pypi.org/project/wildtunnel/)
[![Python versions](https://img.shields.io/pypi/pyversions/wildtunnel)](https://pypi.org/project/wildtunnel/)
[![Publish to PyPI](https://github.com/dasbd72/wildtunnel/actions/workflows/publish.yml/badge.svg)](https://github.com/dasbd72/wildtunnel/actions/workflows/publish.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Expose a home-network service at `<name>.<domain>` — no inbound port opened at home.

```
Browser -> DNS (Cloudflare) -> VPS (Caddy + frps) -> home PC (frpc) -> your app
```

## Install

Install frpc on the client machine separately: [github.com/fatedier/frp/releases](https://github.com/fatedier/frp/releases)

```sh
uv tool install 'wildtunnel[aws-ec2]'   # EC2 provider (CDK)
uv tool install wildtunnel               # Lightsail provider (no CDK)
```

Enable shell completion by adding the matching line to your shell's rc file:

```sh
source <(wildtunnel completion bash)   # ~/.bashrc
source <(wildtunnel completion zsh)    # ~/.zshrc
wildtunnel completion fish | source   # ~/.config/fish/config.fish
```

## Deploy

Pick one provider. Both create frps + Caddy, a static IP, and an admin dashboard.

### AWS EC2 (CDK)

```sh
wildtunnel aws-ec2 config --domain example.com
wildtunnel aws-ec2 deploy
```

### AWS Lightsail (no CDK)

```sh
wildtunnel aws-lightsail config --domain example.com
wildtunnel aws-lightsail deploy
```

`config` options (both providers):

- `--domain` — base domain, e.g. `example.com`.
- `--region` — AWS region. Defaults to your AWS CLI region.
- `--extra-port-range START-END` — open a TCP+UDP range for non-HTTP tunnels (`frpc add --proto tcp|udp --remote-port`).
- `--no-tls` — plain HTTP, no Let's Encrypt, no HTTP→HTTPS redirect. TLS is the default. This only affects fresh deploys; toggle a live box with `sudo wildtunnel-tls-disable` / `sudo wildtunnel-tls-enable`.
- `--yes` — skip confirmation prompts.

Notes:

- `aws-lightsail info` only works on the machine that ran `deploy` — the frp token is never stored remotely.
- Lightsail SSH is open only to the browser console by default. Pass `--ssh-cidr 1.2.3.4/32` to `deploy` to allow a regular SSH client. The flag is not saved: a later `deploy` without it closes regular SSH again, while browser SSH keeps working. `diff` accepts the same flag but only previews firewall changes.
- Re-running Lightsail `deploy` on a live box only syncs the static IP and firewall — it never reinstalls frps/Caddy. Use `aws-lightsail diff` to check firewall drift; `destroy` + `deploy` forces a clean reinstall (and a new IP).

Check status anytime with `wildtunnel aws-ec2 status` / `wildtunnel aws-lightsail status`.

## DNS

Point a wildcard `A` record `*.example.com` at the server IP (from `aws-ec2 info` / `aws-lightsail info`). Keep it DNS-only — don't enable the Cloudflare proxy — and leave port 80 open (Caddy gets a per-host cert via Let's Encrypt HTTP-01).

## frpc client

Run `wildtunnel aws-ec2 info` / `wildtunnel aws-lightsail info` on the deploying machine to get the server address, base domain, token, and a ready-to-copy command. Run that command on the client machine; frpc does not discover the deployed server automatically.

```sh
wildtunnel frpc config \
  --server-addr 1.2.3.4 \
  --base-domain example.com \
  --token <token>

wildtunnel frpc add openwebui 192.168.50.10:8080
# -> https://openwebui.example.com   (or http:// with --no-tls)
```

- `frpc add <name> <ip>:<port>` → HTTP at `<name>.<domain>`; `--proto tcp|udp --remote-port <port>` for non-HTTP services (port must be in `--extra-port-range`).
- `frpc list` / `frpc del <name>` manage tunnels. `frpc render` prints an `frpc.toml` without saving or starting.

## Dashboard

Reachable at `wildtunnel.<domain>` (loopback-only, served by Caddy). `deploy` prints the admin password once; you're prompted to change it on first login.

Shows live connection/traffic stats and day/week/month egress history per tunnel; history survives frps restarts.

Connect to the server to run admin commands with `sudo`:

- **EC2:** `aws ssm start-session --target <instance-id>` — no SSH ingress rule; use SSM Session Manager.
- **Lightsail:** use the console's browser SSH, or allow your IP with `deploy --ssh-cidr` as described above.

Admin commands:

- `sudo wildtunnel-reset-password` — new admin password (the only recovery path).
- `sudo wildtunnel-upgrade` — upgrade `wildtunnel[dashboard]` and re-apply server config. The only way to update a live box; frps/Caddy binaries are not upgraded.
- `sudo wildtunnel-tls-disable` / `sudo wildtunnel-tls-enable` — switch between plain HTTP and HTTPS.

## Cost

| Provider | Resources | Monthly (idle, approx.) |
| --- | --- | --- |
| EC2 | t4g.nano + 8 GiB gp3 + Elastic IP | ~$7.35–$8.30 |
| Lightsail | nano_3_0 bundle (flat) | $5 |

EC2 adds egress charges beyond 100 GB/month. Lightsail includes a monthly transfer allowance (overages may cost extra) and has no Savings Plan discount. Check [current Lightsail pricing](https://aws.amazon.com/lightsail/pricing/).

## Upgrade from aws-reverse-tunnel

Renamed from `aws-reverse-tunnel`. Move your config by hand:

```sh
mv ~/.config/aws-reverse-tunnel ~/.config/wildtunnel
```

## Known limitations

- The frp token ends up in the CloudFormation template (EC2) / instance user data (Lightsail) — visible to anyone with stack/instance read access.
- `aws-lightsail destroy` releases the static IP; re-point DNS after a redeploy.
- Boxes provisioned before `wildtunnel-upgrade` re-applied server config keep the old upgrade script; redeploy to adopt it.

## Development

```sh
uv sync --all-extras
uv run pytest
uv run mypy src tests
uv run pre-commit run --all-files
```

Providers live under `src/wildtunnel/providers/<name>/`; the generic installer (`server-setup.sh`) is provider-agnostic and runs on any Linux VPS. `scripts/new_provider.py <name>` scaffolds a new provider.

## License

MIT. See [LICENSE](LICENSE).
