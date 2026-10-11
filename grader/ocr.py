"""Read screenshot/PDF text locally with Apple Vision, or installed Tesseract."""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .config import DATA

LOCK = threading.Lock()


def native_binary():
    source = Path(__file__).with_name("ocr.swift")
    key = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    folder = DATA / "ocr"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = folder / ("vision-" + key)
    with LOCK:
        if not binary.exists():
            compiler = shutil.which("swiftc")
            if not compiler:
                raise ValueError("图片 OCR 需要 macOS 的 Swift 命令行工具")
            try:
                proc = subprocess.run([compiler, "-module-cache-path", str(folder / "module-cache"), str(source), "-o", str(binary)], capture_output=True, timeout=120)
            except subprocess.TimeoutExpired:
                raise ValueError("本机 OCR 编译超时，请人工复核图片") from None
            if proc.returncode:
                (folder / "compile-error.txt").write_bytes(proc.stderr)
                raise ValueError("本机 OCR 编译失败，请人工复核图片")
    return binary


def recognize(name, data):
    suffix = Path(name).suffix.lower() or ".png"
    with tempfile.TemporaryDirectory(prefix="mini-homework-ocr-") as folder:
        path = Path(folder) / ("input" + suffix)
        path.write_bytes(data)
        if sys.platform == "darwin":
            binary = native_binary()
            try:
                proc = subprocess.run([str(binary), str(path)], capture_output=True, timeout=90)
            except subprocess.TimeoutExpired:
                raise ValueError("图片/PDF OCR 超时，请人工查看原件") from None
            if proc.returncode:
                raise ValueError("图片/PDF OCR 失败，请人工查看原件")
            result = json.loads(proc.stdout)
            text = "\n\n".join(f"[OCR 第 {page['page']} 页]\n{page['text']}" for page in result["pages"] if page.get("text", "").strip())
        else:
            engine = shutil.which("tesseract")
            if not engine or suffix == ".pdf":
                raise ValueError("当前系统未配置该格式的 OCR，请人工查看图片或扫描 PDF")
            try:
                proc = subprocess.run([engine, str(path), "stdout", "-l", "chi_sim+eng"], capture_output=True, timeout=45)
            except subprocess.TimeoutExpired:
                raise ValueError("OCR 超时") from None
            if proc.returncode:
                raise ValueError("Tesseract OCR 失败，请检查中文/英文语言包")
            text = proc.stdout.decode("utf-8").strip()
        if not text.strip():
            raise ValueError("OCR 未识别到文字，请人工查看原件")
        return text[:60_000], ["含 OCR 识别文字，中文、符号和代码缩进可能有误，请对照原附件复核"]
