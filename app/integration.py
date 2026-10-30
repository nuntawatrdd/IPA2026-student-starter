"""Part 4 student implementation."""

from pathlib import Path
import yaml

from app.core import validate_request
from app.dispatcher import dispatch_request
from app.request_handler import validate_webex_envelope


def process_request(mentioned_people, attachment_path, bot_person_id):
    envelope_error = validate_webex_envelope(
        mentioned_people,
        attachment_path,
        bot_person_id,
    )
    if envelope_error is not None:
        return envelope_error

    try:
        with Path(attachment_path).open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return {"status": "error", "result": "invalid_yaml"}

    validation = validate_request(data)
    if validation.get("status") != "ok":
        return validation

    return dispatch_request(data)
