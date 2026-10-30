from app.core import process_request_file


def validate_webex_envelope(
    mentioned_people,
    attachment_path,
    bot_person_id,
):
    """
    Validate the target bot's actual mention and YAML attachment.
    Return an error response, or None when the envelope is valid.
    """
    if (
        not isinstance(bot_person_id, str)
        or not bot_person_id.strip()
        or not isinstance(mentioned_people, list)
        or bot_person_id not in mentioned_people
    ):
        return {"status": "error", "result": "missing_bot_mention"}

    if attachment_path is None or (
        isinstance(attachment_path, str) and not attachment_path.strip()
    ):
        return {"status": "error", "result": "no_yaml"}

    return None


def handle_webex_request(
    mentioned_people,
    attachment_path,
    bot_person_id,
):
    """
    Validate the envelope, then validate the attached YAML request.
    Return a response dictionary. Do not dispatch to a router.
    """
    error = validate_webex_envelope(
        mentioned_people,
        attachment_path,
        bot_person_id,
    )
    if error is not None:
        return error

    return process_request_file(attachment_path)
