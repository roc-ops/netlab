# syntax=docker/dockerfile:1
# ISC Kea DHCPv4/DHCPv6 + REST control agent, for the netlab 'kea' daemon device.
#
# 'netlab clab build' builds this Dockerfile with an EMPTY build context (no COPY of
# side files -- see netsim/cli/clab/build.py), so every config/script the image needs
# is baked in with heredocs instead of COPY (same trick netscaler/Dockerfile.j2 uses).
# The baked kea-dhcp{4,6}.conf are empty-subnet stubs: netsim/daemons/kea/dhcp.j2
# overwrites them with the lab's real dhcp.pools on every 'netlab initial'.
FROM debian:12-slim
ENV DEBIAN_FRONTEND=noninteractive

LABEL maintainer="Netlab project <netlab.tools>"
LABEL description="ISC Kea DHCP server (DHCPv4 + DHCPv6, REST control agent)"

ARG KEA_REPO=kea-2-6
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates iputils-ping iproute2 procps supervisor \
    && curl -1sLf "https://dl.cloudsmith.io/public/isc/${KEA_REPO}/setup.deb.sh" | bash \
    && apt-get update && apt-get install -y --no-install-recommends \
        isc-kea-dhcp4 isc-kea-dhcp6 isc-kea-ctrl-agent \
        isc-kea-hooks isc-kea-perfdhcp \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/kea /run/kea /var/lib/kea /var/log/kea /etc/supervisor/conf.d

RUN <<'BUILD'
set -e

# Empty-subnet stub -- safe on any topology, replaced by dhcp.j2 on 'netlab initial'
cat <<'FILE' >/etc/kea/kea-dhcp4.conf
{
  "Dhcp4": {
    "interfaces-config": { "interfaces": [ "*" ] },
    "control-socket": {
      "socket-type": "unix",
      "socket-name": "/var/run/kea/kea4-ctrl-socket"
    },
    "hooks-libraries": [
      { "library": "/usr/lib/x86_64-linux-gnu/kea/hooks/libdhcp_lease_cmds.so" },
      { "library": "/usr/lib/x86_64-linux-gnu/kea/hooks/libdhcp_stat_cmds.so" }
    ],
    "lease-database": { "type": "memfile", "persist": true, "name": "/var/lib/kea/kea-leases4.csv" },
    "valid-lifetime": 3600,
    "renew-timer": 900,
    "rebind-timer": 1800,
    "loggers": [ { "name": "kea-dhcp4", "severity": "INFO", "output_options": [ { "output": "stdout" } ] } ],
    "subnet4": []
  }
}
FILE

cat <<'FILE' >/etc/kea/kea-dhcp6.conf
{
  "Dhcp6": {
    "interfaces-config": { "interfaces": [ "*" ] },
    "control-socket": {
      "socket-type": "unix",
      "socket-name": "/var/run/kea/kea6-ctrl-socket"
    },
    "hooks-libraries": [
      { "library": "/usr/lib/x86_64-linux-gnu/kea/hooks/libdhcp_lease_cmds.so" },
      { "library": "/usr/lib/x86_64-linux-gnu/kea/hooks/libdhcp_stat_cmds.so" }
    ],
    "lease-database": { "type": "memfile", "persist": true, "name": "/var/lib/kea/kea-leases6.csv" },
    "valid-lifetime": 3600,
    "renew-timer": 900,
    "rebind-timer": 1800,
    "loggers": [ { "name": "kea-dhcp6", "severity": "INFO", "output_options": [ { "output": "stdout" } ] } ],
    "subnet6": []
  }
}
FILE

cat <<'FILE' >/etc/kea/kea-ctrl-agent.conf
{
  "Control-agent": {
    "http-host": "0.0.0.0",
    "http-port": 8000,
    "control-sockets": {
      "dhcp4": { "socket-type": "unix", "socket-name": "/var/run/kea/kea4-ctrl-socket" },
      "dhcp6": { "socket-type": "unix", "socket-name": "/var/run/kea/kea6-ctrl-socket" }
    },
    "loggers": [ { "name": "kea-ctrl-agent", "severity": "INFO", "output_options": [ { "output": "stdout" } ] } ]
  }
}
FILE

# supervisorctl control socket is needed by dhcp.j2's restart-fallback (used when a
# daemon isn't answering the REST control agent) -- without [unix_http_server] /
# [supervisorctl], supervisorctl has nothing to talk to.
cat <<'FILE' >/etc/supervisor/conf.d/kea.conf
[supervisord]
nodaemon=true
user=root
logfile=/var/log/kea/supervisord.log
pidfile=/run/supervisord.pid

[unix_http_server]
file=/var/run/supervisor.sock
chmod=0700

[supervisorctl]
serverurl=unix:///var/run/supervisor.sock

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[program:kea-dhcp4]
command=/usr/sbin/kea-dhcp4 -c /etc/kea/kea-dhcp4.conf
autorestart=true
priority=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

[program:kea-dhcp6]
command=/usr/sbin/kea-dhcp6 -c /etc/kea/kea-dhcp6.conf
autorestart=true
priority=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

[program:kea-ctrl-agent]
command=/usr/sbin/kea-ctrl-agent -c /etc/kea/kea-ctrl-agent.conf
autorestart=true
priority=20
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
FILE
BUILD

WORKDIR /root
EXPOSE 8000/tcp
CMD [ "/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/kea.conf" ]
