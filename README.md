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
