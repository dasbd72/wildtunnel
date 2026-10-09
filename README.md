# AWS Reverse Tunnel

This tool makes a home-network service available at `<name>.<domain>`. It does not open an inbound port at home.

```
Browser -> DNS (Cloudflare) -> EC2 (Caddy + frps) -> tunnel -> home PC (frpc) -> your app
```

## Setup and Usage

**1. Install the CLI:**

```sh
# On a different machine, install frpc first:
# https://github.com/fatedier/frp/releases
uv tool install 'aws-reverse-tunnel[infra]'
```

**2. Deploy the AWS stack**

The AWS stack contains an EC2 host, frps, Caddy, an Elastic IP, and an IAM role.

```sh
aws-reverse-tunnel infra config --domain dasbd72.com   # The command finds the region.
aws-reverse-tunnel infra deploy
```

- `infra config`
  - `--domain` is the base domain for the tunnel subdomains, for example `dasbd72.com`.
  - `--region` is the AWS region for the stack. The command reads the region from the
    AWS CLI configuration if you do not give it.
  - `--extra-port-range START-END` opens a TCP and UDP port range on the EC2 host and in
    frps. Set this range before you use `frpc add --proto tcp --remote-port` or
    `frpc add --proto udp --remote-port`. The command does not open the range if you do
    not give this option. You must run `infra deploy` to apply the change.
  - `--yes` skips the confirmation prompts.

Record the `ElasticIp` output. You can also run `aws-reverse-tunnel infra status` at any time.

**2b. Point the DNS at the Elastic IP.** An external provider controls the DNS,
for example Cloudflare. Make a wildcard record `*.dasbd72.com` of type `A`.
Point the record at the `ElasticIp` from the previous step. Keep the record
DNS-only. Do not use the Cloudflare proxy (the grey cloud, not the orange
cloud). Make sure that port 80 is open. Caddy then gets a TLS certificate for
each host on demand with Let's Encrypt HTTP-01.

**3.1. Configure the tunnel client and make a service available:**

```sh
aws-reverse-tunnel frpc config   # The command reads the server address, domain, region, and token from step 2.
aws-reverse-tunnel frpc add openwebui 192.168.50.10:8080
# -> https://openwebui.dasbd72.com
```

- `frpc config`
  - `--server-addr` is the public IP address of the EC2 host. The command reads it from
    step 2 if you do not give it.
  - `--base-domain` is the base domain for the tunnel subdomains. The command reads it
    from step 2 if you do not give it.
  - `--region` is the AWS region for the stack. The command reads the region from the
    AWS CLI configuration if you do not give it.
  - `--token` is the frp authentication token. The command reads it from SSM if you run
    it on the same machine as step 2.
- `frpc add <name> <local-ip>:<local-port>` adds a tunnel for a local service.
  - By default, the command makes an HTTP or HTTPS service available at
    `<name>.<base-domain>`.
  - For a service that is not HTTP, for example WireGuard, give `--proto tcp` or
    `--proto udp` with `--remote-port <port>`. This binds a specified port on the frps
    server. For example: `frpc add wireguard 127.0.0.1:51820 --proto udp --remote-port 51820`.
    The port must be in the range that you set with `infra config --extra-port-range`.
- `frpc list` shows all the configured tunnels.
- `frpc del <name>` removes a configured tunnel.


**3.2. Run the frpc client on a different machine:**

```sh
# Get the frpc configuration.
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
- `src/aws_reverse_tunnel/` is the CLI. It includes `infra/`, which is the CDK
  stack. The package contains the stack. Thus `infra deploy` operates without a
  clone of this repository.

**Known limitation:** The CDK application code makes the frp authentication
token. Thus the plaintext token goes into the synthesized CloudFormation
template. AWS keeps the token as a SecureString when stored. But a person who
can read the stack can see the token. This is acceptable for this personal,
single-account setup.

**License:** MIT. Refer to [LICENSE](LICENSE).
