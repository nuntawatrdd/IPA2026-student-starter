from ipaddress import IPv4Interface
import json
import os
from pathlib import Path
import subprocess

ANSIBLE_DIR = Path(__file__).resolve().parents[2] / "ansible"
PLAYBOOK_PATH = ANSIBLE_DIR / "apply_interface.yml"
INVENTORY_PATH = ANSIBLE_DIR / "inventory.ini"


def _default_run_playbook(variables):
    user = os.environ.get("ROUTER_USER", "")
    password = os.environ.get("ROUTER_PASS", "")

    extra_vars = {
        **variables,
        "ansible_host": variables["router"],
        "ansible_user": user,
        "ansible_password": password,
        "ansible_ssh_pass": password,
    }

    env = os.environ.copy()
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ANSIBLE_NETWORK_CLI_SSH_TYPE"] = "paramiko"
    env["ANSIBLE_RETRY_FILES_ENABLED"] = "False"

    try:
        proc = subprocess.run(
            [
                "ansible-playbook",
                "-i",
                str(INVENTORY_PATH),
                str(PLAYBOOK_PATH),
                "-e",
                json.dumps(extra_vars),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConnectionError("Ansible playbook timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to invoke ansible-playbook: {exc}") from exc

    if proc.returncode != 0:
        combined = f"{proc.stdout}\n{proc.stderr}".lower()
        if any(
            token in combined
            for token in (
                "authentication failed",
                "authentication failure",
                "permission denied",
                "invalid credentials",
                "auth fail",
                "badpasswords",
            )
        ):
            raise PermissionError(proc.stderr or proc.stdout)
        if proc.returncode == 4 or any(
            token in combined
            for token in (
                "unreachable=1",
                "timed out",
                "connection refused",
                "unable to connect",
                "no route to host",
                "network is unreachable",
                "could not resolve",
                "ssh session not active",
            )
        ):
            raise ConnectionError(proc.stderr or proc.stdout)
        raise RuntimeError(proc.stderr or proc.stdout)


def apply_interface(
    router,
    desired_interface,
    run_playbook=None,
):
    """Apply complete desired interface state using Ansible."""
    runner = run_playbook or _default_run_playbook

    try:
        ipv4 = IPv4Interface(desired_interface["ipv4"])
        variables = {
            "router": router,
            "interface_name": desired_interface["name"],
            "description": desired_interface["description"],
            "ipv4_address": str(ipv4.ip),
            "netmask": str(ipv4.network.netmask),
            "shutdown": desired_interface.get("admin_state") == "down",
        }
    except Exception:
        return {"status": "error", "result": "backend_failed"}

    try:
        runner(variables)
    except PermissionError:
        return {"status": "error", "result": "authentication_failed"}
    except (ConnectionError, TimeoutError):
        return {"status": "error", "result": "connection_failed"}
    except Exception:
        return {"status": "error", "result": "backend_failed"}

    return {
        "status": "ok",
        "result": "applied",
    }
