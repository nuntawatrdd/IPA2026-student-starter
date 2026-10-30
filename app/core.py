from pathlib import Path
import yaml

ALLOWED_ROUTERS = (
    "10.0.29.101",
    "10.0.29.102",
    "10.0.29.103",
    "10.0.29.104",
    "10.0.29.105",
)

VALID_ACTIONS_BY_METHOD = {
    "restconf": ("status", "plan", "apply"),
    "netconf": ("status", "plan", "apply"),
    "ansible": ("apply",),
    "netmiko-textfsm": ("status", "delete"),
}

ALLOWED_ADMIN_STATES = ("up", "down")


def _is_missing_str(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def validate_request(data):
    """
    Validate one IPA2026 desired-state request.

    Return a response dictionary according to
    specs/response-spec-v1.md.
    """
    if not isinstance(data, dict):
        return {"status": "error", "result": "invalid_yaml"}

    # 1. version: must be the YAML integer 1 (not str, float, or bool)
    if "version" not in data:
        return {"status": "error", "result": "invalid_version"}
    version = data["version"]
    if type(version) is not int or version != 1:
        return {"status": "error", "result": "invalid_version"}

    # 2. router: missing_router, then invalid_router
    if "router" not in data or _is_missing_str(data["router"]):
        return {"status": "error", "result": "missing_router"}
    router = data["router"]
    if not isinstance(router, str) or router not in ALLOWED_ROUTERS:
        return {"status": "error", "result": "invalid_router"}

    # 3. method: missing_method, then invalid_method
    if "method" not in data or _is_missing_str(data["method"]):
        return {"status": "error", "result": "missing_method"}
    method = data["method"]
    if not isinstance(method, str) or method not in VALID_ACTIONS_BY_METHOD:
        return {"status": "error", "result": "invalid_method"}

    # 4. action: missing_action, then invalid_action
    if "action" not in data or _is_missing_str(data["action"]):
        return {"status": "error", "result": "missing_action"}
    action = data["action"]
    if not isinstance(action, str) or action not in VALID_ACTIONS_BY_METHOD[method]:
        return {"status": "error", "result": "invalid_action"}

    # 5. desired, desired.interface, desired.interface.name
    if "desired" not in data or data["desired"] is None:
        return {"status": "error", "result": "missing_desired"}
    desired = data["desired"]
    if not isinstance(desired, dict) or "interface" not in desired or desired["interface"] is None:
        return {"status": "error", "result": "missing_interface"}
    interface = desired["interface"]
    if (
        not isinstance(interface, dict)
        or "name" not in interface
        or _is_missing_str(interface["name"])
        or not isinstance(interface["name"], str)
    ):
        return {"status": "error", "result": "missing_interface_name"}

    # 6. For plan and apply only: ipv4, description, admin_state
    if action in ("plan", "apply"):
        if (
            "ipv4" not in interface
            or _is_missing_str(interface["ipv4"])
            or not isinstance(interface["ipv4"], str)
        ):
            return {"status": "error", "result": "missing_ipv4"}
        if (
            "description" not in interface
            or _is_missing_str(interface["description"])
            or not isinstance(interface["description"], str)
        ):
            return {"status": "error", "result": "missing_description"}
        if "admin_state" not in interface or _is_missing_str(interface["admin_state"]):
            return {"status": "error", "result": "missing_admin_state"}
        admin_state = interface["admin_state"]
        if type(admin_state) is not str or admin_state not in ALLOWED_ADMIN_STATES:
            return {"status": "error", "result": "invalid_admin_state"}

    return {"status": "ok", "result": "accepted"}


def process_request_file(path):
    """
    Read a YAML request file, validate it,
    and return a response dictionary.
    """
    if path is None:
        return {"status": "error", "result": "no_yaml"}
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return {"status": "error", "result": "invalid_yaml"}

    return validate_request(data)
