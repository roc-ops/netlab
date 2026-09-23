"""`netlab status` and lab-instance lookup must read the status file the user's defaults name.

`netlab up` loads user defaults (~/.netlab.yml, ./topology-defaults.yml) and so records a lab in
whatever `defaults.lab_status_file` they set. `netlab status` and change_lab_instance() (used by
`netlab down --instance`) loaded system defaults only, so with a shared status file configured they
read ~/.netlab/status.yaml instead -- a lab could be started where they would never find it.
"""
import json
import os

from netsim.cli import change_lab_instance
from netsim.cli import status as status_cli


def board_in_user_defaults(tmp_path, monkeypatch):
  home = tmp_path / 'home'
  home.mkdir()
  labdir = tmp_path / 'lab'
  labdir.mkdir()
  board = tmp_path / 'board' / 'status.yaml'
  board.parent.mkdir()
  board.write_text(f'191:\n  dir: {labdir}\n  status: started\n  providers: [clab]\n')
  (home / '.netlab.yml').write_text(f'lab_status_file: {board}\n')
  monkeypatch.setenv('HOME', str(home))
  work = tmp_path / 'work'                                  # no ./topology-defaults.yml here
  work.mkdir()
  monkeypatch.chdir(work)
  return labdir


def test_status_all_reads_the_status_file_named_in_user_defaults(tmp_path, monkeypatch, capsys):
  board_in_user_defaults(tmp_path, monkeypatch)
  status_cli.run(['--all', '--format', 'json'])
  labs = json.loads(capsys.readouterr().out)
  assert '191' in labs


def test_change_lab_instance_finds_a_lab_in_the_status_file_named_in_user_defaults(tmp_path, monkeypatch):
  labdir = board_in_user_defaults(tmp_path, monkeypatch)
  change_lab_instance(191, quiet=True)
  assert os.getcwd() == str(labdir)
