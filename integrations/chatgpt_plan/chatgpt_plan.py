"""Local ChatGPT plan connection. No API-key or paid-API fallback."""

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
import webbrowser

import httpx
import jwt
from response_results import complete_objects, validate_individual

ROOT = Path(__file__).resolve().parent
AUTH = "https://auth.openai.com"
API = "https://api.openai.com/v1"
SESSION = ROOT / ".private" / "account.dpapi"
HOST = ROOT / ".private" / "host.json"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    """Windows DPAPI binds secrets to this Windows user; never writes plain tokens."""
    buffer = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise RuntimeError("Windows credential encryption failed.")
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        kernel = ctypes.WinDLL("kernel32")
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(dst.data, ctypes.c_void_p))


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def save_account(account):
    atomic(SESSION, protect(json.dumps(account).encode()))


def read_account():
    if not SESSION.exists():
        raise RuntimeError("Not connected. Run login first.")
    return json.loads(protect(SESSION.read_bytes(), decrypt=True))


def checked(response):
    if response.status_code >= 400:
        # Do not print response bodies, tokens or authorization codes.
        try:
            error = response.json().get("error", {})
            code = error.get("code") if isinstance(error, dict) else error
        except Exception:
            code = "request_failed"
        raise RuntimeError(f"HTTP {response.status_code}: {str(code)[:100]}")
    return response.json()


def validate_token(token, client_id, nonce, keys):
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256":
        raise RuntimeError("Unexpected identity signature algorithm.")
    matching = [key for key in keys["keys"] if key.get("kid") == header.get("kid")]
    if len(matching) != 1:
        raise RuntimeError("Identity signing key not found.")
    claims = jwt.decode(
        token,
        jwt.PyJWK.from_dict(matching[0]).key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=AUTH,
        options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
    )
    if not secrets.compare_digest(claims["nonce"], nonce):
        raise RuntimeError("Identity nonce mismatch.")
    return claims


def login():
    HOST.parent.mkdir(parents=True, exist_ok=True)
    if not HOST.exists():
        atomic(HOST, json.dumps({"id": "urn:uuid:" + str(uuid.uuid4())}).encode())
    host = json.loads(HOST.read_text())["id"]
    prior = read_account() if SESSION.exists() else None
    client_id = prior["client_id"] if prior else "dynamic_agent_client"
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    callback = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path != "/auth/callback" or not secrets.compare_digest(
                query.get("state", [""])[0], state
            ):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid callback. Please return to the application.")
                return
            callback.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Sign-in received. Return to the application to check the connection."
            )

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        params = dict(
            client_id=client_id,
            ext_agent_host_id=host,
            response_type="code",
            redirect_uri=redirect,
            scope="openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
            resource=API,
            state=state,
            nonce=nonce,
            code_challenge_method="S256",
            code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("="),
        )
        if prior:
            params["id_token_hint"] = prior["id_token"]
        else:
            params["agent_name_hint"] = "Voice Transcript Cleanup"
        url = AUTH + "/api/accounts/authorize?" + urlencode(params)
        print("Continue with ChatGPT: opening your browser.", flush=True)
        print("Approve ChatGPT plan usage only. API keys are not needed.", flush=True)
        webbrowser.open(url)
        end = time.monotonic() + 600
        while not callback and time.monotonic() < end:
            server.handle_request()
    if not callback:
        raise RuntimeError("Sign-in timed out. Run login to try again.")
    if callback.get("error"):
        raise RuntimeError("Sign-in was declined or did not complete.")
    issued = callback.get("client_id", client_id if prior else "")
    if not issued or issued == "dynamic_agent_client" or (prior and issued != client_id):
        raise RuntimeError("Client registration was not valid.")
    if not callback.get("code"):
        raise RuntimeError("Authorization code missing.")
    with httpx.Client(timeout=30) as client:
        tokens = checked(
            client.post(
                AUTH + "/api/accounts/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": issued,
                    "code": callback["code"],
                    "code_verifier": verifier,
                    "redirect_uri": redirect,
                    "resource": API,
                },
            )
        )
        keys = checked(client.get(AUTH + "/.well-known/jwks.json"))
    claims = validate_token(tokens["id_token"], issued, nonce, keys)
    if prior and claims["sub"] != prior["subject"]:
        raise RuntimeError("Different account returned. Existing connection was preserved.")
    scopes = tokens.get("scope", "").split()
    account = dict(
        tokens,
        client_id=issued,
        subject=claims["sub"],
        scopes=scopes,
        expires_at=time.time() + tokens["expires_in"],
        ext_agent_host_id=host,
    )
    save_account(account)
    if "chatgpt.tokens.use.direct" not in scopes:
        raise RuntimeError("Signed in, but permission to use ChatGPT plan was not granted.")
    print("Connected. Validated signed identity and ChatGPT plan permission.", flush=True)
    models()


def access():
    account = read_account()
    if "chatgpt.tokens.use.direct" not in account["scopes"]:
        raise RuntimeError("ChatGPT plan permission is missing.")
    if time.time() >= account["expires_at"] - 90:
        tokens = checked(
            httpx.post(
                AUTH + "/api/accounts/oauth/token",
                timeout=30,
                data={
                    "grant_type": "refresh_token",
                    "client_id": account["client_id"],
                    "refresh_token": account["refresh_token"],
                    "resource": API,
                },
            )
        )
        if not tokens.get("refresh_token"):
            raise RuntimeError("Rotating refresh token missing. Sign in again.")
        account.update(tokens)
        account["scopes"] = tokens.get("scope", " ".join(account["scopes"])).split()
        account["expires_at"] = time.time() + tokens["expires_in"]
        save_account(account)
        if "chatgpt.tokens.use.direct" not in account["scopes"]:
            raise RuntimeError("ChatGPT plan permission was removed.")
    return account["access_token"]


