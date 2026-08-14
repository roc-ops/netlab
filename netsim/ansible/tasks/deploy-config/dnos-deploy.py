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
ERROR_PAT   = re.compile(r"ERROR|Invalid command|Unknown word|failed|Failed", re.I)


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
    i = child.expect([ CONFIRM, expect_pat, pexpect.TIMEOUT, pexpect.EOF ], timeout=timeout)
    out += child.before or ""
    if i == 0:                      # a confirmation prompt -- answer it, keep reading
      child.sendline("yes")
      continue
    if i == 1:
      return out
    sys.exit(f"dnos-deploy: timeout/EOF waiting for prompt after {line!r}\n--- got ---\n{out}")


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

  send(child,"configure",PROMPT_CFG)
  load = send(child,f"load merge {remote}",PROMPT_CFG)
  if ERROR_PAT.search(load):
    sys.exit(f"dnos-deploy: load merge failed\n{load}")

  diff = send(child,"show config compare",PROMPT_CFG)
  print("--- candidate diff ---");  print(diff.strip())

  chk = send(child,"commit check",PROMPT_CFG)
  if "passed successfully" not in chk:
    send(child,"rollback 0",PROMPT_CFG)
    sys.exit(f"dnos-deploy: commit check FAILED (candidate discarded)\n{chk}")

  if a.dry_run:
    send(child,"rollback 0",PROMPT_CFG)
    print("--- dry run: commit check passed, candidate discarded ---")
  else:
    out = send(child,"commit",PROMPT_CFG)
    if ERROR_PAT.search(out) or "Commit succeeded" not in out:
      sys.exit(f"dnos-deploy: commit FAILED\n{out}")
    print("--- commit succeeded ---")

  child.sendline("exit"); child.close(force=True)


if __name__ == "__main__":
  main()
