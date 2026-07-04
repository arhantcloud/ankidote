# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import enum
import json
import logging
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from errno import EPROTOTYPE
from http import HTTPStatus
from pathlib import Path

import flask
import stringcase
import waitress.wasyncore
from flask import Response, abort, request
from waitress.server import create_server

import aqt
import aqt.main
import aqt.operations
from anki import hooks
from anki.collection import OpChangesOnly, Progress, SearchNode
from anki.decks import UpdateDeckConfigs, UpdateDeckConfigsMode
from anki.scheduler.v3 import SchedulingStatesWithContext, SetSchedulingStatesRequest
from anki.utils import dev_mode
from aqt.changenotetype import ChangeNotetypeDialog
from aqt.deckoptions import DeckOptionsDialog
from aqt.operations import on_op_finished
from aqt.operations.deck import update_deck_configs as update_deck_configs_op
from aqt.progress import ProgressUpdate
from aqt.qt import *
from aqt.utils import aqt_data_path, show_warning, tr

# https://forums.ankiweb.net/t/anki-crash-when-using-a-specific-deck/22266
waitress.wasyncore._DISCONNECTED = waitress.wasyncore._DISCONNECTED.union({EPROTOTYPE})  # type: ignore

logger = logging.getLogger(__name__)
app = flask.Flask(__name__, root_path="/fake")


@dataclass
class LocalFileRequest:
    # base folder, eg media folder
    root: str
    # path to file relative to root folder
    path: str
    # collection media is untrusted user content; add-on web exports are not
    untrusted: bool = True


UNTRUSTED_MEDIA_CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'none'",
        "connect-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "child-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "sandbox",
    )
)


def _editor_content_security_policy(port: int) -> str:
    csp_paths = (
        f"http://127.0.0.1:{port}/_anki/",
        f"http://127.0.0.1:{port}/_addons/",
    )
    return "; ".join((f"script-src {' '.join(csp_paths)}",))


@dataclass
class BundledFileRequest:
    # path relative to aqt data folder
    path: str


@dataclass
class NotFound:
    message: str


DynamicRequest = Callable[[], Response]


class PageContext(enum.IntEnum):
    UNKNOWN = enum.auto()
    EDITOR = enum.auto()
    REVIEWER = enum.auto()
    PREVIEWER = enum.auto()
    CARD_LAYOUT = enum.auto()
    DECK_OPTIONS = enum.auto()
    # something in /_anki/pages/
    NON_LEGACY_PAGE = enum.auto()
    # Do not use this if you present user content (e.g. content from cards), as it's a
    # security issue.
    ADDON_PAGE = enum.auto()


@dataclass
class LegacyPage:
    html: str
    context: PageContext


class MediaServer(threading.Thread):
    _ready = threading.Event()
    daemon = True

    def __init__(self, mw: aqt.main.AnkiQt) -> None:
        super().__init__()
        self.is_shutdown = False
        # map of webview ids to pages
        self._legacy_pages: dict[int, LegacyPage] = {}

    def run(self) -> None:
        try:
            desired_host = os.getenv("ANKI_API_HOST", "127.0.0.1")
            desired_port = int(os.getenv("ANKI_API_PORT") or 0)
            self.server = create_server(
                app,
                host=desired_host,
                port=desired_port,
                clear_untrusted_proxy_headers=True,
            )
            logger.info(
                "Serving on http://%s:%s",
                self.server.effective_host,  # type: ignore[union-attr]
                self.server.effective_port,  # type: ignore[union-attr]
            )

            self._ready.set()
            self.server.run()

        except Exception:
            if not self.is_shutdown:
                raise

    def shutdown(self) -> None:
        self.is_shutdown = True
        sockets = list(self.server._map.values())  # type: ignore
        for socket in sockets:
            socket.handle_close()
        # https://github.com/Pylons/webtest/blob/4b8a3ebf984185ff4fefb31b4d0cf82682e1fcf7/webtest/http.py#L93-L104
        self.server.task_dispatcher.shutdown()

    def getPort(self) -> int:
        self._ready.wait()
        return int(self.server.effective_port)  # type: ignore

    def set_page_html(
        self, id: int, html: str, context: PageContext = PageContext.UNKNOWN
    ) -> None:
        self._legacy_pages[id] = LegacyPage(html, context)

    def get_page(self, id: int) -> LegacyPage | None:
        return self._legacy_pages.get(id)

    def get_page_html(self, id: int) -> str | None:
        if page := self.get_page(id):
            return page.html
        else:
            return None

    def get_page_context(self, id: int) -> PageContext | None:
        if page := self.get_page(id):
            return page.context
        else:
            return None

    def clear_page_html(self, id: int) -> None:
        try:
            del self._legacy_pages[id]
        except KeyError:
            pass


@app.route("/favicon.ico")
def favicon() -> Response:
    request = BundledFileRequest(os.path.join("imgs", "favicon.ico"))
    return _handle_builtin_file_request(request)


def _mime_for_path(path: str) -> str:
    "Mime type for provided path/filename."

    _, ext = os.path.splitext(path)
    ext = ext.lower()

    # Badly-behaved apps on Windows can alter the standard mime types in the registry, which can completely
    # break Anki's UI. So we hard-code the most common extensions.
    mime_types = {
        ".css": "text/css",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".html": "text/html",
        ".htm": "text/html",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".json": "application/json",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".ogg": "audio/ogg",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }

    if mime := mime_types.get(ext):
        return mime
    else:
        # fallback to mimetypes, which may consult the registry
        mime, _encoding = mimetypes.guess_type(path)
        return mime or "application/octet-stream"


def _text_response(code: HTTPStatus, text: str) -> Response:
    """Return an error message.

    Response is returned as text/plain, so no escaping of untrusted
    input is required."""
    resp = flask.make_response(text, code)
    resp.headers["Content-type"] = "text/plain"
    return resp


class UnsafePathException(Exception):
    def __init__(self, path: str):
        super().__init__(f"Invalid path: {path}")


def ensure_safe_path(base_dir: str | Path, path: str | Path) -> str:
    base_dir = os.path.realpath(base_dir)
    path = os.path.normpath(path)
    fullpath = os.path.abspath(os.path.join(base_dir, path))

    # protect against directory traversal: https://security.openstack.org/guidelines/dg_using-file-paths.html
    if not fullpath.startswith(base_dir + os.sep):
        raise UnsafePathException(path)
    return fullpath


_LOCALHOST_HOSTS = ("127.0.0.1", "localhost", "[::1]")

_ALLOWED_ORIGIN_PREFIXES = tuple(
    f"{scheme}{host}" for scheme in ("http://", "https://") for host in _LOCALHOST_HOSTS
)


def is_localhost_origin(origin: str) -> bool:
    for prefix in _ALLOWED_ORIGIN_PREFIXES:
        if (
            origin == prefix
            or origin.startswith(prefix + ":")
            or origin.startswith(prefix + "/")
        ):
            return True
    return False


def _handle_local_file_request(request: LocalFileRequest) -> Response:
    directory = request.root
    path = request.path
    try:
        isdir = os.path.isdir(os.path.join(directory, path))
    except ValueError:
        return _text_response(
            HTTPStatus.BAD_REQUEST, f"Path for '{directory} - {path}' is too long!"
        )

    fullpath = ensure_safe_path(directory, path)

    if isdir:
        return _text_response(
            HTTPStatus.FORBIDDEN,
            f"Path for '{directory} - {path}' is a directory (not supported)!",
        )

    try:
        mimetype = _mime_for_path(fullpath)
        if os.path.exists(fullpath):
            if fullpath.endswith(".css"):
                # caching css files prevents flicker in the webview, but we want
                # a short cache
                max_age = 10
            elif fullpath.endswith(".js"):
                # don't cache js files
                max_age = 0
            else:
                max_age = 60 * 60
            response = flask.send_file(
                fullpath,
                mimetype=mimetype,
                conditional=True,
                max_age=max_age,
                download_name="foo",  # type: ignore[call-arg]
            )
            if request.untrusted:
                # Prevent user-provided HTML/SVG from running as an active document.
                response.headers["Content-Security-Policy"] = UNTRUSTED_MEDIA_CSP
            return response
        else:
            print(f"Not found: {path}")
            return _text_response(HTTPStatus.NOT_FOUND, f"Invalid path: {path}")

    except Exception as error:
        if dev_mode:
            print(
                "Caught HTTP server exception,\n%s"
                % "".join(traceback.format_exception(*sys.exc_info())),
            )

        # swallow it - user likely surfed away from
        # review screen before an image had finished
        # downloading
        return _text_response(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))


