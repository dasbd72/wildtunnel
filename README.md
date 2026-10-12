# Wildtunnel

[![PyPI](https://img.shields.io/pypi/v/wildtunnel)](https://pypi.org/project/wildtunnel/)
[![Python versions](https://img.shields.io/pypi/pyversions/wildtunnel)](https://pypi.org/project/wildtunnel/)
[![Publish to PyPI](https://github.com/dasbd72/wildtunnel/actions/workflows/publish.yml/badge.svg)](https://github.com/dasbd72/wildtunnel/actions/workflows/publish.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

This tool makes a home-network service available at `<name>.<domain>`. It does not open an inbound port at home.

```
Browser -> DNS (Cloudflare) -> VPS (Caddy + frps) -> tunnel -> home PC (frpc) -> your app
```

Wildtunnel puts the tunnel server on a VPS. It supports two providers: AWS EC2 (deployed via CDK) and AWS Lightsail (deployed via direct API calls, no CDK toolchain needed). The code separates the generic server setup (frps + Caddy) from each provider's deploy logic, so adding another provider needs no rewrite.

## Setup and usage

### Step 1: Install the CLI

Install frpc on a different machine first: [github.com/fatedier/frp/releases](https://github.com/fatedier/frp/releases)

```sh
# For the AWS EC2 provider:
uv tool install 'wildtunnel[aws-ec2]'

# For the AWS Lightsail provider (no extra needed, no CDK toolchain):
uv tool install wildtunnel
```

Tab completion for subcommands, flags, and tunnel names (`frpc del <tab>`) is available for bash, zsh, and fish. Add the line for your shell to its rc file, then restart your shell:

```sh
source <(wildtunnel completion bash)   # ~/.bashrc
source <(wildtunnel completion zsh)    # ~/.zshrc
wildtunnel completion fish | source    # ~/.config/fish/config.fish
```

### Step 2: Deploy the server

Pick one provider.

#### Step 2a: Deploy to AWS EC2

Creates an EC2 host, frps, Caddy, an Elastic IP, and an IAM role, via CDK.

```sh
wildtunnel aws-ec2 config --domain example.com   # reads the region from your AWS CLI config
wildtunnel aws-ec2 deploy
```

Options for `aws-ec2 config`:

- `--domain`: base domain for the tunnel subdomains, e.g. `example.com`.
- `--region`: AWS region for the stack. Defaults to your AWS CLI region.
- `--extra-port-range START-END`: opens a TCP+UDP port range on the EC2 host and in frps, for `frpc add --proto tcp|udp --remote-port`. Not opened unless given. Run `aws-ec2 deploy` again to apply a change.
- `--no-tls`: provision Caddy in plain-HTTP mode — no Let's Encrypt, no HTTP→HTTPS redirect. TLS is the default. This only sets the initial mode for a fresh deploy; toggle a live box with `sudo wildtunnel-tls-disable` / `sudo wildtunnel-tls-enable`.
- `--yes`: skips confirmation prompts.

Record the `ElasticIp` output. Check status anytime with `wildtunnel aws-ec2 status`.

Get the frpc client's data (server address, base domain, token, and a ready `frpc config` command):

```sh
wildtunnel aws-ec2 info
```

#### Step 2b: Deploy to AWS Lightsail

Creates a `nano_3_0` Lightsail instance, frps, Caddy, and a static IP, via direct Lightsail API calls. No CDK, no CloudFormation.

```sh
wildtunnel aws-lightsail config --domain example.com   # reads the region from your AWS CLI config
wildtunnel aws-lightsail deploy
```

`aws-lightsail config` takes the same options as `aws-ec2 config`: `--domain`, `--region`, `--extra-port-range`, `--no-tls`, `--yes`. Check status anytime with `wildtunnel aws-lightsail status`.

Lightsail instances cannot have an IAM role, so wildtunnel writes the frp token directly into the instance's user data at deploy time, instead of fetching it from SSM at boot. One effect: `wildtunnel aws-lightsail info` only works on the machine that ran `deploy`, since the token is never stored remotely.

```sh
wildtunnel aws-lightsail info
```

Re-running `deploy` on a live instance only syncs the static IP and firewall rules (for example after changing `--extra-port-range` or `--ssh-cidr`) — Lightsail cannot re-run cloud-init, so it never reinstalls frps/Caddy. Use `wildtunnel aws-lightsail diff` to check for firewall drift, or `destroy` + `deploy` to force a clean reinstall.

By default, `deploy` opens SSH (port 22) only to the Lightsail console's browser-based SSH client (the `lightsail-connect` firewall alias) — not to any public IP; Lightsail's browser SSH needs this rule to exist, it isn't exempt from the firewall. Pass `--ssh-cidr 1.2.3.4/32` (your current IP) to `deploy` or `diff` to additionally allow a regular SSH client; it's a one-shot flag, not saved to config, so a later `deploy`/`diff` without it closes client SSH again (browser-based SSH keeps working either way).

### Step 3: Point your DNS at the server address

Get the server address: the `ElasticIp` from `aws-ec2 info`, or the static IP from `aws-lightsail info`.

At your DNS provider (for example Cloudflare), add a wildcard `A` record, `*.example.com`, pointed at that address. Keep it DNS-only — do not enable the Cloudflare proxy (grey cloud, not orange). Make sure port 80 is open: Caddy needs it to get a TLS certificate per host on demand, via Let's Encrypt HTTP-01.

### Step 4: Configure the frpc client

#### Step 4.1: Add a service

frpc does not read the deployed server automatically. Copy the values, or the ready command, from `aws-ec2 info` / `aws-lightsail info`.

```sh
wildtunnel frpc config \
  --server-addr 1.2.3.4 \
  --base-domain example.com \
  --token <token>
wildtunnel frpc add openwebui 192.168.50.10:8080
# -> https://openwebui.example.com
```

`frpc config` options `--server-addr`, `--base-domain`, and `--token` are all required.

`frpc add <name> <local-ip>:<local-port>` adds a tunnel for a local service — HTTP/HTTPS at `<name>.<base-domain>` by default. For a non-HTTP service, for example WireGuard, add `--proto tcp` or `--proto udp` with `--remote-port <port>` (must be in the range you set with `--extra-port-range`):

```sh
frpc add wireguard 127.0.0.1:51820 --proto udp --remote-port 51820
```

`frpc list` shows configured tunnels. `frpc del <name>` removes one.

#### Step 4.2: Run frpc on a different machine

Use the same data as step 4.1. The `render` command writes an `frpc.toml` to stdout, without saving a file or starting a service:

```sh
wildtunnel frpc render \
  --server-addr 1.2.3.4 \
  --base-domain example.com \
  --token <token>
```

## Dashboard

Each provider installs an admin dashboard with frps and Caddy. The dashboard address is `wildtunnel.<domain>`. The VPS binds the dashboard to the loopback address. Caddy makes the dashboard reachable over TLS.

The `deploy` command creates the admin password on your machine. The command prints the password one time:

```sh
wildtunnel aws-lightsail deploy
# deployed; static IP: 1.2.3.4
# dashboard admin password: <generated password>
```

Log in with that password. The dashboard then prompts you to set a new password.

The dashboard has these pages:

- **Live stats** (`/stats`): Shows the current connection count and today's traffic for each tunnel. The dashboard reads this data directly from frps.
- **Egress history** (`/egress`): Shows day, week, and month totals for each tunnel. The dashboard samples the data every 5 minutes. The totals stay correct after you restart frps.

If you open a dashboard page before you log in, the dashboard sends you to `/login`.

### Server-side admin commands

Connect to the instance to run these as root:

- **AWS EC2**: `aws ssm start-session --target <instance-id>` — EC2's security group has no SSH rule at all; admin access goes through SSM Session Manager via the instance's IAM role instead.
- **AWS Lightsail**: SSH is closed to the public internet by default — use the Lightsail console's browser-based SSH, or pass `--ssh-cidr 1.2.3.4/32` to `deploy`/`diff` to additionally open a regular SSH client to your IP (see the SSH note under Step 2b above).

- `sudo wildtunnel-reset-password`: generates a new admin password, forces a change on next login, and prints the new password. The only account-recovery path if you're locked out — there's no email-based forgot-password flow.
- `sudo wildtunnel-upgrade`: upgrades the installed `wildtunnel[dashboard]` package to the latest PyPI release and restarts the dashboard service. The only way to pick up dashboard fixes on an already-deployed instance — `deploy` never re-provisions a live box, so it can't push code updates by itself.
- `sudo wildtunnel-tls-disable` / `sudo wildtunnel-tls-enable`: switch the box between plain HTTP and HTTPS by swapping the Caddyfile and reloading Caddy. This is the runtime counterpart to the deploy-time `--no-tls` flag — use it to change TLS mode on an already-deployed box (the `--no-tls` config flag only affects a fresh deploy).

## Upgrade from aws-reverse-tunnel

This project was renamed from `aws-reverse-tunnel` to `wildtunnel`. There is no automatic migration. To keep your existing configuration, move the config directory by hand:

```sh
mv ~/.config/aws-reverse-tunnel ~/.config/wildtunnel
```

## Cost

### AWS EC2

Runs one `t4g.nano` EC2 host, an 8 GiB gp3 EBS volume, and one Elastic IP. On-demand list prices below for a 730-hour month; treat as estimates, since AWS prices change.

| Resource | ap-northeast-1 (Tokyo) | us-east-1 |
| --- | --- | --- |
| t4g.nano (730 h) | ~$3.87 | ~$3.07 |
| EBS gp3, 8 GiB | ~$0.77 | ~$0.64 |
| Public IPv4 (Elastic IP) | ~$3.65 | ~$3.65 |
| SSM parameter and KMS | free | free |
| **Total (idle)** | **~$8.30** | **~$7.35** |

- AWS charges $0.005/hour per public IPv4 address since February 2024 — this applies to the Elastic IP even when idle, making it the largest cost after the host.
- The table excludes data transfer: egress scales with traffic. 100 GB/month is free, then AWS charges about $0.114/GB in Tokyo and about $0.09/GB in us-east-1.
- The SSM parameter uses the AWS-managed KMS key, which has no monthly fee.
- A one-year Compute Savings Plan or Reserved Instance lowers the host cost by about 30–40%.

### AWS Lightsail

The `nano_3_0` bundle is a flat $5/month (check [aws.amazon.com/lightsail/pricing](https://aws.amazon.com/lightsail/pricing/) for current prices) — includes the instance, SSD storage, and a monthly data-transfer allowance. No separate EBS or per-GB egress charges like EC2, and no $0.005/hour IP surcharge (a static IP is free while attached to a running instance). Simpler pricing, at the cost of a fixed 0.5 GiB RAM (same as `t4g.nano`) and no Savings Plan or Reserved Instance discount.

## Development

```sh
uv sync --all-extras
uv run pytest
uv run mypy src tests
uv run pre-commit run --all-files
```

### Code layout

- `src/wildtunnel/`: the CLI and the generic server installer (`server/assets/server-setup.sh`), which reads its input from environment variables and makes no AWS calls — so it also runs by hand on any Linux VPS.
- `src/wildtunnel/providers/aws_ec2/`: all AWS EC2 code — the CDK stack, app entry point, SSM token helpers, and the `aws-ec2` CLI group. Includes the stack itself, so `aws-ec2 deploy` needs no clone of this repo.
- `src/wildtunnel/providers/aws_lightsail/`: all AWS Lightsail code — the `aws-lightsail` CLI group and direct boto3 calls. No CDK: Lightsail has no useful CDK L2 constructs, and the point of Lightsail is to be simpler than EC2+CloudFormation.

### Add a new provider

`scripts/new_provider.py` is a maintainer-only scaffold, not shipped in the package and not wired into the CLI. It generates `providers/<name>/` and `tests/providers/<name>/`, mirroring `providers/aws_ec2/`'s shape (a Click group, a config dataclass, matching test stubs), then prints the remaining manual steps. Refuses to run if `providers/<name>/` already exists.

```sh
uv run python scripts/new_provider.py hetzner
```

## Known limitations

- The CDK app creates the frp token, so the plaintext token ends up in the generated CloudFormation template. AWS stores it as a SecureString, but a person with read access to the stack can still see it. Acceptable for this personal, single-account setup.
- Lightsail instances cannot have an IAM role, so wildtunnel writes the frp token directly into the instance's user data instead of fetching it from SSM. A person who can call `lightsail:GetInstance` can see it there. Also, `wildtunnel aws-lightsail info` only works on the machine that ran `deploy`, since the token is never stored remotely.
- The `aws-lightsail destroy` command releases the static IP address. The next `deploy` command gets a new static IP address. After a destroy and deploy, you must update your DNS records by hand.

## License

MIT. See [LICENSE](LICENSE).
