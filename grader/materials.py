"""Read student content as data. Never execute or extract uploaded programs."""
import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree
from .config import save_bytes
from .net import RemoteError

MAX_TEXT = 60_000
MAX_ARCHIVE = 4_000_000


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "br", "div", "li", "pre", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_html(source):
    parser = PlainHTML()
    parser.feed(source or "")
    return "".join(parser.parts).strip()


def decode(data):
    for codec in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            pass
    raise ValueError("文本编码无法识别")


def extract(name, data):
    suffix = PurePosixPath(name).suffix.lower()
    notes = []
    if not suffix:
        if data.startswith(b"%PDF"):
            suffix = ".pdf"
        elif data.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
            suffix = ".docx" if "word/document.xml" in names else ".zip"
        elif data.startswith(b"\x89PNG"):
            suffix = ".png"
        elif data.startswith(b"\xff\xd8"):
            suffix = ".jpg"
        else:
            candidate = decode(data)
            if sum(not c.isprintable() and c not in "\n\r\t" for c in candidate) > max(2, len(candidate) // 100):
                raise ValueError("无后缀附件不是可读取的文本")
            suffix = ".txt"
    if suffix in (".py", ".txt", ".md", ".csv", ".json"):
        text = decode(data)
        if suffix == ".py":
            try:
                ast.parse(text)
            except SyntaxError as e:
                notes.append(f"语法检查：第 {e.lineno} 行 {e.msg}（本机 Python 语法检查，未运行代码）")
    elif suffix in (".html", ".htm"):
        text = plain_html(decode(data))
    elif suffix == ".ipynb":
        notebook = json.loads(decode(data))
        text = "\n\n".join(f"[{cell.get('cell_type', '')} cell {i + 1}]\n" + ("".join(cell.get("source", [])) if isinstance(cell.get("source", []), list) else cell["source"]) for i, cell in enumerate(notebook.get("cells", [])) if cell.get("cell_type") in ("code", "markdown"))
    elif suffix == ".rar":
        from .rar import read_rar
        chunks = []
        for name, raw in read_rar(data, max_bytes=MAX_ARCHIVE):
            path = PurePosixPath(name)
            if path.name.startswith(".") or "__MACOSX" in path.parts:
                continue
            if path.suffix.lower() in (".zip", ".rar", ".pages"):
                notes.append("RAR 中的嵌套压缩文件无法读取，请人工查看")
                continue
            try:
                inner, warnings = extract(name, raw)
                chunks.append(f"[压缩包内附件 {len(chunks) + 1}，格式 {path.suffix}]\n{inner}")
                notes.extend(warnings)
            except Exception as e:
                notes.append("RAR 内附件读取失败：" + (str(e) if isinstance(e, ValueError) else type(e).__name__))
        text = "\n\n".join(chunks)
    elif suffix == ".doc":
        if data.startswith(b"PK"):
            return extract("converted.docx", data)
        if sys.platform != "darwin":
            raise ValueError("旧版 .doc 需要转换为 .docx 或人工查看")
        with tempfile.TemporaryDirectory(prefix="mini-homework-doc-") as folder:
            from pathlib import Path
            source = Path(folder) / "input.doc"
            source.write_bytes(data)
            try:
                converted = subprocess.run(["textutil", "-convert", "docx", "-stdout", str(source)], capture_output=True, timeout=30)
            except subprocess.TimeoutExpired:
                raise ValueError("旧版 Word 转换超时") from None
            if converted.returncode or not converted.stdout.startswith(b"PK"):
                raise ValueError("旧版 Word 转换失败，请人工查看")
            return extract("converted.docx", converted.stdout)
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".heic"):
        from .ocr import recognize
        return recognize("image" + suffix, data)
    elif suffix in (".zip", ".docx", ".pptx", ".pages"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = [f for f in archive.infolist() if not f.is_dir() and not f.filename.startswith("__MACOSX/")]
            if len(files) > 100 or sum(f.file_size for f in files) > MAX_ARCHIVE:
                raise ValueError("压缩文件超出解压大小或文件数限制")
            if suffix == ".docx":
                xml = archive.read("word/document.xml")
                root = ElementTree.fromstring(xml)
                text = "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
            elif suffix == ".pptx":
                slides = sorted(f.filename for f in files if re.fullmatch(r"ppt/slides/slide\d+\.xml", f.filename))
                text = "\n\n".join("\n".join(node.text or "" for node in ElementTree.fromstring(archive.read(slide)).iter() if node.tag.endswith("}t")) for slide in slides)
            elif suffix == ".pages":
                text = ""
                notes.append("Pages 仅尝试读取预览图，可能不是完整文档，请人工检查")
            else:
                chunks = []
                for item in files:
                    if PurePosixPath(item.filename).name.startswith("."):
                        continue
                    if PurePosixPath(item.filename).suffix.lower() in (".zip", ".docx"):
                        notes.append(f"未读取嵌套压缩文件：{item.filename}")
                        continue
                    try:
                        inner, warnings = extract(item.filename, archive.read(item))
                        chunks.append(f"--- {item.filename} ---\n{inner}")
                        notes.extend(warnings)
                    except Exception as e:
                        notes.append(f"未读取 {item.filename}：{type(e).__name__}")
                text = "\n\n".join(chunks)
            if suffix in (".docx", ".pptx", ".pages"):
                images = [f for f in files if ((f.filename.startswith("word/media/") or f.filename.startswith("ppt/media/")) or (suffix == ".pages" and "preview" in f.filename.lower())) and PurePosixPath(f.filename).suffix.lower() in (".png", ".jpg", ".jpeg")]
                for index, item in enumerate(images[:30]):
                    try:
                        image_text, warnings = extract("image" + PurePosixPath(item.filename).suffix, archive.read(item))
                        text += f"\n\n[附件中的截图 {index + 1}]\n{image_text}"
                        notes.extend(warnings)
                    except Exception:
                        notes.append(f"文档中的截图 {index + 1} 无法读取，请人工查看")
                if len(images) > 30:
                    notes.append("文档截图超过 30 张，剩余图片需人工查看")
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("PDF 需要安装：pip install '.[pdf]'；扫描版 PDF 需要人工复核") from None
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > 100:
            raise ValueError("PDF 页数超过 100 页")
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages)
        has_images = any(page.get("/Resources") and page.get("/Resources").get_object().get("/XObject") for page in reader.pages)
        if not text.strip() or has_images:
            try:
                from .ocr import recognize
                ocr_text, warnings = recognize("document.pdf", data)
                text += "\n\n[PDF 页面 OCR]\n" + ocr_text
                notes.extend(warnings)
            except ValueError as e:
                if not text.strip():
                    raise ValueError("PDF 无可提取文字，OCR 也未读到内容，请人工查看") from None
                notes.append("PDF 图片部分未读到：" + str(e))
        blank = [str(i + 1) for i, page in enumerate(pages) if not page.strip()]
        if blank:
            notes.append("PDF 以下页没有可提取文字，需人工查看是否为扫描内容：" + ", ".join(blank))
    else:
        raise ValueError(f"暂不支持 {suffix or '无后缀'} 文件")
    if len(text) > MAX_TEXT:
        notes.append("材料超过长度限制，已截断，需人工查看原文件")
    return text[:MAX_TEXT], notes


