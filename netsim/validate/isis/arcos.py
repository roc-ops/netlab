"""
ArcOS IS-IS validation over docker-exec + confd_cli.

Same transport as the OSPF/BGP ArcOS plugins: netlab_show_command (devices/arcos.yml ->
clab.group_vars) runs  printf 'show <path> | display json' | confd_cli -C -u admin  over
ansible_connection: docker, so _result is the parsed OpenConfig IS-IS subtree JSON. ArcOS speaks
OpenConfig JSON (not FRR), so these walk the OC tree directly rather than aliasing frr.

These functions take the canonical netlab argument list -- see netsim/validate/isis/frr.py:

  show_isis_neighbor (id)
  valid_isis_neighbor(id,present=True,state='Up',level='',area='')
  show_isis_prefix   (pfx,level='2')
  valid_isis_prefix  (pfx,level='2',af='ipv4',present=True,cost=None)

The validation dispatcher (netsim/cli/validate/plugin.py) evaluates the suite's single `plugin:`
expression against both the show_ and the valid_ function, so a plugin that renames or reorders
those arguments cannot be called by a generic suite at all -- which is exactly how the BGP plugin
next door was broken (see bgp/arcos.py). This file used to declare the first argument as `sysid`
and to accept `level`/`area`/`af`/`cost` only through **kwargs, where they were silently dropped:
`isis_neighbor('dut',level='L2',area='49.0001')` checked neither the level nor the area and passed
regardless. A check that cannot fail is worse than a check that is missing.

Everything below is read from one show path, the per-instance ISIS subtree, which carries the
adjacencies AND the per-level link-state database (~119KB on a two-node lab, measured 2026-09-08).
Asking for the instance rather than the whole network-instance is deliberate, for the reason
spelled out in ospf/arcos.py: the wide path returns 228507 bytes and confd truncates it mid-object
at 228467 every time, so netlab_show_command's parse-retry loop can never succeed.

Measured field shapes (arcos:8.2.1A.P2, live two-node IS-IS lab, 2026-09-08):
  interfaces/interface[]/levels/level[]/adjacencies/adjacency[]/state:
      system-id: 'r2'                 <- the DYNAMIC HOSTNAME, i.e. the netlab node name the
                                         canonical `id` argument carries (not 0000.0000.0002)
      adjacency-state: 'UP'           <- uppercase, where the canonical default is 'Up'
      neighbor-circuit-type: 'LEVEL_2', adjacency-type: 'LEVEL_2'
  levels/level[]/link-state-database/lsp[]:
      lsp-id: 'r1.00-00'
      tlvs/tlv[]/area-addresses/state/address[]: [ '49.0001' ]
      tlvs/tlv[]/extended-ipv4-reachability/prefixes/prefix[]: prefix + state.metric
      tlvs/tlv[]/ipv6-reachability/prefixes/prefix[]:          prefix + state.metric
The adjacency itself carries no area, so the area check reads the neighbour's own LSP.
"""
import typing

from netsim.data import global_vars

from ...utils import routing as _rp_utils

ISIS_INSTANCE: typing.Final[str] = 'i1'   # netlab renders a single default-VRF IS-IS instance named i1


def _local(name: typing.Any) -> str:
  # OpenConfig JSON keys and identityref values arrive module-qualified
  # ('arcos-openconfig-isis-augments:usable', 'openconfig-isis-lsdb-types:AREA_ADDRESSES')
  return str(name).split(':')[-1]


def _collect(obj: typing.Any, pred: typing.Callable[[dict], bool], out: list) -> None:
  if isinstance(obj, dict):
    if pred(obj):
      out.append(obj)
    for v in obj.values():
      _collect(v, pred, out)
  elif isinstance(obj, list):
    for v in obj:
      _collect(v, pred, out)


def _state_match(p_state: typing.Any, state: typing.Any) -> bool:
  # ArcOS reports the adjacency state as an uppercase OpenConfig enum (UP) where the canonical
  # default is FRR's mixed case (Up). Compare case-insensitively instead of changing the default.
  wanted = state if isinstance(state,(list,tuple)) else [ state ]
  return any(str(w).lower() == str(p_state).lower() for w in wanted)


