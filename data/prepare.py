from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional, TextIO


ROOT_DIR = Path(__file__).resolve().parents[1]
GROBID_VERSION = "0.7.3"
DEFAULT_GROBID_DIR = Path.home() / f"grobid-{GROBID_VERSION}"
GROBID_ISALIVE_URL = "http://localhost:8070/api/isalive"
GROBID_DOWNLOAD_URL = (
    f"https://github.com/grobidOrg/grobid/archive/refs/tags/{GROBID_VERSION}.zip"
)
DEFAULT_GROBID_START_TIMEOUT_SECONDS = 300
REMOVE_KEYS = {
    "cite_spans",
    "ref_spans",
    "eq_spans",
    "authors",
    "bib_entries",
    "year",
    "venue",
    "identifiers",
    "_pdf_hash",
    "header",
}


def default_s2orc_dir() -> Path:
    candidates = [
        ROOT_DIR / "s2orc-doc2json",
        ROOT_DIR / "assets" / "Other-repo" / "s2orc-doc2json",
    ]
    process_script_relative = Path("doc2json") / "grobid2json" / "process_pdf.py"
    for candidate in candidates:
        if (candidate / process_script_relative).is_file():
            return candidate
    return candidates[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a paper PDF into PaperCoder-ready JSON files. "
            "The script writes both [paper].json and [paper]_cleaned.json."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("pdf_path", type=Path, help="Path to the input PDF file.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the generated JSON files. Defaults to the PDF directory.",
    )
    parser.add_argument(
        "--paper-name",
        type=str,
        default=None,
        help="Base name for the generated JSON files. Defaults to the PDF stem.",
    )
    parser.add_argument(
        "--s2orc-dir",
        type=Path,
        default=default_s2orc_dir(),
        help="Path to the s2orc-doc2json repository.",
    )
    parser.add_argument(
        "--temp-root",
        type=Path,
        default=None,
        help=(
            "Root directory for doc2json temporary files. "
            "Defaults to [s2orc-dir]/temp_dir."
        ),
    )
    parser.add_argument(
        "--python-bin",
        type=str,
        default=sys.executable,
        help="Python executable used to run s2orc-doc2json.",
    )
    parser.add_argument(
        "--grobid-url",
        type=str,
        default=GROBID_ISALIVE_URL,
        help="Grobid health-check URL.",
    )
    parser.add_argument(
        "--grobid-dir",
        type=Path,
        default=DEFAULT_GROBID_DIR,
        help="Local Grobid installation directory.",
    )
    parser.add_argument(
        "--grobid-start-timeout",
        type=int,
        default=DEFAULT_GROBID_START_TIMEOUT_SECONDS,
        help="Seconds to wait for Grobid to become healthy after auto-start.",
    )
    parser.add_argument(
        "--skip-grobid-check",
        action="store_true",
        help="Skip the Grobid health check before running the conversion.",
    )
    parser.add_argument(
        "--no-auto-start-grobid",
        action="store_true",
        help="Do not auto-download or start Grobid if it is not already running.",
    )
    parser.add_argument(
        "--keep-grobid-running",
        action="store_true",
        help="Leave Grobid running after this script finishes if this script started it.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary doc2json directory after the run finishes.",
    )
    return parser.parse_args()


def remove_spans(data: Any) -> Any:
    if isinstance(data, dict):
        for key in REMOVE_KEYS:
            data.pop(key, None)
        for key, value in data.items():
            data[key] = remove_spans(value)
    elif isinstance(data, list):
        return [remove_spans(item) for item in data]
    return data


def require_existing_pdf(pdf_path: Path) -> Path:
    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {pdf_path}")
    return pdf_path


def require_s2orc_repo(s2orc_dir: Path) -> Path:
    s2orc_dir = s2orc_dir.expanduser().resolve()
    process_script = s2orc_dir / "doc2json" / "grobid2json" / "process_pdf.py"
    if not process_script.is_file():
        raise FileNotFoundError(
            "Could not find s2orc-doc2json. Expected process_pdf.py at "
            f"{process_script}. Clone the repo first with:\n"
            "git clone https://github.com/allenai/s2orc-doc2json.git"
        )
    return s2orc_dir


def grobid_is_alive(grobid_url: str, timeout_seconds: int = 5) -> bool:
    try:
        with urllib.request.urlopen(grobid_url, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", response.getcode())
            return 200 <= status_code < 300
    except (urllib.error.URLError, TimeoutError):
        return False


def check_grobid(grobid_url: str, timeout_seconds: int = 5) -> None:
    if grobid_is_alive(grobid_url, timeout_seconds=timeout_seconds):
        return
    raise RuntimeError(
        "Grobid is not reachable at "
        f"{grobid_url}. Start it first, for example:\n"
        "cd ./s2orc-doc2json/grobid-0.7.3\n"
        "./gradlew run"
    )


def download_and_extract_grobid(grobid_dir: Path) -> None:
    if grobid_dir.is_dir():
        return

    grobid_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_path = grobid_dir.parent / f"{grobid_dir.name}.zip"
    urllib.request.urlretrieve(GROBID_DOWNLOAD_URL, archive_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(grobid_dir.parent)
    finally:
        archive_path.unlink(missing_ok=True)

    if not grobid_dir.is_dir():
        raise FileNotFoundError(
            f"Grobid archive was downloaded but {grobid_dir} was not created."
        )


def sync_grobid_config(s2orc_dir: Path, grobid_dir: Path) -> None:
    source_config = (
        s2orc_dir / "doc2json" / "grobid2json" / "grobid" / "grobid.yaml"
    )
    target_config = grobid_dir / "grobid-home" / "config" / "grobid.yaml"
    if source_config.is_file() and target_config.parent.is_dir():
        shutil.copyfile(source_config, target_config)


def sanitize_grobid_native_libraries(grobid_dir: Path) -> None:
    native_lib_dir = grobid_dir / "grobid-home" / "lib" / "lin-64"
    if not native_lib_dir.is_dir():
        return

    # These bundled glibc-era libraries break on modern Ubuntu; let Grobid use
    # the system runtime while keeping the CRF/Wapiti binaries in place.
    for library_name in (
        "ld-linux-x86-64.so.2",
        "libc.so.6",
        "libgcc_s.so.1",
        "libm.so.6",
        "libpthread.so.0",
        "libstdc++.so.6",
    ):
        library_path = native_lib_dir / library_name
        disabled_path = Path(f"{library_path}.disabled")
        if library_path.is_file() and not disabled_path.exists():
            library_path.rename(disabled_path)


def ensure_grobid_executables(grobid_dir: Path) -> None:
    executable_paths = [
        grobid_dir / "gradlew",
        grobid_dir / "grobid-home" / "pdfalto" / "lin-64" / "pdfalto",
        grobid_dir / "grobid-home" / "pdfalto" / "lin-64" / "pdfalto_server",
    ]
    for executable_path in executable_paths:
        if executable_path.is_file():
            executable_path.chmod(executable_path.stat().st_mode | 0o111)


def tail_text_file(file_path: Path, line_count: int = 20) -> str:
    if not file_path.is_file():
        return ""
    with file_path.open("r", encoding="utf-8", errors="replace") as infile:
        lines = infile.readlines()
    return "".join(lines[-line_count:])


def wait_for_grobid(
    grobid_url: str,
    timeout_seconds: int,
    process: Optional[subprocess.Popen] = None,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if grobid_is_alive(grobid_url):
            return
        if process is not None and process.poll() is not None:
            break
        time.sleep(2)
    raise RuntimeError(f"Grobid did not become ready at {grobid_url}.")


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=20)


def ensure_grobid_running(
    s2orc_dir: Path,
    grobid_dir: Path,
    grobid_url: str,
    timeout_seconds: int,
) -> tuple[Optional[subprocess.Popen], Optional[TextIO], Optional[Path]]:
    if grobid_is_alive(grobid_url):
        return None, None, None

    download_and_extract_grobid(grobid_dir)
    sync_grobid_config(s2orc_dir, grobid_dir)
    sanitize_grobid_native_libraries(grobid_dir)
    ensure_grobid_executables(grobid_dir)

    gradlew_path = grobid_dir / "gradlew"
    if not gradlew_path.is_file():
        raise FileNotFoundError(f"Could not find gradlew at {gradlew_path}")

    log_path = grobid_dir / "papercoder_grobid.log"
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(gradlew_path), "run"],
        cwd=grobid_dir,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    try:
        wait_for_grobid(
            grobid_url=grobid_url,
            timeout_seconds=timeout_seconds,
            process=process,
        )
    except Exception as exc:
        stop_process(process)
        log_handle.close()
        tail_text = tail_text_file(log_path)
        raise RuntimeError(
            "Failed to auto-start Grobid. "
            f"See {log_path} for details.\n{tail_text}"
        ) from exc

    return process, log_handle, log_path


def run_doc2json(
    pdf_path: Path,
    output_dir: Path,
    paper_name: str,
    s2orc_dir: Path,
    temp_dir: Path,
    python_bin: str,
) -> Path:
    process_script = s2orc_dir / "doc2json" / "grobid2json" / "process_pdf.py"
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{s2orc_dir}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(s2orc_dir)
    )

    command = [
        python_bin,
        str(process_script),
        "-i",
        str(pdf_path),
        "-t",
        str(temp_dir),
        "-o",
        str(output_dir),
    ]
    subprocess.run(command, cwd=s2orc_dir, env=env, check=True)

    generated_json_path = output_dir / f"{pdf_path.stem}.json"
    if not generated_json_path.is_file():
        raise FileNotFoundError(
            "s2orc-doc2json finished without producing the expected JSON file: "
            f"{generated_json_path}"
        )

    target_json_path = output_dir / f"{paper_name}.json"
    if generated_json_path != target_json_path:
        if target_json_path.exists():
            target_json_path.unlink()
        shutil.move(str(generated_json_path), str(target_json_path))

    return target_json_path


def write_clean_json(raw_json_path: Path, clean_json_path: Path) -> None:
    with raw_json_path.open("r", encoding="utf-8") as infile:
        data = json.load(infile)

    cleaned_data = remove_spans(data)

    with clean_json_path.open("w", encoding="utf-8") as outfile:
        json.dump(cleaned_data, outfile)


def main() -> None:
    args = parse_args()

    pdf_path = require_existing_pdf(args.pdf_path)
    s2orc_dir = require_s2orc_repo(args.s2orc_dir)

    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else pdf_path.parent
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    paper_name = args.paper_name or pdf_path.stem
    raw_json_path = output_dir / f"{paper_name}.json"
    clean_json_path = output_dir / f"{paper_name}_cleaned.json"

    temp_root = (
        args.temp_root.expanduser().resolve()
        if args.temp_root is not None
        else s2orc_dir / "temp_dir"
    )
    temp_root.mkdir(parents=True, exist_ok=True)

    temp_dir_path = Path(
        tempfile.mkdtemp(prefix=f"{paper_name}_", dir=str(temp_root))
    ).resolve()

    grobid_process: Optional[subprocess.Popen] = None
    grobid_log_handle: Optional[TextIO] = None

    try:
        if not args.skip_grobid_check:
            if args.no_auto_start_grobid:
                check_grobid(args.grobid_url)
            else:
                (
                    grobid_process,
                    grobid_log_handle,
                    _,
                ) = ensure_grobid_running(
                    s2orc_dir=s2orc_dir,
                    grobid_dir=args.grobid_dir.expanduser().resolve(),
                    grobid_url=args.grobid_url,
                    timeout_seconds=args.grobid_start_timeout,
                )

        try:
            raw_json_path = run_doc2json(
                pdf_path=pdf_path,
                output_dir=output_dir,
                paper_name=paper_name,
                s2orc_dir=s2orc_dir,
                temp_dir=temp_dir_path,
                python_bin=args.python_bin,
            )
            write_clean_json(raw_json_path, clean_json_path)
        finally:
            if not args.keep_temp:
                shutil.rmtree(temp_dir_path, ignore_errors=True)
    finally:
        if grobid_log_handle is not None:
            grobid_log_handle.close()
        if grobid_process is not None and not args.keep_grobid_running:
            stop_process(grobid_process)

    print(f"[SAVED] {raw_json_path}")
    print(f"[SAVED] {clean_json_path}")


if __name__ == "__main__":
    main()
