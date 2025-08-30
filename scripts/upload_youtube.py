# scripts/upload_youtube.py
import pickle, json
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import google.auth.exceptions

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"; STATE.mkdir(parents=True, exist_ok=True)
CONF = ROOT / "config"
CLIENT_SECRET = CONF / "client_secret.json"
TOKEN = STATE / "youtube_token.pickle"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def get_youtube():
    creds = None
    if TOKEN.exists():
        with open(TOKEN, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except google.auth.exceptions.RefreshError:
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            for port in (8080, 8090, 8765):
                try:
                    creds = flow.run_local_server(port=port, prompt="consent")
                    break
                except OSError:
                    continue
            if not creds:
                raise RuntimeError("No se pudo iniciar OAuth local.")
        with open(TOKEN, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)

def main(video_path: str | None = None):
    meta = json.loads((STATE / "youtube_metadata.json").read_text(encoding="utf-8"))
    tmdb_id = meta["tmdb_id"]

    if video_path is None:
        shorts_dir = ROOT / "output" / "shorts"
        cands = sorted(shorts_dir.glob(f"{tmdb_id}_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            raise SystemExit(f"No se encontró el MP4 de {tmdb_id} en {shorts_dir}")
        video_path = str(cands[0])

    youtube = get_youtube()
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta.get("tags", []),
            "categoryId": "24"   # Entertainment
        },
        "status": {
            "privacyStatus": meta.get("default_visibility", "unlisted"),
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Subiendo... {int(status.progress()*100)}%")
    video_id = response["id"]
    print("✅ Subido:", f"https://studio.youtube.com/video/{video_id}/edit")
    return video_id

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=str, default=None, help="Ruta MP4 (opcional). Si se omite, autodetecta por tmdb_id.")
    args = ap.parse_args()
    main(args.video)
