# Wildtunnel

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

### Step 2: Deploy the server

Pick one provider.

#### Step 2a: Deploy to AWS EC2

Creates an EC2 host, frps, Caddy, an Elastic IP, and an IAM role, via CDK.

```sh
wildtunnel aws-ec2 config --domain dasbd72.com   # reads the region from your AWS CLI config
wildtunnel aws-ec2 deploy
```

Options for `aws-ec2 config`:

- `--domain`: base domain for the tunnel subdomains, e.g. `dasbd72.com`.
- `--region`: AWS region for the stack. Defaults to your AWS CLI region.
- `--extra-port-range START-END`: opens a TCP+UDP port range on the EC2 host and in frps, for `frpc add --proto tcp|udp --remote-port`. Not opened unless given. Run `aws-ec2 deploy` again to apply a change.
- `--yes`: skips confirmation prompts.

Record the `ElasticIp` output. Check status anytime with `wildtunnel aws-ec2 status`.

Get the frpc client's data (server address, base domain, token, and a ready `frpc config` command):

```sh
wildtunnel aws-ec2 info
```

#### Step 2b: Deploy to AWS Lightsail

Creates a `nano_3_0` Lightsail instance, frps, Caddy, and a static IP, via direct Lightsail API calls. No CDK, no CloudFormation.

```sh
wildtunnel aws-lightsail config --domain dasbd72.com   # reads the region from your AWS CLI config
wildtunnel aws-lightsail deploy
```

`aws-lightsail config` takes the same options as `aws-ec2 config`: `--domain`, `--region`, `--extra-port-range`, `--yes`. Check status anytime with `wildtunnel aws-lightsail status`.

Lightsail instances cannot have an IAM role, so wildtunnel writes the frp token directly into the instance's user data at deploy time, instead of fetching it from SSM at boot. One effect: `wildtunnel aws-lightsail info` only works on the machine that ran `deploy`, since the token is never stored remotely.

```sh
wildtunnel aws-lightsail info
```

Re-running `deploy` on a live instance only syncs the static IP and firewall rules (for example after changing `--extra-port-range`) — Lightsail cannot re-run cloud-init, so it never reinstalls frps/Caddy. Use `wildtunnel aws-lightsail diff` to check for firewall drift, or `destroy` + `deploy` to force a clean reinstall.

### Step 3: Point your DNS at the server address

Get the server address: the `ElasticIp` from `aws-ec2 info`, or the static IP from `aws-lightsail info`.

At your DNS provider (for example Cloudflare), add a wildcard `A` record, `*.dasbd72.com`, pointed at that address. Keep it DNS-only — do not enable the Cloudflare proxy (grey cloud, not orange). Make sure port 80 is open: Caddy needs it to get a TLS certificate per host on demand, via Let's Encrypt HTTP-01.

### Step 4: Configure the frpc client

#### Step 4.1: Add a service

frpc does not read the deployed server automatically. Copy the values, or the ready command, from `aws-ec2 info` / `aws-lightsail info`.

```sh
wildtunnel frpc config \
  --server-addr 1.2.3.4 \
  --base-domain dasbd72.com \
  --token <token>
wildtunnel frpc add openwebui 192.168.50.10:8080
# -> https://openwebui.dasbd72.com
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
  --base-domain dasbd72.com \
  --token <token>
```

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

## License

MIT. See [LICENSE](LICENSE).
