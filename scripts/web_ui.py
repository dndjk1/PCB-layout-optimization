from __future__ import annotations

import argparse
from dataclasses import replace
import html
import json
import sys
import time
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parents[1]
    RESOURCE_ROOT = ROOT
sys.path.insert(0, str(ROOT))

from src.artifacts import format_number, safe_extract_zip, write_pl
from src.hpwl import total_hpwl
from src.legality import check_layout_legality, infer_board
from src.optimizer import available_algorithms, optimize
from src.pcb_data import Dataset, Placement, find_dataset_files, load_dataset, load_dataset_from_files
from src.geometry import canonical_orient


UPLOAD_ROOT = ROOT / "results" / "ui_uploads"
OUTPUT_ROOT = ROOT / "results" / "ui_outputs"
DATA_ROOT = RESOURCE_ROOT / "data"
UI_VERSION = "slot-flow-topology-v16"
BUILTIN_DATASETS = ("small-1", "small-2", "small-3", "small-4", "small-5")
ALGORITHM_EXPLANATIONS = (
    ("结构化模拟退火优化", "用温度衰减接受少量变差移动，适合跳出局部最优。"),
    ("结构化随机搜索优化", "在结构化初始布局上做随机扰动和合法性修复，速度快、结果稳。"),
    ("结构化解析式 Nesterov 优化", "用线长梯度、密度惩罚和自适应步长连续调整器件位置。"),
    ("结构化两阶段混合优化", "先排大芯片，再按引脚边缘分配小器件槽位，并做局部交换修正。"),
)


