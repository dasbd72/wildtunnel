# Wildtunnel

This tool makes a home-network service available at `<name>.<domain>`. It does not open an inbound port at home.

```
Browser -> DNS (Cloudflare) -> VPS (Caddy + frps) -> tunnel -> home PC (frpc) -> your app
```

Wildtunnel puts the tunnel server on a VPS. Today AWS EC2 is the only provider that this
tool supports. The code separates the generic box setup (frps + Caddy) from the
AWS-specific deploy logic, so the project can add another provider later without a
rewrite.

## Setup and Usage

**1. Install the CLI:**

```sh
# On a different machine, install frpc first:
# https://github.com/fatedier/frp/releases
uv tool install 'wildtunnel[aws-ec2]'
```

**2. Deploy the AWS EC2 stack**

The stack contains an EC2 host, frps, Caddy, an Elastic IP, and an IAM role.

```sh
wildtunnel aws-ec2 config --domain dasbd72.com   # The command reads the region.
wildtunnel aws-ec2 deploy
```

- `aws-ec2 config`
  - `--domain` is the base domain for the tunnel subdomains, for example `dasbd72.com`.
  - `--region` is the AWS region for the stack. The command reads the region from the
    AWS CLI configuration if you do not give it.
  - `--extra-port-range START-END` opens a TCP and UDP port range on the EC2 host and in
    frps. Set this range before you use `frpc add --proto tcp --remote-port` or
    `frpc add --proto udp --remote-port`. The command does not open the range if you do
    not give this option. You must run `aws-ec2 deploy` to apply the change.
  - `--yes` skips the confirmation prompts.

Record the `ElasticIp` output. You can also run `wildtunnel aws-ec2 status` at any time.

Run `wildtunnel aws-ec2 info` to read the values that the frpc client
needs. The command prints the server address, the base domain, and the token.
It also prints a ready `frpc config` command.

```sh
wildtunnel aws-ec2 info
```

**2b. Point the DNS at the Elastic IP.** An external provider controls the DNS,
for example Cloudflare. Make a wildcard record `*.dasbd72.com` of type `A`.
Point the record at the `ElasticIp` from the previous step. Keep the record
DNS-only. Do not use the Cloudflare proxy (the grey cloud, not the orange
cloud). Make sure that port 80 is open. Caddy then gets a TLS certificate for
each host on demand with Let's Encrypt HTTP-01.

**3.1. Configure the tunnel client and make a service available:**

The frpc client does not read the AWS stack. You must give the data from
`wildtunnel aws-ec2 info`. Copy the values, or copy the command from the
output.

```sh
wildtunnel frpc config \
  --server-addr 1.2.3.4 \
  --base-domain dasbd72.com \
  --token <token>
wildtunnel frpc add openwebui 192.168.50.10:8080
# -> https://openwebui.dasbd72.com
```

- `frpc config`
  - `--server-addr` is the public IP address of the VPS host. This value is required.
  - `--base-domain` is the base domain for the tunnel subdomains. This value is required.
  - `--token` is the frp authentication token. This value is required.
- `frpc add <name> <local-ip>:<local-port>` adds a tunnel for a local service.
  - By default, the command makes an HTTP or HTTPS service available at
    `<name>.<base-domain>`.
  - For a service that is not HTTP, for example WireGuard, give `--proto tcp` or
    `--proto udp` with `--remote-port <port>`. This binds the port that you give on
    the frps server. For example: `frpc add wireguard 127.0.0.1:51820 --proto udp --remote-port 51820`.
    The port must be in the range that you set with `aws-ec2 config --extra-port-range`.
- `frpc list` shows all the tunnels that you configured.
- `frpc del <name>` removes a tunnel that you configured.


**3.2. Run the frpc client on a different machine:**

Give the same data as step 3.1. The `render` command writes an `frpc.toml` to
stdout. It does not save a file and it does not start a service.

```sh
wildtunnel frpc render \
  --server-addr 1.2.3.4 \
  --base-domain dasbd72.com \
  --token <token>
```

## Upgrading from aws-reverse-tunnel

This project was renamed from `aws-reverse-tunnel` to `wildtunnel`. There is no
automatic migration. To keep your existing configuration, move the old config
directory by hand: `mv ~/.config/aws-reverse-tunnel ~/.config/wildtunnel`.

## Cost

The stack runs one `t4g.nano` EC2 host, an 8 GiB gp3 EBS volume, and one
Elastic IP. The values below are on-demand list prices for a 730-hour month.
Treat them as estimates, because AWS prices change.

| Resource | ap-northeast-1 (Tokyo) | us-east-1 |
| --- | --- | --- |
| t4g.nano (730 h) | ~$3.87 | ~$3.07 |
| EBS gp3, 8 GiB | ~$0.77 | ~$0.64 |
| Public IPv4 (Elastic IP) | ~$3.65 | ~$3.65 |
| SSM parameter and KMS | free | free |
| **Total (idle)** | **~$8.30** | **~$7.35** |

Note these points:

- AWS charges $0.005 per hour for every public IPv4 address since 1 February
  2024. This charge applies to the Elastic IP, even when the host runs. The
  Elastic IP is thus the largest cost after the host.
- The table does not include data transfer. This tool is a tunnel, so the
  egress scales with your traffic. AWS gives 100 GB of internet egress each
  month at no cost. After 100 GB, AWS charges about $0.114 per GB in Tokyo and
  about $0.09 per GB in us-east-1.
- The SSM parameter uses the AWS-managed KMS key. This key has no monthly fee.
- A one-year Compute Savings Plan or Reserved Instance lowers the host cost by
  about 30 to 40 percent.

## Development

```sh
uv sync --all-extras
uv run pytest
uv run mypy src tests
uv run pre-commit run --all-files
```

**Layout:**
- `src/wildtunnel/` is the CLI and the generic box installer
  (`server/assets/server-setup.sh`). The installer takes its input from
  environment variables and makes no AWS API calls, so it also runs by hand
  on any Linux VPS.
- `src/wildtunnel/providers/aws_ec2/` holds all AWS-EC2-specific code: the CDK
  stack, the CDK app entry point, the SSM token helpers, and the `aws-ec2`
  CLI group. The package contains the stack, so `aws-ec2 deploy` operates
  without a clone of this repository. A future provider would get its own
  sibling directory here, for example `providers/aws_lightsail/`.

**Known limitation:** The CDK application code makes the frp authentication
token. Thus the plaintext token goes into the synthesized CloudFormation
template. AWS stores the token as a SecureString. However, a person who can
read the stack can see the token. This is acceptable for this personal,
single-account setup.

**License:** MIT. Refer to [LICENSE](LICENSE).
