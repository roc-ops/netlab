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
import os, subprocess, shlex


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

    def apply_push(self, *_a, **_k):
        raise PushDisabled(
            "apply_push is HARD-DISABLED in hardware phase 1. Applying to the live box is "
            "human-gated on review of the dry-run. This method never reaches the hardware.")