UPLOAD_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PCB 布局工具</title>
  <style>
    :root { font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; color: #172437; background: #f4f7fa; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 28px; }
    main { width: min(760px, 100%); background: #fff; border: 1px solid #dce5ee; border-radius: 10px; padding: 28px; }
    h1 { margin: 0 0 10px; font-size: 30px; }
    p { color: #5c6878; line-height: 1.7; }
    form { display: grid; gap: 16px; margin-top: 22px; }
    .drop { border: 2px dashed #8aa7c4; border-radius: 10px; background: #f8fbff; padding: 24px; }
    input[type=file], input[type=number] { box-sizing: border-box; width: 100%; border: 1px solid #cfd9e3; border-radius: 8px; padding: 10px; font: inherit; }
    button { justify-self: start; border: 0; border-radius: 8px; background: #235f9c; color: #fff; padding: 11px 16px; font: inherit; cursor: pointer; }
  </style>
</head>
<body>
  <main>
    <h1>PCB 布局工具</h1>
    <p>上传包含 <code>.nodes</code>、<code>.nets</code>、<code>.pl</code> 的 zip，或同时选择这三个文件。上传成功后会直接进入布局工作台。</p>
    <form action="/upload" method="post" enctype="multipart/form-data">
      <label class="drop">
        <strong>选择文件</strong>
        <input name="files" type="file" multiple accept=".zip,.nodes,.nets,.pl" required>
      </label>
      <button type="submit">上传并打开工作台</button>
    </form>
  </main>
</body>
</html>
"""


class PCBUIHandler(BaseHTTPRequestHandler):
    server_version = "SimplePCBUI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_html(render_upload_page())
            return
        if path == "/dataset":
            try:
                name = parse_qs(parsed.query).get("name", [""])[0]
                self._send_html(render_workspace(process_builtin_dataset(name)))
            except Exception as exc:
                self._send_html(render_error(str(exc)))
            return
        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/upload":
            try:
                result = process_upload(self._read_upload_files())
                self._send_html(render_workspace(result))
            except Exception as exc:
                self._send_html(render_error(str(exc)))
            return

        if path == "/api/optimize":
            try:
                payload = self._read_json_body()
                self._send_json(process_optimization(payload))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/save-layout":
            try:
                payload = self._read_json_body()
                self._send_json(process_save_layout(payload))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"error": "Not found"}, status=404)

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
            if filename:
                files.append((Path(filename).name, part.get_payload(decode=True) or b""))
        if not files:
            raise ValueError("没有读取到上传文件。")
        return files

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求内容错误。")
        return payload

    def _send_html(self, content: str) -> None:
        payload = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
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


def render_upload_page() -> str:
    return UPLOAD_PAGE


def process_upload(files: list[tuple[str, bytes]]) -> dict:
    upload_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    upload_dir = UPLOAD_ROOT / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in files:
        suffix = Path(filename).suffix.lower()
        if suffix == ".zip":
            zip_path = upload_dir / filename
            zip_path.write_bytes(content)
            safe_extract_zip(zip_path, upload_dir, unsafe_path_message="zip 中包含不安全路径：{name}")
        elif suffix in {".nodes", ".nets", ".pl"}:
            (upload_dir / filename).write_bytes(content)
        else:
            raise ValueError(f"不支持的文件类型：{html.escape(filename)}")

    datasets = find_dataset_files(upload_dir)
    if not datasets:
        raise ValueError("没有找到完整数据集。请上传包含 .nodes、.nets、.pl 的 zip，或同时选择这三个文件。")

    dataset = load_dataset_from_files(datasets[0])
    return dataset_result(upload_id, dataset)


def process_builtin_dataset(name: str) -> dict:
    dataset_name = clean_builtin_dataset_name(name)
    dataset = load_dataset(DATA_ROOT, dataset_name)
    return dataset_result(f"builtin-{dataset_name}", dataset)


def process_optimization(payload: dict) -> dict:
    upload_id = clean_upload_id(str(payload.get("upload_id", "")))
    algorithm = str(payload.get("algorithm", "two_stage"))
    max_iter = max(100, min(int(payload.get("max_iter", 3000)), 100_000))

    if algorithm not in available_algorithms():
        raise ValueError(f"不支持的优化算法：{html.escape(algorithm)}")

    dataset = load_uploaded_dataset(upload_id)
    placements_payload = payload.get("placements")
    if isinstance(placements_payload, dict):
        dataset = replace(dataset, placements=placements_from_payload(dataset, placements_payload))

    result = optimize(dataset, algorithm=algorithm, max_iter=max_iter, seed=0, history_interval=100)
    optimized = replace(dataset, placements=result.placements)

    output_dir = OUTPUT_ROOT / upload_id
    output_dir.mkdir(parents=True, exist_ok=True)
    pl_path = output_dir / f"{dataset.name}_{algorithm}.pl"
    write_pl(pl_path, result.placements)

    legality = result.optimized_legality
    return {
        "upload_id": upload_id,
        "dataset": dataset.name,
        "algorithm": algorithm,
        "hpwl": result.optimized_hpwl,
        "is_legal": legality.is_legal,
        "gap_violations": len(legality.gap_violations),
        "boundary_violations": len(legality.boundary_violations),
        "reference_violations": len(legality.reference_violations),
        "improvement_percent": result.improvement_ratio * 100.0,
        "optimized_pl": str(pl_path.relative_to(ROOT)),
        "layout": layout_payload(optimized, board=result.board),
    }


def process_save_layout(payload: dict) -> dict:
    upload_id = clean_upload_id(str(payload.get("upload_id", "")))
    dataset = load_uploaded_dataset(upload_id)
    placements_payload = payload.get("placements")
    if not isinstance(placements_payload, dict):
        raise ValueError("没有收到布局坐标。")

    placements = placements_from_payload(dataset, placements_payload)
    legality = check_layout_legality(dataset.components, placements, dataset.nets, min_gap=2.0)
    hpwl = total_hpwl(dataset.nets, dataset.components, placements)

    output_dir = OUTPUT_ROOT / upload_id
    output_dir.mkdir(parents=True, exist_ok=True)
    pl_path = output_dir / f"{dataset.name}_manual_{int(time.time())}.pl"
    write_pl(pl_path, placements)

    return {
        "hpwl": hpwl,
        "is_legal": legality.is_legal,
        "gap_violations": len(legality.gap_violations),
        "boundary_violations": len(legality.boundary_violations),
        "reference_violations": len(legality.reference_violations),
        "saved_pl": str(pl_path.relative_to(ROOT)),
    }


def dataset_result(upload_id: str, dataset: Dataset) -> dict:
    legality = check_layout_legality(dataset.components, dataset.placements, dataset.nets, min_gap=2.0)
    return {
        "upload_id": upload_id,
        "dataset": dataset.name,
        "components": len(dataset.components),
        "nets": len(dataset.nets),
        "pins": dataset.pin_count,
        "hpwl": total_hpwl(dataset.nets, dataset.components, dataset.placements),
        "is_legal": legality.is_legal,
        "gap_violations": len(legality.gap_violations),
        "boundary_violations": len(legality.boundary_violations),
        "reference_violations": len(legality.reference_violations),
        "layout": layout_payload(dataset),
        "algorithms": available_algorithms(),
    }


def render_workspace(result: dict) -> str:
    data = json.dumps(result, ensure_ascii=False).replace("</", "<\\/")
    algorithm_notes = "\n".join(
        f"<li><strong>{html.escape(name)}</strong><span>{html.escape(description)}</span></li>"
        for name, description in ALGORITHM_EXPLANATIONS
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PCB 布局工作台</title>
  <style>
    :root {{ font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; color: #172437; background: #f4f7fa; }}
    body {{ margin: 0; min-height: 100vh; display: grid; grid-template-columns: 320px 1fr; }}
    aside {{ background: #fff; border-right: 1px solid #d7e1eb; padding: 22px; overflow: auto; }}
    main {{ padding: 20px; overflow: hidden; display: grid; grid-template-rows: auto 1fr; gap: 12px; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    h2 {{ margin: 0 0 10px; font-size: 16px; }}
    p {{ color: #5c6878; line-height: 1.6; }}
    .metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin: 16px 0; }}
    .metric {{ border: 1px solid #dce5ee; border-radius: 8px; padding: 10px; background: #fff; }}
    .metric span {{ display: block; color: #667386; font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; font-size: 18px; word-break: break-word; }}
    .panel {{ border: 1px solid #dce5ee; border-radius: 8px; padding: 13px; margin: 12px 0; background: #fff; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .algorithm-actions {{ display: grid; grid-template-columns: 1fr; gap: 9px; }}
    .algorithm-actions button {{ width: 100%; text-align: left; padding: 10px 12px; }}
    button, a.button {{ border: 0; border-radius: 7px; background: #235f9c; color: #fff; padding: 8px 11px; font: inherit; cursor: pointer; text-decoration: none; }}
    button.secondary {{ background: #eef3f8; color: #24364a; border: 1px solid #cfd9e3; }}
    button:disabled {{ background: #9aa9b8; cursor: progress; }}
    input[type=number] {{ box-sizing: border-box; width: 100%; border: 1px solid #cfd9e3; border-radius: 7px; padding: 8px; font: inherit; }}
    label {{ display: grid; gap: 6px; margin: 12px 0; color: #3f4b59; }}
    #status {{ margin-top: 12px; padding: 11px; border-radius: 8px; background: #eef2f6; color: #24364a; }}
    #boardToolbar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 10px; background: #fff; border: 1px solid #d7e1eb; border-radius: 8px; }}
    #boardHost {{ height: calc(100vh - 104px); min-height: 520px; background: #f8fbff; border: 1px solid #d7e1eb; border-radius: 8px; overflow: hidden; touch-action: none; }}
    svg {{ width: 100%; height: 100%; display: block; user-select: none; }}
    .grid-dot {{ fill: #b7c7d8; opacity: .85; }}
    .net-line {{ stroke: #8aa8c4; stroke-width: 1; opacity: .65; vector-effect: non-scaling-stroke; }}
    .component {{ cursor: grab; }}
    .component rect {{ fill: #d9ecff; stroke: #236399; stroke-width: 1.4; vector-effect: non-scaling-stroke; }}
    .component.large rect {{ fill: #fff1c9; stroke: #8a6a2f; }}
    .component.selected rect {{ stroke: #e35d2f; stroke-width: 2.4; }}
    .component.invalid rect {{ fill: #ffdede; stroke: #c23232; stroke-width: 2.6; }}
    .component.locked rect {{ stroke-dasharray: 3 2; }}
    .component text {{ font-size: 4px; fill: #1c3045; text-anchor: middle; dominant-baseline: central; pointer-events: none; }}
    .pin {{ fill: #172437; stroke: #ffffff; stroke-width: .7; opacity: .95; pointer-events: none; vector-effect: non-scaling-stroke; }}
    .hint {{ font-size: 13px; }}
    .algorithm-list {{ margin: 0; padding-left: 18px; color: #3f4b59; line-height: 1.5; font-size: 13px; }}
    .algorithm-list li {{ margin: 0 0 8px; }}
    .algorithm-list strong {{ display: block; color: #24364a; }}
    .algorithm-list span {{ display: block; color: #5c6878; }}
    .selection-rect {{ fill: rgba(35, 95, 156, .12); stroke: #235f9c; stroke-width: 1.2; vector-effect: non-scaling-stroke; stroke-dasharray: 4 3; pointer-events: none; }}
  </style>
</head>
<body>
  <aside>
    <h1>PCB 布局工作台</h1>
    <div class="metrics">
      <div class="metric"><span>数据集</span><strong id="mDataset"></strong></div>
      <div class="metric"><span>HPWL</span><strong id="mHpwl"></strong></div>
      <div class="metric"><span>合法性</span><strong id="mLegal"></strong></div>
      <div class="metric"><span>间距违规</span><strong id="mGap"></strong></div>
    </div>
    <label>优化迭代次数<input id="maxIter" type="number" min="100" max="100000" step="100" value="3000"></label>
    <div class="panel">
      <h2>优化算法</h2>
      <div class="algorithm-actions" id="algorithmActions"></div>
    </div>
    <div class="panel">
      <h2>四种优化算法</h2>
      <ul class="algorithm-list">{algorithm_notes}</ul>
    </div>
    <p class="hint">左键框选或拖动选中元件；滚轮缩放；中键或右键拖动画布；Ctrl+R 右转；Ctrl+空格锁定/解锁。</p>
    <p class="hint">Ctrl+Z 撤回上一步布局操作。</p>
    <div id="status">布局已载入。</div>
    <p><a class="button" href="/">重新上传</a></p>
  </aside>
  <main>
    <div id="boardToolbar">
      <button class="secondary" type="button" id="zoomInBtn">放大</button>
      <button class="secondary" type="button" id="zoomOutBtn">缩小</button>
      <button class="secondary" type="button" id="resetViewBtn">重置视图</button>
      <button class="secondary" type="button" id="resetLayoutBtn">重置布局</button>
      <button class="secondary" type="button" id="saveLayoutBtn">保存布局</button>
    </div>
    <div id="boardHost"></div>
  </main>
  <script>
    const initialData = {data};
    let currentData = JSON.parse(JSON.stringify(initialData));
    let originalLayout = JSON.parse(JSON.stringify(initialData.layout));
    let layout = JSON.parse(JSON.stringify(initialData.layout));
    let viewBox = null;
    let dragState = null;
    let selectedNames = new Set();
    let invalidNames = new Set();
    let undoStack = [];
    const host = document.querySelector("#boardHost");
    const statusBox = document.querySelector("#status");
    const maxNets = 180;
    const maxPins = 1600;
    const minGap = 2.0;

    const labels = {{
      annealing: "结构化模拟退火优化",
      random: "结构化随机搜索优化",
      analytical: "结构化解析式 Nesterov 优化",
      two_stage: "结构化两阶段混合优化"
    }};
    initialData.algorithms.forEach(algorithm => {{
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = labels[algorithm] || algorithm;
      button.onclick = () => runAlgorithm(algorithm, button);
      document.querySelector("#algorithmActions").appendChild(button);
    }});

    document.querySelector("#zoomInBtn").onclick = () => zoom(0.82);
    document.querySelector("#zoomOutBtn").onclick = () => zoom(1.22);
    document.querySelector("#resetViewBtn").onclick = () => {{ viewBox = null; render(); }};
    document.querySelector("#resetLayoutBtn").onclick = () => {{
      pushUndo("重置布局前");
      layout = JSON.parse(JSON.stringify(originalLayout));
      currentData = JSON.parse(JSON.stringify(initialData));
      selectedNames.clear();
      viewBox = null;
      statusBox.textContent = "已重置到初始布局。";
      render();
    }};
    document.querySelector("#saveLayoutBtn").onclick = saveLayout;
    document.addEventListener("keydown", event => {{
      if (!event.ctrlKey && !event.metaKey) return;
      if (event.key.toLowerCase() === "z") {{
        event.preventDefault();
        undoLastAction();
      }} else if (event.key.toLowerCase() === "r") {{
        event.preventDefault();
        rotateSelectedClockwise();
      }} else if (event.code === "Space") {{
        event.preventDefault();
        toggleSelectedLocked();
      }}
    }});

    function formatNumber(value) {{
      const number = Number(value);
      return Number.isFinite(number) ? number.toFixed(3).replace(/\\.?0+$/, "") : String(value);
    }}

    function updateMetrics() {{
      document.querySelector("#mDataset").textContent = currentData.dataset;
      document.querySelector("#mHpwl").textContent = formatNumber(currentData.hpwl);
      document.querySelector("#mLegal").textContent = currentData.is_legal ? "合法" : "不合法";
      document.querySelector("#mGap").textContent = currentData.gap_violations;
    }}

    function hpwlChangeText(percent) {{
      const value = Number(percent);
      if (!Number.isFinite(value)) return "HPWL 变化率未知";
      if (value >= 0) return `HPWL 降低 ${{formatNumber(value)}}%`;
      return `HPWL 增加 ${{formatNumber(Math.abs(value))}}%`;
    }}

    function makeUndoSnapshot(label) {{
      return {{
        label,
        layout: JSON.parse(JSON.stringify(layout)),
        currentData: JSON.parse(JSON.stringify(currentData)),
        selectedNames: Array.from(selectedNames)
      }};
    }}

    function pushUndo(label) {{
      pushUndoSnapshot(makeUndoSnapshot(label));
    }}

    function pushUndoSnapshot(snapshot) {{
      undoStack.push(snapshot);
      if (undoStack.length > 50) undoStack.shift();
    }}

    function undoLastAction() {{
      const snapshot = undoStack.pop();
      if (!snapshot) {{
        statusBox.textContent = "没有可以撤回的操作。";
        return;
      }}
      layout = JSON.parse(JSON.stringify(snapshot.layout));
      currentData = JSON.parse(JSON.stringify(snapshot.currentData));
      selectedNames = new Set(snapshot.selectedNames || []);
      dragState = null;
      statusBox.textContent = `已撤回：${{snapshot.label}}。`;
      render();
    }}

    function defaultViewBox() {{
      const b = layout.board;
      const margin = Math.max(8, Math.max(b.right - b.left, b.top - b.bottom) * 0.06);
      return {{x: b.left - margin, y: b.bottom - margin, width: b.right - b.left + margin * 2, height: b.top - b.bottom + margin * 2}};
    }}

    function escapeHtml(value) {{
      return String(value).replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#039;"}}[ch]));
    }}

    function canonicalOrient(orient) {{
      const value = String(orient || "N").toUpperCase();
      if (["N", "E", "S", "W"].includes(value)) return value;
      for (const item of ["N", "E", "S", "W"]) {{
        if (value.endsWith(item)) return item;
      }}
      return "N";
    }}

    function componentSize(component, placement) {{
      const orient = canonicalOrient(placement.orient);
      if (orient === "E" || orient === "W") return {{width: component.height, height: component.width}};
      return {{width: component.width, height: component.height}};
    }}

    function rotatePinOffset(dx, dy, orient) {{
      const value = canonicalOrient(orient);
      if (value === "E") return {{dx: dy, dy: -dx}};
      if (value === "S") return {{dx: -dx, dy: -dy}};
      if (value === "W") return {{dx: -dy, dy: dx}};
      return {{dx, dy}};
    }}

    function rotateOrientClockwise(orient) {{
      const order = ["N", "E", "S", "W"];
      const current = canonicalOrient(orient);
      return order[(order.indexOf(current) + 1) % order.length];
    }}

    function rotateSelectedClockwise() {{
      const movable = Array.from(selectedNames).filter(name => {{
        const placement = layout.placements[name];
        return placement && !placement.fixed && layout.components[name];
      }});
      if (!movable.length) return;
      pushUndo("旋转器件前");
      let changed = 0;
      for (const name of movable) {{
        const component = layout.components[name];
        const placement = layout.placements[name];
        const oldSize = componentSize(component, placement);
        const center = {{x: placement.x + oldSize.width / 2, y: placement.y + oldSize.height / 2}};
        const newOrient = rotateOrientClockwise(placement.orient);
        const temp = Object.assign({{}}, placement, {{orient: newOrient}});
        const newSize = componentSize(component, temp);
        placement.orient = newOrient;
        placement.x = _clampClient(center.x - newSize.width / 2, layout.board.left, layout.board.right - newSize.width);
        placement.y = _clampClient(center.y - newSize.height / 2, layout.board.bottom, layout.board.top - newSize.height);
        changed += 1;
      }}
      if (changed) {{
        statusBox.textContent = `已右转 ${{changed}} 个器件。`;
        render();
      }}
    }}

    function toggleSelectedLocked() {{
      if (!selectedNames.size) return;
      pushUndo("切换锁定前");
      let changed = 0;
      for (const name of selectedNames) {{
        const placement = layout.placements[name];
        if (!placement) continue;
        placement.fixed = !placement.fixed;
        changed += 1;
      }}
      if (changed) {{
        statusBox.textContent = `已切换 ${{changed}} 个器件的锁定状态。锁定器件不会被拖动，也会作为固定器件参与优化。`;
        render();
      }}
    }}

    function pinCoordinate(pin) {{
      const component = layout.components[pin.component];
      const placement = layout.placements[pin.component];
      if (!component || !placement) return null;
      const size = componentSize(component, placement);
      const offset = rotatePinOffset(pin.dx, pin.dy, placement.orient);
      return {{
        x: placement.x + size.width / 2 + offset.dx,
        y: placement.y + size.height / 2 + offset.dy
      }};
    }}

    function componentRect(name) {{
      const component = layout.components[name];
      const placement = layout.placements[name];
      if (!component || !placement) return null;
      const size = componentSize(component, placement);
      return {{left: placement.x, bottom: placement.y, right: placement.x + size.width, top: placement.y + size.height}};
    }}

    function rectsHaveMinGap(a, b) {{
      const gapX = Math.max(a.left, b.left) - Math.min(a.right, b.right);
      const gapY = Math.max(a.bottom, b.bottom) - Math.min(a.top, b.top);
      return gapX >= minGap || gapY >= minGap;
    }}

    function calculateHpwl() {{
      let total = 0;
      for (const net of layout.nets) {{
        const coords = net.pins.map(pinCoordinate).filter(Boolean);
        if (!coords.length) continue;
        const xs = coords.map(point => point.x);
        const ys = coords.map(point => point.y);
        total += (Math.max(...xs) - Math.min(...xs)) + (Math.max(...ys) - Math.min(...ys));
      }}
      return total;
    }}

    function analyzeLayout() {{
      const invalid = new Set();
      let boundaryViolations = 0;
      let gapViolations = 0;
      const b = layout.board;
      const names = Object.keys(layout.components).filter(name => layout.placements[name]);
      const rects = {{}};
      for (const name of names) {{
        const rect = componentRect(name);
        rects[name] = rect;
        if (rect.left < b.left || rect.bottom < b.bottom || rect.right > b.right || rect.top > b.top) {{
          invalid.add(name);
          boundaryViolations += 1;
        }}
      }}
      for (let i = 0; i < names.length; i += 1) {{
        for (let j = i + 1; j < names.length; j += 1) {{
          if (!rectsHaveMinGap(rects[names[i]], rects[names[j]])) {{
            invalid.add(names[i]);
            invalid.add(names[j]);
            gapViolations += 1;
          }}
        }}
      }}
      return {{
        hpwl: calculateHpwl(),
        invalidNames: invalid,
        gapViolations,
        boundaryViolations,
        isLegal: invalid.size === 0
      }};
    }}

    function render() {{
      const analysis = analyzeLayout();
      invalidNames = analysis.invalidNames;
      currentData.hpwl = analysis.hpwl;
      currentData.is_legal = analysis.isLegal;
      currentData.gap_violations = analysis.gapViolations;
      currentData.boundary_violations = analysis.boundaryViolations;
      const box = viewBox || defaultViewBox();
      viewBox = box;
      const b = layout.board;
      let svg = `<svg id="boardSvg" viewBox="${{box.x}} ${{box.y}} ${{box.width}} ${{box.height}}">`;
      svg += renderGrid(box);
      svg += `<rect x="${{b.left}}" y="${{b.bottom}}" width="${{b.right - b.left}}" height="${{b.top - b.bottom}}" fill="none" stroke="#6d879f" stroke-width="1.4" vector-effect="non-scaling-stroke"></rect>`;
      for (const net of layout.nets.slice(0, maxNets)) {{
        const coords = net.pins.map(pinCoordinate).filter(Boolean);
        if (coords.length < 2) continue;
        const anchor = coords[0];
        for (const coord of coords.slice(1)) {{
          svg += `<line class="net-line" x1="${{anchor.x}}" y1="${{anchor.y}}" x2="${{coord.x}}" y2="${{coord.y}}"></line>`;
        }}
      }}
      for (const [name, component] of Object.entries(layout.components)) {{
        const placement = layout.placements[name];
        if (!placement) continue;
        const size = componentSize(component, placement);
        const large = component.width * component.height > 100;
        const selected = selectedNames.has(name);
        const invalid = invalidNames.has(name);
        const locked = Boolean(placement.fixed);
        const cls = `component${{large ? " large" : ""}}${{selected ? " selected" : ""}}${{invalid ? " invalid" : ""}}${{locked ? " locked" : ""}}`;
        svg += `<g class="${{cls}}" data-name="${{escapeHtml(name)}}">`;
        svg += `<rect x="${{placement.x}}" y="${{placement.y}}" width="${{size.width}}" height="${{size.height}}"></rect>`;
        svg += `<text x="${{placement.x + size.width / 2}}" y="${{placement.y + size.height / 2}}">${{escapeHtml(name)}}</text>`;
        svg += `</g>`;
      }}
      let pinCount = 0;
      for (const net of layout.nets) {{
        for (const pin of net.pins) {{
          if (pinCount >= maxPins) break;
          const coord = pinCoordinate(pin);
          if (!coord) continue;
          svg += `<circle class="pin" cx="${{coord.x}}" cy="${{coord.y}}" r="1.35"></circle>`;
          pinCount += 1;
        }}
        if (pinCount >= maxPins) break;
      }}
      if (dragState && dragState.type === "select") {{
        const left = Math.min(dragState.start.x, dragState.current.x);
        const top = Math.min(dragState.start.y, dragState.current.y);
        const width = Math.abs(dragState.current.x - dragState.start.x);
        const height = Math.abs(dragState.current.y - dragState.start.y);
        svg += `<rect class="selection-rect" x="${{left}}" y="${{top}}" width="${{width}}" height="${{height}}"></rect>`;
      }}
      svg += `</svg>`;
      host.innerHTML = svg;
      bindBoardEvents();
      updateMetrics();
    }}

    function renderGrid(box) {{
      const step = 20;
      const startX = Math.floor(box.x / step) * step;
      const endX = box.x + box.width;
      const startY = Math.floor(box.y / step) * step;
      const endY = box.y + box.height;
      let dots = "";
      for (let x = startX; x <= endX; x += step) {{
        for (let y = startY; y <= endY; y += step) {{
          dots += `<circle class="grid-dot" cx="${{x}}" cy="${{y}}" r="1.2"></circle>`;
        }}
      }}
      return dots;
    }}

    function svgPoint(event) {{
      const svg = document.querySelector("#boardSvg");
      const point = svg.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      return point.matrixTransform(svg.getScreenCTM().inverse());
    }}

    function namesInSelectionBox(a, b) {{
      const box = {{
        left: Math.min(a.x, b.x),
        right: Math.max(a.x, b.x),
        bottom: Math.min(a.y, b.y),
        top: Math.max(a.y, b.y)
      }};
      const names = [];
      for (const name of Object.keys(layout.components)) {{
        const rect = componentRect(name);
        if (!rect) continue;
        if (rect.right >= box.left && rect.left <= box.right && rect.top >= box.bottom && rect.bottom <= box.top) {{
          names.push(name);
        }}
      }}
      return names;
    }}

    function selectOnly(name) {{
      selectedNames = new Set(name ? [name] : []);
    }}

    function bindBoardEvents() {{
      const svg = document.querySelector("#boardSvg");
      svg.oncontextmenu = event => event.preventDefault();
      svg.onwheel = event => {{
        event.preventDefault();
        zoomAt(event.deltaY < 0 ? 0.85 : 1.18, svgPoint(event));
      }};
      svg.onmousedown = event => {{
        const component = event.target.closest(".component");
        const point = svgPoint(event);
        if (event.button === 0 && component) {{
          const name = component.dataset.name;
          if (event.shiftKey || event.ctrlKey || event.metaKey) {{
            if (selectedNames.has(name)) selectedNames.delete(name);
            else selectedNames.add(name);
          }} else if (!selectedNames.has(name)) {{
            selectOnly(name);
          }}
          const starts = {{}};
          for (const selected of selectedNames) {{
            const placement = layout.placements[selected];
            if (placement) starts[selected] = {{x: placement.x, y: placement.y}};
          }}
          dragState = {{type: "component", start: point, starts, before: makeUndoSnapshot("移动器件前"), changed: false}};
          render();
          return;
        }}
        if (event.button === 0) {{
          selectedNames.clear();
          dragState = {{type: "select", start: point, current: point}};
          render();
          return;
        }}
        if (event.button === 1 || event.button === 2) {{
          dragState = {{type: "pan", clientX: event.clientX, clientY: event.clientY}};
        }}
      }};
      svg.onmousemove = event => {{
        if (!dragState) return;
        const point = svgPoint(event);
        if (dragState.type === "component") {{
          const dx = point.x - dragState.start.x;
          const dy = point.y - dragState.start.y;
          let moved = false;
          for (const [name, start] of Object.entries(dragState.starts)) {{
            const placement = layout.placements[name];
            const component = layout.components[name];
            if (!placement || !component || placement.fixed) continue;
            const size = componentSize(component, placement);
            const nextX = _clampClient(start.x + dx, layout.board.left, layout.board.right - size.width);
            const nextY = _clampClient(start.y + dy, layout.board.bottom, layout.board.top - size.height);
            if (Math.abs(nextX - placement.x) > 1e-9 || Math.abs(nextY - placement.y) > 1e-9) moved = true;
            placement.x = nextX;
            placement.y = nextY;
          }}
          dragState.changed = dragState.changed || moved;
          statusBox.textContent = `已移动 ${{selectedNames.size}} 个器件，指标已实时更新。`;
          render();
        }} else if (dragState.type === "select") {{
          dragState.current = point;
          selectedNames = new Set(namesInSelectionBox(dragState.start, dragState.current));
          statusBox.textContent = `已选中 ${{selectedNames.size}} 个器件。`;
          render();
        }} else {{
          const svgWidth = Math.max(1, svg.clientWidth);
          const svgHeight = Math.max(1, svg.clientHeight);
          viewBox.x -= (event.clientX - dragState.clientX) * viewBox.width / svgWidth;
          viewBox.y -= (event.clientY - dragState.clientY) * viewBox.height / svgHeight;
          dragState.clientX = event.clientX;
          dragState.clientY = event.clientY;
          render();
        }}
      }};
      svg.onmouseup = () => {{
        if (dragState && dragState.type === "component" && dragState.changed) {{
          pushUndoSnapshot(dragState.before);
        }}
        if (dragState && dragState.type === "select") {{
          statusBox.textContent = `已选中 ${{selectedNames.size}} 个器件。`;
        }}
        dragState = null;
        render();
      }};
      svg.onmouseleave = () => {{
        if (!dragState) return;
        dragState = null;
        render();
      }};
    }}

    function _clampClient(value, low, high) {{
      if (high < low) return low;
      return Math.min(Math.max(value, low), high);
    }}

    function zoom(factor) {{
      const center = {{x: viewBox.x + viewBox.width / 2, y: viewBox.y + viewBox.height / 2}};
      zoomAt(factor, center);
    }}

    function zoomAt(factor, point) {{
      viewBox = {{
        x: point.x - (point.x - viewBox.x) * factor,
        y: point.y - (point.y - viewBox.y) * factor,
        width: viewBox.width * factor,
        height: viewBox.height * factor
      }};
      render();
    }}

    function placementsPayload() {{
      const payload = {{}};
      for (const [name, placement] of Object.entries(layout.placements)) {{
        payload[name] = {{x: placement.x, y: placement.y, orient: placement.orient, fixed: Boolean(placement.fixed)}};
      }}
      return payload;
    }}

    async function runAlgorithm(algorithm, button) {{
      button.disabled = true;
      statusBox.textContent = `正在运行 ${{button.textContent}}...`;
      try {{
        const response = await fetch("/api/optimize", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{
            upload_id: initialData.upload_id,
            algorithm,
            max_iter: Number(document.querySelector("#maxIter").value) || 3000,
            placements: placementsPayload()
          }})
        }});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "优化失败");
        pushUndo("优化前");
        const stableBoard = layout.board || originalLayout.board;
        layout = data.layout;
        layout.board = stableBoard;
        currentData = Object.assign(currentData, data);
        selectedNames.clear();
        statusBox.textContent = `${{button.textContent}} 完成，${{hpwlChangeText(data.improvement_percent)}}。`;
        render();
      }} catch (error) {{
        statusBox.textContent = `错误：${{error.message}}`;
      }} finally {{
        button.disabled = false;
      }}
    }}

    async function saveLayout() {{
      statusBox.textContent = "正在保存当前布局...";
      try {{
        const response = await fetch("/api/save-layout", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{upload_id: initialData.upload_id, placements: placementsPayload()}})
        }});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "保存失败");
        currentData = Object.assign(currentData, data);
        statusBox.textContent = `已保存：${{data.saved_pl}}`;
        render();
      }} catch (error) {{
        statusBox.textContent = `错误：${{error.message}}`;
      }}
    }}

    render();
  </script>
</body>
</html>"""


def render_error(message: str) -> str:
    safe = html.escape(message)
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>上传失败</title></head>
<body style="font-family: Segoe UI, Microsoft YaHei, Arial, sans-serif; margin: 32px;">
  <h1>上传失败</h1>
  <p>{safe}</p>
  <p><a href="/">返回重新上传</a></p>
</body>
</html>"""


def layout_payload(dataset: Dataset, board=None) -> dict:
    board = board or infer_board(dataset.components, dataset.placements)
    return {
        "board": {"left": board.left, "bottom": board.bottom, "right": board.right, "top": board.top},
        "components": {
            name: {"width": component.width, "height": component.height, "terminal": component.terminal}
            for name, component in dataset.components.items()
        },
        "placements": {
            name: {"x": placement.x, "y": placement.y, "orient": placement.orient, "fixed": placement.fixed}
            for name, placement in dataset.placements.items()
        },
        "nets": [
            {
                "name": net.name,
                "pins": [{"component": pin.component, "dx": pin.dx, "dy": pin.dy} for pin in net.pins],
            }
            for net in dataset.nets
        ],
    }


def placements_from_payload(dataset: Dataset, payload: dict) -> dict[str, Placement]:
    placements: dict[str, Placement] = {}
    for name, original in dataset.placements.items():
        item = payload.get(name, {})
        placements[name] = Placement(
            name=name,
            x=float(item.get("x", original.x)),
            y=float(item.get("y", original.y)),
            orient=canonical_orient(str(item.get("orient", original.orient))),
            fixed=bool(item.get("fixed", original.fixed)),
        )
    return placements


def load_uploaded_dataset(upload_id: str) -> Dataset:
    if upload_id.startswith("builtin-"):
        return load_dataset(DATA_ROOT, clean_builtin_dataset_name(upload_id.removeprefix("builtin-")))

    upload_dir = (UPLOAD_ROOT / upload_id).resolve()
    upload_root = UPLOAD_ROOT.resolve()
    if upload_root not in upload_dir.parents or not upload_dir.exists():
        raise ValueError("上传任务不存在或已失效。")
    datasets = find_dataset_files(upload_dir)
    if not datasets:
        raise ValueError("没有找到可用数据集。")
    return load_dataset_from_files(datasets[0])


def clean_upload_id(upload_id: str) -> str:
    if upload_id.startswith("builtin-"):
        dataset_name = clean_builtin_dataset_name(upload_id.removeprefix("builtin-"))
        return f"builtin-{dataset_name}"
    if not upload_id or any(char not in "0123456789-" for char in upload_id):
        raise ValueError("上传任务不存在或已失效。")
    return upload_id


def clean_builtin_dataset_name(name: str) -> str:
    dataset_name = (name or "").strip()
    if dataset_name not in BUILTIN_DATASETS:
        raise ValueError("内置样本不存在或未加入白名单。")
    return dataset_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the simplified PCB layout UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--open-browser", action="store_true", default=None, help="Open the UI in the default browser after startup.")
    parser.add_argument("--no-open-browser", action="store_false", dest="open_browser", help="Do not open a browser automatically.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    server, port = create_server(args.host, args.port)
    url = f"http://{args.host}:{port}/"
    print(f"PCB layout UI running at {url}")
    open_browser = args.open_browser if args.open_browser is not None else bool(getattr(sys, "frozen", False))
    if open_browser:
        webbrowser.open(url)
    server.serve_forever()
    return 0


def create_server(host: str, port: int) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    for candidate in range(port, port + 20):
        try:
            return ThreadingHTTPServer((host, candidate), PCBUIHandler), candidate
        except OSError as exc:
            last_error = exc
    raise last_error or OSError(f"Cannot bind to {host}:{port}")


if __name__ == "__main__":
    raise SystemExit(main())
