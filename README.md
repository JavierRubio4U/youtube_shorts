# YouTube Shorts Generator Project

Welcome to the **YouTube Shorts Generator Project**! This repository showcases an exciting learning journey into video automation, where I’ve built a Python-based tool to create and manage YouTube Shorts from movie data. This project is a personal exploration of programming, API integration, and multimedia processing—perfect for anyone looking to dive into creative coding. Best of all, it’s not intended for monetization; it’s purely for educational purposes and sharing knowledge with the community! Current work can be seen in https://www.youtube.com/@EstrenoscineEspa%C3%B1a-r5j

## Project Overview

This project automates the creation of YouTube Shorts by fetching movie data, generating narrated videos, and preparing them for upload (though uploads are limited by YouTube’s API quotas). It leverages APIs (like TMDb), machine learning (Ollama for narration), and video editing libraries (MoviePy) to produce polished 1080x1920 videos with dynamic overlays. Whether you're a beginner or an advanced coder, this repo offers a treasure trove of techniques to explore and learn from.

- **Goal**: A hands-on learning experience in Python, API usage, and video processing.
- **Non-Commercial**: This is not for profit—just a passion project to grow my skills and inspire others.
- **GitHub Appeal**: A beautifully organized repo with clear documentation, ready to impress your peers or future employers!

## How It Works

The project follows a structured pipeline with multiple scripts, each handling a specific task. Below is the order of execution and the purpose of each script:

### Script Execution Order
1. **select_next_release.py**
   - **Function**: Selects the next movie to process from a list of upcoming releases fetched from TMDb.
   - **Details**: Analyzes candidates based on title availability and backdrop count, saving the selection to `next_release.json`.

2. **download_assets.py**
   - **Function**: Downloads movie posters and backdrops from TMDb, converting them to vertical (9:16) formats.
   - **Details**: Saves assets in the `assets` directory and updates `assets_manifest.json` with file paths.

3. **build_youtube_metadata.py**
   - **Function**: Generates metadata (title, description, tags) for the YouTube video based on movie data.
   - **Details**: Uses AI (Ollama) for translation if needed and saves to `youtube_metadata.json`.

4. **ai_narration.py**
   - **Function**: Generates a narrated synopsis using AI and synthesizes audio with Coqui TTS.
   - **Details**: Produces a WAV file for the video’s audio track, leveraging machine learning models.

5. **overlay.py**
   - **Function**: Creates an overlay image with the movie title and release date on black bands.
   - **Details**: Generates a 1080x1920 PNG with semi-transparent bands, saved as `overlay_test_<tmdb_id>.png`.

6. **build_short.py**
   - **Function**: Assembles the final video by combining the poster, backdrops, narration audio, and overlay.
   - **Details**: Outputs a 1080x1920 MP4 file in `output/shorts`, with a 4-second intro without bands.

7. **upload_youtube.py**
   - **Function**: Uploads the generated video to YouTube (optional, quota-dependent).
   - **Details**: Requires `client_secret.json` for authentication; currently commented out in `publish.py` due to daily limits.

8. **publish.py**
   - **Function**: Orchestrates the entire pipeline, executing the above scripts in order.
   - **Details**: Serves as the main entry point; upload functionality is disabled until YouTube quota resets.

### Workflow Diagram
```
[select_next_release] --> [download_assets] --> [build_youtube_metadata] --> [ai_narration] --> [overlay] --> [build_short] --> [upload_youtube]
         ↑                                                                      ↓
         └-------------------[publish.py]--------------------------------------┘
```

## Project Structure
- **`scripts/`**: Contains all Python scripts.
  - `publish.py`: Main script to run the pipeline.
  - `select_next_release.py`: Movie selection logic.
  - `download_assets.py`: Asset downloader.
  - `build_youtube_metadata.py`: Metadata generator.
  - `ai_narration.py`: AI narration and audio synthesis.
  - `overlay.py`: Overlay image creator.
  - `build_short.py`: Video assembler.
  - `upload_youtube.py`: YouTube uploader.
- **`assets/`**: Stores downloaded posters and backdrops.
- **`output/`**: Contains generated files (JSON, MP4, PNG).
- **`config/`**: Holds `client_secret.json` for YouTube API (optional).

## Setup Instructions
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/JavierRubio4U/youtube_shorts.git
   cd youtube-shorts-generator
   ```

2. **Install Dependencies**:
   - Create a virtual environment:
     ```bash
     python -m venv shorts311
     shorts311\Scripts\activate
     ```
   - Install required packages:
     ```bash
     pip install moviepy Pillow numpy ollama langdetect TTS requests google-auth-oauthlib google-auth-httplib2 google-api-python-client
     ```
   - Ensure FFmpeg is installed (e.g., via `choco install ffmpeg` on Windows with Chocolatey).

3. **Configure YouTube API** (Optional):
   - Create a project in [Google Cloud Console](https://console.cloud.google.com/).
   - Enable the YouTube Data API.
   - Generate OAuth 2.0 credentials and save `client_secret.json` in the `config` folder.
   - Note: Upload is disabled due to daily limits; enable it in `publish.py` when ready.

4. **Run the Project**:
   ```bash
   python scripts/publish.py
   ```

## Learning Outcomes
This project is a goldmine for learning:
- **API Integration**: Fetching data from TMDb.
- **Machine Learning**: Using Ollama for text generation.
- **Video Processing**: Editing with MoviePy and Pillow.
- **Automation**: Building a full pipeline from data to video.
- **GitHub Presence**: A clean, documented repo to showcase your skills!

## Non-Commercial Disclaimer
This project is for educational purposes only and not intended for monetization. It’s a personal experiment to explore coding and multimedia, shared openly to inspire and educate. Feel free to fork, learn, and contribute!

## Future Improvements
- Add subtitles or transitions.
- Optimize audio synthesis speed.
- Enhance overlay customization (colors, fonts).

---