def _builtin_data(path: str) -> bytes:
    """Return data from file in aqt/data folder."""
    full_path = ensure_safe_path(aqt_data_path().parent, path)
    with open(full_path, "rb") as f:
        return f.read()


def _handle_builtin_file_request(request: BundledFileRequest) -> Response:
    path = request.path
    # do we need to serve the fallback page?
    immutable = "immutable" in path
    if path.startswith("sveltekit/") and not immutable:
        path = "sveltekit/index.html"
    mimetype = _mime_for_path(path)
    data_path = f"data/web/{path}"
    try:
        data = _builtin_data(data_path)
        response = Response(data, mimetype=mimetype)
        if immutable:
            response.headers["Cache-Control"] = "max-age=31536000"
        return response
    except FileNotFoundError:
        if dev_mode:
            print(f"404: {data_path}")
        resp = _text_response(HTTPStatus.NOT_FOUND, f"Invalid path: {path}")
        # we're including the path verbatim in our response, so we need to either use
        # plain text, or escape HTML characters to avoid reflecting untrusted input
        resp.headers["Content-type"] = "text/plain"
        return resp
    except Exception as error:
        if dev_mode:
            print(
                "Caught HTTP server exception,\n%s"
                % "".join(traceback.format_exception(*sys.exc_info())),
            )

        # swallow it - user likely surfed away from
        # review screen before an image had finished
        # downloading
        return _text_response(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))


@app.route("/<path:pathin>", methods=["GET", "POST"])
def handle_request(pathin: str) -> Response:
    if os.environ.get("ANKI_API_HOST") != "0.0.0.0":
        host = request.headers.get("Host", "").lower()
        origin = request.headers.get("Origin", "").lower()
        allowed_hosts = tuple(f"{h}:" for h in _LOCALHOST_HOSTS)
        if not any(host.startswith(h) for h in allowed_hosts):
            logger.warning("denied non-local host: %s", host)
            abort(403)
        if origin and not is_localhost_origin(origin):
            logger.warning("denied non-local origin: %s", origin)
            abort(403)

    req = _extract_request(pathin)
    logger.debug("%s /%s", flask.request.method, pathin)

    try:
        if isinstance(req, NotFound):
            print(req.message)
            return _text_response(HTTPStatus.NOT_FOUND, f"Invalid path: {pathin}")
        elif callable(req):
            return _handle_dynamic_request(req)
        elif isinstance(req, BundledFileRequest):
            return _handle_builtin_file_request(req)
        elif isinstance(req, LocalFileRequest):
            return _handle_local_file_request(req)
        else:
            return _text_response(HTTPStatus.FORBIDDEN, f"unexpected request: {pathin}")
    except UnsafePathException as exc:
        return _text_response(HTTPStatus.FORBIDDEN, str(exc))


def is_sveltekit_page(path: str) -> bool:
    page_name = path.split("/")[0]
    return page_name in [
        "graphs",
        "congrats",
        "card-info",
        "change-notetype",
        "deck-options",
        "import-anki-package",
        "import-csv",
        "import-page",
        "image-occlusion",
        "ankidote",
    ]


def _extract_internal_request(
    path: str,
) -> BundledFileRequest | DynamicRequest | NotFound | None:
    "Catch /_anki references and rewrite them to web export folder."
    if is_sveltekit_page(path):
        path = f"_anki/sveltekit/_app/{path}"
    if path.startswith("_app/"):
        path = path.replace("_app", "_anki/sveltekit/_app")

    prefix = "_anki/"
    if not path.startswith(prefix):
        return None

    dirname = os.path.dirname(path)
    filename = os.path.basename(path)
    additional_prefix = None

    if dirname == "_anki":
        if flask.request.method == "POST":
            return _extract_collection_post_request(filename)
        elif get_handler := _extract_dynamic_get_request(filename):
            return get_handler

        # remap legacy top-level references
        base, ext = os.path.splitext(filename)
        if ext == ".css":
            additional_prefix = "css/"
        elif ext == ".js":
            if base in ("jquery-ui", "jquery", "plot"):
                additional_prefix = "js/vendor/"
            else:
                additional_prefix = "js/"
    # handle requests for vendored libraries
    elif dirname == "_anki/js/vendor":
        base, ext = os.path.splitext(filename)

        if base == "jquery":
            base = "jquery.min"
            additional_prefix = "js/vendor/"

        elif base == "jquery-ui":
            base = "jquery-ui.min"
            additional_prefix = "js/vendor/"

    if additional_prefix:
        oldpath = path
        path = f"{prefix}{additional_prefix}{base}{ext}"
        print(f"legacy {oldpath} remapped to {path}")

    return BundledFileRequest(path=path[len(prefix) :])


def _extract_addon_request(path: str) -> LocalFileRequest | NotFound | None:
    "Catch /_addons references and rewrite them to addons folder."
    prefix = "_addons/"
    if not path.startswith(prefix):
        return None

    addon_path = path[len(prefix) :]

    try:
        manager = aqt.mw.addonManager
    except AttributeError as error:
        if dev_mode:
            print(f"_redirectWebExports: {error}")
        return None

    try:
        addon, sub_path = addon_path.split("/", 1)
    except ValueError:
        return None
    if not addon:
        return None

    pattern = manager.getWebExports(addon)
    if not pattern:
        return None

    if re.fullmatch(pattern, sub_path):
        return LocalFileRequest(
            root=manager.addonsFolder(), path=addon_path, untrusted=False
        )

    return NotFound(message=f"couldn't locate item in add-on folder {path}")


def _extract_request(
    path: str,
) -> LocalFileRequest | BundledFileRequest | DynamicRequest | NotFound:
    if internal := _extract_internal_request(path):
        return internal
    elif addon := _extract_addon_request(path):
        return addon

    if not aqt.mw.col:
        return NotFound(message=f"collection not open, ignore request for {path}")

    path = hooks.media_file_filter(path)
    return LocalFileRequest(root=aqt.mw.col.media.dir(), path=path)


def congrats_info() -> bytes:
    if not aqt.mw.col.sched._is_finished():
        aqt.mw.taskman.run_on_main(lambda: aqt.mw.moveToState("overview"))
    return raw_backend_request("congrats_info")()


def get_deck_configs_for_update() -> bytes:
    return aqt.mw.col._backend.get_deck_configs_for_update_raw(request.data)


def _on_update_deck_configs_success(input: UpdateDeckConfigs) -> None:
    is_compute_all = (
        input.mode == UpdateDeckConfigsMode.UPDATE_DECK_CONFIGS_MODE_COMPUTE_ALL_PARAMS
    )
    if not is_compute_all and isinstance(
        window := aqt.mw.app.activeModalWidget(), DeckOptionsDialog
    ):
        window.reject()


def update_deck_configs() -> bytes:
    # the regular change tracking machinery expects to be started on the main
    # thread and uses a callback on success, so we need to run this op on
    # main, and return immediately from the web request

    input = UpdateDeckConfigs()
    input.ParseFromString(request.data)

    def on_progress(progress: Progress, update: ProgressUpdate) -> None:
        if progress.HasField("compute_memory"):
            val = progress.compute_memory
            update.max = val.total_cards
            update.value = val.current_cards
            update.label = val.label
        elif progress.HasField("compute_params"):
            val2 = progress.compute_params
            # prevent an indeterminate progress bar from appearing at the start of each preset
            update.max = max(val2.total, 1)
            update.value = val2.current
            pct = str(int(val2.current / val2.total * 100) if val2.total > 0 else 0)
            label = tr.deck_config_optimizing_preset(
                current_count=val2.current_preset, total_count=val2.total_presets
            )
            if val2.reviews:
                reviews = tr.deck_config_percent_of_reviews(
                    pct=pct, reviews=val2.reviews
                )
            else:
                reviews = tr.qt_misc_processing()

            update.label = label + "\n" + reviews
        else:
            return
        if update.user_wants_abort:
            update.abort = True

    def handle_on_main() -> None:
        update_deck_configs_op(parent=aqt.mw, input=input).success(
            lambda _: _on_update_deck_configs_success(input)
        ).with_backend_progress(on_progress).run_in_background()

    aqt.mw.taskman.run_on_main(handle_on_main)
    return b""