def submission_materials(canvas, submission, download_dir=None, attachment_records=None, *, reuse_records=None):
    chunks, notes = [], []
    if submission.get("body"):
        chunks.append(plain_html(submission["body"]))
    if submission.get("url"):
        notes.append("URL 提交未自动抓取，请人工查看其内容")
    for attachment in submission.get("attachments", []):
        try:
            saved = next((a for a in (reuse_records or []) if a["id"] == attachment["id"]), None)
            raw = None
            if saved and download_dir is not None and re.fullmatch(str(int(attachment["id"])) + r"\.[a-z0-9]{1,12}", saved.get("stored_name", "")):
                original = download_dir / saved["stored_name"]
                if original.is_file() and original.stat().st_size == saved.get("bytes") and original.stat().st_size <= 10_000_000:
                    raw = original.read_bytes()
                    if saved.get("sha256") and hashlib.sha256(raw).hexdigest() != saved["sha256"]:
                        raw = None
                    else:
                        name = saved["name"]
            if raw is None:
                context = {"submission_id": submission["id"]} if submission.get("id") is not None else {}
                name, raw = canvas.file(attachment["id"], **context)
            if download_dir is not None:
                suffix = PurePosixPath(name).suffix.lower()
                stored_name = str(int(attachment["id"])) + (suffix if re.fullmatch(r"\.[a-z0-9]{1,12}", suffix) else ".bin")
                save_bytes(download_dir / stored_name, raw)
                if attachment_records is not None:
                    attachment_records.append({"id": int(attachment["id"]), "name": name, "stored_name": stored_name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            text, warnings = extract(name, raw)
            # Do not send attachment filenames (often student names / IDs) to the model.
            chunks.append(f"[附件 {len(chunks) + 1}，格式 {PurePosixPath(name).suffix}]\n{text}")
            notes.extend(warnings)
        except RemoteError as e:
            # A transport/permission failure is not evidence that the student omitted work.
            raise type(e)("附件下载失败：" + str(e), status_code=e.status_code) from None
        except Exception as e:
            notes.append(f"附件 {attachment.get('id')} 读取失败：{e if isinstance(e, ValueError) else type(e).__name__}")
    text = "\n\n".join(chunks)
    if len(text) > MAX_TEXT:
        notes.append("提交总长度超过限制，已截断")
    if not text.strip():
        notes.append("未获得可批改的文本")
    return text[:MAX_TEXT], notes


class FileLinks(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.host = urlsplit(base).hostname
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for value in (attrs.get("href", ""), attrs.get("src", "")):
            parsed = urlsplit(value)
            if parsed.netloc and parsed.hostname != self.host:
                continue
            match = re.search(r"(?:^|/)files/(\d+)(?:/|$)", parsed.path)
            if match:
                self.ids.add(int(match.group(1)))


def assignment_materials(canvas, assignment):
    description = assignment.get("description") or ""
    text, notes = plain_html(description), []
    links = FileLinks(canvas.base)
    links.feed(description)
    for file_id in sorted(links.ids)[:10]:
        try:
            name, data = canvas.file(file_id)
            content, warnings = extract(name, data)
            text += f"\n\n[题目附件 {name}]\n{content}"
            notes.extend(warnings)
        except Exception as e:
            notes.append(f"题目附件 {file_id} 未读取：{e if isinstance(e, ValueError) else type(e).__name__}")
    if len(links.ids) > 10 or len(text) > MAX_TEXT:
        notes.append("题目材料超过限制，需人工确认完整性")
    return text[:MAX_TEXT], notes
