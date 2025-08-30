# scripts/upload_youtube.py
import json
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# ---------------------------------------------------------------------
# Rutas y constantes
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
CONFIG_DIR = ROOT / "config"
STATE_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = STATE_DIR / "youtube_token.json"           # guardamos token en JSON
CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"  # tu client secret

# ---------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------
def _debug_paths():
    print(f"[DBG] STATE_DIR     = {STATE_DIR.resolve()}")
    print(f"[DBG] TOKEN_FILE    = {TOKEN_FILE.resolve()}  exists={TOKEN_FILE.exists()}")
    print(f"[DBG] CLIENT_SECRET = {CLIENT_SECRET_FILE.resolve()}  exists={CLIENT_SECRET_FILE.exists()}")

def _load_metadata():
    meta_path = STATE_DIR / "youtube_metadata.json"
    if not meta_path.exists():
        raise SystemExit(f"No existe metadata: {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------
# OAuth / Servicio YouTube
# ---------------------------------------------------------------------
def _oauth_get_credentials():
    """
    Compatibilidad total:
    - Si hay token JSON, lo usa/refresh.
    - Si no, lanza flujo OAuth:
      * Si existe flow.run_console, se usa.
      * Si NO existe, hace flujo manual: imprime URL, pides "code", y token listo.
    """
    _debug_paths()

    creds = None

    # 1) Cargar token si existe
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            print("[DBG] Token cargado desde JSON.")
        except Exception as e:
            print(f"[DBG] Fallo leyendo TOKEN_FILE: {e}")
            creds = None

    # 2) Refrescar/crear
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[DBG] Token expirado: intentando refresh…")
            try:
                creds.refresh(Request())
                print("[DBG] Refresh OK.")
            except Exception as e:
                print(f"[DBG] Refresh falló: {e}")
                creds = None

        if not creds:
            if not CLIENT_SECRET_FILE.exists():
                raise SystemExit(f"Falta client_secret.json en: {CLIENT_SECRET_FILE}")
            print("[DBG] Creando flujo OAuth desde client_secret.json…")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)

            # Preferimos run_console si está disponible en tu versión
            if hasattr(flow, "run_console"):
                creds = flow.run_console()  # imprime URL y pide el código en la terminal
            else:
                # Fallback universal: generamos URL, la imprimes, pegas el "code"
                auth_url, _ = flow.authorization_url(
                    access_type="offline",
                    include_granted_scopes="true",
                    prompt="consent",
                )
                print("\n=== AUTORIZACIÓN MANUAL ===")
                print("1) Abre esta URL en el navegador:")
                print(auth_url)
                print("2) Autoriza y copia el 'código' que te da Google.")
                code = input("Pega aquí el código y pulsa Enter: ").strip()
                flow.fetch_token(code=code)
                creds = flow.credentials

        # 3) Guardar token JSON
        try:
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            print(f"[DBG] Token guardado en: {TOKEN_FILE.resolve()}")
        except Exception as e:
            print(f"[DBG] Error guardando token: {e}")

    return creds

from pathlib import Path
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
CONFIG_DIR = ROOT / "config"
STATE_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = CONFIG_DIR / "youtube_token.json"
CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"

def _debug_paths():
    print(f"[DBG] STATE_DIR       = {STATE_DIR}")
    print(f"[DBG] TOKEN_FILE      = {TOKEN_FILE}  exists={TOKEN_FILE.exists()}")
    print(f"[DBG] CLIENT_SECRET   = {CLIENT_SECRET_FILE}  exists={CLIENT_SECRET_FILE.exists()}")

def get_youtube_service():
    _debug_paths()
    creds = None

    # Lee token si existe
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            print("[DBG] Token cargado.")
        except Exception as e:
            print(f"[DBG] Fallo leyendo TOKEN_FILE: {e}")
            creds = None

    # Refresca o crea token
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[DBG] Token expirado: intentando refresh…")
            try:
                creds.refresh(Request())
                print("[DBG] Refresh OK.")
            except Exception as e:
                print(f"[DBG] Refresh falló: {e}")
                creds = None

        if not creds:
            print("[DBG] Abriendo flujo OAuth local (Desktop app)…")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRET_FILE), SCOPES
            )
            # Probar varios puertos por si están ocupados
            last_err = None
            for port in (8765, 8090, 8085, 8080):
                try:
                    creds = flow.run_local_server(
                        port=port,
                        prompt="consent",
                        authorization_prompt_message="Autoriza en el navegador, vuelve aquí cuando termine…"
                    )
                    print(f"[DBG] Autorizado en puerto {port}.")
                    break
                except OSError as e:
                    last_err = e
                    continue
            if not creds:
                raise RuntimeError(f"No se pudo iniciar OAuth local. Último error: {last_err}")

        # Guarda token (formato JSON)
        try:
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            print(f"[DBG] Token guardado en: {TOKEN_FILE}")
        except Exception as e:
            print(f"[DBG] Error guardando token: {e}")

    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------
# Subida
# ---------------------------------------------------------------------
def upload_video(video_path: str, meta: dict) -> str:
    service = get_youtube_service()

    body = {
        "snippet": {
            "title":       meta["title"],
            "description": meta["description"],
            "tags":        meta.get("tags", []),
            "categoryId":  "24"  # Entertainment
        },
        "status": {
            "privacyStatus": meta.get("default_visibility", "unlisted"),
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Subiendo... {int(status.progress() * 100)}%")

    video_id = response["id"]
    print("✅ Subido:", f"https://studio.youtube.com/video/{video_id}/edit")
    return video_id

# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def main(video_path: str | None = None):
    meta = _load_metadata()
    tmdb_id = meta["tmdb_id"]

    # Si no recibimos ruta explícita, cogemos el MP4 más reciente con ese tmdb_id
    if video_path is None:
        shorts_dir = ROOT / "output" / "shorts"
        cands = sorted(
            shorts_dir.glob(f"{tmdb_id}_*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        if not cands:
            raise SystemExit(f"No se encontró el MP4 de {tmdb_id} en {shorts_dir}")
        video_path = str(cands[0])

    video_id = upload_video(video_path, meta)
    return video_id

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=str, default=None,
                    help="Ruta MP4 (opcional). Si se omite, autodetecta por tmdb_id.")
    args = ap.parse_args()
    main(args.video)
