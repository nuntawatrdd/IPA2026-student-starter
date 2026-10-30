from ipaddress import IPv4Interface
import os
import socket
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from app.backends.planning import plan_interface as _plan_interface

NETCONF_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
IETF_IF_NS = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
IETF_IP_NS = "urn:ietf:params:xml:ns:yang:ietf-ip"
NATIVE_NS = "http://cisco.com/ns/yang/Cisco-IOS-XE-native"


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
        for k in (
            "connect",
            "timeout",
            "ssh",
            "socket",
            "sessionclose",
            "transport",
        )
    ):
        return {"status": "error", "result": "connection_failed"}

    return {"status": "error", "result": "backend_failed"}


def _default_netconf_get_config(router, filter_xml):
    from ncclient import manager

    with manager.connect(
        host=router,
        port=830,
        username=os.environ.get("ROUTER_USER", ""),
        password=os.environ.get("ROUTER_PASS", ""),
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False,
        timeout=10,
    ) as m:
        reply = m.get_config(source="running", filter=("subtree", filter_xml))
        return reply.xml


def _default_netconf_edit_config(router, config_xml):
    from ncclient import manager

    with manager.connect(
        host=router,
        port=830,
        username=os.environ.get("ROUTER_USER", ""),
        password=os.environ.get("ROUTER_PASS", ""),
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False,
        timeout=10,
    ) as m:
        m.edit_config(target="running", config=config_xml)


def get_interface_state(
    router,
    interface_name,
    netconf_get_config=None,
):
    """Return normalized interface status using NETCONF."""
    get_fn = netconf_get_config or _default_netconf_get_config
    escaped_name = escape(str(interface_name))
    filter_xml = (
        f'<interfaces xmlns="{IETF_IF_NS}">'
        f"<interface><name>{escaped_name}</name></interface>"
        f"</interfaces>"
    )

    try:
        xml_text = get_fn(router, filter_xml)
    except Exception as exc:
        return _map_exception(exc)

    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return {"status": "error", "result": "backend_failed"}

    if root.tag == f"{{{IETF_IF_NS}}}interface":
        iface_elem = root
    else:
        iface_elem = root.find(f".//{{{IETF_IF_NS}}}interface")

    if iface_elem is None:
        return {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        }

    try:
        name_el = iface_elem.find(f"{{{IETF_IF_NS}}}name")
        desc_el = iface_elem.find(f"{{{IETF_IF_NS}}}description")
        enabled_el = iface_elem.find(f"{{{IETF_IF_NS}}}enabled")

        ip_el = iface_elem.find(
            f".//{{{IETF_IP_NS}}}ipv4/{{{IETF_IP_NS}}}address/{{{IETF_IP_NS}}}ip"
        )
        mask_el = iface_elem.find(
            f".//{{{IETF_IP_NS}}}ipv4/{{{IETF_IP_NS}}}address/{{{IETF_IP_NS}}}netmask"
        )

        ipv4_cidr = None
        if (
            ip_el is not None
            and mask_el is not None
            and ip_el.text
            and mask_el.text
        ):
            ipv4_cidr = str(
                IPv4Interface(f"{ip_el.text.strip()}/{mask_el.text.strip()}")
            )

        enabled_str = (
            enabled_el.text.strip().lower()
            if (enabled_el is not None and enabled_el.text)
            else "true"
        )
        admin_state = "down" if enabled_str == "false" else "up"

        description = (
            desc_el.text.strip()
            if (desc_el is not None and desc_el.text is not None)
            else None
        )
        name = (
            name_el.text.strip()
            if (name_el is not None and name_el.text)
            else interface_name
        )

        return {
            "status": "ok",
            "result": "found",
            "interface": {
                "name": name,
                "ipv4": ipv4_cidr,
                "description": description,
                "admin_state": admin_state,
            },
        }
    except Exception:
        return {"status": "error", "result": "backend_failed"}


def plan_interface(
    router,
    desired_interface,
    netconf_get_config=None,
):
    """Return create/update/no_change plan using NETCONF status."""
    return _plan_interface(
        router,
        desired_interface,
        lambda r, name: get_interface_state(
            r, name, netconf_get_config=netconf_get_config
        ),
    )


def apply_interface(
    router,
    desired_interface,
    netconf_edit_config=None,
):
    """Apply complete desired interface state using NETCONF."""
    edit_fn = netconf_edit_config or _default_netconf_edit_config

    try:
        name = desired_interface["name"]
        if not isinstance(name, str) or not name.startswith("Loopback"):
            return {"status": "error", "result": "backend_failed"}
        number = name[len("Loopback"):]
        int(number)
        ipv4 = IPv4Interface(desired_interface["ipv4"])
        ip_str = str(ipv4.ip)
        mask_str = str(ipv4.network.netmask)
        desc_str = escape(str(desired_interface["description"]))
    except Exception:
        return {"status": "error", "result": "backend_failed"}

    shutdown_xml = (
        "<shutdown/>"
        if desired_interface.get("admin_state") == "down"
        else ""
    )

    config_xml = (
        f'<config xmlns="{NETCONF_NS}" xmlns:nc="{NETCONF_NS}">'
        f'<native xmlns="{NATIVE_NS}">'
        f"<interface>"
        f'<Loopback nc:operation="replace">'
        f"<name>{number}</name>"
        f"<description>{desc_str}</description>"
        f"<ip><address><primary>"
        f"<address>{ip_str}</address>"
        f"<mask>{mask_str}</mask>"
        f"</primary></address></ip>"
        f"{shutdown_xml}"
        f"</Loopback>"
        f"</interface>"
        f"</native>"
        f"</config>"
    )

    try:
        edit_fn(router, config_xml)
    except Exception as exc:
        return _map_exception(exc)

    return {
        "status": "ok",
        "result": "applied",
    }
