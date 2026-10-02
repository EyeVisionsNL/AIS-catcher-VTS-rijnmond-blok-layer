#!/usr/bin/env python3
"""Restricted relay for the AIS-Catcher Rijnmond VTS overlay.

This is deliberately not a general-purpose HTTP proxy. It forwards only the
two RWS WaterWebservices endpoints used by the overlay, only for Hoek van
Holland, and only the tide/wind request shapes used by the bundled plugin.
"""

import json
import os
import socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


UPSTREAM = "https://ddapi20-waterwebservices.rijkswaterstaat.nl"
HOST = os.environ.get("AIS_CATCHER_RWS_PROXY_HOST", "127.0.0.1")
PORT = int(os.environ.get("AIS_CATCHER_RWS_PROXY_PORT", "8120"))
MAX_BODY_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 15

OBSERVATIONS = "/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen"
LATEST = "/ONLINEWAARNEMINGENSERVICES/OphalenLaatsteWaarnemingen"
ALLOWED_PATHS = {OBSERVATIONS, LATEST}


def aquo_code(metadata, field):
    value = metadata.get(field, {}) if isinstance(metadata, dict) else {}
    return value.get("Code") if isinstance(value, dict) else None


def validate_request(path, payload):
    """Return an error string unless the request is one used by the plugin."""
    if not isinstance(payload, dict):
        return "JSON body must be an object"

    if path == OBSERVATIONS:
        location = payload.get("Locatie")
        if not isinstance(location, dict) or location.get("Code") != "hoekvanholland":
            return "Only Hoek van Holland is allowed"
        plus = payload.get("AquoPlusWaarnemingMetadata", {})
        metadata = plus.get("AquoMetadata", {}) if isinstance(plus, dict) else {}
        if aquo_code(metadata, "Compartiment") != "OW":
            return "Only surface-water observations are allowed"
        if aquo_code(metadata, "Grootheid") != "WATHTE":
            return "Only water-level observations are allowed"
        if aquo_code(metadata, "Hoedanigheid") != "NAP":
            return "Only NAP water levels are allowed"
        if aquo_code(metadata, "Eenheid") != "cm":
            return "Only centimetre water levels are allowed"
        if metadata.get("ProcesType") not in ("meting", "astronomisch"):
            return "Only measured or astronomical water levels are allowed"
        period = payload.get("Periode", {})
        if not isinstance(period, dict):
            return "A valid ISO-8601 period is required"
        try:
            start = datetime.fromisoformat(period["Begindatumtijd"])
            end = datetime.fromisoformat(period["Einddatumtijd"])
        except (KeyError, TypeError, ValueError):
            return "A valid ISO-8601 period is required"
        if start.utcoffset() is None or end.utcoffset() is None:
            return "The period must include timezone offsets"
        try:
            duration = (end - start).total_seconds()
        except TypeError:
            return "The period must use matching timezone formats"
        if duration < 0 or duration > 24 * 60 * 60:
            return "The requested period must not exceed 24 hours"
        return None

    if path == LATEST:
        locations = payload.get("LocatieLijst", [])
        if not isinstance(locations, list) or len(locations) != 1:
            return "Exactly one location is required"
        if not isinstance(locations[0], dict) or locations[0].get("Code") != "hoekvanholland":
            return "Only Hoek van Holland is allowed"
        metadata_list = payload.get("AquoPlusWaarnemingMetadataLijst", [])
        if not isinstance(metadata_list, list) or len(metadata_list) != 1:
            return "Exactly one wind parameter is required"
        item = metadata_list[0]
        metadata = item.get("AquoMetadata", {}) if isinstance(item, dict) else {}
        if aquo_code(metadata, "Compartiment") != "LT":
            return "Only air measurements are allowed"
        if aquo_code(metadata, "Grootheid") not in ("WINDSHD", "WINDRTG"):
            return "Only wind speed or direction is allowed"
        observation_metadata = item.get("WaarnemingMetadata", {})
        if not isinstance(observation_metadata, dict) or observation_metadata.get(
            "OpdrachtgevendeInstantieLijst"
        ) != ["RIKZ_METEO"]:
            return "Only RIKZ meteorological observations are allowed"
        return None

    return "Endpoint is not allowed"


class Handler(BaseHTTPRequestHandler):
    server_version = "AIS-Catcher-RWS-relay/1.0"
    sys_version = ""

    def _allowed_origin(self):
        origin = self.headers.get("Origin", "")
        try:
            parsed_origin = urlsplit(origin)
            request_host = urlsplit("//" + self.headers.get("Host", "")).hostname
            return (
                origin
                if parsed_origin.scheme in ("http", "https")
                and not parsed_origin.username
                and not parsed_origin.password
                and parsed_origin.path in ("", "/")
                and not parsed_origin.query
                and not parsed_origin.fragment
                and parsed_origin.port == 8119
                and parsed_origin.hostname == request_host
                else None
            )
        except ValueError:
            return None

    def _cors_headers(self):
        origin = self._allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if self.path not in ALLOWED_PATHS:
            self.send_error(404)
            return
        if not self._allowed_origin():
            self.send_error(403, "Origin must match AIS-Catcher on port 8119")
            return
        self.send_response(204)
        self.send_header("Allow", "POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path not in ALLOWED_PATHS:
            self._send_json(404, {"error": "Endpoint is not allowed"})
            return
        if not self._allowed_origin():
            self._send_json(403, {"error": "Origin must match AIS-Catcher on port 8119"})
            return
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._send_json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid Content-Length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "Request body size is not allowed"})
            return
        try:
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "Invalid JSON body"})
            return
        validation_error = validate_request(self.path, payload)
        if validation_error:
            self._send_json(400, {"error": validation_error})
            return

        upstream_request = Request(
            UPSTREAM + self.path,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "AIS-Catcher-RWS-relay/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(upstream_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = response.status
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
                content_type = response.headers.get("Content-Type", "application/json")
        except HTTPError as error:
            status = error.code
            response_body = error.read(MAX_RESPONSE_BYTES + 1)
            content_type = error.headers.get("Content-Type", "application/json")
        except (TimeoutError, socket.timeout):
            self._send_json(504, {"error": "RWS request timed out"})
            return
        except URLError as error:
            self._send_json(502, {"error": "RWS connection failed", "detail": str(error.reason)})
            return
        except Exception as error:  # Keep upstream errors visible without exposing a traceback.
            self._send_json(502, {"error": "RWS relay failed", "detail": str(error)})
            return

        if len(response_body) > MAX_RESPONSE_BYTES:
            self._send_json(502, {"error": "RWS response exceeded the size limit"})
            return
        if status == 204:
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        if response_body:
            self.wfile.write(response_body)

    def log_message(self, fmt, *args):
        # Log method/path/status only; never log query bodies or user information.
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = Server((HOST, PORT), Handler)
    print("AIS-Catcher RWS relay listening on %s:%s" % (HOST, PORT), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
