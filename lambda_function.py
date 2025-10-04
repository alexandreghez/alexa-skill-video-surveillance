import json, time, logging, base64, os
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.utils import is_request_type, is_intent_name
from ask_sdk_model.interfaces.alexa.presentation.apl import (
    RenderDocumentDirective,
    ExecuteCommandsDirective,
)

# ---------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # passe à INFO pour déboguer

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
TOKEN = "LONG TOKEN HOME ASSISTANT"

CAMERAS = [
    { "label": "Caméra 1",  "url": "https://<MON DOMAINE HOME ASSISTANT>/api/camera_proxy/camera.<MA CAMERA 1>" },
    { "label": "Caméra 2",  "url": "https://<MON DOMAINE HOME ASSISTANT>/api/camera_proxy/camera.<MA CAMERA 2>" },
    { "label": "Caméra 3",  "url": "https://<MON DOMAINE HOME ASSISTANT>/api/camera_proxy/camera.<MA CAMERA 3>" },
    { "label": "Caméra 4",  "url": "https://<MON DOMAINE HOME ASSISTANT>/api/camera_proxy/camera.<MA CAMERA 4>" }
]

REFRESH_DELAY     = 500        # ms (≈ 2 fps)
DURATION_MS       = 60_000     # ms (durée totale par caméra)
ANIM_DURATION_MS  = 120        # ms fondu (0 = switch sec)
EXIT_IDLE_MS      = 80         # petite pause après Back avant quit_done

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def now_ms() -> int:
    return int(time.time() * 1000)

def end_session_like_cancel(rb):
    return rb.set_should_end_session(True).response

def get_session_attrs(handler_input):
    return handler_input.attributes_manager.session_attributes

def get_current_gen(handler_input) -> int:
    sa = get_session_attrs(handler_input)
    return int(sa.get("gen", 1))

def bump_gen(handler_input) -> int:
    sa = get_session_attrs(handler_input)
    sa["gen"] = int(sa.get("gen", 0)) + 1
    return sa["gen"]

def set_gen(handler_input, value: int) -> None:
    get_session_attrs(handler_input)["gen"] = int(value)

# ---------------------------------------------------------------------
# Image (HTTP + base64)
# ---------------------------------------------------------------------
_TRANSPARENT_PX = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)

def _http_get_bytes(url: str, headers: dict, timeout: int = 5) -> (bytes, str):
    req = urlrequest.Request(url)
    for k, v in headers.items():
        req.add_header(k, v)
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type") or "application/octet-stream"
        data = resp.read()
        return data, content_type

def fetch_image_as_data_url(url: str, token: str, timeout: int = 5) -> str:
    sep = "&" if "?" in url else "?"
    busted_url = f"{url}{sep}_t={int(time.time() * 1000)}"
    headers = {"Authorization": f"Bearer {token}"}
    data, content_type = _http_get_bytes(busted_url, headers, timeout=timeout)
    content_type = content_type.split(";", 1)[0].strip() or "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{b64}"

def fetch_image_as_data_url_safe(url: str, token: str, timeout: int = 5) -> str:
    try:
        return fetch_image_as_data_url(url, token, timeout)
    except (HTTPError, URLError) as e:
        logger.warning("Échec récupération image (%s).", e)
        return _TRANSPARENT_PX
    except Exception as e:
        logger.exception("Erreur inattendue image base64: %s", e)
        return _TRANSPARENT_PX

# ---------------------------------------------------------------------
# APL utils
# ---------------------------------------------------------------------
def load_apl_doc():
    # Doit contenir : Image id=imageA, Image id=imageB, Text id=counterText
    with open("camera_mjpeg.json", "r", encoding="utf-8") as f:
        return json.load(f)

def blank_apl_doc():
    return {
        "type": "APL",
        "version": "1.4",
        "mainTemplate": { "items": [ { "type": "Text", "text": " ", "opacity": 0 } ] }
    }

# ---------------------------------------------------------------------
# Commandes APL (mode tick)
# ---------------------------------------------------------------------
def set_counter_and_image_commands(count: int, cam_index: int):
    cam = CAMERAS[cam_index]
    data_url = fetch_image_as_data_url_safe(cam["url"], TOKEN, timeout=6)

    show_id = "imageA" if (count % 2 == 1) else "imageB"
    hide_id = "imageB" if (count % 2 == 1) else "imageA"

    cmds = [
        { "type": "SetValue", "componentId": show_id, "property": "source",  "value": data_url },
        { "type": "SetValue", "componentId": show_id, "property": "opacity", "value": 0 },
        { "type": "SetValue", "componentId": hide_id, "property": "opacity", "value": 1 },
    ]
    if ANIM_DURATION_MS > 0:
        cmds.append({
            "type": "AnimateItem",
            "componentId": show_id,
            "easing": "linear",
            "duration": ANIM_DURATION_MS,
            "value": [ { "property": "opacity", "from": 0, "to": 1 } ]
        })
    else:
        cmds.append({ "type": "SetValue", "componentId": show_id, "property": "opacity", "value": 1 })
    cmds += [
        { "type": "SetValue", "componentId": hide_id, "property": "opacity", "value": 0 },
        { "type": "SetValue", "componentId": "counterText", "property": "text", "value": cam["label"] }
    ]
    return cmds

