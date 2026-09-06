#
# Tests for the netlab API server: job bookkeeping and the netlab commands it runs
#
import json
import pathlib
import subprocess
import typing

import pytest

from netsim.cli import api


@pytest.fixture(autouse=True)
def clean_jobs() -> typing.Iterator[None]:
  api.JOBS.clear()
  api.JOB_THREADS.clear()
  yield
  api.JOBS.clear()
  api.JOB_THREADS.clear()


def run_job(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, action: typing.Callable) -> dict:
  monkeypatch.setenv('NETLAB_API_DATA_DIR',str(tmp_path))
  monkeypatch.setattr(api,'run_netlab_action',action)

  job = api.start_job({'action': 'up'})
  api.JOB_THREADS[job['id']].join(10)
  return api.JOBS[job['id']]


def job_log(job: dict) -> str:
  with open(job['logPath'],'r',encoding='utf-8') as fp:
    return fp.read()


# netlab reports a fatal error with sys.exit, which the job runner has to treat as a failure
def test_api_job_fatal_exit_is_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  def fatal_action(payload: dict, log_fp: typing.TextIO) -> None:
    log_fp.write('Fatal error in netlab: cannot start the lab\n')
    raise SystemExit(1)

  job = run_job(monkeypatch,tmp_path,fatal_action)

  assert job['state'] == 'failed'
  assert job['error'] == 'netlab exited with code 1'
  assert job['finishedAt'] is not None
  assert 'cannot start the lab' in job_log(job)


def test_api_job_exit_message_is_the_error(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  def exit_action(payload: dict, log_fp: typing.TextIO) -> None:
    raise SystemExit('lab directory does not exist')

  job = run_job(monkeypatch,tmp_path,exit_action)

  assert job['state'] == 'failed'
  assert job['error'] == 'lab directory does not exist'


# sys.exit() and sys.exit(0) are how a netlab command reports success
def test_api_job_clean_exit_is_success(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  def exit_action(payload: dict, log_fp: typing.TextIO) -> None:
    raise SystemExit(0)

  job = run_job(monkeypatch,tmp_path,exit_action)

  assert job['state'] == 'success'
  assert job['error'] is None
  assert job['finishedAt'] is not None


def test_api_job_exception_is_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  def broken_action(payload: dict, log_fp: typing.TextIO) -> None:
    raise RuntimeError('no such workdir')

  job = run_job(monkeypatch,tmp_path,broken_action)

  assert job['state'] == 'failed'
  assert job['error'] == 'no such workdir'
  assert job['finishedAt'] is not None
  assert 'RuntimeError' in job_log(job)


def netlab_action(
      monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
      payload: dict, returncode: int = 0) -> dict:
  call: dict = {}

  def fake_run(command: list, **kwargs: typing.Any) -> subprocess.CompletedProcess:
    call['command'] = command
    call['kwargs'] = kwargs
    return subprocess.CompletedProcess(command,returncode)

  monkeypatch.setattr(api.subprocess,'run',fake_run)
  with open(tmp_path / 'job.log','w',encoding='utf-8') as log_fp:
    api.run_netlab_action(payload,log_fp)
  return call


# netlab commands run as subprocesses in the job working directory, never in the server process
def test_api_action_up_runs_netlab_up(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  payload = {'action': 'up', 'workdir': str(tmp_path), 'topologyPath': 'topology.yml'}
  call = netlab_action(monkeypatch,tmp_path,payload)

  assert call['command'][1:] == ['up',str(tmp_path.resolve() / 'topology.yml')]
  assert call['kwargs']['cwd'] == tmp_path.resolve()
  # A netlab command asking for a password must fail instead of blocking the job
  assert call['kwargs']['stdin'] == subprocess.DEVNULL


def test_api_action_down_runs_netlab_down(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  payload = {'action': 'down', 'workdir': str(tmp_path), 'cleanup': True}
  call = netlab_action(monkeypatch,tmp_path,payload)

  assert call['command'][1:] == ['down','--cleanup']
  assert call['kwargs']['cwd'] == tmp_path.resolve()


def test_api_action_collect_runs_netlab_collect(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  payload = {
    'action': 'collect',
    'workdir': str(tmp_path),
    'instance': 20,
    'collectOutput': 'config',
    'collectTar': 'lab.tar',
    'collectCleanup': True}
  call = netlab_action(monkeypatch,tmp_path,payload)

  assert call['command'][1:] == ['collect','--instance','20','--output','config','--tar','lab.tar','--cleanup']
  assert call['kwargs']['cwd'] == tmp_path.resolve()


def test_api_action_failure_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  payload = {'action': 'down', 'workdir': str(tmp_path)}
  with pytest.raises(RuntimeError,match='down failed with exit code 1'):
    netlab_action(monkeypatch,tmp_path,payload,1)


def test_api_action_killed_by_signal_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
  payload = {'action': 'down', 'workdir': str(tmp_path)}
  with pytest.raises(RuntimeError,match='down killed by SIGKILL'):
    netlab_action(monkeypatch,tmp_path,payload,-9)


def status_call(monkeypatch: pytest.MonkeyPatch, instance: typing.Optional[str],
                result: subprocess.CompletedProcess) -> tuple:
  calls = []

  def fake_run(command: list, **kwargs: typing.Any) -> subprocess.CompletedProcess:
    calls.append(command)
    return result

  monkeypatch.setattr(api.subprocess,'run',fake_run)
  status_code, reply = api.lab_status(instance,'json')
  return calls[0], status_code, reply


# 'netlab status' chdirs into the lab directory, so the API server has to run it as a subprocess
def test_api_status_runs_netlab_status(monkeypatch: pytest.MonkeyPatch) -> None:
  result = subprocess.CompletedProcess([],0,stdout=json.dumps({'1': {'name': 'lab'}}),stderr='')
  command, status_code, reply = status_call(monkeypatch,None,result)

  assert command[1:] == ['status','--format','json','--all']
  assert status_code == 200
  assert reply == {'1': {'name': 'lab'}}


def test_api_status_instance_runs_netlab_status(monkeypatch: pytest.MonkeyPatch) -> None:
  result = subprocess.CompletedProcess([],0,stdout=json.dumps({'name': 'lab'}),stderr='')
  command, status_code, reply = status_call(monkeypatch,'20',result)

  assert command[1:] == ['status','--format','json','--instance','20']
  assert status_code == 200


# An unknown lab instance is an error in the JSON document, not a non-zero exit code
def test_api_status_unknown_instance_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
  result = subprocess.CompletedProcess([],0,stdout=json.dumps({'error': 'Unknown lab instance 99'}),stderr='')
  status_code, reply = status_call(monkeypatch,'99',result)[1:]

  assert status_code == 404
  assert reply['error'] == 'Unknown lab instance 99'


def test_api_status_failure_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
  result = subprocess.CompletedProcess([],1,stdout='',stderr='Cannot find lab instance 20')
  status_code, reply = status_call(monkeypatch,'20',result)[1:]

  assert status_code == 404
  assert 'Cannot find lab instance 20' in reply['status']
