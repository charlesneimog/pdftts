<h2 align="center"><code>pdftts</code> - Read PDFs with real IAs voices from Microsoft Edge</h2>
<p align="center">
   <img src="pdftts/pdftts.png" width="10%">
</p>

<p align="center">
    <a href="https://img.shields.io/pypi/pyversions/pd4web"><img src="https://img.shields.io/badge/platforms-macOS%20%7C%20Windows%20%7C%20Linux-green" alt="License"></a>
</p>

---

A Python application that converts PDF text to speech with seamless page streaming and natural phrase segmentation.

<img src="https://github.com/user-attachments/assets/885ef5c2-e611-4079-bcf9-5c450e7b3f59" width="80%" />


## Features

- 📄 **PDF Text Extraction**: Extracts clean text from PDF files using `pdfplumber`
- 🎧 **Natural Speech Synthesis**: Uses Microsoft Edge TTS for high-quality voice synthesis
- 🔄 **Seamless Streaming**: Preloads next page's audio while finishing current page
- 🌍 **Multilingual Support**: Detects language and selects appropriate voice automatically
- ⏯️ **Playback Control**: Play/pause, phrase navigation, and page navigation
- 📍 **Session Persistence**: Remembers last position in each PDF

## Installation


1. Install dependencies:
   ```bash
   pip install git+https://github.com/yourusername/pdftts.git 
   ```
   
## Usage

1. Launch the application:
   ```bash
   pdftts
   ```

2. Use the interface controls:
   - **🗂️ Open**: Select a PDF file
   - **▶️/⏸️ Play/Pause**: Toggle playback
   - **⏮️/⏭️**: Previous/Next page
   - **←/→**: Previous/Next phrase

### Keyboard Shortcuts

- `Space`: Play/Pause
- `Left/Right`: Previous/Next page
- `←/→`: Previous/Next phrase
- `Esc` or `Q`: Quit application

## Technical Details

### Architecture

- **Main Thread**: Handles UI and user input
- **Processing Thread**: Extracts text and generates audio files
- **Preload Thread**: Prepares upcoming phrases for smooth playback
- **Playback**: Uses Pygame mixer for audio playback

### Supported Languages

| Language | spaCy Model | TTS Voice |
|----------|------------|-----------|
| English | `en_core_web_sm` | `en-US-AvaMultilingualNeural` |
| Portuguese | `pt_core_news_sm` | `pt-BR-AntonioNeural` |
| Spanish | `es_core_news_sm` | `es-ES-AlvaroNeural` |
| French | `fr_core_news_sm` | `fr-FR-DeniseNeural` |

## Contributing

Contributions are welcome! Please note that all contributions will be licensed under GPLv3.

By contributing to this project, you agree to license your work under the GNU General Public License version 3.0.

## License

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or any later version.
