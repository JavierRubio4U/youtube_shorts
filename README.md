# YouTube Shorts Generator Project

Welcome to the **YouTube Shorts Generator Project**! This repository showcases an exciting learning journey into video automation, where I’ve built a Python-based tool to create and manage YouTube Shorts from movie data. This project is a personal exploration of programming, API integration, and multimedia processing—perfect for anyone looking to dive into creative coding. Best of all, it’s not intended for monetization; it’s purely for educational purposes and sharing knowledge with the community! Current work can be seen in https://www.youtube.com/@EstrenoscineEspaña-r5j

## Project Overview

This project automates the creation of YouTube Shorts by fetching movie data, generating narrated videos, and preparing them for upload (though uploads are limited by YouTube’s API quotas). It leverages APIs (like TMDb), machine learning (Ollama for narration), text-to-speech (ElevenLabs for voice synthesis), and video editing libraries (MoviePy) to produce polished 1080x1920 videos with dynamic content. Whether you're a beginner or an advanced coder, this repo offers a treasure trove of techniques to explore and learn from.

- **Goal**: A hands-on learning experience in Python, API usage, and video processing.
- **Non-Commercial**: This is not for profit—just a passion project to grow my skills and inspire others.
- **GitHub Appeal**: A beautifully organized repo with clear documentation, ready to impress your peers or future employers!

## How It Works

The project follows a structured pipeline with multiple scripts, each handling a specific task. Below is the order of execution and the purpose of each script:

### Script Execution Order

1. **select_next_release.py**
   - **Function**: Selects the next movie to process from a list of upcoming releases fetched from TMDb.
   - **Details**: Analyzes candidates based on title availability, backdrop count, and trailer presence, saving the selection to `next_release.json`.

2. **download_assets.py**
   - **Function**: Downloads movie posters and backdrops from TMDb, converting them to vertical (9:16) formats.
   - **Details**: Saves assets in the `assets` directory and updates `assets_manifest.json` with file paths.

3. **extract_video_clips_from_trailer.py**
   - **Function**: Downloads the movie trailer from YouTube and extracts short video clips using FFmpeg.
   - **Details**: Filters out static or low-motion clips and saves them in `assets/video_clips`.

4. **build_youtube_metadata.py**
   - **Function**: Generates metadata (title, description, tags) for the YouTube video based on movie data.
   - **Details**: Uses AI (Ollama) for translation if needed and saves to `youtube_metadata.json`.

5. **ai_narration.py**
   - **Function**: Generates a narrated synopsis using AI (Ollama for text generation) and synthesizes audio with ElevenLabs TTS.
   - **Details**: Produces a WAV file for the video’s audio track, adjusting for duration and adding pauses.

6. **build_short.py**
   - **Function**: Assembles the final video by combining the poster, extracted video clips (or backdrops as fallback), narration audio, and background music.
   - **Details**: Outputs a 1080x1920 MP4 file in `output/shorts`, with a 4-second intro and music mixed at low volume.

7. **upload_youtube.py**
   - **Function**: Uploads the generated video to YouTube (optional, quota-dependent).
   - **Details**: Requires `client_secret.json` for authentication; handles thumbnails (from poster, backdrop, or video frame) and metadata.

8. **publish.py**
   - **Function**: Orchestrates the entire pipeline, executing the above scripts in order.
   - **Details**: Serves as the main entry point; upload functionality is simulated or disabled due to daily limits.

### Workflow Diagram

```
[select_next_release] --> [download_assets] --> [extract_video_clips_from_trailer] --> [build_youtube_metadata] --> [ai_narration] --> [build_short] --> [upload_youtube]
         ↑ ↓
         └-------------------[publish.py]--------------------------------------┘
```

## Project Structure

- **`scripts/`**: Contains all Python scripts.
  - `publish.py`: Main script to run the pipeline.
  - `select_next_release.py`: Movie selection logic.
  - `download_assets.py`: Asset downloader.
  - `extract_video_clips_from_trailer.py`: Trailer clip extractor.
  - `build_youtube_metadata.py`: Metadata generator.
  - `ai_narration.py`: AI narration and audio synthesis.
  - `build_short.py`: Video assembler.
  - `upload_youtube.py`: YouTube uploader.
- **`assets/`**: Stores downloaded posters, backdrops, trailers, and video clips.
- **`output/`**: Contains generated files (JSON, MP4, WAV).
- **`config/`**: Holds `client_secret.json` for YouTube API and `elevenlabs_api_key.txt` for TTS (optional).
- **`temp/`**: Temporary directory for processing files.

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
   - Install required packages (see `requirements.txt` for full list):
     ```bash
     pip install -r requirements.txt
     ```
   - Ensure FFmpeg is installed (e.g., via `choco install ffmpeg` on Windows with Chocolatey).

3. **Configure APIs**:
   - **TMDb API**: Obtain a key from [The Movie Database](https://www.themoviedb.org/) and set it in environment variables or scripts as needed.
   - **ElevenLabs TTS**: Add your API key to `config/elevenlabs_api_key.txt`.
   - **YouTube API** (Optional): Create a project in [Google Cloud Console](https://console.cloud.google.com/). Enable the YouTube Data API. Generate OAuth 2.0 credentials and save `client_secret.json` in the `config` folder. Note: Upload is disabled due to daily limits; enable it in `publish.py` when ready.

4. **Run the Project**:
   ```bash
   python scripts/publish.py
   ```

## Learning Outcomes

This project is a goldmine for learning:
- **API Integration**: Fetching data from TMDb and YouTube.
- **Machine Learning**: Using Ollama for text generation.
- **Audio Synthesis**: Integrating ElevenLabs for realistic voice narration.
- **Video Processing**: Editing with MoviePy, FFmpeg, and Pillow.
- **Automation**: Building a full pipeline from data to video.
- **GitHub Presence**: A clean, documented repo to showcase your skills!

## Non-Commercial Disclaimer

This project is for educational purposes only and not intended for monetization. It’s a personal experiment to explore coding and multimedia, shared openly to inspire and educate. Feel free to fork, learn, and contribute!

## Future Improvements

- Add subtitles or dynamic transitions.
- Optimize audio synthesis speed and quality.
- Enhance error handling for API quotas.
- Integrate more advanced AI for synopsis refinement.

---
