#!/usr/bin/env python3
"""Deploy a rendered DNOS configuration file to a DriveNets box.

DNOS cannot be driven over the SSH exec channel: the CLI rejects commands passed as ssh
arguments and takes ~10s to load, so this drives an interactive session. Several commands
prompt for confirmation ("request file delete", and "commit" when another session committed
first); a transport that ignores prompts hangs or silently does nothing, so every prompt is
matched explicitly.

Deployment is transactional:
    scp <cfg> -> /config/<name>      (there is no sftp subsystem on DNOS)
    configure
    load merge <name>
    show config compare             -- the real candidate diff, always captured
    commit check                    -- validates WITHOUT applying
    commit | rollback 0             -- apply, or discard in --dry-run

--dry-run stops after "commit check" and discards the candidate, so it can be run against
production hardware without changing anything.
"""
import argparse, os, re, subprocess, sys

import pexpect

PROMPT_OPER = r"[\w.-]+# "
PROMPT_CFG  = r"[\w.-]+\(cfg\)# "
CONFIRM     = r"\(yes/no\)\s*\[no\]\?"
# The DNOS CLI paginates. Show commands below use "| no-more", but match the pager too as a
# safety net: an unmatched "-- More --" hangs the session until timeout, and the timeout
# path used to exit WITHOUT rolling back, stranding a loaded candidate in an open config
# session on the router.
PAGER       = r"-- (More|End) -- \(Press q to quit\)"
ERROR_PAT   = re.compile(r"ERROR|Invalid command|Unknown word|failed|Failed", re.I)
NOOP_PAT    = re.compile(r"no configuration changes were made", re.I)


class DeployError(Exception):
  """Anything that should abort the deploy.

  Raised rather than calling sys.exit() so the caller can GUARANTEE cleanup. sys.exit raises
  SystemExit, which is a BaseException and not an Exception, so an "except Exception" cleanup
  handler does not catch it -- the candidate is then left loaded in an open config session on
  the router, which is the exact outcome the cleanup exists to prevent.
  """



def scp_config(host: str, user: str, password: str, src: str, remote_name: str) -> None:
  cmd = [ "sshpass","-p",password,"scp","-O",
          "-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null",
          "-o","LogLevel=ERROR", src, f"{user}@{host}:/config/{remote_name}" ]
  r = subprocess.run(cmd,capture_output=True,text=True,timeout=120)
  if r.returncode:
    sys.exit(f"dnos-deploy: scp of {src} failed: {r.stderr.strip() or r.stdout.strip()}")


def send(child, line: str, expect_pat: str, timeout: int = 120) -> str:
  """Send a line, absorbing any confirmation prompt before the expected prompt returns."""
  child.sendline(line)
  out = ""
  while True:
    i = child.expect([ CONFIRM, PAGER, expect_pat, pexpect.TIMEOUT, pexpect.EOF ], timeout=timeout)
    out += child.before or ""
    if i == 0:                      # a confirmation prompt -- answer it, keep reading
      child.sendline("yes")
      continue
    if i == 1:                      # the pager -- page through rather than hang
      child.send(" ")
      continue
    if i == 2:
      return out
    raise DeployError(f"timeout/EOF waiting for prompt after {line!r}\n--- got ---\n{out}")


def discard_candidate(child) -> None:
  """Best effort: discard the candidate and leave config mode. Never raises.

  Called from the failure path, so it must not be able to fail in a way that skips the rest of
  itself -- hence the per-step guards on BaseException rather than one try around the lot.
  """
  for step in ( lambda: send(child,"rollback 0",PROMPT_CFG,timeout=60),
                lambda: child.sendline("exit"),
                lambda: child.close(force=True) ):
    try:
      step()
    except BaseException:                        # noqa: BLE001 - cleanup is best effort
      pass


def config_session(child, a, remote: str) -> str:
  """Everything that happens inside configuration mode.

  Raises DeployError on any failure; the caller guarantees the candidate is discarded. Returns
  the line to print on success.
  """
  load = send(child,f"load merge {remote}",PROMPT_CFG)
  if ERROR_PAT.search(load):
    raise DeployError(f"load merge failed\n{load}")

  diff = send(child,"show config compare | no-more",PROMPT_CFG)
  if ERROR_PAT.search(diff):
    raise DeployError(f"could not read the candidate diff\n{diff}")
  print("--- candidate diff ---")
  print(diff.strip())

  chk = send(child,"commit check",PROMPT_CFG)
  # A no-op deploy is a success, not a failure: DNOS answers an empty candidate with
  # "commit action is not applicable", which is a NOTICE rather than an error.
  if NOOP_PAT.search(chk):
    send(child,"rollback 0",PROMPT_CFG)
    return "--- nothing to do: candidate is empty, running config already matches ---"
  if "passed successfully" not in chk:
    raise DeployError(f"commit check FAILED\n{chk}")

  if a.dry_run:
    send(child,"rollback 0",PROMPT_CFG)
    return "--- dry run: commit check passed, candidate discarded ---"

  out = send(child,"commit",PROMPT_CFG)
  if "Commit succeeded" not in out:
    raise DeployError(f"commit FAILED\n{out}")
  return "--- commit succeeded ---"


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--host",required=True)
  ap.add_argument("--user",default="dnroot")
  ap.add_argument("--password-file",required=True)
  ap.add_argument("--config",required=True,help="rendered DNOS config file")
  ap.add_argument("--name",default=None,help="remote file name under /config/")
  ap.add_argument("--dry-run",action="store_true",help="commit check only, then discard")
  a = ap.parse_args()

  password = open(os.path.expanduser(a.password_file)).read().strip()
  remote   = a.name or f"netlab-{os.path.basename(a.config)}"
  scp_config(a.host,a.user,password,a.config,remote)

  child = pexpect.spawn(
    "sshpass",[ "-p",password,"ssh","-tt",
                "-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=/dev/null",
                "-o","LogLevel=ERROR",f"{a.user}@{a.host}" ],
    encoding="utf-8",timeout=180)
  child.expect(PROMPT_OPER,timeout=180)          # wait out "DRIVENETS CLI Loading..."

  try:
    # "configure" belongs inside the guard too. Outside it, a timeout entering configuration
    # mode escaped as an uncaught DeployError traceback instead of a clean one-line reason.
    # No candidate exists at that point, so nothing could be stranded -- but a traceback is
    # still the wrong thing to hand an operator on a failure path.
    send(child,"configure",PROMPT_CFG)
    print(config_session(child,a,remote))
  except BaseException as exc:
    # EVERY failure lands here, not only the ones we detect and name. A timeout, a dropped
    # management link, a confirmation prompt whose wording CONFIRM does not match, Ctrl-C --
    # all of them previously exited straight out of a live config session with the candidate
    # still loaded. Fixing the known cause of one hang is not the same as closing the path.
    discard_candidate(child)
    if isinstance(exc,DeployError):
      sys.exit(f"dnos-deploy: {exc}")
    raise

  child.sendline("exit"); child.close(force=True)


if __name__ == "__main__":
  main()
