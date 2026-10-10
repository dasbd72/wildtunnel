# Wildtunnel

Wildtunnel makes a home-network service available at `<name>.<domain>`. Wildtunnel does not open an inbound port on your home network.

```
Browser -> DNS (Cloudflare) -> VPS (Caddy + frps) -> tunnel -> home PC (frpc) -> your app
```

Wildtunnel puts the tunnel server on a VPS. Wildtunnel supports two providers: AWS EC2 and AWS Lightsail. You deploy the AWS EC2 provider with CDK. You deploy the AWS Lightsail provider with direct API calls. The AWS Lightsail provider does not need the CDK toolchain.

The code separates the generic server setup (frps and Caddy) from each provider's deploy logic. This separation lets you add a new provider without a rewrite of the existing code.

## Setup and usage

### Step 1: Install the CLI

Before you install wildtunnel, install frpc on a different machine. Get frpc from [github.com/fatedier/frp/releases](https://github.com/fatedier/frp/releases).

To install wildtunnel for the AWS EC2 provider, run this command:

```sh
uv tool install 'wildtunnel[aws-ec2]'
```

To install wildtunnel for the AWS Lightsail provider, run this command. This provider does not need an extra, because it does not use the CDK toolchain.

```sh
uv tool install wildtunnel
```

### Step 2: Deploy the server

Select one provider: AWS EC2 or AWS Lightsail.

#### Step 2a: Deploy to AWS EC2

The AWS EC2 provider creates these resources: an EC2 host, frps, Caddy, an Elastic IP address, and an IAM role. Wildtunnel deploys these resources with CDK.

Run these commands to configure and deploy the stack. The `config` command reads the AWS region from your AWS CLI configuration.

```sh
wildtunnel aws-ec2 config --domain dasbd72.com
wildtunnel aws-ec2 deploy
```

Options for the `aws-ec2 config` command:

- `--domain`: the base domain for the tunnel subdomains. Example: `dasbd72.com`.
- `--region`: the AWS region for the stack. If you do not give this option, the command reads the region from your AWS CLI configuration.
- `--extra-port-range START-END`: opens a TCP port range and a UDP port range on the EC2 host and in frps. Set this option before you use `frpc add --proto tcp --remote-port` or `frpc add --proto udp --remote-port`. If you do not give this option, the command does not open a port range. If you change this option, run `wildtunnel aws-ec2 deploy` again to apply the change.
- `--yes`: skips the confirmation prompts.

Record the `ElasticIp` value from the deploy output. To check the stack status at a later time, run this command:

```sh
wildtunnel aws-ec2 status
```

To get the data for the frpc client, run this command:

```sh
wildtunnel aws-ec2 info
```

This command shows the server address, the base domain, and the token. This command also shows a ready `frpc config` command.

#### Step 2b: Deploy to AWS Lightsail

The AWS Lightsail provider creates these resources: a `nano_3_0` Lightsail instance, frps, Caddy, and a static IP address. Wildtunnel deploys these resources with direct Lightsail API calls. This provider does not use CDK or CloudFormation.

Run these commands to configure and deploy the instance. The `config` command reads the AWS region from your AWS CLI configuration.

```sh
wildtunnel aws-lightsail config --domain dasbd72.com
wildtunnel aws-lightsail deploy
```

The `aws-lightsail config` command has the same options as the `aws-ec2 config` command: `--domain`, `--region`, `--extra-port-range`, and `--yes`.

To check the instance status at a later time, run this command:

```sh
wildtunnel aws-lightsail status
```

Lightsail instances cannot have an IAM role attached. For this reason, wildtunnel writes the frp token directly into the instance user data at deploy time. Unlike the AWS EC2 provider, wildtunnel does not fetch the token from SSM at boot time.

Because of this, the `wildtunnel aws-lightsail info` command works only on the machine that ran the `deploy` command. Wildtunnel does not store the token on a remote server.

To get the data for the frpc client, run this command:

```sh
wildtunnel aws-lightsail info
```

If you run `aws-lightsail deploy` again on a running instance, the command only syncs the static IP address and the firewall rules. For example, run `deploy` again after you change the `--extra-port-range` option. Lightsail cannot run cloud-init again on an existing instance. For this reason, the `deploy` command never installs frps and Caddy again on an existing instance.

To check for firewall rule differences, run this command:

```sh
wildtunnel aws-lightsail diff
```

To force a new installation of frps and Caddy, run the `destroy` command. Then run the `deploy` command again.

#### Step 2c: Point your DNS at the server address

Get the server address. Use the `ElasticIp` value from `aws-ec2 info`, or the static IP address from `aws-lightsail info`.

Go to your DNS provider, for example Cloudflare. Add a wildcard DNS record: `*.dasbd72.com`, type `A`. Point this record at the server address.

Keep this DNS record in DNS-only mode. Do not enable the Cloudflare proxy (the orange cloud icon). Use the grey cloud icon instead.

Make sure that port 80 is open on your server. Caddy needs port 80 to get a TLS certificate for each host. Caddy gets each certificate on demand, through the Let's Encrypt HTTP-01 method.

### Step 3: Configure the frpc client

#### Step 3.1: Add a service

The frpc client does not read data from the deployed server automatically. You must give this data manually. Get this data from the `wildtunnel aws-ec2 info` command or the `wildtunnel aws-lightsail info` command. Copy the values, or copy the full command from the output.

Run these commands to configure the client and add a service:

```sh
wildtunnel frpc config \
  --server-addr 1.2.3.4 \
  --base-domain dasbd72.com \
  --token <token>
wildtunnel frpc add openwebui 192.168.50.10:8080
# Result: https://openwebui.dasbd72.com
```

Options for the `frpc config` command:

- `--server-addr`: the public IP address of the server. This option is mandatory.
- `--base-domain`: the base domain for the tunnel subdomains. This option is mandatory.
- `--token`: the frp authentication token. This option is mandatory.

Use `frpc add <name> <local-ip>:<local-port>` to add a tunnel for a local service. By default, this command makes an HTTP or HTTPS service available at `<name>.<base-domain>`.

To add a service that does not use HTTP, for example WireGuard, add the `--proto tcp` option or the `--proto udp` option. Also add the `--remote-port <port>` option. This option sets the port on the frps server. Example:

```sh
frpc add wireguard 127.0.0.1:51820 --proto udp --remote-port 51820
```

The port number must be in the range that you set with the `aws-ec2 config --extra-port-range` option.

Use `frpc list` to show all configured tunnels.

Use `frpc del <name>` to remove a configured tunnel.

#### Step 3.2: Run frpc on a different machine

Use the same data as in step 3.1.

The `render` command writes an `frpc.toml` file to the standard output. This command does not save a file. This command does not start a service.

```sh
wildtunnel frpc render \
  --server-addr 1.2.3.4 \
  --base-domain dasbd72.com \
  --token <token>
```

## Upgrade from aws-reverse-tunnel

The developer renamed this project from `aws-reverse-tunnel` to `wildtunnel`. There is no automatic migration procedure.

To keep your existing configuration, move the configuration directory manually:

```sh
mv ~/.config/aws-reverse-tunnel ~/.config/wildtunnel
```

## Cost

### AWS EC2 cost

The AWS EC2 stack runs these resources: one `t4g.nano` EC2 host, one 8 GiB gp3 EBS volume, and one Elastic IP address.

The table below shows on-demand list prices for a 730-hour month. These values are estimates. AWS prices can change.

| Resource | ap-northeast-1 (Tokyo) | us-east-1 |
| --- | --- | --- |
| t4g.nano (730 h) | ~$3.87 | ~$3.07 |
| EBS gp3, 8 GiB | ~$0.77 | ~$0.64 |
| Public IPv4 (Elastic IP) | ~$3.65 | ~$3.65 |
| SSM parameter and KMS | free | free |
| **Total (idle)** | **~$8.30** | **~$7.35** |

Notes on the AWS EC2 cost table:

- AWS started to charge $0.005 per hour for each public IPv4 address on 1 February 2024. This charge applies to the Elastic IP address, even while the host runs. For this reason, the Elastic IP address is the largest cost after the host cost.
- This table does not include data transfer cost. Wildtunnel is a tunnel tool, so the egress cost increases with your traffic. AWS gives 100 GB of free internet egress each month. After 100 GB, AWS charges about $0.114 per GB in Tokyo and about $0.09 per GB in us-east-1.
- The SSM parameter uses the AWS-managed KMS key. This key has no monthly fee.
- A one-year Compute Savings Plan or a Reserved Instance can lower the host cost by 30 to 40 percent.

### AWS Lightsail cost

The `nano_3_0` bundle costs a flat $5 per month. Check the current price at [aws.amazon.com/lightsail/pricing](https://aws.amazon.com/lightsail/pricing/).

This price includes the instance, the SSD storage, and a monthly data-transfer allowance. Unlike the AWS EC2 cost model, the AWS Lightsail cost model has no separate EBS charge and no per-GB egress charge. The AWS Lightsail cost model also has no $0.005 per hour public IP address charge. A Lightsail static IP address is free while it stays attached to a running instance.

This simpler pricing has two limits. First, the instance size is fixed at 0.5 GiB RAM, the same RAM size as the EC2 `t4g.nano` instance. Second, AWS does not offer a Savings Plan discount or a Reserved Instance discount for Lightsail.

## Development

Run these commands to set up and test the development environment:

```sh
uv sync --all-extras
uv run pytest
uv run mypy src tests
uv run pre-commit run --all-files
```

### Code layout

- `src/wildtunnel/`: the CLI code and the generic server installer script (`server/assets/server-setup.sh`). This script reads its input from environment variables. This script makes no AWS API calls. For this reason, you can also run this script manually on any Linux VPS.
- `src/wildtunnel/providers/aws_ec2/`: all AWS EC2 code. This includes the CDK stack, the CDK application entry point, the SSM token helper functions, and the `aws-ec2` CLI command group. This package includes a copy of the stack code. For this reason, the `aws-ec2 deploy` command does not need a clone of this repository.
- `src/wildtunnel/providers/aws_lightsail/`: all AWS Lightsail code. This includes the `aws-lightsail` CLI command group and the direct boto3 API calls. This provider does not use CDK, because Lightsail has no useful CDK L2 constructs. Also, Lightsail is simpler than EC2 and CloudFormation together, by design.

### Add a new provider

`scripts/new_provider.py` is a script for maintainers only. The installed package does not include this script. The `wildtunnel` CLI does not call this script.

This script generates two directories: `providers/<name>/` and `tests/providers/<name>/`. These directories copy the structure of `providers/aws_ec2/`: a Click command group, a configuration dataclass, and matching test files. After the script runs, the script prints the remaining manual steps.

If the directory `providers/<name>/` already exists, the script stops. The script does not change any files in this case.

Run the script like this:

```sh
uv run python scripts/new_provider.py hetzner
```

## Known limitations

- The CDK application code creates the frp authentication token. For this reason, the plaintext token is part of the generated CloudFormation template. AWS stores this template parameter as a SecureString value. But a person with read access to the stack can still see the token value. This risk is acceptable for a personal, single-account setup.
- Lightsail instances cannot have an IAM role attached. For this reason, wildtunnel writes the frp token directly into the instance user data at deploy time. Wildtunnel does not fetch the token from SSM, unlike the AWS EC2 provider. A person who can call the `lightsail:GetInstance` API operation can see the token in the user data. Also, the `wildtunnel aws-lightsail info` command works only on the machine that ran the `deploy` command. Wildtunnel does not store this token on a remote server.

## License

This project uses the MIT license. See the [LICENSE](LICENSE) file for the full license text.
