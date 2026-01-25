import logging
import json
import sys
import io
# Force UTF-8 output for Windows terminals
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# --- Configuración de Paths ---
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STATE_DIR = ROOT / "output" / "state"
TMP_DIR = ROOT / "assets" / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)
NEXT_FILE = TMP_DIR / "next_release.json"

# Añadimos scripts al path para importar módulos
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# --- Imports de tus módulos ---
from movie_utils import (
    api_get, enrich_movie_basic, get_deep_research_data, load_config
)
import download_assets
import build_youtube_metadata
import build_short
import upload_youtube
import cleanup_temp
import movie_utils

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def get_youtube_service():
    TOKEN_FILE = STATE_DIR / "youtube_token.json"
    if not TOKEN_FILE.exists():
        logging.error("Falta youtube_token.json")
        return None
    try:
        with open(TOKEN_FILE, 'r') as f: token_data = json.load(f)
        creds = Credentials(token=token_data['token'], refresh_token=token_data['refresh_token'],
                            token_uri=token_data['token_uri'], client_id=token_data['client_id'],
                            client_secret=token_data['client_secret'], scopes=token_data['scopes'])
        if creds.expired and creds.refresh_token: creds.refresh(Request())
        return build('youtube', 'v3', credentials=creds)
    except Exception as e:
        logging.error(f"Error auth YouTube: {e}")
        return None

def find_youtube_trailer(title, year):
    youtube = get_youtube_service()
    if not youtube: return None, None
    
    query = f"{title} {year} official trailer movie"
    logging.info(f"🔎 Buscando tráiler en YouTube: '{query}'...")
    
    try:
        req = youtube.search().list(part="id,snippet", q=query, type="video", maxResults=5)
        res = req.execute()
        items = res.get("items", [])
        if not items: return None, None
        
        # Cogemos el primero
        first = items[0]
        video_id = first['id']['videoId']
        video_title = first['snippet']['title']
        logging.info(f"   -> Encontrado: {video_title}")
        return f"https://www.youtube.com/watch?v={video_id}", first['snippet']['publishedAt']
    except Exception as e:
        logging.error(f"Fallo búsqueda YT: {e}")
        return None, None

class Tee(object):
    def __init__(self, *files):
        self.files = files
    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush() # If you want the output to be visible immediately
    def flush(self) :
        for f in self.files:
            f.flush()

def main():
    # 0. Configurar logging dual (Terminal + Archivo)
    log_file = open("log_ejecucion.txt", "w", encoding="utf-8")
    sys.stdout = Tee(sys.stdout, log_file)
    sys.stderr = Tee(sys.stderr, log_file)

    # 0.1 Limpieza inicial
    cleanup_temp.cleanup_on_start()

    # 1. Inputs del Usuario
    print("\n" + "="*40)
    print("🎬 PUBLICADOR MANUAL DE SHORTS")
    print("="*40)
    
    if len(sys.argv) >= 3:
        target_title = sys.argv[1]
        target_year = sys.argv[2]
        print(f"Argumentos detectados: {target_title} ({target_year})")
    else:
        target_title = input("Nombre de la película: ").strip()
        target_year = input("Año de estreno: ").strip()

    if not target_title or not target_year:
        print("❌ Datos inválidos.")
        return

    # 2. Buscar en TMDB
    logging.info(f"🔎 Buscando '{target_title}' en TMDB...")
    res = api_get("/search/movie", {"query": target_title, "year": target_year, "language": "es-ES"})
    
    if not res or not res.get("results"):
        logging.error("❌ No encontrada en TMDB.")
        return

    # Selección simple (el primer resultado)
    tmdb_movie = res["results"][0]
    logging.info(f"✅ Coincidencia: {tmdb_movie['title']} ({tmdb_movie.get('release_date')})")
    
    # 3. Buscar Trailer
    trailer_url, upload_date = find_youtube_trailer(tmdb_movie['title'], target_year)
    if not trailer_url:
        logging.error("❌ No se encontró tráiler en YouTube.")
        return

    # 4. Enriquecer datos (Metadatos básicos)
    data = enrich_movie_basic(tmdb_movie["id"], tmdb_movie['title'], int(target_year), trailer_url)
    if not data:
        logging.error("❌ Fallo al enriquecer datos de TMDB.")
        return

    # Añadimos datos extra necesarios
    data['upload_date'] = upload_date
    data['views'] = 0 # Dummy value, es manual
    data['score'] = 0 # Valor manual
    data['ia_platform_from_title'] = "Cine" # Default

    # 5. DEEP RESEARCH (El Editor IA)
    logging.info("🕵️  Consultando al Editor IA (Deep Research)...")
    main_actor_ref = data.get('actors', [data['titulo']])[0]
    deep_data = get_deep_research_data(data['titulo'], str(target_year), main_actor_ref, data['tmdb_id'])

    if deep_data:
        strategy = deep_data.get('hook_angle', 'CURIOSITY')
        
        logging.info("\n" + "█"*60)
        logging.info(f"🧠 ESTRATEGIA ELEGIDA: {strategy} 🔥")
        logging.info("█"*60)
        logging.info(f"🤫 Salseo:       {deep_data.get('movie_curiosity', 'N/A')}")
        logging.info(f"📝 Sinopsis:     {deep_data.get('synopsis', 'N/A')[:80]}...")
        logging.info("-" * 60 + "\n")
        
        if deep_data.get('synopsis'): data['sinopsis'] = deep_data['synopsis']
        data['actor_reference'] = deep_data.get('actor_reference')
        data['director'] = deep_data.get('director')
        data['movie_curiosity'] = deep_data.get('movie_curiosity')
        data['hook_angle'] = strategy

    # 6. Guardar JSON (Contrato)
    payload = {
        **data,
        "seleccion_generada": datetime.now(timezone.utc).isoformat() + "Z"
    }
    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("✅ next_release.json generado.")

    # --- EJECUCIÓN DEL PIPELINE ---
    try:
        # Descarga
        logging.info("▶ Paso 2: Descargando assets...")
        download_assets.main()

        # Clips
        logging.info("▶ Paso 2.5: Extrayendo clips...")
        subprocess.run([sys.executable, str(SCRIPTS / "extract_video_clips_from_trailer.py")], 
                        check=True, cwd=ROOT, stdout=subprocess.DEVNULL)

        # Metadata
        logging.info("▶ Paso 3: Metadata...")
        build_youtube_metadata.main()

        # Build Short
        logging.info("▶ Paso 4: Creando VIDEO...")
        mp4_path = build_short.main()

        if mp4_path:
            # Subida
            logging.info(f"▶ Paso 5: Subiendo a YouTube ({Path(mp4_path).name})...")
            video_id = upload_youtube.main(mp4_path)
            
            if video_id:
                logging.info(f"🎉 ¡SUBIDO! https://youtu.be/{video_id}")
                movie_utils.mark_published(data, video_id)
                cleanup_temp.cleanup_on_end()
            else:
                logging.error("❌ Fallo en la subida.")
        else:
            logging.error("❌ No se generó el MP4.")

    except Exception as e:
        logging.error(f"❌ Error en el proceso: {e}")

if __name__ == "__main__":
    main()