def _levels(value: typing.Any) -> set:
  # 'L1L2' (netlab/FRR), 'LEVEL_1_2' (ArcOS), 2 (a level-number leaf) -> {'1','2'}
  return { c for c in str(value) if c in '12' }


def _adjacencies(_result: typing.Any) -> list:
  adj: list = []
  _collect(_result, lambda d: 'adjacency-state' in d, adj)
  return adj


def _hostname_aliases(_result: typing.Any) -> dict:
  """
  Both spellings of every IS-IS system, keyed both ways: '0000.0000.0002' <-> 'r2'.

  ArcOS reports the neighbour's DYNAMIC HOSTNAME in the adjacency's system-id leaf -- but only once
  it holds that neighbour's LSP. In the window between adjacency-up and LSP receipt the same leaf
  carries the raw system-id, so a suite that checks the adjacency the moment it forms sees
  '0000.0000.0002' where the canonical `id` argument says 'r2' (both measured on the same lab,
  2026-09-08). The IS-IS dynamic-hostname table (an ArcOS augment on the level state) maps them.
  """
  table: list = []
  _collect(_result, lambda d: 'system-id' in d and 'hostname' in d, table)

  alias: dict = {}
  for e in table:
    alias[str(e['system-id'])] = str(e['hostname'])
    alias[str(e['hostname'])] = str(e['system-id'])

  return alias


def _names(name: typing.Any, alias: dict) -> set:
  return { str(name), alias.get(str(name),str(name)) }


def _adj_ids(a: dict, alias: dict) -> set:
  ids: set = set()
  for k in ('system-id','neighbor-sysid','neighbor-hostname'):
    if k in a:
      ids |= _names(a[k],alias)
  return ids


def _adj_level(a: dict) -> typing.Any:
  return a.get('neighbor-circuit-type',a.get('adjacency-type',None))


def _lsp_areas(_result: typing.Any, id: str, alias: dict) -> set:
  # The area of a neighbour is not in the adjacency -- read it from that neighbour's own LSP
  # (lsp-id 'r2.00-00' -> hostname 'r2'), area-addresses TLV.
  lsps: list = []
  _collect(_result, lambda d: isinstance(d.get('lsp-id',None),str), lsps)

  wanted = _names(id,alias)
  areas: set = set()
  for lsp in lsps:
    if str(lsp['lsp-id']).split('.')[0] not in wanted:
      continue
    tlvs: list = []
    _collect(lsp, lambda d: 'area-addresses' in d, tlvs)
    for tlv in tlvs:
      areas.update(str(a) for a in tlv['area-addresses'].get('state',{}).get('address',[]))

  return areas


def show_isis_neighbor(
      id: str,
      present: bool = True,
      state: str = 'Up',
      level: str = '',
      area: str = '', *,
      vrf: str = 'default',
      instance: str = ISIS_INSTANCE,
      **kwargs: typing.Any) -> str:
  # netlab_show_command wraps this: printf 'show <this> | display json' | confd_cli -C -u admin
  # One path carries the adjacencies and the LSDB the area/prefix checks need.
  return f'network-instance {vrf} protocol ISIS {instance}'


def valid_isis_neighbor(
      id: str,
      present: bool = True,
      state: str = 'Up',
      level: str = '',
      area: str = '', *,
      vrf: str = 'default',
      instance: str = ISIS_INSTANCE,
      **kwargs: typing.Any) -> str:
  _result = global_vars.get_result_dict('_result')

  alias = _hostname_aliases(_result)
  adj = [ a for a in _adjacencies(_result) if _names(id,alias) & _adj_ids(a,alias) ]
  if not adj:
    if not present:
      return f'IS-IS neighbor {id} is (correctly) not adjacent'
    seen = sorted({ i for a in _adjacencies(_result) for i in _adj_ids(a,alias) })
    raise Exception(f'No IS-IS neighbor with ID {id}; neighbors seen: {seen}')

  if not present:
    raise Exception(f'Unexpected IS-IS neighbor {id} in state {adj[0].get("adjacency-state")}')

  errors: list = []
  for a in adj:                                       # A neighbour can be adjacent on more than one
    a_err: list = []                                  # interface/level -- one good adjacency is enough
    if state and not _state_match(a.get('adjacency-state',''),state):
      a_err.append(f'Neighbor {id} in unexpected state {a.get("adjacency-state")} (expected {state})')
    if level:
      a_level = _adj_level(a)
      if a_level is None:
        a_err.append(f'Unknown IS-IS level for neighbor {id}')
      elif _levels(a_level) != _levels(level):
        a_err.append(f'Invalid IS-IS level for neighbor {id}: expected {level} found {a_level}')
    if area:
      a_areas = _lsp_areas(_result,id,alias)
      if not a_areas:
        a_err.append(f'Unknown IS-IS area for neighbor {id} (no LSP from {id} in the database)')
      elif str(area) not in a_areas:
        a_err.append(f'Invalid IS-IS area for neighbor {id}: expected {area} found {sorted(a_areas)}')
    if not a_err:
      return (f'ArcOS IS-IS neighbor {id} is {a.get("adjacency-state")}'
              + (f' at level {_adj_level(a)}' if level else '')
              + (f' in area {area}' if area else ''))
    errors += a_err

  raise Exception('; '.join(errors))


