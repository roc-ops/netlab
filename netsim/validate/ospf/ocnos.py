"""
OcNOS OSPFv2 / OSPFv3 validation.

OcNOS cmlsh has no non-interactive exec and its show output is CLI text (not JSON),
so these validators use the *exec* plugin contract: exec_<test> returns the show
command (run over the Ansible validation transport -- see netsim/cli/connect.py
ansible_connect), and valid_<test> screen-scrapes the returned text held in
_result.stdout. Mirrors the FRR/EOS OSPF validators' function signatures so the
stock integration tests (plugin: ospf_neighbor(...), ospf_prefix(...)) work on an
OcNOS device under test.
"""

import ipaddress
import typing

from box import Box

from netsim.data import global_vars


def _text(_result: typing.Any) -> str:
  # The ansible transport hands back {'stdout': '<cli text>'}; be defensive.
  if isinstance(_result, Box) or isinstance(_result, dict):
    out = _result.get('stdout', '')
    return '\n'.join(out) if isinstance(out, list) else str(out)
  return str(_result or '')


# ---------------------------------------------------------------------------
# OSPFv2 / OSPFv3 neighbors
# ---------------------------------------------------------------------------

def exec_ospf_neighbor(id: str, present: bool = True, vrf: str = 'default', bfd: bool = False) -> str:
  try:
    ipaddress.IPv4Address(id)
  except Exception as exc:
    raise Exception(f'OSPF router ID {id} is not a valid IPv4 address') from exc
  scope = '' if vrf == 'default' else f' vrf {vrf}'
  return f'show ip ospf{scope} neighbor'


def valid_ospf_neighbor(id: str, present: bool = True, vrf: str = 'default',
                        bfd: bool = False, proto_name: str = 'OSPFv2') -> str:
  text = _text(global_vars.get_result_dict('_result'))
  # Neighbor rows look like:
  #   10.0.0.2          1   Full/DR          00:00:34    10.1.0.2   eth1   0
  rows = [ln for ln in text.splitlines() if id in ln.split()]
  full = any('Full' in ln for ln in rows)

  if not present:
    if rows:
      raise Exception(f'Unexpected {proto_name} neighbor {id} (state present)')
    return f'{proto_name} neighbor {id} is correctly absent'

  if not rows:
    raise Exception(f'There is no {proto_name} neighbor {id} in VRF {vrf}')
  if not full:
    raise Exception(f'{proto_name} neighbor {id} is not in state Full')
  return f'{proto_name} neighbor {id} is Full'


def exec_ospf6_neighbor(id: str, present: bool = True, vrf: str = 'default',
                        **kwargs: typing.Any) -> str:
  try:
    ipaddress.IPv4Address(id)
  except Exception as exc:
    raise Exception(f'OSPFv3 router ID {id} is not a valid IPv4 address') from exc
  scope = '' if vrf == 'default' else f' vrf {vrf}'
  return f'show ipv6 ospf{scope} neighbor'


def valid_ospf6_neighbor(id: str, present: bool = True, vrf: str = 'default',
                         **kwargs: typing.Any) -> str:
  return valid_ospf_neighbor(id, present=present, vrf=vrf, proto_name='OSPFv3')


# ---------------------------------------------------------------------------
# OSPF prefixes (route present in the OSPF-derived RIB)
# ---------------------------------------------------------------------------

def _pfx_str(pfx: typing.Any) -> str:
  return pfx if isinstance(pfx, str) else str(pfx)


def exec_ospf_prefix(pfx: str, vrf: str = 'default', **kwargs: typing.Any) -> str:
  scope = '' if vrf == 'default' else f' vrf {vrf}'
  return f'show ip route{scope} ospf'


def valid_ospf_prefix(pfx: str, state: str = 'present', **kwargs: typing.Any) -> str:
  text = _text(global_vars.get_result_dict('_result'))
  pfx = _pfx_str(pfx)
  # `show ip route ospf` lists OSPF routes prefixed with 'O'; match the exact prefix token.
  seen = any(pfx in ln for ln in text.splitlines())
  if state in ('missing', 'absent'):
    if seen:
      raise Exception(f'Prefix {pfx} unexpectedly present in the OSPF routing table')
    return f'Prefix {pfx} is correctly absent from the OSPF routing table'
  if not seen:
    raise Exception(f'Prefix {pfx} is not in the OSPF routing table')
  return f'Prefix {pfx} is in the OSPF routing table'


def exec_ospf6_prefix(pfx: str, vrf: str = 'default', **kwargs: typing.Any) -> str:
  scope = '' if vrf == 'default' else f' vrf {vrf}'
  return f'show ipv6 route{scope} ospf'


def valid_ospf6_prefix(pfx: str, state: str = 'present', **kwargs: typing.Any) -> str:
  return valid_ospf_prefix(pfx, state=state)
