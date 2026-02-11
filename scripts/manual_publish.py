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
import download_assets
import build_youtube_metadata
import build_short
import upload_youtube
import cleanup_temp
import movie_utils
from movie_utils import (
    api_get, enrich_movie_basic, get_deep_research_data
)

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
        if creds.expired and creds.refresh_token: 
            creds.refresh(Request())
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
        return build('youtube', 'v3', credentials=creds)
    except Exception as e:
        logging.error(f"Error auth YouTube: {e}")
        return None

def find_youtube_trailer(title, year, actor=None):
    youtube = get_youtube_service()
    if not youtube: return None, None
    
    query = f"{title} {year} {actor if actor else ''} official trailer movie".replace("  ", " ")
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
            try:
                f.write(obj)
            except UnicodeEncodeError:
                if hasattr(obj, 'encode'):
                    f.write(obj.encode('ascii', 'replace').decode('ascii'))
                else:
                    f.write(str(obj))
            f.flush()
    def flush(self) :
        for f in self.files:
            f.flush()

def main():
    # 0. Configurar logging dual (Terminal + Archivo)
    # Sobreescribimos el log diario, el histórico se mantiene en log_history
    f_daily = open(ROOT / "log_autopilot.txt", "w", encoding="utf-8")
    f_history = open(ROOT / "log_history.txt", "a", encoding="utf-8")
    
    # Redirigimos todo a ambos archivos y a la consola
    sys.stdout = Tee(sys.stdout, f_daily, f_history)
    sys.stderr = Tee(sys.stderr, f_daily, f_history)

    # Re-configuramos logging para que use el nuevo sys.stderr (nuestro Tee)
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s', stream=sys.stderr)

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
    res = api_get("/search/movie", {"query": target_title, "language": "es-ES"})
    
    if not res or not res.get("results"):
        logging.error("❌ No encontrada en TMDB.")
        return

    # --- FILTRADO DE RESULTADOS (Anti-Bollywood y limpieza de años) ---
    excluded_langs = ['hi', 'te', 'ta', 'ml', 'kn', 'pa', 'ur']
    filtered_results = []
    for m in res["results"]:
        # 1. Filtro de idioma original (Evita scripts no latinos/cine regional indio)
        if m.get("original_language") in excluded_langs:
            continue
        
        # 2. Filtro de año estricto (Margen de 1 año para evitar errores de TMDB)
        date_str = m.get("release_date")
        if date_str:
            m_year = date_str[:4]
            if abs(int(m_year) - int(target_year)) > 1:
                continue
        filtered_results.append(m)

    if not filtered_results:
        logging.error(f"❌ No se encontraron coincidencias válidas para '{target_title}' en {target_year} tras filtrar idiomas y fechas.")
        return

    results = filtered_results
    if len(results) > 1:
        print(f"\nSe han encontrado {len(results)} coincidencias:")
        for i, m in enumerate(results):
            title = m.get('title')
            orig_title = m.get('original_title', '')
            display_title = title if title == orig_title else f"{title} ({orig_title})"
            overview = m.get('overview', 'Sin descripción disponible.')
            short_desc = (overview[:100] + '...') if len(overview) > 100 else overview
            print(f"  [{i+1}] {display_title} - {m.get('release_date', 'N/A')} [ID: {m['id']}]\n      └─ {short_desc}")
        
        while True:
            try:
                choice = input("\nSelecciona el número correcto (o 'q' para cancelar): ").strip().lower()
                if choice == 'q':
                    logging.info("Proceso cancelado.")
                    return
                idx = int(choice) - 1
                if 0 <= idx < len(results):
                    tmdb_movie = results[idx]
                    break
                else:
                    print("Número fuera de rango.")
            except ValueError:
                print("Por favor, introduce un número válido.")
    else:
        tmdb_movie = results[0]

    logging.info(f"✅ Seleccionado: {tmdb_movie.get('title')}")

    # Verificación de si ya existe
    if movie_utils.is_published(tmdb_movie["id"]):
        print(f"\n⚠️  ATENCIÓN: '{tmdb_movie['title']}' ya ha sido publicada anteriormente.")
        confirm = input("¿Deseas continuar con la publicación manual? (s/n): ").strip().lower()
        if confirm != 's':
            logging.info("Proceso cancelado por el usuario.")
            return
    
    # 3. Obtener datos y buscar Trailer
    # Primero enriquecemos para tener el reparto y buscar el tráiler con precisión
    data = enrich_movie_basic(tmdb_movie["id"], tmdb_movie.get('title'), int(target_year))

    if not data:
        logging.error("❌ Fallo al enriquecer datos de TMDB.")
        return

    if not data.get('has_poster'):
        logging.warning(f"⚠️ La película '{tmdb_movie['title']}' no tiene póster ni backdrop en TMDB.")
        if input("¿Deseas continuar sin imagen? (El vídeo fallará al generarse si no hay visuales) (s/n): ").lower() != 's':
            return
    
    main_actor = data.get('actors', [""])[0]
    trailer_url, upload_date = find_youtube_trailer(data['titulo'], target_year, actor=main_actor)
    
    if not trailer_url:
        logging.error("❌ No se encontró tráiler en YouTube.")
        return

    data['trailer_url'] = trailer_url

    # --- VALIDACIÓN DE SINOPSIS ---
    # Si no hay sinopsis, no abortamos aquí; dejamos que el Deep Research intente buscarla en la web.
    if not data.get('sinopsis') or len(data['sinopsis'].strip()) < 10:
        logging.warning(f"⚠️ Sin sinopsis en TMDB para '{tmdb_movie['title']}'. Intentaremos búsqueda web en el siguiente paso...")

    # Añadimos datos extra necesarios
    data['upload_date'] = upload_date
    data['views'] = 0 # Dummy value, es manual
    data['score'] = 0 # Valor manual
    data['ia_platform_from_title'] = "Cine" # Default

    # 5. DEEP RESEARCH (El Editor IA)
    logging.info("🕵️  Consultando al Editor IA (Deep Research)...")
    main_actor_ref = data.get('actors', [data['titulo']])[0]
    deep_data = get_deep_research_data(data['titulo'], str(target_year), main_actor_ref, data['tmdb_id'], data.get('sinopsis', ''))

    if not deep_data:
        logging.error(f"❌ ABORTANDO: El Editor IA no ha podido encontrar información real sobre '{data['titulo']}' ({target_year}). Para evitar inventar datos, detenemos el proceso.")
        return

    if deep_data:
        strategy = deep_data.get('hook_angle', 'CURIOSITY')
        
        logging.info("\n" + "█"*60)
        logging.info(f"🧠 ESTRATEGIA ELEGIDA: {strategy} 🔥")
        logging.info("█"*60)
        logging.info(f"🤫 Salseo:       {deep_data.get('movie_curiosity', 'N/A')}")
        logging.info(f"📝 Sinopsis:     {deep_data.get('synopsis', 'N/A')[:80]}...")
        logging.info("-" * 60 + "\n")
        
        if deep_data.get('synopsis'): data['sinopsis'] = deep_data['synopsis']
        if deep_data.get('platform'): data['ia_platform_from_title'] = deep_data['platform']
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

                # --- RESUMEN FINAL ESTILO PUBLISH.PY ---
                # Intentar recargar el JSON actualizado con el guion final
                final_sel = data
                try:
                    if NEXT_FILE.exists():
                        final_sel = json.loads(NEXT_FILE.read_text(encoding="utf-8"))
                except: pass

                logging.info("\n" + "="*70)
                logging.info("🎬 RESUMEN DE LA PUBLICACIÓN MANUAL:")
                logging.info(f"   📼 Título: {final_sel.get('titulo', 'N/A')}")
                logging.info(f"   🎯 Estrategia: {final_sel.get('hook_angle', 'N/A')}")
                logging.info(f"   📝 Sinopsis: {final_sel.get('sinopsis', 'N/A')}")
                logging.info(f"   🔗 Trailer: {final_sel.get('trailer_url', 'N/A')}")
                logging.info(f"   ✅ Short: https://studio.youtube.com/video/{video_id}/edit")
                logging.info(f"\n   📜 GUIÓN GENERADO:")
                logging.info(f"   {final_sel.get('guion_generado', 'N/A')}")
                logging.info("="*70 + "\n")
            else:
                logging.error("❌ Fallo en la subida.")
        else:
            logging.error("❌ No se generó el MP4.")

    except Exception as e:
        logging.error(f"❌ Error en el proceso: {e}")

if __name__ == "__main__":
    main()