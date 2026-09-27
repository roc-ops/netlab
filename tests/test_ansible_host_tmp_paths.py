"""No Ansible task that runs on the CONTROLLER may write to a fixed /tmp path.

netlab's playbooks run on a shared lab box. A task that renders to a fixed name in the
controller's /tmp -- `/tmp/dnos_pe1_initial.cfg` -- collides across users: /tmp is sticky, so the
first user's file (mode 0600) can be replaced by nobody else. Measured 2026-09-27 on netlab-server:
a DNOS lab's `netlab up` failed on all four routers with

    Operation not permitted: '/tmp/.ansible_tmp...dnos_pe1_initial.cfg' -> '/tmp/dnos_pe1_initial.cfg'

because another user's earlier run had left that file behind. Two concurrent labs of ONE user race
on the same name too, and the config is left lying in /tmp afterwards.

A path is only a problem where it lives on the controller. A `template` to /tmp/config.sh over a
docker or paramiko connection lands INSIDE the node, which is the node's own filesystem. So this
test classifies a task as host-side when:

  * the task (or an enclosing block) has `delegate_to: localhost`, or is a `local_action`, or
    sets `connection: local`; or
  * the whole file belongs to a device whose group_vars set `ansible_connection: local`
    unconditionally (dnos), so every task in it runs on the controller.

and fails on any literal `/tmp/` in such a task. Host-side scratch files come from
ansible.builtin.tempfile (unique per run) and are removed in an `always:` block.
"""
import functools
import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
TASKS = REPO / 'netsim' / 'ansible' / 'tasks'
DEVICES = REPO / 'netsim' / 'devices'

# Host-side /tmp paths that are KNOWN and deliberately not changed here. Both are physical
# devices whose push goes through a guardrail; RocContLab's roclab-deploy drift check reads
# arcos.yml's _push resolution, so a change there needs its own reviewed change.
KNOWN_PHYSICAL = {
  'deploy-config/arcos.yml': 'ArcOS hardware push through the guardrail -- separate reviewed change',
  'deploy-config/casa.yml': 'Casa CMTS push through the guardrail -- separate reviewed change',
}

BLOCK_KEYS = ('block', 'rescue', 'always')


@functools.lru_cache(maxsize=None)
def local_connection_devices() -> frozenset:
  """Devices whose TOP-LEVEL group_vars run every task on the controller."""
  devs = set()
  for f in DEVICES.glob('*.yml'):
    data = yaml.safe_load(f.read_text()) or {}
    if (data.get('group_vars') or {}).get('ansible_connection') == 'local':
      devs.add(f.stem)
  return frozenset(devs)


def is_host_side(task: dict) -> bool:
  return (
    task.get('delegate_to') in ('localhost', '127.0.0.1')
    or 'local_action' in task
    or task.get('connection') == 'local')


def strings(value):
  if isinstance(value, str):
    yield value
  elif isinstance(value, dict):
    for v in value.values():
      yield from strings(v)
  elif isinstance(value, list):
    for v in value:
      yield from strings(v)


def walk(tasks, host_side, section=None):
  """Yield (task, host_side, section) for every leaf task, inheriting block delegation."""
  for task in tasks or []:
    if not isinstance(task, dict):
      continue
    here = host_side or is_host_side(task)
    if any(k in task for k in BLOCK_KEYS):
      for k in BLOCK_KEYS:
        yield from walk(task.get(k), here, k if k == 'always' else section)
    else:
      yield task, here, section


def task_files():
  return sorted(p for p in TASKS.rglob('*.yml'))


def host_side_tmp_paths(path: pathlib.Path, local_devs: set) -> list:
  rel = path.relative_to(TASKS).as_posix()
  whole_file = path.parent.name == 'deploy-config' and path.stem in local_devs
  found = []
  for task, host, _ in walk(yaml.safe_load(path.read_text()), whole_file):
    if not host:
      continue
    body = {k: v for k, v in task.items() if k != 'name'}   # a task NAME is not a path
    for s in strings(body):
      if '/tmp/' in s:
        found.append(f'{rel}: {task.get("name", "<unnamed>")!r}: {s.strip()}')
  return found


def test_the_classifier_sees_dnos_as_a_controller_side_device():
  # If this ever stops holding, the whole-file rule below silently checks nothing for dnos.
  assert 'dnos' in local_connection_devices()


@pytest.mark.parametrize('path', task_files(), ids=lambda p: p.relative_to(TASKS).as_posix())
def test_no_host_side_task_uses_a_fixed_tmp_path(path):
  rel = path.relative_to(TASKS).as_posix()
  if rel in KNOWN_PHYSICAL:
    pytest.skip(KNOWN_PHYSICAL[rel])
  assert host_side_tmp_paths(path, local_connection_devices()) == []


@pytest.mark.parametrize('rel', ['deploy-config/dnos.yml', 'vmx/initial.yml'])
def test_host_side_tempfile_is_removed_in_always(rel):
  tasks = list(walk(yaml.safe_load((TASKS / rel).read_text()), False))
  made = [t for t, host, _ in tasks
          if host and ('ansible.builtin.tempfile' in t or 'tempfile' in t)]
  assert made, f'{rel}: no controller-side tempfile -- the path is not unique per run'
  removed = [t for t, host, section in tasks
             if host and section == 'always'
             and (t.get('ansible.builtin.file') or t.get('file') or {}).get('state') == 'absent']
  assert removed, f'{rel}: the per-run file is not removed in an always: block'
