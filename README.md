# YouTube Shorts Generator Project

Welcome to the **YouTube Shorts Generator Project**! This repository showcases an exciting learning journey into video automation, where I’ve built a Python-based tool to create and manage YouTube Shorts from movie data. This project is a personal exploration of programming, API integration, and multimedia processing—perfect for anyone looking to dive into creative coding. Best of all, it’s not intended for monetization; it’s purely for educational purposes and sharing knowledge with the community! Current work can be seen in https://www.youtube.com/@EstrenoscineEspaña-r5j

## Project Overview

This project automates the creation of YouTube Shorts by fetching movie data, generating narrated videos, and preparing them for upload. It leverages APIs (like TMDb), AI (Gemini for strategy and script, Google Neural2 for voice synthesis), and video editing libraries (MoviePy) to produce polished 1080x1920 videos with dynamic content.

## ⚠️ Important: Environment Setup

**This project relies on a specific local Python environment.**

The full path to the python executable is:
`C:\Users\carth\code\youtube_shorts\venv\Scripts\python.exe`

**You must use this full path for executing all scripts to ensure the correct dependencies are used.**

> [!NOTE]
> We have upgraded to **Python 3.12**. Please ensure your environment is set up accordingly.

For example:
```powershell
C:\Users\carth\code\youtube_shorts\venv\Scripts\python.exe scripts/publish.py
```

## Setup Instructions

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/JavierRubio4U/youtube_shorts.git
   cd youtube-shorts-generator
   ```

2. **Install Dependencies**:
   - Create a virtual environment:
     ```bash
     python -m venv venv
     .\venv\Scripts\activate
     ```
   - Install required packages (see `requirements.txt` for full list):
     ```bash
     pip install -r requirements.txt
     ```
   - Ensure FFmpeg is installed (e.g., via `choco install ffmpeg` on Windows with Chocolatey).

3. **Configure APIs**:
   - **`config/` folder**:
     - `google_api_key.txt`: Your key for Gemini (AI) and Google TTS (Voice).
     - `tmdb_api_key.txt`: Your key for movie data.
     - `client_secret.json`: (Optional) YouTube API credentials for uploading.

## 📘 User Manual & Workflow

This section outlines the main commands to generate content. **Remember to prepend the full python path** (`C:\Users\carth\code\youtube_shorts\venv\Scripts\python.exe`) to these commands.

### 🚀 1. Main Workflow

#### A. Automatic Mode ("Autopilot")
The system selects the best trending movie, researches it, and publishes the video without your intervention.

* **Command:**
    ```powershell
    python scripts/publish.py
    ```
* **Process:**
    1.  Runs `find.py`: Searches candidates and applies **Deep Research** to decide the strategy (Actor, Director, Curiosity, or Plot).
    2.  Downloads Trailer and Poster.
    3.  Extracts dynamic clips.
    4.  Generates Script (with "cheeky" personality) and Audio (Google Neural2 with dramatic pauses).
    5.  Edits vertical MP4.
    6.  Uploads to YouTube Shorts.

#### B. Manual Mode ("Sniper")
You choose a specific movie, and the system handles the heavy lifting.

* **Command:**
    ```powershell
    python scripts/manual_publish.py
    ```
* **Process:**
    1.  Prompts for **Title** and **Year** of the movie.
    2.  Finds the official trailer on YouTube.
    3.  Runs **Deep Research** to find the perfect hook.
    4.  Continues with editing and uploading like automatic mode.

### 🛠️ 2. Analysis & Testing Tools

Useful for verifying data before creating a video or troubleshooting.

#### C. Search & Analyze Only (`find.py`)
Run this to see what movie the system would pick and what gossip/hook it finds, without downloading or editing anything.

* **Command:**
    ```powershell
    python scripts/find.py
    ```
* **Result:**
    * Generates state file: `output/state/next_release.json`.
    * Prints "Gossip Sheet" to console: Chosen strategy, Curiosity, Actor reference, etc.

#### D. Test Voice & Narration (`test_google_voice.py`)
Generates a test audio with the current synopsis to verify Google API works and the rhythm (SSML) is correct.

* **Command:**
    ```powershell
    python test/test_google_voice.py
    ```
* **Result:**
    * Creates an MP3 file in `output/`.

#### E. Regenerate Video Only (`build_short.py`)
If you already have the movie downloaded (in `next_release.json`) but want to change the edit, music, or script without re-searching.

* **Command:**
    ```powershell
    python scripts/build_short.py
    ```

#### F. List Available AI Models (`list_models.py`)
Queries Google API to see which Gemini models are active with your current key.

* **Command:**
    ```powershell
    python test/list_models.py
    ```

## 📂 Project Structure

- **`scripts/`**:
    - `publish.py`: Main automatic pipeline.
    - `manual_publish.py`: Main manual pipeline.
    - `find.py`: Movie selection and Deep Research logic.
    - `ai_narration.py`: AI personality, word limits, and SSML control.
    - `movie_utils.py`: The "researcher brain". Contains the Deep Research prompt.
    - `build_short.py`: Video assembler.
- **`config/`**: API keys (`google_api_key.txt`, `tmdb_api_key.txt`).
- **`output/state/next_release.json`**: Temporary file with current movie info.

## ⚠️ Troubleshooting

1.  **Error `403 PERMISSION_DENIED` (Google TTS):**
    * Your API Key lacks voice permission. Go to Google Cloud Console > Credentials > Restrictions and enable **"Cloud Text-to-Speech API"**.
2.  **Video uploads but not as a Short:**
    * Verify duration is under 60 seconds. The script chops the script to ensure 30-40 seconds, so this shouldn't happen.

## ⏰ Automatización Diaria (Windows Task Scheduler)

Si tienes configurada una tarea programada en Windows para que este script se ejecute automáticamente todos los días (usando el lanzador `lanzar_y_log.bat`), aquí tienes una guía rápida para gestionarla:

### 1. Abrir el Programador de Tareas
Tienes dos opciones:
*   **Opción A (Rápida):** Pulsa la tecla `Windows`, escribe **"Programador de tareas"** y pulsa Enter.
*   **Opción B (Comando):** Pulsa `Windows + R`, escribe `taskschd.msc` y pulsa Enter.

### 2. Encontrar la Tarea
En la ventana que se abre, busca en la lista central (dentro de "Biblioteca del Programador de tareas") una tarea que probablemente llamaste **"YouTube Shorts Auto"** o similar.

### 3. Activar, Desactivar o Modificar
Haz clic derecho sobre el nombre de la tarea para ver las opciones:

*   **Desactivar (Disable):** Detiene la ejecución automática. Útil si te vas de vacaciones o quieres pausar el canal temporalmente.
*   **Activar (Enable):** Reactiva la programación diaria si estaba pausada.
*   **Ejecutar (Run):** Lanza el script en ese mismo instante (ideal para probar si funciona sin esperar a la hora programada).
*   **Propiedades (Properties):**
    *   Pestaña **Desencadenadores (Triggers):** Aquí puedes cambiar la **hora** a la que se ejecuta (ej: cambiar de las 18:00 a las 20:00).
    *   Pestaña **Acciones (Actions):** Verifica que la ruta apunte correctamente a tu fichero `lanzar_y_log.bat`.
        *   *Nota:* Si mueves la carpeta del proyecto de sitio, **debes** actualizar la ruta aquí, o la tarea fallará.

