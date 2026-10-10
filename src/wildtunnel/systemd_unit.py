from __future__ import annotations

_TEMPLATE = """[Unit]
Description=wildtunnel frpc client
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={frpc_path} -c {config_path}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def render(frpc_path: str, config_path: str) -> str:
    return _TEMPLATE.format(frpc_path=frpc_path, config_path=config_path)
