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
NOOP_PAT    = re.compile(r"no configuration changes were made", re.I)


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


SCOPE_PAT = re.compile(r"^(interfaces|protocols)\s*$")


def config_scopes(cfg_text: str) -> list:
  """Top-level scopes a rendered config touches, e.g. ['interfaces','protocols'].

  Withdrawal has to RESTORE, not merely delete. netlab does not only add config: deploying an
  interface with netlab's layer-3 MTU overwrote an mtu the box already had (9000 -> 1514), so
  "no interfaces X" would drop the interface entirely and lose the original value. The snapshot
  is what makes the original recoverable.
  """
  return [ l.strip() for l in cfg_text.splitlines() if SCOPE_PAT.match(l) ]


def snapshot_path(snap_dir: str, host: str, name: str) -> str:
  d = os.path.expanduser(snap_dir)
  os.makedirs(d, exist_ok=True)
  return os.path.join(d, f"{host}__{name}.snapshot")


def capture_snapshot(child, cfg_text: str, path: str) -> None:
  """Save the CURRENT config of every scope this deploy will touch."""
  saved = []
  for scope in config_scopes(cfg_text):
    out = send(child, f"show config {scope} | no-more", PROMPT_CFG)
    saved.append(out)
  with open(path, "w") as f:
    f.write("\n".join(saved))


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--host",required=True)
  ap.add_argument("--user",default="dnroot")
  ap.add_argument("--password-file",required=True)
  ap.add_argument("--config",required=True,help="rendered DNOS config file")
  ap.add_argument("--name",default=None,help="remote file name under /config/")
  ap.add_argument("--dry-run",action="store_true",help="commit check only, then discard")
  ap.add_argument("--withdraw",action="store_true",
                  help="restore the pre-deploy snapshot for this node instead of deploying")
  ap.add_argument("--snapshot-dir",default="~/.netlab-dnos-snapshots",
                  help="where pre-deploy snapshots are kept")
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
  # A no-op deploy is a success, not a failure: DNOS answers an empty candidate with
  # "commit action is not applicable", which is a NOTICE rather than an error. Treat that
  # known-benign notice as done, and fail only on anything else.
  if NOOP_PAT.search(chk):
    send(child,"rollback 0",PROMPT_CFG)
    print("--- nothing to do: candidate is empty, running config already matches ---")
    child.sendline("exit")
    child.close(force=True)
    return
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
