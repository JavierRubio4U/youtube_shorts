# scripts/upload_youtube.py
import json
import sys
from pathlib import Path
import argparse
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
TOKEN_FILE = STATE_DIR / "youtube_token.json"
CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"

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
        raise SystemExit(f"No se encontró el archivo de metadata: {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------
def get_youtube_service():
    _debug_paths()
    creds = None

    if TOKEN_FILE.exists():
        print("[DBG] Token JSON encontrado. Usando credenciales existentes...")
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            print("[DBG] Token cargado.")
        except Exception as e:
            print(f"[DBG] Fallo leyendo TOKEN_FILE: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[DBG] Token expirado. Refrescando...")
            try:
                creds.refresh(Request())
                print("[DBG] Refresh OK.")
            except Exception as e:
                print(f"[DBG] Refresh falló: {e}")
                creds = None
        else:
            if not CLIENT_SECRET_FILE.exists():
                raise SystemExit(f"Falta client_secret.json en: {CLIENT_SECRET_FILE}")

            print("[DBG] Creando flujo OAuth desde client_secret.json…")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)

            # Autenticación para consola (sin navegador gráfico)
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("Por favor, abre la siguiente URL en tu navegador y autoriza el acceso:")
            print(auth_url)
            print("\nLuego, copia el código que aparece y pégalo aquí:")

            # Esperar a que el usuario introduzca el código de autorización
            try:
                code = input("Código de autorización: ").strip()
            except (EOFError, KeyboardInterrupt) as e:
                raise SystemExit("Fallo en la entrada del código. Intenta de nuevo.")

            flow.fetch_token(code=code)
            creds = flow.credentials

        # Guardar token JSON para futuras ejecuciones
        try:
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            print(f"[DBG] Token guardado en: {TOKEN_FILE.resolve()}")
        except Exception as e:
            print(f"[DBG] Error guardando token: {e}")

    return build("youtube", "v3", credentials=creds)

# --------------------------------------------------------------------
# Subida de video
# --------------------------------------------------------------------
def upload_video(video_path: str, meta: dict) -> str:
    service = get_youtube_service()

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta.get("tags", []),
            "defaultLanguage": "es",
            "defaultAudioLanguage": "es"
        },
        "status": {
            "privacyStatus": meta.get("default_visibility", "unlisted")
        },
        "kind": "youtube#video"
    }

    media = MediaFileUpload(video_path, resumable=True, chunksize=-1, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"Subiendo... {int(status.progress() * 100)}%")
        except Exception as e:
            print(f"Error durante la subida: {e}")
            break

    if response is not None:
        video_id = response["id"]
        print("✅ Subido:", f"https://studio.youtube.com/video/{video_id}/edit")
        return video_id
    else:
        raise SystemExit("Fallo en la subida del video.")

# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def main(video_path: str | None = None):
    meta = _load_metadata()
    tmdb_id = meta["tmdb_id"]

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

    print(f"▶ Subiendo video: {video_path}")
    video_id = upload_video(video_path, meta)
    print("✅ Subida completada. ID:", video_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sube un video de un short a YouTube")
    parser.add_argument("--video", type=str, default=None,
                        help="Ruta MP4 (opcional). Si se omite, autodetecta por tmdb_id.")
    args = parser.parse_args()
    main(args.video)
