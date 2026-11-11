"""Run pipeline stages in order, logging their combined output to file and console."""

from datetime import datetime
import subprocess
import sys

from pyimport_pipeline_config import BASE_DIR


# Each entry is (display name, Python arguments). Order is execution order.
STAGES = (
    ("docling_converter.py", ("docling_converter.py",)),
    ("review conversion", ("review_stage.py", "conversion")),
    ("latex_docling_enricher.py", ("latex_docling_enricher.py",)),
    ("review latex", ("review_stage.py", "latex")),
    ("docling_hybrid_chunker.py", ("docling_hybrid_chunker.py",)),
    ("review chunks", ("review_stage.py", "chunks")),
)


def main():
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    log_path = log_dir / f"pipeline-run_{timestamp}.log"
    with log_path.open("x", encoding="utf-8") as log:
        def emit(message):
            print(message, end="", flush=True)
            log.write(message)
            log.flush()

        def event(message):
            emit(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}\n")

        event(f"Log file: {log_path}")
        event(f"Python: {sys.executable}")
        try:
            for index, (script, arguments) in enumerate(STAGES, start=1):
                event(f"[{index}/{len(STAGES)}] Starting {script}")
                command = [sys.executable, "-u", *arguments]
                with subprocess.Popen(
                    command, cwd=BASE_DIR, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", bufsize=1,
                ) as process:
                    try:
                        for line in process.stdout:
                            emit(line)
                        returncode = process.wait()
                    except BaseException:
                        # Do not leave an orphan stage after interruption.
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        raise
                if returncode:
                    raise subprocess.CalledProcessError(returncode, command)
                event(f"Finished {script}")
        except KeyboardInterrupt:
            event("Pipeline interrupted by user.")
            raise
        except Exception as error:
            event(f"Pipeline failed: {error}")
            raise
        event("Pipeline completed.")
    return log_path


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode if error.returncode > 0 else 1)
    except KeyboardInterrupt:
        sys.exit(130)
