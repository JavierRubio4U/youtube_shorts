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
        raise SystemExit(f"Falta el archivo de metadata: {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))

def _get_youtube_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[DBG] Token expirado. Refrescando...")
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET_FILE.exists():
                raise SystemExit(f"[ERROR] Falta el archivo 'client_secret.json' en {CONFIG_DIR}")
            print("No hay token. Iniciando flujo OAuth2...")
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
            print("[DBG] Token guardado.")
    return build("youtube", "v3", credentials=creds)

def upload_video(video_path: str, meta: dict):
    if not Path(video_path).exists():
        raise SystemExit(f"Falta el archivo de video: {video_path}")
    
    youtube = _get_youtube_service()

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "defaultLanguage": "es"
        },
        "status": {
            "privacyStatus": meta["default_visibility"],
            "selfDeclaredMadeForKids": meta["made_for_kids"] # <--- AQUI ESTÁ EL CAMBIO
        },
    }

    print(f"Subiendo a YouTube... Título: '{meta['title']}'")
    insert_req = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True)
    )

    response = None
    try:
        response = insert_req.execute()
        return response.get("id")
    except Exception as e:
        print(f"Fallo en la subida: {e}")
        return None

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
    
    if video_id:
        print("✅ Subida completada. ID:", video_id)
    return video_id


if __name__ == "__main__":
    main()