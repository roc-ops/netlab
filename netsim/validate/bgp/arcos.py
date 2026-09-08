"""
ArcOS BGP validation over docker-exec + confd_cli.

ArcOS is a native containerlab container (arrcus_arcos); on this build SSH/NETCONF/gNMI are disabled,
so validation reaches the CLI over the SAME docker-exec + confd_cli path config deployment uses
(ansible_connection: docker). netlab_show_command (devices/arcos.yml -> clab.group_vars) wraps the
returned path as:  printf 'show <path> | display json' | confd_cli -C -u admin  -- so _result is the
parsed OpenConfig BGP subtree JSON.  ArcOS speaks OpenConfig JSON, not FRR, so this cannot alias frr
the way the SONiC container does; the checks below walk the OpenConfig tree directly.

What a device plugin may NOT do is invent its own argument list. The validation dispatcher
(netsim/cli/validate/plugin.py) evaluates the suite's single `plugin:` expression against both the
show_ and the valid_ function, so a device is reachable only if it accepts the canonical call. The
reference implementations are netsim/validate/bgp/frr.py and bgp/eos.py:

  show_bgp_neighbor (ngb,n_id,af='ipv4',*,vrf='default',activate='')
  valid_bgp_neighbor(ngb,n_id,af='ipv4',*,vrf='default',state='Established',activate='',intf='')
  show_bgp_prefix   (pfx,af='ipv4',vrf='default')
  valid_bgp_prefix  (pfx,*,af='ipv4',state='present',vrf='default',<path checks>)

This file used to declare show_bgp_neighbor(peer_as,vrf='default',...) -- peer_as first, vrf second.
The standard call every suite makes, `plugin: bgp_neighbor(node.bgp.neighbors,'r2')`, therefore passed
the neighbour LIST as peer_as and the peer NAME as vrf: the show path became
`network-instance r2 protocol BGP b1`, confd returned nothing, and the test died with
"Failed to parse result output ... as JSON" (measured on a live two-node lab, 2026-09-08). No generic
BGP test could validate an ArcOS device at all.

Both checks read the whole per-instance BGP subtree (network-instance <vrf> protocol BGP <instance>),
which carries neighbor session-state AND the loc-rib / adj-rib routes, so one show path serves both.
Asking for the instance rather than the whole network-instance is deliberate, for the same reason
spelled out in ospf/arcos.py: `show network-instance default | display json` returns 228507 bytes and
confd truncates it mid-object at 228467 every time, so netlab_show_command's parse-retry loop can
never succeed. The instance path returns ~121KB and parses.
"""
import typing

from box import Box, BoxList

from netsim.data import global_vars

from ...utils import routing as _rp_utils
from .. import _common
from . import BGP_PREFIX_NAMES

BGP_INSTANCE: typing.Final[str] = 'b1'   # netlab renders a single default-VRF BGP instance named b1

# netlab address-family keywords -> OpenConfig AFI-SAFI identity (module prefix stripped, see _local)
AF_LOOKUP: typing.Final[dict] = {
  'ipv4' : 'IPV4_UNICAST',
  'ipv6' : 'IPV6_UNICAST',
  'evpn' : 'L2VPN_EVPN',
  'vpnv4': 'L3VPN_IPV4_UNICAST',
  'vpnv6': 'L3VPN_IPV6_UNICAST',
}


def _local(name: typing.Any) -> str:
  # OpenConfig JSON keys and identityref values arrive module-qualified
  # ('openconfig-bgp-types:IPV4_UNICAST', 'arcos-openconfig-rib-bgp-augments:next-hop').
  return str(name).split(':')[-1]


def _leaf(d: typing.Any, name: str) -> typing.Any:
  # Fetch a leaf by its LOCAL name -- ArcOS augments carry a module prefix that upstream leaves do not
  if not isinstance(d,dict):
    return None
  for k, v in d.items():
    if _local(k) == name:
      return v
  return None


def _collect(obj: typing.Any, pred: typing.Callable[[dict], bool], out: list) -> None:
  # Recursively collect every dict in the OpenConfig JSON tree for which pred() is true.
  if isinstance(obj, dict):
    if pred(obj):
      out.append(obj)
    for v in obj.values():
      _collect(v, pred, out)
  elif isinstance(obj, list):
    for v in obj:
      _collect(v, pred, out)


