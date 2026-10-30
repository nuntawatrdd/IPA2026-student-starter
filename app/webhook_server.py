"""Part 4 student implementation."""

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from app.integration import process_request
from app.request_handler import validate_webex_envelope
from app.webex_client import WEBEX_API_BASE, WebexClient

logger = logging.getLogger(__name__)


class WebexEventHandler:
    def __init__(self, client, bot_person_id, processor=None):
        self.client = client
        self.bot_person_id = bot_person_id
        self.processor = processor if processor is not None else process_request

    def handle_event(self, event):
        if not isinstance(event, dict):
            return None

        if event.get("resource") != "messages" or event.get("event") != "created":
            return None

        data = event.get("data")
        if not isinstance(data, dict):
            return None

        message_id = data.get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            return None

        if self.bot_person_id and data.get("personId") == self.bot_person_id:
            return None

        message = self.client.get_message(message_id)
        if not isinstance(message, dict):
            return None

        if self.bot_person_id and message.get("personId") == self.bot_person_id:
            return None

        room_id = message.get("roomId")
        if not room_id:
            return None

        mentioned_people = message.get("mentionedPeople")
        files = message.get("files") or []

        if len(files) == 0:
            response = self.processor(
                mentioned_people,
                None,
                self.bot_person_id,
            )
        elif len(files) > 1:
            mention_check = validate_webex_envelope(
                mentioned_people,
                "placeholder.yaml",
                self.bot_person_id,
            )
            if mention_check is not None:
                response = mention_check
            else:
                response = {
                    "status": "error",
                    "result": "multiple_attachments",
                }
        else:
            mention_check = validate_webex_envelope(
                mentioned_people,
                "placeholder.yaml",
                self.bot_person_id,
            )
            if mention_check is not None:
                response = mention_check
            else:
                with TemporaryDirectory() as tmp_dir:
                    attachment_path = Path(tmp_dir) / "request.yaml"
                    content = self.client.download_file(files[0])
                    attachment_path.write_bytes(content)
                    response = self.processor(
                        mentioned_people,
                        attachment_path,
                        self.bot_person_id,
                    )

        self.client.send_message(room_id, json.dumps(response))
        return response


def make_http_handler(submit_event):
    class WebhookHTTPHandler(BaseHTTPRequestHandler):
        def _send_json(self, status_code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/health":
                self._send_json(200, {"status": "ok", "result": "healthy"})
            else:
                self._send_json(404, {"status": "error", "result": "not_found"})

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/webhook":
                self._send_json(404, {"status": "error", "result": "not_found"})
                return

            content_length_raw = self.headers.get("Content-Length")
            try:
                content_length = int(content_length_raw)
            except (TypeError, ValueError):
                self._send_json(400, {"status": "error", "result": "invalid_event"})
                return

            if content_length < 1:
                self._send_json(400, {"status": "error", "result": "invalid_event"})
                return

            if content_length > 1_000_000:
                remaining = min(content_length, 10_000_000)
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self._send_json(400, {"status": "error", "result": "invalid_event"})
                return

            raw_body = self.rfile.read(content_length)
            if len(raw_body) < 1 or len(raw_body) > 1_000_000:
                self._send_json(400, {"status": "error", "result": "invalid_event"})
                return

            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"status": "error", "result": "invalid_event"})
                return

            if not isinstance(payload, dict):
                self._send_json(400, {"status": "error", "result": "invalid_event"})
                return

            submit_event(payload)
            self._send_json(200, {"status": "ok", "result": "accepted"})

        def log_message(self, format, *args):
            return

    return WebhookHTTPHandler


def main():
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8000"))

    handler_state = {"handler": None}

    def get_or_init_handler():
        if handler_state["handler"] is not None:
            return handler_state["handler"]
        client = WebexClient()
        bot_person_id = os.environ.get("WEBEX_BOT_PERSON_ID")
        if not bot_person_id:
            me = client.get_me()
            bot_person_id = me["id"]
        handler_state["handler"] = WebexEventHandler(client, bot_person_id)
        return handler_state["handler"]

    if os.environ.get("WEBEX_BOT_TOKEN", "").strip():
        try:
            get_or_init_handler()
        except Exception as exc:
            logger.warning("Deferred Webex bot identity resolution: %s", exc)

    executor = ThreadPoolExecutor(max_workers=1)

    def _process_in_background(event):
        try:
            handler = get_or_init_handler()
            handler.handle_event(event)
        except Exception:
            logger.exception("Error handling Webex webhook event")

    def submit_event(event):
        executor.submit(_process_in_background, event)

    server = ThreadingHTTPServer((host, port), make_http_handler(submit_event))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        executor.shutdown(wait=False)


if __name__ == "__main__":
    main()
