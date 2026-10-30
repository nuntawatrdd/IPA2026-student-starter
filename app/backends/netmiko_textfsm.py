import os
import socket


def _normalize_if_name(name):
    if not isinstance(name, str):
        return ""
    s = name.strip()
    lower = s.lower()
    if lower.startswith("loopback"):
        return "Loopback" + s[len("loopback"):]
    if lower.startswith("lo"):
        return "Loopback" + s[2:]
    return s


def _map_exception(exc):
    if isinstance(exc, PermissionError):
        return {"status": "error", "result": "authentication_failed"}
    if isinstance(exc, (ConnectionError, TimeoutError, socket.error, EOFError)):
        return {"status": "error", "result": "connection_failed"}

    cls_names = [c.__name__.lower() for c in type(exc).__mro__]
    msg = str(exc).lower()

    if any("auth" in n or "permission" in n for n in cls_names) or "authentication" in msg:
        return {"status": "error", "result": "authentication_failed"}

    if any(
        k in n
        for n in cls_names
        for k in ("timeout", "connect", "ssh", "socket", "eof")
    ):
        return {"status": "error", "result": "connection_failed"}

    return {"status": "error", "result": "backend_failed"}


def _parse_textfsm_list(output):
    if isinstance(output, list):
        return output
    if isinstance(output, str):
        lower = output.strip().lower()
        if (
            not lower
            or "invalid input" in lower
            or "incomplete command" in lower
            or "does not exist" in lower
        ):
            return []
    raise RuntimeError("TextFSM failed to parse CLI output")


_NO_INTERFACE_MARKERS = ("invalid input", "incomplete command", "does not exist")


def _show_ip_interface(conn, interface_name):
    """Run `show ip interface` and return parsed rows; [] when the interface is absent."""
    command = f"show ip interface {interface_name}"
    raw = conn.send_command(command)
    lower = raw.strip().lower() if isinstance(raw, str) else ""
    if not lower or any(marker in lower for marker in _NO_INTERFACE_MARKERS):
        return []
    return _parse_textfsm_list(conn.send_command(command, use_textfsm=True))


def _default_netmiko_get(router, interface_name):
    from netmiko import ConnectHandler

    device = {
        "device_type": "cisco_ios",
        "host": router,
        "username": os.environ.get("ROUTER_USER", ""),
        "password": os.environ.get("ROUTER_PASS", ""),
        "timeout": 10,
        "conn_timeout": 10,
    }

    with ConnectHandler(**device) as conn:
        ip_info = _show_ip_interface(conn, interface_name)
        desc_raw = conn.send_command(
            "show interfaces description",
            use_textfsm=True,
        )

    descriptions = desc_raw if isinstance(desc_raw, list) else []
    return ip_info, descriptions


def _default_netmiko_delete(router, interface_name):
    from netmiko import ConnectHandler

    device = {
        "device_type": "cisco_ios",
        "host": router,
        "username": os.environ.get("ROUTER_USER", ""),
        "password": os.environ.get("ROUTER_PASS", ""),
        "timeout": 10,
        "conn_timeout": 10,
    }

    target_norm = _normalize_if_name(interface_name)

    with ConnectHandler(**device) as conn:
        ip_info = _show_ip_interface(conn, interface_name)
        exists = any(
            _normalize_if_name(item.get("interface")) == target_norm
            for item in ip_info
            if isinstance(item, dict)
        )
        if not exists:
            return "not_found"

        conn.send_config_set([f"no interface {interface_name}"])

        verify_info = _show_ip_interface(conn, interface_name)
        still_exists = any(
            _normalize_if_name(item.get("interface")) == target_norm
            for item in verify_info
            if isinstance(item, dict)
        )
        if still_exists:
            raise RuntimeError("Interface still exists after deletion")

        return "deleted"


def get_interface_state(
    router,
    interface_name,
    netmiko_get=None,
):
    """Return normalized interface status using Netmiko + TextFSM."""
    get_fn = netmiko_get or _default_netmiko_get

    try:
        ip_info, descriptions = get_fn(router, interface_name)
    except Exception as exc:
        return _map_exception(exc)

    if not isinstance(ip_info, list) or not isinstance(descriptions, list):
        return {"status": "error", "result": "backend_failed"}

    target_norm = _normalize_if_name(interface_name)

    matched_ip = None
    for record in ip_info:
        if isinstance(record, dict) and (
            _normalize_if_name(record.get("interface")) == target_norm
        ):
            matched_ip = record
            break

    if matched_ip is None:
        return {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        }

    try:
        ip_list = matched_ip.get("ip_address") or []
        prefix_list = matched_ip.get("prefix_length") or []

        ip_addr = (
            ip_list[0]
            if isinstance(ip_list, list) and ip_list
            else (ip_list if isinstance(ip_list, str) and ip_list else None)
        )
        prefix_len = (
            prefix_list[0]
            if isinstance(prefix_list, list) and prefix_list
            else (
                prefix_list
                if isinstance(prefix_list, (str, int)) and str(prefix_list) != ""
                else None
            )
        )

        ipv4 = (
            f"{ip_addr}/{prefix_len}"
            if (ip_addr and prefix_len is not None)
            else None
        )

        link_status = str(matched_ip.get("link_status", "")).strip().lower()
        admin_state = (
            "down" if link_status == "administratively down" else "up"
        )

        description = None
        for desc_rec in descriptions:
            if not isinstance(desc_rec, dict):
                continue
            port = desc_rec.get("port") or desc_rec.get("interface") or ""
            if _normalize_if_name(port) == target_norm:
                raw_desc = desc_rec.get("description")
                description = raw_desc if raw_desc not in (None, "") else None
                break

        return {
            "status": "ok",
            "result": "found",
            "interface": {
                "name": target_norm,
                "ipv4": ipv4,
                "description": description,
                "admin_state": admin_state,
            },
        }
    except Exception:
        return {"status": "error", "result": "backend_failed"}


def delete_interface(
    router,
    interface_name,
    netmiko_delete=None,
):
    """Delete an interface using Netmiko."""
    delete_fn = netmiko_delete or _default_netmiko_delete

    try:
        outcome = delete_fn(router, interface_name)
    except Exception as exc:
        return _map_exception(exc)

    if outcome == "deleted":
        return {
            "status": "ok",
            "result": "deleted",
        }

    if outcome == "not_found":
        return {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        }

    return {
        "status": "error",
        "result": "backend_failed",
    }
