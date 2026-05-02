# AI Teleprompter with Real-Time Speech Highlighting

Python desktop teleprompter built with PySide6, Vosk, sounddevice, and a modular parsing/alignment pipeline.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Download an offline Vosk model from https://alphacephei.com/vosk/models, unzip it, and either:

- set `VOSK_MODEL_PATH` before launching, or
- choose the model directory in the Settings panel.

## Run

```powershell
python -m teleprompter_app.main
```

The app supports `.txt`, `.md`, `.markdown`, `.html`, and `.htm` scripts. Every script is normalized to HTML, tokenized word by word, rendered in a scrollable teleprompter view, and aligned against streaming speech recognition.

## Lossless Recording

The Recording panel writes raw microphone PCM directly to disk with no filtering, normalization, AGC, noise suppression, or sample-rate conversion in the recording path.

- Choose a project folder when starting a recording.
- The app creates `audio/` and `subtitles/` folders automatically.
- WAV is raw PCM.
- FLAC is lossless compression of the same PCM stream.
- Subtitle `.srt` and transcript `.txt` files are generated from the existing speech recognition pipeline.

For bit-perfect capture, disable Windows/driver microphone enhancements manually when your device exposes them.