def models():
    data = checked(
        httpx.get(API + "/models", headers={"Authorization": "Bearer " + access()}, timeout=30)
    )
    available = [item for item in data.get("models", []) if item.get("visibility") == "list"]
    if not available:
        raise RuntimeError("No visible ChatGPT plan models returned.")
    atomic(ROOT / "available_models.json", json.dumps(available, indent=2).encode())
    for item in available:
        print(item["slug"] + " | " + item.get("display_name", item["slug"]), flush=True)
    return available


def correct(input_path, model, output_path, instructions_path=None):
    catalog = models()
    if model not in {item["slug"] for item in catalog}:
        raise RuntimeError("Choose a model from the connected account catalog.")
    raw = json.loads(Path(input_path).read_text(encoding="utf8"))
    items = raw["items"]
    if not 1 <= len(items) <= 5:
        raise RuntimeError("Pilot accepts 1 to 5 samples per run.")
    # Copy only ASR and per-voice nearby chat; never send previous corrections.
    source = []
    for item in items:
        context = [
            dict(message, source_id=f"{item['id']}:neighbor:{index}")
            for index, message in enumerate(item.get("chat_context", []))
        ]
        source.append(
            {"id": item["id"], "alternatives": item["alternatives"], "chat_context": context}
        )
    template = ROOT.parent.parent / "correction_prompt.txt"
    instructions = (
        Path(instructions_path).read_text(encoding="utf8")
        if instructions_path
        else template.read_text(encoding="utf8")
    )
    request = {
        "model": model,
        "instructions": instructions + "\nKeep the final JSON short; no explanatory prose.",
        "input": [{"role": "user", "content": json.dumps({"items": source}, ensure_ascii=False)}],
        "store": False,
        "stream": True,
    }
    if len(json.dumps(request).encode()) > 40000:
        raise RuntimeError("Pilot input is too large.")
    pieces, completed = [], False
    token = access()
    stop_reason = None
    try:
        with httpx.Client(timeout=60) as client:
            with client.stream(
                "POST",
                API + "/responses",
                headers={"Authorization": "Bearer " + token},
                json=request,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    checked(response)
                for line in response.iter_lines():
                    if not line.startswith("data: ") or line[6:] == "[DONE]":
                        continue
                    event = json.loads(line[6:])
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        pieces.append(event["delta"])
                        if sum(map(len, pieces)) > 50000:
                            stop_reason = "output_size_limit"
                            break
                    elif kind in ("response.failed", "response.incomplete", "error"):
                        failed = event.get("response") or {}
                        detail = failed.get("error") or event.get("error") or {}
                        stop_reason = (
                            (failed.get("incomplete_details") or {}).get("reason")
                            or detail.get("code")
                            or event.get("code")
                            or "unspecified"
                        )
                        break
                    elif kind == "response.completed":
                        completed = True
    except httpx.TransportError as error:
        stop_reason = type(error).__name__
    if not completed and not stop_reason:
        stop_reason = "stream_ended_without_completion"
    text = "".join(pieces).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # A closed JSON object can be retained even when a later object was interrupted.
    rows = complete_objects(text)
    accepted, errors = validate_individual(rows, source)
    accepted_ids = {row["id"] for row in accepted}
    pending = []
    for item in source:
        if item["id"] in accepted_ids:
            continue
        reason = (
            "content_filter"
            if stop_reason == "content_filter"
            else (errors.get(item["id"]) or stop_reason or "missing_or_invalid_item")
        )
        pending.append(
            {
                "id": item["id"],
                "status": "blocked" if reason == "content_filter" else "error",
                "error": reason,
            }
        )
    parsed = {"items": accepted}
    atomic(
        Path(output_path),
        json.dumps(
            {
                "model": model,
                "raw_input": source,
                "result": parsed,
                "pending": pending,
                "stop_reason": stop_reason,
                "automatic_replacement": False,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf8"),
    )
    print(
        f"Correction response saved: {len(accepted)} complete; {len(pending)} pending. Reason: {stop_reason or 'none'}",
        flush=True,
    )
    print("Saved to:", output_path, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["login", "models", "correct", "logout"])
    parser.add_argument("--input")
    parser.add_argument("--model")
    parser.add_argument("--output", default=str(ROOT / "correction_results.json"))
    parser.add_argument("--instructions", help="Plain-text correction instructions file")
    args = parser.parse_args()
    try:
        if args.action == "login":
            login()
        elif args.action == "models":
            models()
        elif args.action == "logout":
            SESSION.unlink(missing_ok=True)
            print(
                "Local connection removed. App access can also be disconnected in ChatGPT settings."
            )
        elif not args.input or not args.model:
            parser.error("correct needs --input and --model")
        else:
            correct(args.input, args.model, args.output, args.instructions)
    except Exception as error:
        # Only our controlled messages are printed; library errors can contain credentials.
        print(
            "Connection stopped:",
            str(error) if isinstance(error, RuntimeError) else type(error).__name__,
            flush=True,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
