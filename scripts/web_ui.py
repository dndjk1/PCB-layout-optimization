from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
import time
import zipfile
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.hpwl import total_hpwl
from src.legality import check_layout_legality
from src.pcb_data import Dataset, find_dataset_files, load_dataset_from_files
from src.visualization import visualize_dataset


UPLOAD_ROOT = ROOT / "results" / "ui_uploads"
IMAGE_ROOT = ROOT / "results" / "ui_images"


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PCB 布局可视化</title>
  <style>
    :root {
      color-scheme: light;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      background: #f4f6f8;
      color: #1d2733;
    }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(320px, 420px) 1fr;
    }
    aside {
      background: #ffffff;
      border-right: 1px solid #d9e1ea;
      padding: 28px;
    }
    main {
      padding: 28px;
      overflow: auto;
    }
    h1 {
      font-size: 24px;
      margin: 0 0 8px;
    }
    p {
      color: #5b6675;
      line-height: 1.7;
    }
    .drop {
      border: 2px dashed #8aa7c4;
      border-radius: 8px;
      background: #f8fbff;
      padding: 24px;
      text-align: center;
      cursor: pointer;
      transition: border-color .15s, background .15s;
    }
    .drop.dragover {
      border-color: #2f75b5;
      background: #eef6ff;
    }
    input[type=file] {
      display: none;
    }
    button {
      border: 0;
      border-radius: 6px;
      background: #235f9c;
      color: white;
      padding: 10px 14px;
      font-size: 14px;
      cursor: pointer;
    }
    button:disabled {
      background: #9aa9b8;
      cursor: progress;
    }
    .files {
      margin: 16px 0;
      padding: 0;
      list-style: none;
      font-size: 13px;
      color: #3f4b59;
    }
    .files li {
      padding: 6px 0;
      border-bottom: 1px solid #edf1f5;
      word-break: break-all;
    }
    .status {
      margin-top: 16px;
      padding: 12px;
      border-radius: 6px;
      background: #eef2f6;
      color: #344154;
      white-space: pre-wrap;
      font-size: 13px;
    }
    .result {
      display: grid;
      gap: 16px;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
    }
    .metric {
      background: white;
      border: 1px solid #dce4ed;
      border-radius: 8px;
      padding: 12px;
    }
    .metric span {
      display: block;
      color: #657285;
      font-size: 12px;
    }
    .metric strong {
      display: block;
      margin-top: 4px;
      font-size: 20px;
    }
    .preview {
      background: white;
      border: 1px solid #dce4ed;
      border-radius: 8px;
      padding: 12px;
    }
    .preview img {
      width: 100%;
      height: auto;
      display: block;
    }
    @media (max-width: 880px) {
      body {
        grid-template-columns: 1fr;
      }
      aside {
        border-right: 0;
        border-bottom: 1px solid #d9e1ea;
      }
    }
  </style>