def make_tick_commands(next_count: int, delay: int, start_ms: int, cam_index: int, gen: int):
    """
    On propage:
      - start_ms: départ de la durée
      - delay: délai entre ticks
      - next_count
      - cam_index: caméra en cours
      - gen: génération courante (anti-concurrence)
    """
    return [
        *set_counter_and_image_commands(next_count, cam_index),
        { "type": "Idle", "delay": delay },
        { "type": "SendEvent", "arguments": ["tick", str(start_ms), str(delay), str(next_count), str(cam_index), str(gen)] }
    ]

# ---------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------
class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        # initialise la génération à 1 au début de la session
        set_gen(handler_input, 1)

        doc = load_apl_doc()
        rb = handler_input.response_builder
        rb.add_directive(RenderDocumentDirective(token="cam", document=doc))

        start = now_ms()
        default_cam = 0  # caméra 1
        gen = get_current_gen(handler_input)
        rb.add_directive(ExecuteCommandsDirective(
            token="cam",
            commands=make_tick_commands(1, REFRESH_DELAY, start, default_cam, gen)
        ))
        return rb.response

class OpenCameraByNumberIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("OpenCameraByNumberIntent")(handler_input)

    def handle(self, handler_input):
        rb = handler_input.response_builder
        intent = handler_input.request_envelope.request.intent
        slots = intent.slots or {}

        def parse_number():
            if "numero" in slots and slots["numero"] and slots["numero"].value:
                try:
                    return int(slots["numero"].value)
                except Exception:
                    pass
            if "rang" in slots and slots["rang"] and slots["rang"].value:
                import re
                m = re.search(r"\d+", slots["rang"].value)
                if m:
                    return int(m.group(0))
                words = slots["rang"].value.lower()
                mapping = {"premier":1, "première":1, "deuxième":2, "troisième":3, "quatrième":4, "cinquième":5}
                for k,v in mapping.items():
                    if k in words:
                        return v
            return 1

        numero = parse_number()
        cam_index = max(0, min(len(CAMERAS) - 1, numero - 1))

        # Incrémente la génération pour invalider toute boucle précédente
        gen = bump_gen(handler_input)

        # (re)render du doc et lancement d’une nouvelle boucle avec start_ms réinitialisé
        doc = load_apl_doc()
        rb.add_directive(RenderDocumentDirective(token="cam", document=doc))
        start = now_ms()
        rb.add_directive(ExecuteCommandsDirective(
            token="cam",
            commands=make_tick_commands(1, REFRESH_DELAY, start, cam_index, gen)
        ))
        return rb.response

class APLUserEventHandler(AbstractRequestHandler):
    """
    Boucle "tick" avec génération (anti-concurrence) + deadline réelle :
      - args: ["tick", start_ms, delay, count, cam_index, gen]
      - on ignore tout tick dont gen != session.gen (ancienne caméra)
      - fin: Back -> Idle -> SendEvent("quit_done") puis fermeture de session
    """
    def can_handle(self, handler_input):
        return is_request_type("Alexa.Presentation.APL.UserEvent")(handler_input)

    def handle(self, handler_input):
        rb = handler_input.response_builder
        args = handler_input.request_envelope.request.arguments or []
        if not args:
            return rb.response

        # Fermeture finale
        if args[0] == "quit_done":
            return end_session_like_cancel(rb)

        if args[0] != "tick":
            return rb.response

        try:
            start = int(args[1])
            delay = int(args[2])
            curr  = int(args[3])
            cam_index = int(args[4]) if len(args) >= 5 else 0
            gen_from_event = int(args[5]) if len(args) >= 6 else 1
        except Exception:
            start, delay, curr, cam_index, gen_from_event = now_ms(), REFRESH_DELAY, 1, 0, 1

        # Si l’événement provient d’une ancienne génération, on l’ignore
        gen_current = get_current_gen(handler_input)
        if gen_from_event != gen_current:
            return rb.response  # ignore les ticks "orphelins" de l’ancienne caméra

        cam_index = max(0, min(len(CAMERAS) - 1, cam_index))

        if now_ms() >= start + DURATION_MS:
            rb.add_directive(ExecuteCommandsDirective(
                token="cam",
                commands=[
                    { "type": "Back" },
                    { "type": "Idle", "delay": EXIT_IDLE_MS },
                    { "type": "SendEvent", "arguments": ["quit_done"] }
                ]
            ))
            return rb.response

        rb.add_directive(ExecuteCommandsDirective(
            token="cam",
            commands=make_tick_commands(curr + 1, delay, start, cam_index, gen_current)
        ))
        return rb.response

# ---------------------------------------------------------------------
# Handlers additionnels
# ---------------------------------------------------------------------
class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)
    def handle(self, handler_input):
        return handler_input.response_builder.speak("Désolé, je n'ai pas compris.").response

class CancelAndStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.CancelIntent")(handler_input) or is_intent_name("AMAZON.StopIntent")(handler_input)
    def handle(self, handler_input):
        return end_session_like_cancel(handler_input.response_builder)

class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)
    def handle(self, handler_input):
        return handler_input.response_builder.response

class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True
    def handle(self, handler_input, exception):
        logger.exception("Exception non gérée: %s", exception)
        return end_session_like_cancel(handler_input.response_builder)

# ---------------------------------------------------------------------
# Entrée skill
# ---------------------------------------------------------------------
sb = SkillBuilder()
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(OpenCameraByNumberIntentHandler())
sb.add_request_handler(APLUserEventHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(CancelAndStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
