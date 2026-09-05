from netsim.cli.status import attach_interface_status
from netsim.data import get_box, get_empty_box


def ifstate(ifindex,peer_index,carrier=True):
  return {'ifindex': ifindex, 'kind': 'veth', 'admin': True, 'carrier': carrier,
          'state': 'up' if carrier else 'down', 'peer_index': peer_index}

# s1 renames its netdevs (SONiC), s2 does not, and s3 is a node the provider cannot
# report on
def get_topology():
  return get_box({
    'nodes': {
      's1': {'interfaces': [{'ifname': 'Ethernet0', 'clab': {'name': 'eth1'}},
                            {'ifname': 'Ethernet4', 'clab': {'name': 'eth2'}}]},
      's2': {'interfaces': [{'ifname': 'eth1'}]},
      's3': {'interfaces': [{'ifname': 'eth1'}]} },
    'links': [
      {'linkindex': 1, 'interfaces': [{'node': 's1', 'ifname': 'Ethernet0'}, {'node': 's2', 'ifname': 'eth1'}]},
      {'linkindex': 2, 'interfaces': [{'node': 's1', 'ifname': 'Ethernet4'}, {'node': 's3', 'ifname': 'eth1'}]} ]})

def get_raw():
  return {
    's1': {'eth1': ifstate(2,3), 'eth2': ifstate(4,None,carrier=False)},
    's2': {'eth1': ifstate(3,2)} }

def test_attach_interface_status():
  ls = get_empty_box()
  attach_interface_status(ls,get_topology(),get_raw())

  s1 = ls.nodes.s1.interfaces
  assert list(s1.keys()) == ['Ethernet0','Ethernet4']       # keyed by netlab interface name
  assert s1.Ethernet0.name == 'eth1'                        # container netdev name kept when it differs
  assert s1.Ethernet0.peer == {'node': 's2', 'ifname': 'eth1'}
  assert s1.Ethernet4.peer is None                          # no veth peer, no guessing
  assert s1.Ethernet4.carrier is False and s1.Ethernet4.state == 'down'

  s2 = ls.nodes.s2.interfaces
  assert 'name' not in s2.eth1                              # netdev name equals the netlab name
  assert s2.eth1.peer == {'node': 's1', 'ifname': 'Ethernet0'}   # peer uses netlab names

  assert 'interfaces' not in ls.nodes.s3                    # node the provider cannot report on

  links = {l['linkindex']: l for l in ls.links}
  assert links[1]['state'] == 'up' and links[1]['wired'] is True
  assert links[2]['state'] == 'unknown' and links[2]['wired'] is None
