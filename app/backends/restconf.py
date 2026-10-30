from ipaddress import IPv4Interface
import os
import socket
import requests
import urllib3

from app.backends.planning import plan_interface as _plan_interface

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RESTCONF_HEADERS = {
    "Accept": "application/yang-data+json",
    "Content-Type": "application/yang-data+json",
}


def _router_auth():
    return (
        os.environ.get("ROUTER_USER", ""),
        os.environ.get("ROUTER_PASS", ""),
    )


def _default_http_get(url):
    return requests.get(
        url,
        auth=_router_auth(),
        headers=RESTCONF_HEADERS,
        verify=False,
        timeout=10,
    )


def _default_http_put(url, json=None):
    return requests.put(
        url,
        json=json,
        auth=_router_auth(),
        headers=RESTCONF_HEADERS,
        verify=False,
        timeout=10,
    )


def _map_exception(exc):
    if isinstance(exc, PermissionError):
        return {"status": "error", "result": "authentication_failed"}
    if isinstance(
        exc,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            ConnectionError,
            TimeoutError,
            socket.error,
        ),
    ):
        return {"status": "error", "result": "connection_failed"}
    return {"status": "error", "result": "backend_failed"}


def get_interface_state(
    router,
    interface_name,
    http_get=None,
):
    """Return normalized interface status using RESTCONF."""
    get_fn = http_get or _default_http_get
    url = (
        f"https://{router}/restconf/data/"
        f"ietf-interfaces:interfaces/interface={interface_name}"
    )

    try:
        response = get_fn(url)
    except Exception as exc:
        return _map_exception(exc)

    status_code = getattr(response, "status_code", None)

    if status_code == 404:
        return {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        }

    if status_code in (401, 403):
        return {
            "status": "error",
            "result": "authentication_failed",
        }

    if status_code != 200:
        return {
            "status": "error",
            "result": "backend_failed",
        }

    try:
        body = response.json()
        iface = body.get("ietf-interfaces:interface")
        if isinstance(iface, list):
            iface = iface[0] if iface else None
        if not isinstance(iface, dict):
            return {"status": "error", "result": "backend_failed"}

        ipv4_container = iface.get("ietf-ip:ipv4") or {}
        addresses = ipv4_container.get("address") or []
        ipv4_cidr = None
        if isinstance(addresses, list) and addresses:
            addr_entry = addresses[0]
            ip_val = addr_entry.get("ip")
            mask_val = addr_entry.get("netmask")
            if ip_val and mask_val:
                ipv4_cidr = str(IPv4Interface(f"{ip_val}/{mask_val}"))

        enabled = iface.get("enabled", True)
        admin_state = "up" if enabled is True else "down"

        return {
            "status": "ok",
            "result": "found",
            "interface": {
                "name": iface.get("name", interface_name),
                "ipv4": ipv4_cidr,
                "description": iface.get("description"),
                "admin_state": admin_state,
            },
        }
    except Exception:
        return {
            "status": "error",
            "result": "backend_failed",
        }


def plan_interface(
    router,
    desired_interface,
    http_get=None,
):
    """Return create/update/no_change plan using RESTCONF status."""
    return _plan_interface(
        router,
        desired_interface,
        lambda r, name: get_interface_state(r, name, http_get=http_get),
    )


def apply_interface(
    router,
    desired_interface,
    http_put=None,
):
    """Apply complete desired interface state using RESTCONF."""
    put_fn = http_put or _default_http_put

    try:
        name = desired_interface["name"]
        if not isinstance(name, str) or not name.startswith("Loopback"):
            return {"status": "error", "result": "backend_failed"}
        number = int(name[len("Loopback"):])
        ipv4 = IPv4Interface(desired_interface["ipv4"])
    except Exception:
        return {"status": "error", "result": "backend_failed"}

    url = (
        f"https://{router}/restconf/data/"
        f"Cisco-IOS-XE-native:native/interface/Loopback={number}"
    )

    loopback_payload = {
        "name": number,
        "description": desired_interface["description"],
        "ip": {
            "address": {
                "primary": {
                    "address": str(ipv4.ip),
                    "mask": str(ipv4.network.netmask),
                }
            }
        },
    }

    if desired_interface.get("admin_state") == "down":
        loopback_payload["shutdown"] = [None]

    payload = {
        "Cisco-IOS-XE-native:Loopback": loopback_payload,
    }

    try:
        response = put_fn(url, json=payload)
    except Exception as exc:
        return _map_exception(exc)

    status_code = getattr(response, "status_code", None)

    if status_code in (200, 201, 204):
        return {
            "status": "ok",
            "result": "applied",
        }

    if status_code in (401, 403):
        return {
            "status": "error",
            "result": "authentication_failed",
        }

    return {
        "status": "error",
        "result": "backend_failed",
    }
