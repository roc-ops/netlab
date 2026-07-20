# Add containerlab (docker-sonic-vs) provider for the sonic device

SONiC is already an upstream netlab device (libvirt). This adds a containerlab provider variant
(sonic_clab) for the community docker-sonic-vs image, plus NATIVE validation.

- netsim/devices/sonic_clab.yml inherits SONiC feature set; config push uses Ansible docker
  connection (docker exec) since docker-sonic-vs ships no running sshd.
- netsim/validate/{,ospf,bgp,isis,route}/sonic_clab.py re-export the FRR validation plugins
  (SONiC-VS runs FRR/vtysh), so netlab up -d sonic_clab <test> --validate runs show commands
  natively over docker-exec. Verified: OSPF adjacency validation passes (Tests passed: 2).
- tests/integration/platform/sonic_clab/02-ospf.yml carries a native validate: section as the pattern.
