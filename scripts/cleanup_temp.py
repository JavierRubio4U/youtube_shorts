# scripts/cleanup_temp.py
import logging
import shutil
from pathlib import Path

# --- Definición de Rutas ---
ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "assets"
TEMP_DIR = ROOT / "temp" # La carpeta temp de tu proyecto

# Directorios de assets a vaciar al inicio
POSTERS_VERT_DIR = ASSETS_DIR / "posters_vertical"
POSTERS_DIR = ASSETS_DIR / "posters"
NARRATION_DIR = ASSETS_DIR / "narration"
CLIPS_DIR = ASSETS_DIR / "video_clips"
TMP_ASSETS_DIR = ASSETS_DIR / "tmp"
# Si tienes otras como 'posters_vertical', añádelas aquí
ASSET_DIRS_TO_CLEAR = [POSTERS_DIR, NARRATION_DIR, CLIPS_DIR, POSTERS_VERT_DIR, TMP_ASSETS_DIR]

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def _clear_directory(dir_path: Path):
    """Función interna para borrar de forma segura todo el contenido de una carpeta."""
    if not dir_path.exists():
        logging.warning(f"El directorio a limpiar no existe, creando: {dir_path}")
        dir_path.mkdir(parents=True, exist_ok=True)
        return
    
    logging.info(f"Vaciando el contenido de: {dir_path}...")
    for item in dir_path.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        except Exception as e:
            logging.error(f"No se pudo eliminar {item}: {e}")

def cleanup_on_start():
    """Limpia todos los assets generados por la ejecución anterior y archiva shorts anteriores."""
    logging.info("--- Limpieza de Inicio: Eliminando assets de la ejecución anterior ---")
    for dir_path in ASSET_DIRS_TO_CLEAR:
        _clear_directory(dir_path)
    
    # --- CAMBIO: Borrar shorts anteriores en lugar de archivarlos ---
    shorts_dir = ROOT / "output" / "shorts"
    logging.info(f"Limpiando shorts de la carpeta: {shorts_dir}")
    if shorts_dir.exists():
        for file in shorts_dir.iterdir():
            if file.is_file():
                try:
                    file.unlink() # Borra el archivo
                    logging.info(f"Eliminado short anterior: {file.name}")
                except Exception as e:
                    logging.error(f"No se pudo eliminar el archivo {file.name}: {e}")
    
    logging.info("--- Limpieza de Inicio completada ---")

def cleanup_on_end():
    """Limpia la carpeta /temp/ del proyecto al finalizar."""
    logging.info("--- Limpieza Final: Eliminando archivos y carpetas temporales ---")
    _clear_directory(TEMP_DIR)
    logging.info("--- Limpieza Final completada ---")

if __name__ == '__main__':
    # Esto permite probar el script directamente
    print("Ejecutando limpieza de inicio de prueba...")
    cleanup_on_start()
    print("\nEjecutando limpieza final de prueba...")
    # Creamos contenido falso en /temp para simular una ejecución
    (TEMP_DIR / "tmp_12345").mkdir(exist_ok=True, parents=True)
    (TEMP_DIR / "un_archivo_temporal.tmp").touch()
    cleanup_on_end()