def _state_match(p_state: typing.Any, state: typing.Any) -> bool:
  # ArcOS reports the session state as an uppercase OpenConfig enum (ESTABLISHED) where the canonical
  # default -- and every suite that overrides it -- uses FRR's mixed case (Established, Active, Idle).
  # Compare case-insensitively rather than changing the caller's default. `state` may also be a list
  # (tests/integration/bgp uses state=[ 'Idle','Active' ]).
  wanted = state if isinstance(state,(list,tuple)) else [ state ]
  return any(str(w).lower() == str(p_state).lower() for w in wanted)


def _neighbor_state(_result: typing.Any) -> dict:
  """
  Fold every dict carrying a 'neighbor-address' into one view per neighbour address.

  The subtree reports each neighbour more than once -- neighbors/neighbor[], the state container
  inside it, and the arcos-openconfig-bgp-augments:all-neighbors copy -- and the rib reports the
  same addresses again under its adj-rib containers. Only some of those copies carry the session
  state or the AFI-SAFI list, so the copies are merged instead of picked between.
  """
  entries: list = []
  _collect(_result, lambda d: 'neighbor-address' in d, entries)

  view: dict = {}
  for e in entries:
    n = view.setdefault(str(e['neighbor-address']),{'state': '', 'peer_as': None, 'af': {}})
    s_list: list = []
    _collect(e, lambda d: 'session-state' in d, s_list)
    for s in s_list:
      n['state'] = _local(s['session-state']).upper()
      if 'peer-as' in s:
        n['peer_as'] = s['peer-as']
    af_list: list = []
    _collect(e, lambda d: 'afi-safi-name' in d, af_list)
    for a in af_list:
      n['af'][_local(a['afi-safi-name']).upper()] = a

  return view


def show_bgp_neighbor(
      ngb: list,
      n_id: str,
      af: str = 'ipv4', *,
      vrf: str = 'default',
      activate: str = '',
      instance: str = BGP_INSTANCE,
      **kwargs: typing.Any) -> str:
  # netlab_show_command wraps this: printf 'show <this> | display json' | confd_cli -C -u admin
  #
  # One show path serves every address family: the per-instance subtree carries the whole AFI-SAFI
  # list of every neighbour, so 'activate' needs no second command the way it does on FRR.
  return f'network-instance {vrf} protocol BGP {instance}'


def valid_bgp_neighbor(
      ngb: list,
      n_id: str,
      af: str = 'ipv4', *,
      vrf: str = 'default',
      state: typing.Any = 'Established',
      activate: str = '',
      intf: str = '',
      instance: str = BGP_INSTANCE) -> str:
  _result = global_vars.get_result_dict('_result')
  n_addr = _common.get_bgp_neighbor_id(ngb,n_id,af)

  if n_addr is True:                                  # Unnumbered EBGP neighbor: keyed by interface
    if not intf:
      raise Exception('Need an interface name for an unnumbered EBGP neighbor')
    n_addr = intf

  act_err = f' in address family {activate}' if activate else ''
  if not activate:
    activate = af
  if activate not in AF_LOOKUP:
    raise Exception(f'Unsupported address family {activate}')

  missing = state == 'missing'
  ngb_state = _neighbor_state(_result)

  if str(n_addr) not in ngb_state:
    result = f'The router has no BGP neighbor with {af} address {n_addr} ({n_id}){act_err}'
    if missing:
      return result
    raise Exception(f'{result}; neighbors on the device: {sorted(ngb_state)}')

  n_data = ngb_state[str(n_addr)]
  af_name = AF_LOOKUP[activate]
  if af_name not in n_data['af']:              # AFI-SAFI list of a neighbour == FRR's per-AF peer table
    result = f'The neighbor {n_addr} ({n_id}) has no {activate} address family'
    if missing:
      return result
    raise Exception(f'{result}; address families present: {sorted(n_data["af"])}')

  p_state = n_data['state'] or 'unknown'
  if not _state_match(p_state,state):
    result = f'The neighbor {n_addr} ({n_id}){act_err} is in state {p_state}'
    if missing and not _state_match(p_state,'Established'):
      return result
    raise Exception(f'{result} (expected {state})')

  return f'Neighbor {n_addr} ({n_id}){act_err} is in state {p_state}'


def show_bgp_prefix(
      pfx: str,
      af: str = 'ipv4',
      vrf: str = 'default', *,
      instance: str = BGP_INSTANCE,
      **kwargs: typing.Any) -> str:
  # The whole-instance subtree carries the rib, so the prefix is filtered in valid_bgp_prefix
  return f'network-instance {vrf} protocol BGP {instance}'