def get_scheduling_states_with_context() -> bytes:
    return SchedulingStatesWithContext(
        states=aqt.mw.reviewer.get_scheduling_states(),
        context=aqt.mw.reviewer.get_scheduling_context(),
    ).SerializeToString()


def set_scheduling_states() -> bytes:
    states = SetSchedulingStatesRequest()
    states.ParseFromString(request.data)
    aqt.mw.reviewer.set_scheduling_states(states)
    return b""


def import_done() -> bytes:
    def update_window_modality() -> None:
        if window := aqt.mw.app.activeModalWidget():
            from aqt.import_export.import_dialog import ImportDialog

            if isinstance(window, ImportDialog):
                window.hide()
                window.setWindowModality(Qt.WindowModality.NonModal)
                window.show()

    aqt.mw.taskman.run_on_main(update_window_modality)
    return b""


def import_request(endpoint: str) -> bytes:
    output = raw_backend_request(endpoint)()
    response = OpChangesOnly()
    response.ParseFromString(output)

    def handle_on_main() -> None:
        window = aqt.mw.app.activeModalWidget()
        on_op_finished(aqt.mw, response, window)

    aqt.mw.taskman.run_on_main(handle_on_main)

    return output


def import_csv() -> bytes:
    return import_request("import_csv")


def import_anki_package() -> bytes:
    return import_request("import_anki_package")


def import_json_file() -> bytes:
    return import_request("import_json_file")


def import_json_string() -> bytes:
    return import_request("import_json_string")


def search_in_browser() -> bytes:
    node = SearchNode()
    node.ParseFromString(request.data)

    def handle_on_main() -> None:
        aqt.dialogs.open("Browser", aqt.mw, search=(node,))

    aqt.mw.taskman.run_on_main(handle_on_main)

    return b""


def change_notetype() -> bytes:
    data = request.data

    def handle_on_main() -> None:
        window = aqt.mw.app.activeModalWidget()
        if isinstance(window, ChangeNotetypeDialog):
            window.save(data)

    aqt.mw.taskman.run_on_main(handle_on_main)
    return b""


def deck_options_require_close() -> bytes:
    def handle_on_main() -> None:
        window = aqt.mw.app.activeModalWidget()
        if isinstance(window, DeckOptionsDialog):
            window.require_close()

    # on certain linux systems, askUser's QMessageBox.question unsets the active window
    # so we wait for the next event loop before querying the next current active window
    aqt.mw.taskman.run_on_main(lambda: QTimer.singleShot(0, handle_on_main))
    return b""


def deck_options_ready() -> bytes:
    def handle_on_main() -> None:
        window = aqt.mw.app.activeModalWidget()
        if isinstance(window, DeckOptionsDialog):
            window.set_ready()

    aqt.mw.taskman.run_on_main(handle_on_main)
    return b""


def save_custom_colours() -> bytes:
    colors = [
        QColorDialog.customColor(i).name(QColor.NameFormat.HexRgb)
        for i in range(QColorDialog.customCount())
    ]
    aqt.mw.col.set_config("customColorPickerPalette", colors)
    return b""


# Ankidote diagnostic (CAT/IRT) — see AntiPlan/diagnostic-cat-plan.md §4.4.
# The runner is stateful and lives on the main window for the duration of an
# attempt. The engine is synchronous and cheap, so it runs on the request
# thread; each response payload is JSON rather than protobuf.


