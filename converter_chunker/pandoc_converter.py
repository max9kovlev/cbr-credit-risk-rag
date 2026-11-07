
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

INPUT = BASE_DIR / "input/N845.odt"
OUTPUT = BASE_DIR / "output/pandoc"

if not INPUT.is_file():
    raise FileNotFoundError(f"File not found: {INPUT}")

OUTPUT.mkdir(parents=True, exist_ok=True)

files = [INPUT]

for source in files:
    output = OUTPUT / f"{source.stem}-pandoc.md"
    media = Path(f"{source.stem}_media")

    print(f"Converting: {source.name}")

    subprocess.run(
        [
            "pandoc",
            str(source),
            "-f", "odt",
            "-t", "markdown",
            "--wrap=none",
            f"--extract-media={media}",
            "-o", str(output),
        ],
        cwd=OUTPUT,
        check=True,
    )

    print(f"Created file: {output}")

print("Conversion completed!")
