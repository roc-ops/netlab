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

# VRF-lite launcher for the DHCP daemons. The netlab `vrf` module render
# (netsim/daemons/kea/vrf.j2) writes the VRF name to /run/kea/vrf.<svc> when kea's
# data interface(s) are enslaved into a Linux VRF; the daemon must then open its
# sockets INSIDE that VRF. `ip vrf exec` sets SO_BINDTODEVICE on every socket the
# process opens, which is exactly what kea's fallback/UDP socket needs to bind a
# VRF-local address (a plain bind returns EADDRNOTAVAIL -- the address lives in the
# VRF table, not the default one). No marker -> start normally (unchanged non-VRF
# behavior), so this is transparent for every existing DHCP topology.
cat <<'WRAPFILE' >/usr/local/bin/kea-daemon-start
#!/bin/sh
set -e
svc="$1"                                   # dhcp4 | dhcp6
marker="/run/kea/vrf.$svc"
if [ -s "$marker" ]; then
  exec ip vrf exec "$(cat "$marker")" /usr/sbin/kea-$svc -c /etc/kea/kea-$svc.conf
fi
exec /usr/sbin/kea-$svc -c /etc/kea/kea-$svc.conf
WRAPFILE
chmod +x /usr/local/bin/kea-daemon-start

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
command=/usr/local/bin/kea-daemon-start dhcp4
autorestart=true
priority=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

[program:kea-dhcp6]
command=/usr/local/bin/kea-daemon-start dhcp6
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
