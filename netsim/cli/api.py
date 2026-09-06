#!/usr/bin/env python3
#
# netlab api command: lightweight HTTP wrapper for CLI actions
#
import argparse
import base64
import binascii
import datetime as dt
import hmac
import json
import os
import shlex
import signal
import ssl
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, Optional, Tuple

from ..utils import log
from . import external_commands

DEFAULT_DATA_DIR = Path(tempfile.gettempdir()) / "netlab" / "api"

JOB_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}
JOB_THREADS: Dict[str, threading.Thread] = {}
RUN_LOCK = threading.Lock()

AUTH_USER: Optional[str] = None
AUTH_PASSWORD: Optional[str] = None

def now_iso() -> str:
  return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def data_dir() -> Path:
  return Path(os.getenv("NETLAB_API_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()


def log_dir() -> Path:
  path = data_dir() / "logs"
  path.mkdir(parents=True, exist_ok=True)
  return path


def payload_str(payload: Dict[str, Any], key: str) -> Optional[str]:
  value = payload.get(key)
  if not isinstance(value, str):
    return None
  value = value.strip()
  return value or None


def resolve_path(path: str, base: Path) -> Path:
  candidate = Path(path).expanduser()
  if not candidate.is_absolute():
    candidate = base / candidate
  return candidate.resolve()


def parse_basic_auth(header: str) -> Optional[Tuple[str, str]]:
  if not header.startswith("Basic "):
    return None

  encoded = header[6:].strip()
  try:
    decoded = base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")
  except (binascii.Error, UnicodeDecodeError, ValueError):
    return None

  if ":" not in decoded:
    return None

  user, password = decoded.split(":", 1)
  return user, password


def auth_configured() -> bool:
  return AUTH_USER is not None and AUTH_PASSWORD is not None


def send_unauthorized(handler: BaseHTTPRequestHandler) -> None:
  payload = {"error": "unauthorized"}
  data = json.dumps(payload).encode("utf-8")
  handler.send_response(HTTPStatus.UNAUTHORIZED)
  handler.send_header("WWW-Authenticate", 'Basic realm="netlab api"')
  handler.send_header("Content-Type", "application/json")
  handler.send_header("Content-Length", str(len(data)))
  handler.end_headers()
  handler.wfile.write(data)


def require_auth(handler: BaseHTTPRequestHandler) -> bool:
  if not auth_configured():
    return True

  creds = parse_basic_auth(handler.headers.get("Authorization", ""))
  if creds is None:
    send_unauthorized(handler)
    return False

  user, password = creds
  auth_user = AUTH_USER
  auth_password = AUTH_PASSWORD
  if auth_user is None or auth_password is None:
    send_unauthorized(handler)
    return False

  if hmac.compare_digest(user, auth_user) and hmac.compare_digest(password, auth_password):
    return True

  send_unauthorized(handler)
  return False


def workspace_dir(payload: Dict[str, Any]) -> Path:
  base = Path.cwd()
  raw_path = payload_str(payload, "workdir") or payload_str(payload, "workspaceRoot") or str(base)
  return resolve_path(raw_path, base)


def resolve_topology(payload: Dict[str, Any], workdir: Path) -> str:
  topology_url = payload_str(payload, "topologyUrl")
  if topology_url:
    return topology_url
  topology_path = payload_str(payload, "topologyPath")
  if not topology_path:
    for candidate in ("netlab/topology.yml", "netlab/topology.yaml"):
      if (workdir / candidate).exists():
        topology_path = candidate
        break
  if not topology_path:
    raise ValueError("topologyPath or topologyUrl required")
  return str(resolve_path(topology_path, workdir))


def list_templates(template_dir: str) -> List[Dict[str, str]]:
  results: List[Dict[str, str]] = []
  if not template_dir:
    return results

  template_path = Path(template_dir).expanduser()
  if not template_path.is_dir():
    return results

  templates = list(template_path.glob("*.yml")) + list(template_path.glob("*.yaml"))
  for template in sorted(set(templates), key=lambda p: p.name):
    rel = template.relative_to(template_path).as_posix()
    results.append({"name": rel, "path": rel})
  return results


def add_flag(args: List[str], payload: Dict[str, Any], key: str, flag: str) -> None:
  if payload.get(key):
    args.append(flag)


def add_opt(args: List[str], payload: Dict[str, Any], key: str, opt: str) -> None:
  value = payload.get(key)
  if value is None or value == "":
    return
  args += [opt, str(value)]


"""
netlab_command: build the argument list for a netlab command executed as a subprocess

NETLAB_SCRIPT is the netlab executable this server was started from. lab_commands sets it
before it dispatches the command, and external_commands uses it the same way.
"""
def netlab_command(args: List[str]) -> List[str]:
  from . import NETLAB_SCRIPT

  return [NETLAB_SCRIPT] + args


"""
status_args: the 'netlab status' arguments selecting one lab instance or all of them

A status job reports the CLI text and the /status endpoint asks for JSON, but both have to
select the lab instance in the same way.
"""
def status_args(instance: Optional[str]) -> List[str]:
  return ["--instance", instance] if instance else ["--all"]


def action_command(payload: Dict[str, Any], workdir: Path) -> List[str]:
  action = (payload.get("action") or "up").strip().lower()

  def run_up() -> List[str]:
    return ["up", resolve_topology(payload, workdir)]

  def run_create() -> List[str]:
    return ["create", resolve_topology(payload, workdir)]

  def run_down() -> List[str]:
    args: List[str] = ["down"]
    add_flag(args, payload, "cleanup", "--cleanup")
    return args

  def run_collect() -> List[str]:
    args: List[str] = ["collect"]
    add_opt(args, payload, "instance", "--instance")
    add_opt(args, payload, "collectOutput", "--output")
    add_opt(args, payload, "collectTar", "--tar")
    add_flag(args, payload, "collectCleanup", "--cleanup")
    return args

  def run_status() -> List[str]:
    instance = payload.get("instance")
    return ["status"] + status_args(str(instance) if instance else None)

  handlers: Dict[str, Callable[[], List[str]]] = {
    "up": run_up,
    "create": run_create,
    "down": run_down,
    "collect": run_collect,
    "status": run_status,
  }

  try:
    return handlers[action]()
  except KeyError as exc:
    raise ValueError(f"unknown action {action}") from exc


"""
run_netlab_action: run the requested netlab command in the job workdir

The command runs as a subprocess because the working directory belongs to the job, not to
the server: chdir would move every other thread of this process as well. The subprocess
also writes straight into the job log, capturing the output of the programs netlab starts
(Ansible, Vagrant, containerlab) that an in-process stdout redirect cannot see.

The subprocess gets no standard input: a command asking for a password (sudo, Ansible)
must fail instead of waiting forever while the job holds the run lock.
"""
def run_netlab_action(payload: Dict[str, Any], log_fp: IO[str]) -> None:
  workdir = workspace_dir(payload)
  command = netlab_command(action_command(payload, workdir))

  log_fp.write(f"# {shlex.join(command)} (in {workdir})\n")
  log_fp.flush()                                            # The child appends to the same file
  result = subprocess.run(
    command, cwd=workdir, stdin=subprocess.DEVNULL,
    stdout=log_fp, stderr=subprocess.STDOUT, text=True)
  if result.returncode < 0:                                 # A negative code is a killing signal
    signame = signal.Signals(-result.returncode).name
    raise RuntimeError(f"{shlex.join(command)} killed by {signame}")
  if result.returncode:
    raise RuntimeError(f"{shlex.join(command)} failed with exit code {result.returncode}")


"""
exit_reason: the error message for an exception that ended a job, None if it did not fail

netlab reports fatal errors with log.fatal or error_and_exit, both of which call sys.exit,
so a failing action raises SystemExit carrying an exit code or a message instead of an
error. sys.exit() and sys.exit(0) are how a command reports success.
"""
def exit_reason(exc: BaseException) -> Optional[str]:
  if not isinstance(exc, SystemExit):
    return f"{exc}"

  code = exc.code
  if code is None or code == 0:
    return None

  return f"netlab exited with code {code}" if isinstance(code, int) else str(code)


"""
lab_status: run 'netlab status' as a subprocess and return the HTTP status and the reply

netlab status changes the working directory to the lab directory, which would move the
whole server process (and with it any job thread running in another lab), so it may not be
called in-process. A netlab error becomes a 404, as it did when status exited in-process.
"""
def lab_status(instance: Optional[str], o_format: str) -> Tuple[HTTPStatus, Any]:
  args = ["status"]
  if o_format != "text":
    args += ["--format", "json"]
  args += status_args(instance)

  result = subprocess.run(netlab_command(args), capture_output=True, text=True)
  if result.returncode:
    return HTTPStatus.NOT_FOUND, {"status": result.stdout + result.stderr}

  try:
    reply = json.loads(result.stdout)
  except json.JSONDecodeError:
    return HTTPStatus.OK, {"status": result.stdout}

  if isinstance(reply, dict) and "error" in reply:          # Unknown instance: an error document
    return HTTPStatus.NOT_FOUND, reply

  return HTTPStatus.OK, reply


def job_public(job: Dict[str, Any]) -> Dict[str, Any]:
  return {k: v for k, v in job.items() if k != "thread"}


def start_job(payload: Dict[str, Any]) -> Dict[str, Any]:
  job_id = f"job-{int(time.time() * 1000)}-{os.urandom(3).hex()}"
  log_path = str(log_dir() / f"{job_id}.log")
  job = {
    "id": job_id,
    "action": payload.get("action") or "up",
    "state": "queued",
    "createdAt": now_iso(),
    "startedAt": None,
    "finishedAt": None,
    "error": None,
    "workdir": None,
    "logPath": log_path,
  }

  def _runner() -> None:
    with JOB_LOCK:
      if job["state"] == "canceled":
        return
      job["state"] = "running"
      job["startedAt"] = now_iso()

    try:
      job["workdir"] = str(workspace_dir(payload))
      with RUN_LOCK:
        with open(log_path, "w", encoding="utf-8") as log_fp:
          run_netlab_action(payload, log_fp)
      job["state"] = "success"
    except (Exception, SystemExit) as exc:                  # netlab exits on a fatal error
      reason = exit_reason(exc)
      if reason is None:                                    # A clean exit: the action succeeded
        job["state"] = "success"
      else:
        job["state"] = "failed"
        job["error"] = reason
        with open(log_path, "a", encoding="utf-8") as log_fp:
          log_fp.write("\n")
          log_fp.write(traceback.format_exc())

    # Not set in a 'finally': a job that died with the server has not finished
    job["finishedAt"] = now_iso()

  thread = threading.Thread(target=_runner, daemon=True)
  with JOB_LOCK:
    JOBS[job_id] = job
    JOB_THREADS[job_id] = thread
  thread.start()
  return job_public(job)


def parse_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
  length = int(handler.headers.get("Content-Length", "0") or "0")
  if not length:
    return {}
  raw = handler.rfile.read(length)
  if not raw:
    return {}
  try:
    return json.loads(raw.decode("utf-8"))
  except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    raise ValueError("invalid JSON body") from exc

def send_reply(
      handler: BaseHTTPRequestHandler,
      status: int, ctype: str = 'text/plain', reply: bytes = b'') -> None:
  handler.send_response(status)
  handler.send_header("Content-Type", ctype)
  handler.send_header("Content-Length", str(len(reply)))
  handler.end_headers()
  handler.wfile.write(reply)

def send_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
  data = json.dumps(payload).encode("utf-8")
  send_reply(handler,status,'application/json',data)

def send_error(
      handler: BaseHTTPRequestHandler,
      status: int = HTTPStatus.INTERNAL_SERVER_ERROR,
      error: str = 'Failed miserably') -> None:
  send_json(handler,status,{"error": error})

class NetlabHandler(BaseHTTPRequestHandler):
  def log_message(self, format: str, *args: Any) -> None:
    return

  def _not_found(self) -> None:
    send_error(self, HTTPStatus.NOT_FOUND,"not found")

  def do_GET(self) -> None:
    if not require_auth(self):
      return
    parsed = urllib.parse.urlparse(self.path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not parts:
      return self._not_found()

    def get_healthz() -> None:
      send_json(self, HTTPStatus.OK, {"status": "ok"})

    def get_templates() -> None:
      query = urllib.parse.parse_qs(parsed.query)
      template_dir = query.get("dir", [""])[0]
      send_json(self, HTTPStatus.OK, {"templates": list_templates(template_dir)})

    def get_status(*parts: Any) -> None:
      query = urllib.parse.parse_qs(parsed.query)
      o_format_qs = query.get("output")
      if not o_format_qs:
        o_format = os.environ.get("NETLAB_API_STATUS_OUTPUT","json")
      else:
        o_format = o_format_qs[0]

      try:
        status_code, reply = lab_status(parts[0] if parts else None, o_format)
        send_json(self, status_code, reply)
      except Exception as ex:
        send_error(self, HTTPStatus.INTERNAL_SERVER_ERROR, str(ex))

    def get_jobs(job_id: Optional[str] = None, fmt: Optional[str] = None, *args: Any) -> None:
      if args:
        return self._not_found()

      if not job_id:
        with JOB_LOCK:
          jobs = [job_public(j) for j in JOBS.values()]
        return send_json(self, HTTPStatus.OK, {"jobs": jobs})

      with JOB_LOCK:
        job = JOBS.get(job_id)

      if job is None:
        return send_error(self,HTTPStatus.NOT_FOUND,f'Job {job_id} not found')

      if not fmt:
        return send_json(self, HTTPStatus.OK, job_public(job))
      if fmt == "log":
        try:
          with open(job["logPath"], "r", encoding="utf-8") as fp:
            content = fp.read()
        except FileNotFoundError:
          content = ""
        return send_json(self, HTTPStatus.OK, {"log": content})
      else:
        return send_error(self,HTTPStatus.NOT_IMPLEMENTED,f'Invalid parameter {fmt}')

    simple_handlers: Dict[str, Callable] = {
      "healthz": get_healthz,
      "templates": get_templates,
    }

    path_handlers: Dict[str, Callable] = {
      "jobs": get_jobs,
      "status": get_status,
    }

    key = parts.pop(0)
    if key in simple_handlers and not parts:
      return simple_handlers[key]()
    elif key in path_handlers:
      return path_handlers[key](*parts)
    else:
      return self._not_found()

  def do_POST(self) -> None:
    if not require_auth(self):
      return
    parsed = urllib.parse.urlparse(self.path)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if parts == ["jobs"]:
      try:
        payload = parse_json_body(self)
        job = start_job(payload)
      except ValueError as exc:
        send_json(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        return
      send_json(self, HTTPStatus.ACCEPTED, job)
      return

    if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
      job_id = parts[1]
      with JOB_LOCK:
        job_entry: Optional[Dict[str, Any]] = JOBS.get(job_id)
        if job_entry is None:
          return self._not_found()
        if job_entry["state"] != "queued":
          send_json(self, HTTPStatus.CONFLICT, {"error": "cannot cancel running or finished job"})
          return
        job_entry["state"] = "canceled"
        job_entry["finishedAt"] = now_iso()
      send_json(self, HTTPStatus.OK, job_public(job_entry))
      return

    self._not_found()


def api_parse_args() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="netlab API server")
  parser.add_argument(
    "--bind",
    default=os.getenv("NETLAB_API_BIND", external_commands.get_local_addr()),
    help="Bind address (NETLAB_API_BIND)",
  )
  parser.add_argument(
    "--port",
    type=int,
    default=int(os.getenv("NETLAB_API_PORT", "8090")),
    help="Listen port (NETLAB_API_PORT)",
  )
  parser.add_argument(
    "--auth-user",
    default=os.getenv("NETLAB_API_USER", ""),
    help="Basic auth username (NETLAB_API_USER)",
  )
  parser.add_argument(
    "--auth-password",
    default=os.getenv("NETLAB_API_PASSWORD", ""),
    help="Basic auth password (NETLAB_API_PASSWORD)",
  )
  parser.add_argument(
    "--tls-cert",
    default=os.getenv("NETLAB_API_TLS_CERT", ""),
    help="TLS certificate path (NETLAB_API_TLS_CERT)",
  )
  parser.add_argument(
    "--tls-key",
    default=os.getenv("NETLAB_API_TLS_KEY", ""),
    help="TLS private key path (NETLAB_API_TLS_KEY)",
  )
  return parser


def run_api(cli_args: List[str]) -> None:
  parser = api_parse_args()
  args = parser.parse_args(cli_args)
  auth_user = args.auth_user.strip() or None
  auth_password = args.auth_password.strip() or None
  if (auth_user is None) != (auth_password is None):
    log.fatal("Basic auth requires both user and password")
  global AUTH_USER, AUTH_PASSWORD
  AUTH_USER = auth_user
  AUTH_PASSWORD = auth_password

  tls_cert = args.tls_cert.strip() or None
  tls_key = args.tls_key.strip() or None
  if (tls_cert is None) != (tls_key is None):
    log.fatal("TLS requires both --tls-cert and --tls-key")
  server = ThreadingHTTPServer((args.bind, args.port), NetlabHandler)
  if tls_cert and tls_key:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=tls_cert, keyfile=tls_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    http_proto = 'https'
  else:
    http_proto = 'http'
  log.section_header("Starting", f"netlab API on {http_proto}://{args.bind}:{args.port}")
  server.serve_forever()

def run(cli_args: List[str]) -> None:
  try:
    run_api(cli_args)
  except KeyboardInterrupt:
    print()
    log.info('Exiting the API server')