</head>
<body>
  <aside>
    <h1>PCB 布局可视化</h1>
    <p>上传一个包含 <code>.nodes</code>、<code>.nets</code>、<code>.pl</code> 的 zip 文件，或一次选择这三个文件。生成的布局图会自动显示在右侧。</p>
    <label id="drop" class="drop" for="fileInput">
      <strong>选择文件或拖到这里</strong>
      <p>支持 .zip，或同时选择 .nodes/.nets/.pl</p>
    </label>
    <input id="fileInput" type="file" multiple accept=".zip,.nodes,.nets,.pl">
    <ul id="fileList" class="files"></ul>
    <button id="uploadBtn" disabled>生成布局图</button>
    <div id="status" class="status">等待上传数据。</div>
  </aside>
  <main>
    <div id="result" class="result"></div>
  </main>
  <script>
    const fileInput = document.querySelector("#fileInput");
    const drop = document.querySelector("#drop");
    const uploadBtn = document.querySelector("#uploadBtn");
    const fileList = document.querySelector("#fileList");
    const statusBox = document.querySelector("#status");
    const result = document.querySelector("#result");
    let selectedFiles = [];

    function setFiles(files) {
      selectedFiles = Array.from(files);
      fileList.innerHTML = "";
      selectedFiles.forEach(file => {
        const li = document.createElement("li");
        li.textContent = `${file.name} (${Math.round(file.size / 1024)} KB)`;
        fileList.appendChild(li);
      });
      uploadBtn.disabled = selectedFiles.length === 0;
      statusBox.textContent = selectedFiles.length ? "文件已选择，可以生成图片。" : "等待上传数据。";
    }

    fileInput.addEventListener("change", event => setFiles(event.target.files));
    drop.addEventListener("dragover", event => {
      event.preventDefault();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
    drop.addEventListener("drop", event => {
      event.preventDefault();
      drop.classList.remove("dragover");
      setFiles(event.dataTransfer.files);
    });

    uploadBtn.addEventListener("click", async () => {
      const form = new FormData();
      selectedFiles.forEach(file => form.append("files", file));
      uploadBtn.disabled = true;
      statusBox.textContent = "正在解析数据并生成图片...";
      result.innerHTML = "";
      try {
        const response = await fetch("/api/upload", { method: "POST", body: form });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "生成失败");
        statusBox.textContent = "生成完成。";
        renderResult(data);
      } catch (error) {
        statusBox.textContent = `错误：${error.message}`;
      } finally {
        uploadBtn.disabled = selectedFiles.length === 0;
      }
    });

    function renderResult(data) {
      result.innerHTML = `
        <section class="metrics">
          <div class="metric"><span>数据集</span><strong>${escapeHtml(data.dataset)}</strong></div>
          <div class="metric"><span>元件数</span><strong>${data.components}</strong></div>
          <div class="metric"><span>网络数</span><strong>${data.nets}</strong></div>
          <div class="metric"><span>引脚数</span><strong>${data.pins}</strong></div>
          <div class="metric"><span>初始 HPWL</span><strong>${data.hpwl}</strong></div>
          <div class="metric"><span>合法性</span><strong>${data.is_legal ? "合法" : "不合法"}</strong></div>
          <div class="metric"><span>间距违规</span><strong>${data.gap_violations}</strong></div>
          <div class="metric"><span>越界违规</span><strong>${data.boundary_violations}</strong></div>
          <div class="metric"><span>引用缺失</span><strong>${data.reference_violations}</strong></div>
        </section>
        <section class="preview">
          <img src="${data.image_url}" alt="${escapeHtml(data.dataset)} layout">
        </section>
      `;
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }
  </script>
</body>
</html>
"""


class PCBUIHandler(BaseHTTPRequestHandler):
    server_version = "PCBLayoutUI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
            return
        if parsed.path.startswith("/images/"):
            self._send_image(parsed.path.removeprefix("/images/"))
            return
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/upload":
            self._send_json({"error": "Not found"}, status=404)
            return

        try:
            files = self._read_upload_files()
            result = process_upload(files)
            self._send_json(result)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def log_message(self, format: str, *args) -> None:
        print("%s - %s" % (self.address_string(), format % args))

    def _read_upload_files(self) -> list[tuple[str, bytes]]:
        content_length = int(self.headers.get("Content-Length", "0"))
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("请上传 zip，或 .nodes/.nets/.pl 文件。")

        body = self.rfile.read(content_length)
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
        message = BytesParser(policy=default).parsebytes(header + body)

        files: list[tuple[str, bytes]] = []
        for part in message.iter_parts():
            filename = part.get_filename()
            if not filename:
                continue
            safe_name = Path(filename).name
            files.append((safe_name, part.get_payload(decode=True) or b""))

        if not files:
            raise ValueError("没有读取到上传文件。")
        return files

    def _send_html(self, content: str) -> None:
        payload = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_image(self, relative_name: str) -> None:
        requested = (IMAGE_ROOT / unquote(relative_name)).resolve()
        image_root = IMAGE_ROOT.resolve()
        if image_root not in requested.parents or requested.suffix.lower() != ".png" or not requested.exists():
            self._send_json({"error": "Image not found"}, status=404)
            return
        payload = requested.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def process_upload(files: list[tuple[str, bytes]]) -> dict:
    upload_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    upload_dir = UPLOAD_ROOT / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    IMAGE_ROOT.mkdir(parents=True, exist_ok=True)

    for filename, content in files:
        suffix = Path(filename).suffix.lower()
        if suffix == ".zip":
            zip_path = upload_dir / filename
            zip_path.write_bytes(content)
            _extract_zip(zip_path, upload_dir)
        elif suffix in {".nodes", ".nets", ".pl"}:
            (upload_dir / filename).write_bytes(content)
        else:
            raise ValueError(f"不支持的文件类型：{html.escape(filename)}")

    datasets = find_dataset_files(upload_dir)
    if not datasets:
        raise ValueError("没有找到完整数据集。请上传包含 .nodes、.nets、.pl 的 zip，或同时选择这三个文件。")

    dataset: Dataset = load_dataset_from_files(datasets[0])
    legality = check_layout_legality(dataset.components, dataset.placements, dataset.nets, min_gap=2.0)
    hpwl = total_hpwl(dataset.nets, dataset.components, dataset.placements)

    image_name = f"{upload_id}_{dataset.name}.png"
    image_path = IMAGE_ROOT / image_name
    visualize_dataset(dataset, image_path)

    return {
        "dataset": dataset.name,
        "components": len(dataset.components),
        "nets": len(dataset.nets),
        "pins": dataset.pin_count,
        "hpwl": hpwl,
        "is_legal": legality.is_legal,
        "gap_violations": len(legality.gap_violations),
        "boundary_violations": len(legality.boundary_violations),
        "reference_violations": len(legality.reference_violations),
        "image_url": f"/images/{image_name}",
    }


def _extract_zip(zip_path: Path, target_dir: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("zip 文件里包含不安全路径。")
            output_path = target_dir / member_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, output_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start local PCB layout visualization UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), PCBUIHandler)
    print(f"PCB layout UI running at http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
