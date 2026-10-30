MANAGED_FIELDS = ("ipv4", "description", "admin_state")


def build_plan(desired_interface, current_interface):
    """
    Compare desired interface state with normalized current state
    and return a plan according to specs/plan-spec-v1.md.
    """
    if current_interface is None:
        return {
            "status": "ok",
            "result": "planned",
            "operation": "create",
            "changes": {
                field: {"to": desired_interface[field]}
                for field in MANAGED_FIELDS
            },
        }

    changes = {}
    for field in MANAGED_FIELDS:
        current_val = current_interface.get(field)
        desired_val = desired_interface.get(field)
        if current_val != desired_val:
            changes[field] = {
                "from": current_val,
                "to": desired_val,
            }

    if not changes:
        return {
            "status": "ok",
            "result": "planned",
            "operation": "no_change",
            "changes": {},
        }

    return {
        "status": "ok",
        "result": "planned",
        "operation": "update",
        "changes": changes,
    }
