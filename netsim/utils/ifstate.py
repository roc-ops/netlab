"""
Observed interface state from 'ip -j link' output, veth pairing across containers,
and per-link state derived from both ends.
"""
import json

_STATE = {'UP': 'up', 'DOWN': 'down', 'LOWERLAYERDOWN': 'lowerlayerdown'}

'''
parse_ip_link: turn the JSON printed by 'ip -d -j link' into a netdev-keyed dictionary

Only veth netdevs get a peer index: for them 'link_index' is the index of the other end
of the pair (in another namespace), while macvlan or VLAN netdevs put the index of their
parent netdev in the same attribute. The interface kind is kept for that test alone.
'''
def parse_ip_link(text: str) -> dict[str,dict]:
  result: dict = {}
  for e in json.loads(text or '[]'):
    flags = e.get('flags',[])
    kind = (e.get('linkinfo') or {}).get('info_kind')     # reported by 'ip -d link'
    result[e['ifname']] = {
      'ifindex': e.get('ifindex'),
      'kind': kind,
      'admin': 'UP' in flags,
      'carrier': 'LOWER_UP' in flags,
      'state': _STATE.get(e.get('operstate',''),'unknown'),
      'peer_index': e.get('link_index') if kind == 'veth' else None }

  return result

'''
pair_interfaces: match veth ends across namespaces

Two interfaces are a pair when each one's peer index is the other one's ifindex. The
ifindex map is flat across all nodes because namespaces number their netdevs
independently; the mutual match and the veth-only peer index sort out the collisions.
'''
def pair_interfaces(per_node: dict[str,dict[str,dict]]) -> dict[tuple[str,str],tuple[str,str]]:
  by_index: dict = {}                     # ifindex -> list of (node,netdev,peer_index)
  for node,intfs in per_node.items():
    for name,data in intfs.items():
      if data['peer_index'] is None:
        continue
      by_index.setdefault(data['ifindex'],[]).append((node,name,data['peer_index']))

  pairs: dict = {}
  for node,intfs in per_node.items():
    for name,data in intfs.items():
      candidates = [ c for c in by_index.get(data['peer_index'],[])
                       if c[2] == data['ifindex'] and c[:2] != (node,name) ]
      if len(candidates) == 1:            # exactly one other interface points back at us
        pairs[(node,name)] = (candidates[0][0],candidates[0][1])

  return pairs

'''
derive_links: summarize a topology link from the observed state of its endpoints

The link state comes from the carrier of every end; 'wired' compares the observed peers
with the declared endpoints and is None when no end has a peer -- an end can legitimately
have none (a multi-access link is bridged in the host namespace, an interface can be
attached to a host NIC), and that is not evidence of miswiring.
'''
def derive_links(links: list[dict],node_ifstate: dict[str,dict[str,dict]]) -> list[dict]:
  result = []
  for link in links:
    ends = link.get('interfaces',[])
    obs = [ o for e in ends if (o := node_ifstate.get(e['node'],{}).get(e['ifname'])) is not None ]
    entry = {'linkindex': link.get('linkindex'), 'interfaces': ends}
    if not ends or len(obs) != len(ends):
      entry.update(state='unknown',wired=None)
    else:
      carriers = [ bool(o.get('carrier')) for o in obs ]
      entry['state'] = 'up' if all(carriers) else 'down' if not any(carriers) else 'partial'
      expected = { (e['node'],e['ifname']) for e in ends }
      peered = [ o for o in obs if o.get('peer') is not None ]
      entry['wired'] = None if not peered else all(
        (o['peer']['node'],o['peer']['ifname']) in expected for o in peered)
    result.append(entry)

  return result
