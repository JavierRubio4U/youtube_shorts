# scripts/upload_youtube.py
import json
import time
import sys
from pathlib import Path
import argparse
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# Nuevos imports para fallback de frames y resize
from moviepy.editor import VideoFileClip
from PIL import Image, ImageStat
import logging  # Para logs extras

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# ---------------------------------------------------------------------
# Rutas y constantes
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
CONFIG_DIR = ROOT / "config"
TEMP_DIR = ROOT / "temp"  # Para thumbnails temp
STATE_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = STATE_DIR / "youtube_token.json"
CLIENT_SECRET_FILE = CONFIG_DIR / "client_secret.json"

# ---------------------------------------------------------------------
# Utils extras para thumbnails
# ---------------------------------------------------------------------
def is_black_image(image_path: Path, threshold: float = 10.0) -> bool:
    """Chequea si la imagen es mayormente negra (brillo promedio bajo)."""
    try:
        img = Image.open(image_path).convert('L')  # A grayscale
        stat = ImageStat.Stat(img)
        mean_brightness = stat.mean[0]
        logging.info(f"Brillo promedio de {image_path.name}: {mean_brightness}")
        return mean_brightness < threshold
    except Exception as e:
        logging.warning(f"Error al chequear imagen {image_path}: {e}")
        return True  # Trata como inválida si falla

def resize_image_for_thumbnail(image_path: Path) -> Path:
    """Redimensiona la imagen a 1280x720, calidad 85 para <2MB, guarda en temp."""
    try:
        img = Image.open(image_path)
        img = img.resize((1280, 720), Image.LANCZOS)  # Resize recomendado por YouTube
        out_path = TEMP_DIR / f"{image_path.stem}_resized.jpg"
        img.save(out_path, 'JPEG', quality=85, optimize=True)
        logging.info(f"Imagen redimensionada y guardada en {out_path} (tamaño: {out_path.stat().st_size / 1024:.2f} KB)")
        return out_path
    except Exception as e:
        logging.error(f"Error al redimensionar {image_path}: {e}")
        return None

def extract_frame_from_video(video_path: str, time_sec: float = 5.0) -> Path | None:
    """Extrae un frame del video en el segundo dado y lo guarda como JPG redimensionado."""
    try:
        clip = VideoFileClip(video_path)
        frame = clip.get_frame(time_sec)
        img = Image.fromarray(frame)
        tmdb_id = Path(video_path).stem.split('_')[0]  # Extrae ID del nombre del video
        temp_path = TEMP_DIR / f"{tmdb_id}_temp_thumb.jpg"
        img.save(temp_path)
        resized_path = resize_image_for_thumbnail(temp_path)
        temp_path.unlink(missing_ok=True)  # Limpia original
        clip.close()
        if resized_path:
            logging.info(f"Frame extraído en {time_sec}s, redimensionado y guardado en {resized_path}")
            return resized_path
        return None
    except Exception as e:
        logging.error(f"Error al extraer frame de {video_path}: {e}")
        return None

# ---------------------------------------------------------------------
# Utils existentes
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
            "selfDeclaredMadeForKids": meta["made_for_kids"]
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
        video_id = response.get("id")
        if video_id:
            # --- AQUÍ VA EL CÓDIGO ---
            print(f"✅ Video subido con ID: {video_id}. Esperando 15 segundos...")
            time.sleep(15) # <-- 2. AÑADE LA PAUSA AQUÍ
            # Subir thumbnail: usar el póster almacenado
            tmdb_id = meta["tmdb_id"]
            thumbnail_path = ROOT / "assets" / "posters" / f"{tmdb_id}_poster.jpg"
            resized_path = None
            if thumbnail_path.exists() and not is_black_image(thumbnail_path):
                resized_path = resize_image_for_thumbnail(thumbnail_path)
                if resized_path:
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(str(resized_path))
                    ).execute()
                    print(f"✅ Thumbnail (póster redimensionado) subido para video ID: {video_id}")
                    resized_path.unlink(missing_ok=True)  # Limpia temp
            else:
                print(f"⚠️ Póster no encontrado o inválido (negro): {thumbnail_path}")
                # Fallback 1: Usa el primer backdrop si existe
                backdrops_dir = ROOT / "assets" / "backdrops"  # Ajusta si tu carpeta es distinta
                fallback_thumb = list(backdrops_dir.glob(f"{tmdb_id}_backdrop_*.jpg"))  # Ajusta patrón
                if fallback_thumb and not is_black_image(fallback_thumb[0]):
                    thumbnail_path = fallback_thumb[0]
                    resized_path = resize_image_for_thumbnail(thumbnail_path)
                    if resized_path:
                        print(f"Usando fallback backdrop redimensionado: {resized_path}")
                        youtube.thumbnails().set(
                            videoId=video_id,
                            media_body=MediaFileUpload(str(resized_path))
                        ).execute()
                        print(f"✅ Thumbnail fallback (backdrop) subido para video ID: {video_id}")
                        resized_path.unlink(missing_ok=True)
                else:
                    # Fallback 2: Extrae frame del video
                    print("⚠️ No hay backdrop válido. Extrayendo frame del video...")
                    frame_path = extract_frame_from_video(video_path, time_sec=5.0)  # ~inicio del primer clip, ya redimensiona
                    if frame_path and not is_black_image(frame_path):
                        youtube.thumbnails().set(
                            videoId=video_id,
                            media_body=MediaFileUpload(str(frame_path))
                        ).execute()
                        print(f"✅ Thumbnail (frame del video) subido para video ID: {video_id}")
                        frame_path.unlink(missing_ok=True)
                    else:
                        print("⚠️ No se pudo extraer frame válido. YouTube usará thumbnails auto-generados.")
        return video_id
    except Exception as e:
        print(f"Fallo en la subida: {e}")
        # Limpia temp si existe
        if 'resized_path' in locals() and resized_path:
            resized_path.unlink(missing_ok=True)
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