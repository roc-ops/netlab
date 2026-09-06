#
# Tests for the netlab API server: job bookkeeping
#
import pathlib
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