def show_isis_prefix(
      pfx: str,
      level: str = '2',
      af: str = 'ipv4',
      present: bool = True,
      cost: typing.Optional[int] = None, *,
      vrf: str = 'default',
      instance: str = ISIS_INSTANCE,
      **kwargs: typing.Any) -> str:
  return f'network-instance {vrf} protocol ISIS {instance}'


def _level_databases(_result: typing.Any, level: str) -> list:
  want = _levels(level)
  scopes: list = []
  _collect(_result, lambda d: 'level-number' in d and 'link-state-database' in d, scopes)
  return [ s for s in scopes if not want or (_levels(s['level-number']) & want) ]


def _isis_prefixes(scope: typing.Any, af: str) -> list:
  """
  (prefix,metric) pairs from the IPv4/IPv6 reachability TLVs within `scope`.

  Matches extended-ipv4-reachability / ipv6-reachability (and the legacy ipv4-*-reachability TLVs)
  by local key name; extended-IS-reachability carries adjacencies, not prefixes, and is excluded by
  the address-family test.
  """
  def is_reach(k: typing.Any) -> bool:
    k = _local(k)
    return af in k and 'reachability' in k

  tlvs: list = []
  _collect(scope, lambda d: any(is_reach(k) for k in d), tlvs)

  out: list = []
  for tlv in tlvs:
    for k, v in tlv.items():
      if not is_reach(k) or not isinstance(v,dict):
        continue
      for p in v.get('prefixes',{}).get('prefix',[]):
        p_state = p.get('state',{})
        out.append((
          p.get('prefix',p_state.get('prefix',p_state.get(f'{af}-prefix',None))),
          p_state.get('metric',None)))

  return out


def _same_prefix(value: typing.Any, pfx: str) -> bool:
  if not isinstance(value,str):
    return False
  try:
    return _rp_utils.get_prefix(value) == pfx
  except ValueError:
    return False


def valid_isis_prefix(
      pfx: str,
      level: str = '2',
      af: str = 'ipv4',
      present: bool = True,
      cost: typing.Optional[int] = None, *,
      vrf: str = 'default',
      instance: str = ISIS_INSTANCE,
      **kwargs: typing.Any) -> str:
  _result = global_vars.get_result_dict('_result')
  pfx = _rp_utils.get_prefix(pfx)

  costs = [ metric for scope in _level_databases(_result,level)
                   for p_value, metric in _isis_prefixes(scope,af)
                   if _same_prefix(p_value,pfx) ]

  if not costs:
    if not present:
      return f'{af} prefix {pfx} is (correctly) not in the level-{level} IS-IS database'
    seen = sorted({ str(p) for scope in _level_databases(_result,level)
                           for p, _ in _isis_prefixes(scope,af) })
    raise Exception(f'{af} prefix {pfx} is not in the level-{level} IS-IS database; seen: {seen}')

  if not present:
    raise Exception(f'{af} prefix {pfx} should not be in the level-{level} IS-IS database')

  if cost is not None and cost not in costs:
    raise Exception(f'Invalid cost for prefix {pfx}: expected {cost} found {costs}')

  return (f'ArcOS has {af} prefix {pfx} in the level-{level} IS-IS database'
          + (f' with metric {cost}' if cost is not None else ''))
