#!/usr/bin/env python3
#
# Render the containerlab topology file and check it is valid YAML.
#
# WHY THIS EXISTS
# tests/topology/ compares the TRANSFORMED DATA MODEL and never renders a provider template, so
# nothing exercised provider/clab/clab.j2 at all. A template that emitted a mapping key inside a
# sequence therefore survived four commits: the data model was perfect, and only containerlab
# refused the result, at deploy time, with an error that points at a YAML line rather than at
# the link that produced it.
#
# The fixtures cover each shape the template renders differently -- a bridge (3+ endpoint) link,
# a two-node p2p link, a stub link, and a multi-provider link -- with and without an explicit
# MTU, because MTU placement is what differs between those branches. One assertion per fixture:
# does the rendered file parse. That is cheap, and it catches the whole class.

import pathlib

import pytest
import yaml

from netsim import augment, providers
from netsim.utils import files as _files
from netsim.utils import log, templates
from netsim.utils import read as _read

FIXTURE_DIR = pathlib.Path(__file__).parent / 'clab_render'
FIXTURES = sorted(FIXTURE_DIR.glob('*.yml'))


def render_clab(fname: str) -> str:
  """Render clab.yml exactly the way providers._Provider.create does, minus the file writing."""
  log.init_log_system(header=False)
  topology = _read.load(fname, relative_topo_name=True, user_defaults=[])
  log.exit_on_error()
  augment.main.transform(topology)
  log.exit_on_error()

  p = providers.get_provider_module(topology, 'clab')
  p.transform(topology)
  search_path = _files.get_search_path(
      'clab', pkg_path_component=p.get_template_path(), topology=topology)
  return templates.render_template(
      data=topology.to_dict(), j2_file=p.get_root_template(), extra_path=search_path)


@pytest.mark.parametrize('fixture', FIXTURES, ids=lambda p: p.stem)
def test_clab_topology_is_valid_yaml(fixture: pathlib.Path) -> None:
  text = render_clab(str(fixture))
  try:
    data = yaml.safe_load(text)
  except yaml.YAMLError as ex:                       # print the render -- the parser's line
    pytest.fail(f'{fixture.name}: rendered clab.yml is not valid YAML: {ex}\n\n{text}')
  assert data['topology']['nodes'], f'{fixture.name}: rendered no nodes'


@pytest.mark.parametrize('fixture', FIXTURES, ids=lambda p: p.stem)
def test_link_mtu_is_a_sibling_of_endpoints(fixture: pathlib.Path) -> None:
  """An MTU must sit beside `endpoints:`, never among its list items.

  Parsing alone would not catch every misplacement -- some wrong shapes still parse, just into
  the wrong structure -- so assert the shape directly on every link that carries one.
  """
  data = yaml.safe_load(render_clab(str(fixture)))
  for link in data['topology'].get('links') or []:
    # Two legitimate shapes: a normal link carries a LIST under `endpoints`, while a stub renders
    # as `type: dummy` with a single `endpoint` MAPPING. Both may carry an mtu beside it.
    if 'endpoints' in link:
      assert isinstance(link['endpoints'], list), \
          f'{fixture.name}: endpoints is {type(link["endpoints"]).__name__}, not a list: {link}'
      for ep in link['endpoints']:
        assert isinstance(ep, str), \
            f'{fixture.name}: endpoint list item is not a string -- a key leaked into the ' \
            f'sequence, which is exactly the bug this guards: {link}'
    else:
      assert isinstance(link.get('endpoint'), dict), \
          f'{fixture.name}: link has neither an endpoints list nor a dummy endpoint: {link}'
    if 'mtu' in link:
      assert isinstance(link['mtu'], int), \
          f'{fixture.name}: mtu is not a scalar beside the endpoints: {link}'