def _ankidote_json_response(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def ankidote_diag_start() -> bytes:
    from anki.ankidote import DiagnosticRunner

    body = json.loads(request.data or b"{}")
    confidence = body.get("confidence") or None
    runner = DiagnosticRunner(confidence=confidence)
    aqt.mw._ankidote_diag = runner
    return _ankidote_json_response(runner.state())


def ankidote_diag_answer() -> bytes:
    body = json.loads(request.data or b"{}")
    runner = getattr(aqt.mw, "_ankidote_diag", None)
    if runner is None:
        raise RuntimeError("diagnostic not started")
    revealed = bool(body.get("revealed") or body.get("gaveUp"))
    runner.answer(
        str(body["itemId"]), int(body["chosenChoice"]), revealed=revealed
    )
    return _ankidote_json_response(runner.state())


def ankidote_diag_state() -> bytes:
    runner = getattr(aqt.mw, "_ankidote_diag", None)
    if runner is None:
        raise RuntimeError("diagnostic not started")
    return _ankidote_json_response(runner.state())


# Persisted Ankidote state (diagnostic result, study plan, user inputs). The
# collection config is the local working store (instant + offline). When the
# user is signed in to Firebase it is also mirrored to Firestore per-user, so it
# syncs across devices without AnkiWeb (see aqt.ankidote.sync).
_ANKIDOTE_CONFIG_KEY = "ankidote"


def _ankidote_logged_in() -> bool:
    from aqt.ankidote import sync as ankidote_sync

    return ankidote_sync.is_logged_in()


def _ankidote_state_read() -> dict:
    state = aqt.mw.col.get_config(_ANKIDOTE_CONFIG_KEY, {}) or {}
    if not isinstance(state, dict):
        state = {}
    return state


def _ankidote_state_write(state: dict) -> None:
    aqt.mw.col.set_config(_ANKIDOTE_CONFIG_KEY, state)
    # Mirror to Firestore when signed in (best-effort; never blocks the write).
    try:
        from aqt.ankidote import sync as ankidote_sync

        ankidote_sync.push_app_blob(aqt.mw)
    except Exception as exc:
        print("ankidote: app blob push skipped:", exc)


def ankidote_state_get() -> bytes:
    payload = dict(_ankidote_state_read())
    payload["loggedIn"] = _ankidote_logged_in()
    return _ankidote_json_response(payload)


def ankidote_state_set() -> bytes:
    body = json.loads(request.data or b"{}")
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    state = _ankidote_state_read()
    # Merge top-level keys (e.g. "diagnostic", "plan") so partial saves from
    # different screens don't clobber each other.
    state.update(body)
    _ankidote_state_write(state)
    payload = dict(state)
    payload["loggedIn"] = _ankidote_logged_in()
    return _ankidote_json_response(payload)


# --- Firebase auth endpoints -------------------------------------------


def ankidote_auth_state() -> bytes:
    from aqt.ankidote import sync as ankidote_sync

    return _ankidote_json_response(ankidote_sync.auth_state())


def ankidote_auth_login() -> bytes:
    from aqt.ankidote import sync as ankidote_sync
    from aqt.ankidote.firebase import FirebaseError

    body = json.loads(request.data or b"{}")
    email = str(body.get("email", "")).strip()
    password = str(body.get("password", ""))
    create = bool(body.get("create", False))
    if not email or not password:
        return _ankidote_json_response(
            {"ok": False, "error": "Enter your email and password."}
        )
    try:
        ankidote_sync.login(aqt.mw, email, password, create)
    except FirebaseError as exc:
        return _ankidote_json_response({"ok": False, "error": str(exc)})
    payload = ankidote_sync.auth_state()
    payload["ok"] = True
    return _ankidote_json_response(payload)


def ankidote_auth_logout() -> bytes:
    from aqt.ankidote import sync as ankidote_sync

    ankidote_sync.logout(aqt.mw)
    return _ankidote_json_response({"ok": True, "loggedIn": False})


# Basic study loop (PRD §5, minimal): pick lowest-scoring topic -> study its
# Anki cards -> 2-3 practice problems -> re-estimate theta -> update score.
# The live problem session lives on the main window for the attempt's duration.


# A topic's flashcard deck earns a problem set once this many additional cards
# have crossed the 3-day interval since the last time problems were served for
# it (i.e. this many cards left the "interval < 3 days" bucket). This is the
# "significant change in amount of deck mastered" gate, measured relative to
# the recorded baseline rather than an absolute maturity level.
_ANKIDOTE_MASTERY_DELTA = 5

# A card counts as "mastered" once its interval exceeds this many days.
_ANKIDOTE_MASTERY_IVL = 3


def _ankidote_theta_for(diagnostic: dict, topic: str) -> float:
    for entry in diagnostic.get("topicScores", []) or []:
        if entry.get("topic") == topic:
            return float(entry.get("theta", 0.0))
    return 0.0


def _ankidote_topic_counts(col, topic: str) -> tuple[int, int, int]:
    """(immature, mastered, total) for a topic's deck.

    Immature == interval < 3 days (still includes new/learning cards); mastered
    is everything else. The immature count is what the problem gate tracks over
    time so it can detect cards graduating past the 3-day mark.
    """
    from anki.ankidote import topics as ankidote_topics

    name = ankidote_topics.deck_name(topic)
    total = len(col.find_cards(f'deck:"{name}"'))
    immature = len(col.find_cards(f'deck:"{name}" prop:ivl<{_ANKIDOTE_MASTERY_IVL}'))
    return immature, total - immature, total


def _ankidote_done_for_day(col, topic: str) -> bool:
    """True when a topic's deck has no cards left to study today."""
    from anki.ankidote import topics as ankidote_topics

    name = ankidote_topics.deck_name(topic)
    did = col.decks.id_for_name(name)
    if did is None:
        return False  # not set up yet -> studying will create/seed it
    if len(col.find_cards(f'deck:"{name}"')) == 0:
        return False  # empty -> let the loop seed/study it
    node = col.sched.deck_due_tree(did)
    if node is None:
        return False
    return (node.new_count + node.learn_count + node.review_count) == 0


def _ankidote_immature_baseline(state: dict, topic: str):
    """Recorded immature count at the last problem set, or None if unset."""
    entry = (state.get("topicMastery") or {}).get(topic, {})
    return entry.get("immatureAtLastProblems")


def _ankidote_mastery_delta(col, state: dict, topic: str) -> int:
    """Cards that have matured past the 3-day mark since the problem baseline."""
    immature, _mastered, _total = _ankidote_topic_counts(col, topic)
    baseline = _ankidote_immature_baseline(state, topic)
    if baseline is None:
        baseline = immature
    return baseline - immature


def ankidote_reviewer_topic() -> str | None:
    """The Ankidote topic of the currently selected deck, if any (for the
    reviewer's loop/problems button)."""
    deck = aqt.mw.col.decks.current()
    name = deck.get("name", "") if deck else ""
    prefix = "Ankidote "
    return name[len(prefix):] if name.startswith(prefix) else None


def ankidote_problems_ready(topic: str) -> bool:
    """Whether enough cards have matured for this topic to unlock its problem
    set (the loop's maturity threshold), used to light up the reviewer button.

    Unlike the in-loop gate this does not require the deck to be *done* for the
    day — the point is to surface "problems unlocked" mid-review the moment the
    threshold is crossed. A topic still owing its organize lesson is not ready.
    """
    try:
        from anki.ankidote import organize

        col = aqt.mw.col
        state = _ankidote_state_read()
        if organize.gate_topic(state, topic):
            return False
        return _ankidote_mastery_delta(col, state, topic) >= _ANKIDOTE_MASTERY_DELTA
    except Exception as exc:
        print("ankidote: problems-ready check skipped:", exc)
        return False


def _ankidote_topic_score(diagnostic: dict, topic: str):
    for entry in diagnostic.get("topicScores", []) or []:
        if entry.get("topic") == topic:
            return entry.get("score")
    return None


def _ankidote_loop_topic_payload(
    state: dict, topic: str, section: str, weight: float, phase: str, **extra
) -> dict:
    from anki.ankidote import topics as ankidote_topics

    diagnostic = state.get("diagnostic") or {}
    overall = None
    if diagnostic.get("low") is not None and diagnostic.get("high") is not None:
        overall = {"low": diagnostic["low"], "high": diagnostic["high"]}
    payload = {
        "phase": phase,
        "topic": topic,
        "section": section,
        "sectionLabel": ankidote_topics.SECTION_LABELS.get(section, section),
        "deck": ankidote_topics.deck_name(topic),
        "weightPct": round(weight * 100),
        "target": int((state.get("plan") or {}).get("desiredScore", 705)),
        "hasDiagnostic": bool(diagnostic.get("topicScores")),
        "topicScore": _ankidote_topic_score(diagnostic, topic),
        "overall": overall,
    }
    if phase == "cards":
        # Surface the problem-gate status so the "Do problems" button can be
        # shown but greyed out until enough cards have matured.
        col = aqt.mw.col
        done = _ankidote_done_for_day(col, topic)
        immature, _mastered, _total = _ankidote_topic_counts(col, topic)
        baseline = _ankidote_immature_baseline(state, topic)
        if baseline is None:
            baseline = immature
        delta = baseline - immature
        remaining = max(0, _ANKIDOTE_MASTERY_DELTA - delta)
        payload["problemsUnlocked"] = bool(done and remaining == 0)
        payload["problemsDoneForDay"] = bool(done)
        payload["problemsRemaining"] = remaining
        payload["masteryGained"] = delta

    payload.update(extra)
    return payload


def _ankidote_organize_gate(state: dict, topic: str) -> dict | None:
    """If ``topic`` must be organized first, the loop payload that redirects to
    the mandatory concept lesson; otherwise ``None``."""
    from anki.ankidote import organize
    from anki.ankidote import topics as ankidote_topics

    if not organize.gate_topic(state, topic):
        return None
    section = ankidote_topics.section_for_topic(topic)
    return {
        "phase": "organize",
        "topic": topic,
        "section": section,
        "sectionLabel": ankidote_topics.SECTION_LABELS.get(section, section),
    }


def _ankidote_common_fields(state: dict) -> dict:
    """Global, toggle-gated fields merged into every logged-in loop payload.

    The backend is authoritative for gating: the UI only renders a feature's
    affordance when its commitment flag is on here. Includes the commitment
    flags, the Antidote plan vial, the weekly problem quota, and the check-in /
    organize cadence status.
    """
    from anki.ankidote import commitments, plan_projection, scores

    flags = commitments.flags(state)
    fields: dict = {"commitments": flags}

    # Antidote vial + weekly problem quota (throughput pace, PRD §3.2).
    fields["planVial"] = plan_projection.vial_state(state)
    if flags.get("problems"):
        fields["quota"] = plan_projection.quota(state)

    # Diagnostic check-in cadence (PRD §6.5): due after the cadence elapses,
    # blocking once overdue past the grace window.
    if flags.get("checkins"):
        diagnostic = state.get("diagnostic") or {}
        checkins = state.get("checkins") or {}
        every = int(checkins.get("everyDays", 7))
        grace = int(checkins.get("graceDays", 3))
        last = checkins.get("lastAt") or diagnostic.get("takenAt")
        if last and diagnostic.get("topicScores"):
            days = (time.time() * 1000 - last) / 86_400_000
            fields["checkinDue"] = days >= every
            fields["checkinBlocking"] = days >= every + grace
            fields["checkinStaleTopics"] = scores.stalest_topics(diagnostic, k=3)

    return fields


def _ankidote_loop_state_payload() -> dict:
    """Loop payload augmented with the global toggle-gated feature fields."""
    payload = _ankidote_loop_base_payload()
    if payload.get("phase") != "login_required":
        payload.update(_ankidote_common_fields(_ankidote_state_read()))
    return payload


def _ankidote_loop_base_payload() -> dict:
    from anki.ankidote import topics as ankidote_topics

    # Studying / deck creation is only available while logged in; a logged-out
    # user is limited to the onboarding flow (diagnostic -> plan).
    if not _ankidote_logged_in():
        return {"phase": "login_required"}

    col = aqt.mw.col
    state = _ankidote_state_read()
    diagnostic = state.get("diagnostic") or {}
    target = int((state.get("plan") or {}).get("desiredScore", 705))

    # A pending mistake-review / reveal step takes precedence over everything so
    # a page refresh mid-review lands back on the same gated step.
    reveal = getattr(aqt.mw, "_ankidote_reveal", None)
    loop = getattr(aqt.mw, "_ankidote_loop", None)
    if reveal is not None and loop is not None:
        return _ankidote_loop_topic_payload(
            state,
            loop.topic,
            loop.section,
            ankidote_topics.topic_weight(loop.topic),
            "reveal",
            **_ankidote_reveal_block(reveal),
        )

    # An in-progress organize lesson takes precedence over topic selection so a
    # refresh mid-lesson lands back on it.
    organize_sess = getattr(aqt.mw, "_ankidote_organize", None)
    if isinstance(organize_sess, dict) and organize_sess.get("phase") != "done":
        from anki.ankidote import organize as _organize

        return _organize.payload(organize_sess)

    # An active problem session takes precedence.
    if loop is not None:
        phase = "update" if loop.finished else "problems"
        payload = _ankidote_loop_topic_payload(
            state,
            loop.topic,
            loop.section,
            ankidote_topics.topic_weight(loop.topic),
            phase,
            result=loop.result() if loop.finished else None,
        )
        return payload

    # Topics the user chose to set aside for this study bout via "another topic".
    skipped = getattr(aqt.mw, "_ankidote_skipped", None)
    if not isinstance(skipped, set):
        skipped = set()

    # Just finished (or returned from) studying a topic's deck. Returning to
    # the loop must stay on the same topic (so the user can do its problems) —
    # only an explicit "Study a different topic" (which adds the topic to
    # `skipped`) or finishing its problem set moves the loop on.
    last = getattr(aqt.mw, "_ankidote_last_studied", None)
    if last and last not in skipped:
        gate = _ankidote_organize_gate(state, last)
        if gate is not None:
            return gate
        if _ankidote_done_for_day(col, last):
            # Deck is done for today: offer a problem set if enough cards have
            # matured since the baseline.
            immature, mastered, total = _ankidote_topic_counts(col, last)
            baseline = _ankidote_immature_baseline(state, last)
            if baseline is None:
                baseline = immature
            # How many cards have graduated past the 3-day mark since the
            # baseline (i.e. left the "interval < 3 days" bucket).
            delta = baseline - immature
            if delta >= _ANKIDOTE_MASTERY_DELTA:
                return _ankidote_loop_topic_payload(
                    state,
                    last,
                    ankidote_topics.section_for_topic(last),
                    ankidote_topics.topic_weight(last),
                    "problems_offer",
                    masteryGained=delta,
                    mastered=mastered,
                    total=total,
                )
            # Deck done but not enough cards have matured for a fresh problem
            # set yet. Stay on this same topic (cards phase, with the problems
            # button gated) rather than auto-jumping to a different topic.
        # Return to the topic being studied (this is what "← Loop" from the
        # reviewer and "Back to your Ankidote study loop" from congrats land
        # on). Problems for the topic remain reachable via the gated button.
        return _ankidote_loop_topic_payload(
            state,
            last,
            ankidote_topics.section_for_topic(last),
            ankidote_topics.topic_weight(last),
            "cards",
        )

    # Otherwise pick the lowest-scoring topic that's neither done for today nor
    # set aside via "another topic".
    return _ankidote_loop_select_payload(state, col, diagnostic, target, skipped)


def _ankidote_loop_select_payload(
    state: dict, col, diagnostic: dict, target: int, skipped: set
) -> dict:
    """Pick the next weakest topic and return its loop payload (cards or the
    mandatory organize gate); day-done / empty when nothing is left."""
    from anki.ankidote import topics as ankidote_topics

    done = {
        info.topic
        for info in ankidote_topics.topic_tree()
        if _ankidote_done_for_day(col, info.topic)
    }
    info = ankidote_topics.select_topic(diagnostic, target, exclude=done | skipped)
    if info is None and skipped:
        # Skipped everything that's left; clear the set-aside list and pick the
        # next-lowest topic that isn't done for the day.
        aqt.mw._ankidote_skipped = set()
        info = ankidote_topics.select_topic(diagnostic, target, exclude=done)
    if info is None:
        if done:
            # Everything is done for the day.
            overall = None
            if diagnostic.get("low") is not None:
                overall = {"low": diagnostic["low"], "high": diagnostic["high"]}
            return {"phase": "day_done", "target": target, "overall": overall}
        return {"phase": "empty"}
    gate = _ankidote_organize_gate(state, info.topic)
    if gate is not None:
        return gate
    return _ankidote_loop_topic_payload(
        state, info.topic, info.section, info.weight, "cards"
    )


def ankidote_loop_state() -> bytes:
    return _ankidote_json_response(_ankidote_loop_state_payload())


def ankidote_loop_start() -> bytes:
    from anki.ankidote import commitments
    from anki.ankidote import topics as ankidote_topics
    from anki.ankidote.loop import LoopSession

    if not _ankidote_logged_in():
        return _ankidote_json_response({"phase": "login_required"})

    body = json.loads(request.data or b"{}")
    state = _ankidote_state_read()
    diagnostic = state.get("diagnostic") or {}
    target = int((state.get("plan") or {}).get("desiredScore", 705))

    # Prefer an explicit topic (the one whose deck just earned a problem set);
    # otherwise select the lowest-scoring topic.
    topic = body.get("topic")
    if topic:
        section = ankidote_topics.section_for_topic(topic)
    else:
        info = ankidote_topics.select_topic(diagnostic, target)
        if info is None:
            return _ankidote_json_response({"phase": "empty"})
        topic, section = info.topic, info.section

    # Problems are blocked until the mandatory concept lesson is done.
    gate = _ankidote_organize_gate(state, topic)
    if gate is not None:
        return _ankidote_json_response(gate)

    # The chosen problems/hr pace sets how many problems this set serves.
    loop = LoopSession(
        topic,
        section,
        theta0=_ankidote_theta_for(diagnostic, topic),
        pace=commitments.pace(state, "problems"),
    )
    aqt.mw._ankidote_loop = loop
    aqt.mw._ankidote_reveal = None
    item = loop.next_problem()
    payload = _ankidote_loop_state_payload()
    payload["question"] = _ankidote_wire_problem(state, item)
    return _ankidote_json_response(payload)


def _ankidote_wire_problem(state: dict, item) -> dict | None:
    """Wire form of a problem, with any prior note-to-self attached."""
    from anki.ankidote import commitments
    from anki.ankidote import loop as ankidote_loop
    from anki.ankidote.loop import _problem_to_wire

    if item is None:
        return None
    wire = _problem_to_wire(item)
    # Pre-attempt callout: surface the note the user left the last time they
    # missed this problem (note-to-self, PRD §5.2).
    if commitments.enabled(state, "noteToSelf"):
        note = ankidote_loop.get_note(state, item.id)
        if note:
            wire["note"] = note.get("text", "")
    return wire


def _ankidote_reveal_block(reveal: dict) -> dict:
    """The reveal/mistake/note fields the UI renders after an answer."""
    verdict = reveal.get("verdict") or {}
    block: dict = {
        "reveal": {
            "correct": verdict.get("correct"),
            "score": verdict.get("score"),
            "correctChoice": verdict.get("correctChoice"),
            "correctRanking": verdict.get("correctRanking"),
            "choices": verdict.get("choices"),
            "chosenChoice": verdict.get("chosenChoice"),
            "ranking": verdict.get("ranking"),
            "explanation": verdict.get("explanation"),
            "impact": reveal.get("impact"),
        },
        "resolved": reveal.get("resolved", True),
    }
    if reveal.get("needMistake"):
        until = reveal.get("penaltyUntilMs")
        remaining = max(0, int(until) - int(time.time() * 1000)) if until else 0
        block["mistake"] = {
            "required": True,
            "resolved": reveal.get("resolved", False),
            "attempts": reveal.get("attempts", 0),
            "maxAttempts": _ANKIDOTE_MAX_EXPLANATION_ATTEMPTS,
            "hints": reveal.get("hints", []),
            "feedback": reveal.get("feedback", ""),
            "forced": reveal.get("forced", False),
            "solution": reveal.get("solution", ""),
            "penaltySeconds": reveal.get("penaltySeconds", 0),
            "penaltyRemainingMs": remaining,
            "savedSeconds": reveal.get("savedSeconds", 0),
        }
    if reveal.get("noteDue"):
        block["noteToSelf"] = {
            "due": True,
            "saved": reveal.get("noteSaved", False),
            "problemId": verdict.get("problemId"),
        }
    return block


def _ankidote_apply_finish(loop) -> None:
    """Persist a finished topic's estimate + re-baseline its mastery gate."""
    from anki.ankidote import loop as ankidote_loop

    col = aqt.mw.col
    state = _ankidote_state_read()
    state = ankidote_loop.apply_result(state, loop.result())
    _ankidote_set_mastery_baseline(state, col, loop.topic)
    # Stamp when this topic was last measured, for check-in staleness ranking.
    _ankidote_stamp_measured(state, loop.topic)
    _ankidote_state_write(state)
    aqt.mw._ankidote_last_studied = None
    try:
        from aqt.ankidote import schedsync

        schedsync.push_scheduling(aqt.mw, topics=[loop.topic])
    except Exception as exc:
        print("ankidote: scheduling push skipped:", exc)


def _ankidote_stamp_measured(state: dict, topic: str) -> None:
    diagnostic = state.get("diagnostic") or {}
    for entry in diagnostic.get("topicScores", []) or []:
        if entry.get("topic") == topic:
            entry["measuredAt"] = int(time.time() * 1000)
            break


def ankidote_loop_answer() -> bytes:
    from anki.ankidote import commitments, scores

    body = json.loads(request.data or b"{}")
    loop = getattr(aqt.mw, "_ankidote_loop", None)
    if loop is None:
        raise RuntimeError("loop not started")

    ranking = body.get("ranking")
    ranking = [int(i) for i in ranking] if isinstance(ranking, list) else None
    chosen = body.get("chosenChoice")
    chosen = int(chosen) if chosen is not None else None

    # Answer-choice ranking is a server-side gate when the commitment is on.
    state = _ankidote_state_read()
    if commitments.enabled(state, "ranking") and ranking is None and chosen is None:
        raise ValueError("ranking required")

    revealed = bool(body.get("revealed") or body.get("gaveUp"))
    before = scores.score_range(loop.session.theta, loop.session.se)
    verdict = loop.answer(
        str(body["problemId"]), chosen, ranking=ranking, revealed=revealed
    )
    after = scores.score_range(loop.session.theta, loop.session.se)

    mistakes_on = commitments.enabled(state, "mistakes")
    explain_on = commitments.enabled(state, "explain")
    need_mistake = (mistakes_on or explain_on) and not verdict["correct"]

    note_due = False
    if not verdict["correct"]:
        from anki.ankidote import loop as ankidote_loop

        count = ankidote_loop.bump_miss(state, verdict["problemId"])
        note_due = (
            commitments.enabled(state, "noteToSelf")
            and count >= 2
            and ankidote_loop.get_note(state, verdict["problemId"]) is None
        )
        _ankidote_state_write(state)

    show_reveal = need_mistake or note_due or ranking is not None or (
        not verdict["correct"]
    )

    if not show_reveal:
        # Simple correct single-choice answer: advance immediately (old UX).
        aqt.mw._ankidote_reveal = None
        return _ankidote_advance_after_answer(loop)

    aqt.mw._ankidote_reveal = {
        "verdict": verdict,
        "needMistake": need_mistake,
        "resolved": not need_mistake,
        "attempts": 0,
        "hints": [],
        "feedback": "",
        "noteDue": note_due,
        "noteSaved": False,
        "impact": {
            "before": {"low": before.low, "high": before.high},
            "after": {"low": after.low, "high": after.high},
        },
    }
    return _ankidote_json_response(_ankidote_loop_state_payload())


def _ankidote_advance_after_answer(loop) -> bytes:
    """Finish the topic (if the set is done) or serve the next problem."""
    state = _ankidote_state_read()
    if loop.finished:
        _ankidote_apply_finish(loop)
        payload = _ankidote_loop_state_payload()
        payload["question"] = None
        return _ankidote_json_response(payload)
    nxt = loop.next_problem()
    payload = _ankidote_loop_state_payload()
    payload["question"] = _ankidote_wire_problem(state, nxt)
    return _ankidote_json_response(payload)


# Mistake-review gate: pass on a good explanation, or after this many incorrect
# tries the learner is let through but must sit out a penalty timer first.
_ANKIDOTE_MAX_EXPLANATION_ATTEMPTS = 2
_ANKIDOTE_PENALTY_SECONDS = 10


def ankidote_loop_mistake() -> bytes:
    """Grade the student's explanation; gate the next problem on getting it right.

    The learner passes on a genuinely correct explanation. After
    ``_ANKIDOTE_MAX_EXPLANATION_ATTEMPTS`` incorrect explanations they are let
    through anyway (the correct reasoning is shown), but a 10-second unskippable
    penalty timer must elapse before the next problem unlocks.
    """
    from anki.ankidote import ai
    from anki.ankidote import loop as ankidote_loop

    body = json.loads(request.data or b"{}")
    why = str(body.get("why", ""))
    reveal = getattr(aqt.mw, "_ankidote_reveal", None)
    if reveal is None:
        raise RuntimeError("no mistake pending")
    verdict = reveal["verdict"]
    attempts = int(reveal.get("attempts", 0))

    state = _ankidote_state_read()
    problem = {
        "stem": verdict.get("stem"),
        "choices": verdict.get("choices"),
        "correct": verdict.get("correctChoice"),
        "explanation": verdict.get("explanation"),
    }
    grade = ai.grade_explanation(state, problem, why, attempts)
    new_attempts = attempts + 1
    reveal["attempts"] = new_attempts
    reveal["feedback"] = grade.get("feedback", "")
    reveal["solution"] = grade.get("solution", "") or verdict.get("explanation", "")

    if grade["pass"]:
        reveal["resolved"] = True
        reveal["forced"] = False
        reveal["hints"] = []
        # Reward: the future re-practice time this resolved mistake saves.
        from anki.ankidote import problems as ankidote_problems

        item = ankidote_problems.get(verdict["problemId"])
        difficulty = float(getattr(item, "b", 0.0)) if item is not None else 0.0
        reveal["savedSeconds"] = ankidote_loop.time_saved_seconds(
            state, verdict["problemId"], difficulty
        )
        record = ankidote_loop.build_mistake_record(verdict, why, grade, new_attempts)
        ankidote_loop.record_mistake(state, record)
        _ankidote_state_write(state)
    elif new_attempts >= _ANKIDOTE_MAX_EXPLANATION_ATTEMPTS:
        # Two incorrect explanations: unlock the next problem but impose the
        # unskippable penalty timer and reveal the correct reasoning.
        reveal["resolved"] = True
        reveal["forced"] = True
        reveal["hints"] = grade.get("hints", [])
        reveal["penaltySeconds"] = _ANKIDOTE_PENALTY_SECONDS
        reveal["penaltyUntilMs"] = int(time.time() * 1000) + _ANKIDOTE_PENALTY_SECONDS * 1000
        record = ankidote_loop.build_mistake_record(verdict, why, grade, new_attempts)
        ankidote_loop.record_mistake(state, record)
        _ankidote_state_write(state)
    else:
        reveal["resolved"] = False
        reveal["forced"] = False
        reveal["hints"] = grade.get("hints", [])
    aqt.mw._ankidote_reveal = reveal
    return _ankidote_json_response(_ankidote_loop_state_payload())


def ankidote_loop_continue() -> bytes:
    """Leave the reveal step (once resolved + any penalty elapsed) and advance."""
    reveal = getattr(aqt.mw, "_ankidote_reveal", None)
    loop = getattr(aqt.mw, "_ankidote_loop", None)
    if reveal is not None:
        if not reveal.get("resolved", True):
            # Still gated on the explanation — bounce back to the reveal step.
            return _ankidote_json_response(_ankidote_loop_state_payload())
        until = reveal.get("penaltyUntilMs")
        if until and int(time.time() * 1000) < int(until):
            # Penalty timer still running — unskippable, even via the API.
            return _ankidote_json_response(_ankidote_loop_state_payload())
    aqt.mw._ankidote_reveal = None
    if loop is None:
        return _ankidote_json_response(_ankidote_loop_state_payload())
    return _ankidote_advance_after_answer(loop)


def ankidote_note() -> bytes:
    """Save a note-to-self for a repeatedly-missed problem."""
    from anki.ankidote import loop as ankidote_loop

    body = json.loads(request.data or b"{}")
    key = str(body.get("problemId", ""))
    text = str(body.get("text", "")).strip()
    if key and text:
        state = _ankidote_state_read()
        ankidote_loop.save_note(state, key, text)
        _ankidote_state_write(state)
    reveal = getattr(aqt.mw, "_ankidote_reveal", None)
    if isinstance(reveal, dict):
        reveal["noteSaved"] = True
        aqt.mw._ankidote_reveal = reveal
    return _ankidote_json_response(_ankidote_loop_state_payload())


def ankidote_loop_next() -> bytes:
    # Clear the finished session so a fresh topic is selected next.
    aqt.mw._ankidote_loop = None
    aqt.mw._ankidote_reveal = None
    return _ankidote_json_response(_ankidote_loop_state_payload())


def _ankidote_set_mastery_baseline(state: dict, col, topic: str) -> None:
    immature, _mastered, _total = _ankidote_topic_counts(col, topic)
    topic_mastery = state.setdefault("topicMastery", {})
    topic_mastery[topic] = {"immatureAtLastProblems": immature}


def ankidote_loop_skip() -> bytes:
    """Decline the offered problem set: re-baseline mastery and move on."""
    body = json.loads(request.data or b"{}")
    topic = body.get("topic")
    if topic:
        col = aqt.mw.col
        state = _ankidote_state_read()
        _ankidote_set_mastery_baseline(state, col, topic)
        _ankidote_state_write(state)
    aqt.mw._ankidote_last_studied = None
    return _ankidote_json_response(_ankidote_loop_state_payload())


def ankidote_loop_another() -> bytes:
    """Set the current topic aside and surface the next-lowest unfinished one."""
    body = json.loads(request.data or b"{}")
    topic = body.get("topic")
    skipped = getattr(aqt.mw, "_ankidote_skipped", None)
    if not isinstance(skipped, set):
        skipped = set()
    if topic:
        skipped.add(topic)
    aqt.mw._ankidote_skipped = skipped
    aqt.mw._ankidote_last_studied = None
    return _ankidote_json_response(_ankidote_loop_state_payload())


def ankidote_sort_decks() -> bytes:
    """Distribute the collection's existing cards into per-topic decks."""
    from anki.ankidote import decksort

    counts = decksort.sort_into_topics(aqt.mw.col)
    aqt.mw.taskman.run_on_main(aqt.mw.reset)
    return _ankidote_json_response({"counts": counts, "total": sum(counts.values())})


def ankidote_checkin_start() -> bytes:
    """Begin a stale-targeted mini-CAT to re-anchor the score range (PRD §6.5)."""
    from anki.ankidote import scores
    from anki.ankidote.runner import DiagnosticRunner

    if not _ankidote_logged_in():
        return _ankidote_json_response({"phase": "login_required"})

    state = _ankidote_state_read()
    diagnostic = state.get("diagnostic") or {}
    stale = scores.stalest_topics(diagnostic, k=3)
    # Warm-start each targeted topic from its current estimate.
    theta0 = {
        e.get("topic"): float(e.get("theta", 0.0))
        for e in diagnostic.get("topicScores", []) or []
        if e.get("topic") in set(stale)
    }
    runner = DiagnosticRunner(
        max_questions=10,
        topics=stale or None,
        theta0=theta0,
    )
    aqt.mw._ankidote_checkin = runner
    # Snapshot the affected topics' ranges for the before/after animation.
    before = {
        e.get("topic"): e.get("score")
        for e in diagnostic.get("topicScores", []) or []
        if not stale or e.get("topic") in set(stale)
    }
    payload = runner.state()
    payload["before"] = before
    return _ankidote_json_response(payload)


def ankidote_checkin_answer() -> bytes:
    body = json.loads(request.data or b"{}")
    runner = getattr(aqt.mw, "_ankidote_checkin", None)
    if runner is None:
        raise RuntimeError("check-in not started")
    runner.answer(str(body["itemId"]), int(body["chosenChoice"]))
    payload = runner.state()
    if runner.finished:
        state = _ankidote_state_read()
        after = _ankidote_apply_checkin(state, runner)
        _ankidote_state_write(state)
        aqt.mw._ankidote_checkin = None
        payload["after"] = after
        try:
            from aqt.ankidote import schedsync

            schedsync.push_scheduling(aqt.mw, topics=[s["topic"] for s in runner.topic_states()])
        except Exception as exc:
            print("ankidote: scheduling push skipped:", exc)
    return _ankidote_json_response(payload)


def _ankidote_apply_checkin(state: dict, runner) -> dict:
    """Fold a finished check-in back into the diagnostic; stamp cadence."""
    from anki.ankidote import loop as ankidote_loop

    after: dict = {}
    for result in runner.topic_states():
        ankidote_loop.apply_result(state, result)
        _ankidote_stamp_measured(state, result["topic"])
        after[result["topic"]] = result["score"]
    checkins = state.get("checkins")
    if not isinstance(checkins, dict):
        checkins = {"everyDays": 7, "graceDays": 3}
        state["checkins"] = checkins
    checkins["lastAt"] = int(time.time() * 1000)
    return after


def ankidote_organize_start() -> bytes:
    """Begin (or resume) the mandatory concept lesson for a gated topic.

    Presents the authored key ideas + procedure and asks for a summary. The
    session lives in memory on ``aqt.mw`` so a refresh lands back on the same
    step; completing it stamps ``state["organized"]`` so it never gates again.
    """
    from anki.ankidote import lessons, organize

    if not _ankidote_logged_in():
        return _ankidote_json_response({"phase": "login_required"})

    body = json.loads(request.data or b"{}")
    topic = str(body.get("topic") or "")
    if not lessons.has_lesson(topic):
        return _ankidote_json_response(_ankidote_loop_state_payload())

    session = getattr(aqt.mw, "_ankidote_organize", None)
    if not isinstance(session, dict) or session.get("topic") != topic:
        session = organize.new_session(topic)
        aqt.mw._ankidote_organize = session
    return _ankidote_json_response(organize.payload(session))


def ankidote_organize_ready() -> bytes:
    """Learner finished reading: blank the key ideas + steps into recall boxes."""
    from anki.ankidote import organize

    session = getattr(aqt.mw, "_ankidote_organize", None)
    if not isinstance(session, dict):
        return _ankidote_json_response(_ankidote_loop_state_payload())
    organize.begin_recall(session)
    return _ankidote_json_response(organize.payload(session))


def ankidote_organize_check() -> bytes:
    """Grade one recall box; green it, hint, or reveal after the try limit."""
    from anki.ankidote import ai, organize

    session = getattr(aqt.mw, "_ankidote_organize", None)
    if not isinstance(session, dict):
        return _ankidote_json_response(_ankidote_loop_state_payload())

    body = json.loads(request.data or b"{}")
    blank_id = str(body.get("id") or "")
    text = str(body.get("text") or "")
    blank = organize.get_blank(session, blank_id)
    if blank is None:
        return _ankidote_json_response(organize.payload(session))

    state = _ankidote_state_read()
    grade = ai.grade_recall(state, blank["label"], blank["target"], text)
    organize.check_blank(session, blank_id, bool(grade["pass"]), grade.get("hint", ""))
    result = organize.payload(session)
    if session.get("phase") == "done":
        organize.mark_organized(state, session["topic"])
        _ankidote_state_write(state)
        aqt.mw._ankidote_organize = None
    return _ankidote_json_response(result)


def ankidote_active() -> bytes:
    """Whether the currently selected deck is an Ankidote topic deck.

    Used by the congrats screen to show a 'back to the loop' link only when the
    finished deck belongs to Ankidote.
    """
    deck = aqt.mw.col.decks.current()
    name = deck.get("name", "") if deck else ""
    is_topic = name.startswith("Ankidote ")
    return _ankidote_json_response(
        {
            "isTopicDeck": is_topic,
            "topic": name[len("Ankidote ") :] if is_topic else "",
        }
    )


def ankidote_stats() -> bytes:
    """Real dashboard inputs (no mocks).

    - Memory: how much of each topic's deck is mastered (mature cards), the same
      per-topic values that gate problem sets in the loop.
    - Performance: practice problems actually completed and their correctness.
    - Readiness is derived on the client from the persisted score range.
    """
    from anki.ankidote import topics as ankidote_topics

    col = aqt.mw.col
    state = _ankidote_state_read()
    progress = state.get("progress") or {}

    # Memory: per-topic deck mastery (mature cards / total), aggregated.
    topic_mastery = []
    total_mastered = 0
    total_in_topics = 0
    for info in ankidote_topics.topic_tree():
        _immature, mastered, total = _ankidote_topic_counts(col, info.topic)
        total_mastered += mastered
        total_in_topics += total
        if total > 0:
            topic_mastery.append(
                {
                    "topic": info.topic,
                    "section": info.section,
                    "mastered": mastered,
                    "total": total,
                    "pct": round(mastered / total * 100),
                }
            )
    mastery_pct = (
        round(total_mastered / total_in_topics * 100) if total_in_topics else None
    )
    rows = col.db.all("select 1 from revlog where type in (0,1,2)") if col.db else []
    graded_reviews = len(rows)

    # Performance / practice history from the persisted problem tallies.
    now_ms = time.time() * 1000
    sessions = progress.get("sessions") or []
    history = []
    for session in reversed(sessions[-8:]):
        count = int(session.get("count", 0))
        correct = int(session.get("correct", 0))
        days = int((now_ms - session.get("ts", now_ms)) // 86_400_000)
        history.append(
            {
                "daysAgo": max(days, 0),
                "topic": session.get("topic", ""),
                "count": count,
                "accuracy": round(correct / count * 100) if count else 0,
            }
        )

    payload = {
        "problemsAnswered": int(progress.get("problemsAnswered", 0)),
        "problemsCorrect": int(progress.get("problemsCorrect", 0)),
        "sessions": history,
        "gradedReviews": graded_reviews,
        "topicMastery": topic_mastery,
        "memory": {
            "masteryPct": mastery_pct,
            "masteredCards": total_mastered,
            "totalCards": total_in_topics,
            "reviews": graded_reviews,
        },
    }
    return _ankidote_json_response(payload)


post_handler_list = [
    congrats_info,
    get_deck_configs_for_update,
    update_deck_configs,
    get_scheduling_states_with_context,
    set_scheduling_states,
    change_notetype,
    import_done,
    import_csv,
    import_anki_package,
    import_json_file,
    import_json_string,
    search_in_browser,
    deck_options_require_close,
    deck_options_ready,
    save_custom_colours,
    ankidote_diag_start,
    ankidote_diag_answer,
    ankidote_diag_state,
    ankidote_auth_state,
    ankidote_auth_login,
    ankidote_auth_logout,
    ankidote_state_get,
    ankidote_state_set,
    ankidote_loop_state,
    ankidote_loop_start,
    ankidote_loop_answer,
    ankidote_loop_mistake,
    ankidote_loop_continue,
    ankidote_note,
    ankidote_loop_next,
    ankidote_loop_skip,
    ankidote_loop_another,
    ankidote_checkin_start,
    ankidote_checkin_answer,
    ankidote_organize_start,
    ankidote_organize_ready,
    ankidote_organize_check,
    ankidote_sort_decks,
    ankidote_active,
    ankidote_stats,
]


exposed_backend_list = [
    # CollectionService
    "latest_progress",
    "get_custom_colours",
    # DeckService
    "get_deck_names",
    # I18nService
    "i18n_resources",
    # ImportExportService
    "get_csv_metadata",
    "get_import_anki_package_presets",
    # NotesService
    "get_field_names",
    "get_note",
    # NotetypesService
    "get_notetype_names",
    "get_change_notetype_info",
    # StatsService
    "card_stats",
    "get_review_logs",
    "graphs",
    "get_graph_preferences",
    "set_graph_preferences",
    # TagsService
    "complete_tag",
    # ImageOcclusionService
    "get_image_for_occlusion",
    "add_image_occlusion_note",
    "get_image_occlusion_note",
    "update_image_occlusion_note",
    "get_image_occlusion_fields",
    # SchedulerService
    "compute_fsrs_params",
    "compute_optimal_retention",
    "set_wants_abort",
    "evaluate_params_legacy",
    "get_optimal_retention_parameters",
    "simulate_fsrs_review",
    "simulate_fsrs_workload",
    # DeckConfigService
    "get_ignored_before_count",
    "get_retention_workload",
    # AnkidoteService
    "get_practice_questions",
    "next_diagnostic_question",
    "run_diagnostic",
    "run_loop_problems",
    "apply_loop_result",
    "get_loop_state",
    "get_ankidote_state",
    "set_ankidote_state",
    "get_ankidote_stats",
    "sort_decks",
]


def raw_backend_request(endpoint: str) -> Callable[[], bytes]:
    # check for key at startup
    from anki._backend import RustBackend

    assert hasattr(RustBackend, f"{endpoint}_raw")

    return lambda: getattr(aqt.mw.col._backend, f"{endpoint}_raw")(request.data)


# all methods in here require a collection
post_handlers = {
    stringcase.camelcase(handler.__name__): handler for handler in post_handler_list
} | {
    stringcase.camelcase(handler): raw_backend_request(handler)
    for handler in exposed_backend_list
}


def _extract_collection_post_request(path: str) -> DynamicRequest | NotFound:
    if not aqt.mw.col:
        return NotFound(message=f"collection not open, ignore request for {path}")
    if handler := post_handlers.get(path):
        # convert bytes/None into response
        def wrapped() -> Response:
            try:
                if data := handler():
                    response = flask.make_response(data)
                    response.headers["Content-Type"] = "application/binary"
                else:
                    response = _text_response(HTTPStatus.NO_CONTENT, "")
            except Exception as exc:
                print(traceback.format_exc())
                response = _text_response(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return response

        return wrapped
    else:
        return NotFound(message=f"{path} not found")


def _check_dynamic_request_permissions():
    if request.method == "GET":
        return

    def warn() -> None:
        show_warning(
            "Unexpected API access. Please report this message on the Anki forums."
        )

    # check content type header to ensure this isn't an opaque request from another origin
    if request.headers["Content-type"] != "application/binary":
        aqt.mw.taskman.run_on_main(warn)
        abort(403)

    # does page have access to entire API?
    if _have_api_access():
        return

    # whitelisted API endpoints for reviewer/previewer
    if request.path in (
        "/_anki/getSchedulingStatesWithContext",
        "/_anki/setSchedulingStates",
        "/_anki/i18nResources",
        "/_anki/congratsInfo",
        # Ankidote diagnostic runs in the main webview (like the congrats
        # page), which is not granted full API access.
        "/_anki/ankidoteDiagStart",
        "/_anki/ankidoteDiagAnswer",
        "/_anki/ankidoteDiagState",
        "/_anki/ankidoteAuthState",
        "/_anki/ankidoteAuthLogin",
        "/_anki/ankidoteAuthLogout",
        "/_anki/ankidoteStateGet",
        "/_anki/ankidoteStateSet",
        "/_anki/ankidoteLoopState",
        "/_anki/ankidoteLoopStart",
        "/_anki/ankidoteLoopAnswer",
        "/_anki/ankidoteLoopMistake",
        "/_anki/ankidoteLoopContinue",
        "/_anki/ankidoteNote",
        "/_anki/ankidoteLoopNext",
        "/_anki/ankidoteLoopSkip",
        "/_anki/ankidoteLoopAnother",
        "/_anki/ankidoteCheckinStart",
        "/_anki/ankidoteCheckinAnswer",
        "/_anki/ankidoteOrganizeStart",
        "/_anki/ankidoteOrganizeReady",
        "/_anki/ankidoteOrganizeCheck",
        "/_anki/ankidoteSortDecks",
        "/_anki/ankidoteActive",
        "/_anki/ankidoteStats",
        # Rust AnkidoteService RPCs the pages now call directly (the engine was
        # ported from Python into the backend). Same webview, no full API access.
        "/_anki/runDiagnostic",
        "/_anki/runLoopProblems",
        "/_anki/applyLoopResult",
        "/_anki/getLoopState",
        "/_anki/getAnkidoteState",
        "/_anki/setAnkidoteState",
        "/_anki/getAnkidoteStats",
        "/_anki/sortDecks",
    ):
        pass
    else:
        # other legacy pages may contain third-party JS, so we do not
        # allow them to access our API
        aqt.mw.taskman.run_on_main(warn)
        abort(403)


def _handle_dynamic_request(req: DynamicRequest) -> Response:
    _check_dynamic_request_permissions()
    try:
        return req()
    except Exception as e:
        return _text_response(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))


def legacy_page_data() -> Response:
    id = int(request.args["id"])
    page = aqt.mw.mediaServer.get_page(id)
    if page:
        response = Response(page.html, mimetype="text/html")
        # Prevent JS in field content from being executed in the editor, as it would
        # have access to our internal API, and is a security risk.
        if page.context == PageContext.EDITOR:
            response.headers["Content-Security-Policy"] = (
                _editor_content_security_policy(aqt.mw.mediaServer.getPort())
            )
        return response
    else:
        return _text_response(HTTPStatus.NOT_FOUND, "page not found")


_APIKEY = secrets.token_urlsafe(32)


def _have_api_access() -> bool:
    return (
        request.headers.get("Authorization") == f"Bearer {_APIKEY}"
        or os.environ.get("ANKI_API_HOST") == "0.0.0.0"
    )


# this currently only handles a single method; in the future, idempotent
# requests like i18nResources should probably be moved here
def _extract_dynamic_get_request(path: str) -> DynamicRequest | None:
    if path == "legacyPageData":
        return legacy_page_data
    else:
        return None
