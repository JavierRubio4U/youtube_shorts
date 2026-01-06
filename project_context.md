# Contexto del Proyecto: Generador de YouTube Shorts

Este documento sirve como referencia técnica y operativa para el proyecto de automatización de YouTube Shorts. Describe la estructura, los scripts clave, los comandos de uso y el flujo de trabajo.

## 📂 Estructura del Proyecto

El proyecto está organizado en la carpeta raíz `c:/Users/carth/code/youtube_shorts`.

*   **`scripts/`**: Contiene la lógica principal en Python.
    *   `publish.py`: Orquestador principal para la ejecución automática (busca estrenos, genera y sube).
    *   `manual_publish.py`: Script para generar y subir un Short de una película específica manualmente.
    *   `build_short.py`: Ensambla el video final (imágenes, clips, audio).
    *   `ai_narration.py`: Genera guion y audio con IA (ElevenLabs).
    *   `download_assets.py`: Descarga pósters y metadatos.
    *   `extract_video_clips_from_trailer.py`: Descarga tráilers y extrae clips.
    *   `upload_youtube.py`: Sube el video final a YouTube.
    *   `movie_utils.py`: Utilidades comunes (API TMDB, Deep Research).
*   **`assets/`**: Almacenamiento temporal de recursos multimedia (pósters, clips, música).
*   **`output/`**:
    *   `shorts/`: Donde se guardan los archivos `.mp4` finales.
    *   `state/`: Archivos JSON de estado (`next_release.json`, `youtube_metadata.json`, `assets_manifest.json`).
*   **`lanzar_y_log.bat`**: Script batch para ejecutar el proceso automático y guardar logs.

## 🛠️ Entorno de Ejecución

El proyecto utiliza **Conda** para gestionar las dependencias.
*   **Entorno**: `shorts311`
*   **Ruta Python**: `C:\pinokio\bin\miniconda\envs\shorts311\python.exe`

## 🚀 Comandos de Uso

### 1. Publicación Manual (Película Específica)
Usa este comando para generar y subir un Short de una película concreta que tú elijas.

**Sintaxis:**
```powershell
<Ruta_Python> scripts/manual_publish.py "<Titulo_Pelicula>" <Año>
```

**Ejemplo:**
```powershell
C:\pinokio\bin\miniconda\envs\shorts311\python.exe scripts/manual_publish.py "templo de los huesos" 2026
```

### 2. Publicación Automática (Búsqueda de Estrenos)
Este script busca automáticamente estrenos recientes o próximos, selecciona el mejor candidato y genera el video.

**Comando:**
```powershell
C:\pinokio\bin\miniconda\envs\shorts311\python.exe scripts/publish.py
```

### 3. Ejecución con Log (Batch)
Ejecuta el proceso automático y guarda la salida en `log_ejecucion.txt`. Útil para depuración o ejecución desatendida.

**Comando:**
```powershell
.\lanzar_y_log.bat
```

## 🔄 Flujo de Trabajo (Pipeline)

1.  **Selección**: Se elige una película (manual o automática vía TMDB).
2.  **Deep Research**: Se analiza la película para extraer "salseo", curiosidades y definir una estrategia de gancho (Hook Angle).
3.  **Assets**: Se descargan pósters y el tráiler de YouTube.
4.  **Clips**: Se extraen clips interesantes del tráiler.
5.  **Narración**: Se genera un guion basado en la estrategia y se convierte a audio (ElevenLabs).
6.  **Edición**: Se ensambla el video vertical (9:16) con música de fondo.
7.  **Subida**: Se sube a YouTube con título, descripción y etiquetas optimizadas.

## 📝 Notas Importantes

*   **Visualización de Logs**: Al ejecutar desde VS Code mediante una IA, la terminal puede no mostrar salida en tiempo real. Para ver el progreso, ejecuta los comandos manualmente en una terminal de VS Code.
*   **Tiempos**: El proceso completo suele tardar entre **20 y 40 minutos**, dependiendo de la duración del renderizado y la subida.
*   **Codificación**: En Windows, es crucial forzar la codificación UTF-8 en la terminal para evitar errores con emojis (ya manejado en `manual_publish.py`).
