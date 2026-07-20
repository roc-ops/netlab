#
# PROTOTYPE validation transport: fetch DUT operational state over Ansible
# (instead of `netlab connect`/SSH) so OpenConfig / restricted-CLI NOSes
# (OcNOS cmlsh, ArcOS) can be validated natively.  Peer of suzieq.py.
#
# Design mirrors netsim/cli/validate/suzieq.py::get_result exactly, so a
# `validate:` entry using action `ansible` slots into tests.py alongside
# show/exec/suzieq with no other framework change.
#
import typing
import subprocess

from box import Box

from ...data import get_box
from ...utils import log
from . import report, utils


def get_result(v_entry: Box, n_name: typing.Optional[str], topology: Box, verbosity: int) -> Box:
  node = topology.nodes[n_name]
  # command to run: from the test entry (v_entry.ansible.show) or the device plugin's show_ action
  v_cmd = utils.get_exec_list(v_entry, 'ansible', node, topology)
  err_value = get_box({'_error': True})
  if not v_cmd:
    log.error(f'Test {v_entry.name}: no ansible show command for {n_name}/{node.device}',
              category=log.MissingValue, module='validation')
    return err_value

  # ansible module that runs a show on this device (declared in device settings), e.g.
  #   defaults.devices.ocnos.netlab_validate.ansible_module = ipinfusion.ocnos.ocnos_command
  a_module = topology.defaults.devices[node.device].netlab_validate.ansible_module
  if not a_module:
    log.error(f'Device {node.device} has no netlab_validate.ansible_module; cannot use ansible transport',
              category=log.MissingValue, module='validation')
    return err_value

  # netlab generates ansible.cfg + hosts.yml in the lab dir; run an ad-hoc module against the node.
  cmd = ['ansible', n_name, '-m', a_module, '-a', f"commands=['{v_cmd}']", '--one-line']
  if verbosity >= 3:
    print(f'Preparing to execute {cmd} via ansible')
  try:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    result = out.stdout
  except Exception as ex:                                     # noqa
    report.log_failure(f'Ansible transport failed for "{v_cmd}" on {n_name}', more_data=str(ex), topology=topology)
    return err_value
  if verbosity >= 3:
    print(f'Executed {v_cmd} got {result}')

  # ocnos_command returns JSON with stdout[]; parse to structured _result.
  # Some OcNOS shows emit JSON (`... | json`); where they only emit cmlsh text the device
  # plugin's valid_<test>() screen-scrapes result.stdout (same contract as the `exec` action).
  j = utils.parse_JSON(result)
  if isinstance(j, Exception):
    return get_box({'stdout': result})                       # hand raw text to a text-parsing valid_
  return j
