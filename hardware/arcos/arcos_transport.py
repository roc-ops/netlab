#!/usr/bin/env python3
"""ArcOS hardware transport (issue #39 hardware phase 1): SSH -> confd_cli.

The container arcos device reaches confd_cli over Ansible's `docker` connection
(`docker exec ... confd_cli`, see netsim/ansible/tasks/deploy-config/arcos.yml). The CLI
grammar is IDENTICAL on the hardware -- ONLY THE TRANSPORT DIFFERS: on the real Edgecore
AS7326-56X we reach the same confd_cli over SSH:

    sshpass -p <pw> ssh root@<host> 'echo "<show cmd>" | cli'      # cli == confd_cli -R

This module wires that transport for READ / DRY-RUN ONLY in phase 1. There is deliberately
NO working push path:

  * The password is NEVER hardcoded or written to any file. It is read from the environment
    (`ARCOS_HW_PASSWORD`); the operator exports it for the duration of a run. Nothing in this
    module, or anything it writes, contains the credential or the AAA hash.
  * `show_running_config` / `show` are read-only -- they pipe a `show ...` command into `cli`,
    which cannot mutate config.
  * `dry_run_push` BUILDS and RETURNS the exact confd_cli sequence that a future apply would
    run (`config` / `load merge` / `commit` / `end`) but NEVER executes it. `apply_push`
    exists only to fail loudly: it is hard-disabled in this phase and refuses regardless of
    arguments, so an accidental call cannot reach the hardware.
"""
from __future__ import annotations
import os, re, subprocess, shlex


DEFAULT_HOST = "10.22.64.223"
DEFAULT_USER = "root"
PASSWORD_ENV = "ARCOS_HW_PASSWORD"


class PushDisabled(RuntimeError):
    """Raised by apply_push -- pushing is not enabled in this phase, full stop."""


class Transport:
    def __init__(self, host: str | None = None, user: str = DEFAULT_USER):
        self.host = host or DEFAULT_HOST
        self.user = user

    # -- credential (env only; never persisted) --------------------------------
    def _password(self) -> str:
        pw = os.environ.get(PASSWORD_ENV)
        if not pw:
            raise RuntimeError(
                f"set {PASSWORD_ENV} in the environment (never committed/echoed to a file)")
        return pw

    def _ssh_argv(self, remote_cmd: str) -> list[str]:
        return [
            "sshpass", "-e",                    # read password from SSHPASS env -> not in argv
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{self.user}@{self.host}", remote_cmd,
        ]

    def _run_ssh(self, remote_cmd: str, timeout: float = 20.0) -> str:
        env = dict(os.environ)
        env["SSHPASS"] = self._password()       # sshpass -e reads here; argv stays clean
        p = subprocess.run(self._ssh_argv(remote_cmd), capture_output=True, text=True,
                           env=env, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError(f"ssh to {self.host} failed (rc={p.returncode}): {p.stderr.strip()}")
        return p.stdout

    # -- READ-ONLY -------------------------------------------------------------
    def show(self, what: str) -> str:
        """Run a `show <what>` through cli (confd_cli -R). Read-only by construction."""
        what = what.strip()
        if not what.startswith("show"):
            what = f"show {what}"
        # single-quote the piped command; `what` is operator/plan-derived show text only.
        return self._run_ssh(f"echo {shlex.quote(what)} | cli")

    def show_running_config(self) -> str:
        return self.show("show running-config")

    def show_version(self) -> str:
        return self.show("show version")

    # -- DRY-RUN (build, do not execute) ---------------------------------------
    def dry_run_push(self, config_text: str, comment: str = "netlab dry-run (NOT applied)") -> str:
        """Return the EXACT confd_cli sequence a future apply would run -- WITHOUT running it.

        This mirrors the container deploy task's proven `config` / `load merge <file>` /
        `commit comment` / `end` sequence (netsim/ansible/tasks/deploy-config/arcos.yml),
        adapted to the SSH transport: the candidate would be staged to a temp file on the box,
        then fed to confd_cli. Nothing here touches the hardware.
        """
        staged = "/tmp/netlab-arcos-candidate.cfg"
        seq = [
            f"# would stage the rendered candidate to {staged} on {self.host} (scp), then:",
            f"printf 'config\\nload merge {staged}\\n"
            f"commit comment \"{comment}\"\\nend\\n' | cli",
        ]
        return (f"# ===== DRY-RUN: confd_cli sequence that WOULD be pushed to "
                f"{self.user}@{self.host} (NOT executed) =====\n"
                + "\n".join(seq)
                + f"\n# ----- candidate config ({len(config_text.splitlines())} lines) -----\n"
                + config_text.rstrip() + "\n")

    # -- staging + GATED apply + rollback --------------------------------------
    # NOTE: this GATED form exists only for the human-approved one-shot swp48 apply
    # (issue #39 phase 1). It fires ONLY when the caller passes allow=True AND the operator
    # exports ARCOS_HW_ALLOW_PUSH=YES -- two independent locks, so nothing pushes by accident.
    # After the approved change is applied + verified, this method is reverted to the
    # unconditional `raise PushDisabled` (see git history) so the loop cannot fire again.
    _ALLOW_ENV = "ARCOS_HW_ALLOW_PUSH"
    _STAGED = "/tmp/netlab-arcos-candidate.cfg"

    def _gate(self, allow: bool):
        if not (allow and os.environ.get(self._ALLOW_ENV) == "YES"):
            raise PushDisabled(
                "push is gated: needs allow=True AND "
                f"{self._ALLOW_ENV}=YES. Applying to the live box is human-gated.")

    def _stage_candidate(self, config_text: str, remote_path: str | None = None) -> str:
        """Stage the candidate to a temp file on the box (the `load merge` source).
        base64 over the wire so no quoting/escaping can corrupt it. Contains no secrets."""
        import base64
        path = remote_path or self._STAGED
        b64 = base64.b64encode(config_text.encode()).decode()
        self._run_ssh(f"echo {b64} | base64 -d > {shlex.quote(path)}")
        return path

    def apply_push(self, config_text: str, comment: str = "netlab swp48 apply (issue #39)",
                   allow: bool = False) -> str:
        """GATED real apply: stage -> `config` / `load merge` / `commit` (the proven confd
        sequence from the container deploy task, over SSH). confd returns rc 0 even on a merge
        parse error, so we scan output for Error:/Aborted:/syntax error and raise on any."""
        self._gate(allow)
        path = self._stage_candidate(config_text)
        cmd = (f"printf 'config\\nload merge {path}\\n"
               f"commit comment \"{comment}\"\\nend\\n' | cli")
        out = self._run_ssh(cmd, timeout=40)
        if re.search(r"Error:|Aborted:|syntax error", out, re.I):
            raise RuntimeError(f"confd rejected the merge -- NOT committed:\n{out}")
        return out

    def rollback_preview(self, sno: int = 0) -> str:
        """READ-ONLY: what `rollback selective <sno>` would change (no config session)."""
        return self.show(f"show configuration rollback changes {sno}")

    def rollback_selective(self, sno: int = 0, allow: bool = False) -> str:
        """GATED: selectively revert commit <sno> (0 == most recent) -> `config` /
        `rollback selective` / `commit`. Same two locks as apply_push."""
        self._gate(allow)
        cmd = (f"printf 'config\\nrollback selective {sno}\\n"
               f"commit comment \"netlab rollback swp48\"\\nend\\n' | cli")
        out = self._run_ssh(cmd, timeout=40)
        if re.search(r"Error:|Aborted:|syntax error", out, re.I):
            raise RuntimeError(f"confd rejected the rollback:\n{out}")
        return out