def _same_prefix(value: typing.Any, pfx: str) -> bool:
  if not isinstance(value,str):
    return False
  try:
    return _rp_utils.get_prefix(value) == pfx       # run_prefix_checks normalized pfx for us
  except ValueError:
    return False


def get_bgp_prefix(
      pfx: str,
      data: Box,
      af: str = 'ipv4',
      **kwargs: typing.Any) -> typing.Optional[BoxList]:
  """
  Turn the show output into the list of BGP paths for the prefix (the lookup function
  validate/_common.run_prefix_checks calls).

  Read the address family's loc-rib -- the BGP table proper, what FRR's
  `show bgp <af> unicast <pfx> json` returns. The same subtree also carries per-neighbour
  adj-rib-in-post/adj-rib-out copies of the same prefixes; those are NOT the BGP table, and
  matching them would make a prefix we merely advertise look like one we have.
  """
  af_name = AF_LOOKUP.get(af,'')

  def is_rib_af(d: dict) -> bool:                   # The rib's per-AF entry, not a neighbour's AF entry:
    if _local(d.get('afi-safi-name','')).upper() != af_name:
      return False
    return any(isinstance(v,dict) and 'loc-rib' in v for v in d.values())

  af_entries: list = []
  _collect(data,is_rib_af,af_entries)

  hits: list = []
  for entry in af_entries:                          # rib/afi-safis/afi-safi[]/<af-key>/loc-rib/routes/route[]
    for v in entry.values():                        # <af-key> is 'ipv4-unicast', 'ipv6-unicast', ...
      if not isinstance(v,dict) or 'loc-rib' not in v:
        continue
      for route in v['loc-rib'].get('routes',{}).get('route',[]):
        if _same_prefix(route.get('prefix'),pfx):
          hits.append(route)

  return BoxList(hits) if hits else None


def filter_bgp_nh(data: list, value: typing.Any, pfx: str, state: str, **kwargs: typing.Any) -> list:
  # Select the paths with the specified next hop, reporting the ones found (as frr.py does)
  value = str(value).split('/')[0]
  found_nh: list = []
  result: list = []
  for p_element in data:
    nh = _leaf(p_element.get('state',{}),'next-hop')
    if nh is None:
      nh = _leaf(p_element,'next-hop')
    if nh is None:
      continue
    found_nh.append(str(nh))
    if str(nh) == value:
      result.append(p_element)

  if not result and state != 'missing':
    raise Exception(f'The next hop(s) for prefix {pfx} is/are {",".join(found_nh)}, not {value}')

  return result


# The path checks this plugin can answer from the ArcOS loc-rib. Everything else in
# BGP_PREFIX_NAMES (peer, best, clusterid, community, aspath, as_elements, locpref, med) would need
# the attr-set cross-reference and is not implemented here -- valid_bgp_prefix refuses those calls
# rather than letting them pass silently.
ARCOS_PREFIX_CHECKS: typing.Final[dict] = {
  'nh': filter_bgp_nh,
}


def valid_bgp_prefix(
      pfx: str, *,
      af: str = 'ipv4',
      state: str = 'present',
      vrf: str = 'default',
      instance: str = BGP_INSTANCE,
      **kwargs: typing.Any) -> str:
  _result = global_vars.get_result_dict('_result')

  # Reject the path checks this plugin does not implement instead of letting them vanish into
  # **kwargs and report a pass -- a check that cannot fail is worse than one that is missing.
  # run_prefix_checks would reject them too, but with a message ('Invalid prefix check X') that
  # reads like a typo in the suite rather than a gap in the ArcOS plugin.
  unsupported = [ k for k in kwargs if k not in ARCOS_PREFIX_CHECKS ]
  if unsupported:
    raise Exception(
      f'ArcOS BGP prefix validation does not implement the {",".join(sorted(unsupported))} '
      f'path check(s); implemented: {",".join(sorted(ARCOS_PREFIX_CHECKS))}')

  return _common.run_prefix_checks(
            pfx = pfx,
            state = state,
            data = _result,
            kwargs = kwargs,
            table = f'{af} BGP table',
            lookup = get_bgp_prefix,
            checks = ARCOS_PREFIX_CHECKS,
            names = BGP_PREFIX_NAMES,
            af = af,
            vrf = vrf)
