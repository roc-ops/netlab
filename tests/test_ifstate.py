import json

from netsim.utils import ifstate


def ip_link(*entries):
  return json.dumps(list(entries))

def veth(name,ifindex,peer,admin=True,carrier=True,state='UP'):
  flags = ['BROADCAST','MULTICAST']
  if admin: flags.append('UP')
  if carrier: flags.append('LOWER_UP')
  else: flags.append('NO-CARRIER')
  return {'ifindex': ifindex, 'ifname': name, 'flags': flags, 'operstate': state,
          'link_type': 'ether', 'link_index': peer, 'linkinfo': {'info_kind': 'veth'}}

def test_parse_up_and_cut():
  data = ifstate.parse_ip_link(ip_link(
    veth('eth1',2041,2042),
    veth('eth2',2043,2044,carrier=False,state='LOWERLAYERDOWN'),
    {'ifindex': 1, 'ifname': 'lo', 'flags': ['LOOPBACK','UP','LOWER_UP'], 'operstate': 'UNKNOWN', 'link_type': 'loopback'}))
  assert {k: data['eth1'][k] for k in ('ifindex','admin','carrier','state','peer_index')} == {'ifindex': 2041, 'admin': True, 'carrier': True, 'state': 'up', 'peer_index': 2042}
  assert data['eth2']['carrier'] is False and data['eth2']['state'] == 'lowerlayerdown'
  assert data['lo']['peer_index'] is None and data['lo']['state'] == 'unknown'

# A macvlan reports its parent netdev index in the same attribute a veth uses for its
# peer -- only the veth may be paired
def test_parse_peer_index_only_for_veth():
  data = ifstate.parse_ip_link(ip_link(
    veth('eth1',2041,2042),
    {'ifindex': 7, 'ifname': 'macvlan0', 'flags': ['BROADCAST','MULTICAST','UP','LOWER_UP'],
     'operstate': 'UP', 'link_type': 'ether', 'link': 'eno1', 'link_index': 3,
     'linkinfo': {'info_kind': 'macvlan'}}))
  assert data['eth1']['peer_index'] == 2042
  assert data['macvlan0']['peer_index'] is None
  assert data['macvlan0']['carrier'] is True

def test_pair_mutual_only():
  per_node = {
    'a': ifstate.parse_ip_link(ip_link(veth('eth1',2041,2042),veth('eth9',6,11))),
    'b': ifstate.parse_ip_link(ip_link(veth('eth1',2042,2041))),
    'c': ifstate.parse_ip_link(ip_link(veth('eth1',11,99))),   # points at a:eth9 but a:eth9 does not point back
  }
  pairs = ifstate.pair_interfaces(per_node)
  assert pairs[('a','eth1')] == ('b','eth1') and pairs[('b','eth1')] == ('a','eth1')
  assert ('a','eth9') not in pairs and ('c','eth1') not in pairs

# Namespaces number their netdevs independently, so the same ifindex shows up on several
# nodes; a self-referential entry must never pair with itself
def test_pair_duplicate_ifindex_and_self():
  per_node = {
    'a': ifstate.parse_ip_link(ip_link(veth('eth1',2,3),veth('eth2',5,5))),
    'b': ifstate.parse_ip_link(ip_link(veth('eth1',3,2))),
    'c': ifstate.parse_ip_link(ip_link(veth('eth1',2,9))),
  }
  pairs = ifstate.pair_interfaces(per_node)
  assert pairs[('a','eth1')] == ('b','eth1') and pairs[('b','eth1')] == ('a','eth1')
  assert ('c','eth1') not in pairs           # no interface has ifindex 9
  assert ('a','eth2') not in pairs           # points at itself

def test_derive_links_states():
  links = [
    {'linkindex': 1, 'interfaces': [{'node': 'a', 'ifname': 'Ethernet0'}, {'node': 'b', 'ifname': 'eth1'}]},
    {'linkindex': 2, 'interfaces': [{'node': 'a', 'ifname': 'Ethernet4'}, {'node': 'c', 'ifname': 'eth1'}]},
    {'linkindex': 3, 'interfaces': [{'node': 'a', 'ifname': 'Ethernet8'}, {'node': 'd', 'ifname': 'eth1'}]},
    {'linkindex': 4, 'interfaces': [{'node': 'x', 'ifname': 'ge-0/0/0'}, {'node': 'y', 'ifname': 'ge-0/0/0'}]},
  ]
  ifs = {
    'a': {'Ethernet0': {'carrier': True,  'peer': {'node': 'b', 'ifname': 'eth1'}},
          'Ethernet4': {'carrier': True,  'peer': {'node': 'c', 'ifname': 'eth1'}},
          'Ethernet8': {'carrier': True,  'peer': {'node': 'c', 'ifname': 'eth2'}}},   # miswired
    'b': {'eth1': {'carrier': True,  'peer': {'node': 'a', 'ifname': 'Ethernet0'}}},
    'c': {'eth1': {'carrier': False, 'peer': None}, 'eth2': {'carrier': True, 'peer': {'node': 'a', 'ifname': 'Ethernet8'}}},
    'd': {'eth1': {'carrier': True, 'peer': None}},
  }
  out = {l['linkindex']: l for l in ifstate.derive_links(links,ifs)}
  assert out[1]['state'] == 'up' and out[1]['wired'] is True
  assert out[2]['state'] == 'partial' and out[2]['wired'] is True
  assert out[3]['state'] == 'up' and out[3]['wired'] is False
  assert out[4]['state'] == 'unknown' and out[4]['wired'] is None
  assert out[1]['interfaces'] == links[0]['interfaces']

# A multi-access LAN is bridged in the host namespace: the ends are observed, but none of
# them has a veth peer, so the wiring cannot be confirmed or denied
def test_derive_links_lan_without_peers():
  links = [{'linkindex': 10, 'interfaces': [
    {'node': 'a', 'ifname': 'eth1'}, {'node': 'b', 'ifname': 'eth1'}, {'node': 'c', 'ifname': 'eth1'}]}]
  ifs = {
    'a': {'eth1': {'carrier': True, 'peer': None}},
    'b': {'eth1': {'carrier': True, 'peer': None}},
    'c': {'eth1': {'carrier': False, 'peer': None}},
  }
  out = ifstate.derive_links(links,ifs)[0]
  assert out['state'] == 'partial' and out['wired'] is None

  for i in ifs.values():
    i['eth1']['carrier'] = True
  assert ifstate.derive_links(links,ifs)[0]['state'] == 'up'
