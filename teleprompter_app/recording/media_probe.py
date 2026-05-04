import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

def write_ffprobe_json(path: Path) -> Path | None:
    """
    Run ffprobe on a file and save its stream/format details to a .json sidecar.
    """
    if not path.exists():
        return None

    output_json = path.with_suffix(path.suffix + ".ffprobe.json")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-print_format", "json",
        str(path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )

        if proc.returncode != 0:
            logger.warning("ffprobe failed for %s: %s", path, proc.stderr)
            return None

        data = json.loads(proc.stdout)
        output_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Saved sidecar metadata: %s", output_json.name)
        return output_json

    except Exception:
        logger.exception("Could not write ffprobe metadata for %s", path)
        return None
