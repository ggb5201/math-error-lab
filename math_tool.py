#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高中数学错题 & 套路整理工具

运行方式：
    python math_tool.py

访问地址：
    本机：    http://127.0.0.1:8000
    局域网：  http://<本机IP>:8000
    在线：    部署到 Railway 后会自动分配域名

依赖：仅 Python 3.10+ 标准库，无需 pip install 任何第三方包。

环境变量配置（Railway 部署时在 Dashboard 设置）：
    ARK_API_KEY          — 火山方舟 API Key（必填）
    ARK_MODEL_EP_ID      — 文本模型接入点 ID（默认 doubao-seed-2-1-pro-260628）
    ARK_VISION_MODEL_EP_ID — 视觉模型接入点 ID（可选，用于 OCR）
    PORT                 — 服务端口（Railway 自动注入）
"""

import base64
import http.server
import json
import os
import sys
import uuid
import threading
import urllib.request
import urllib.error
from datetime import datetime

# ============================================================
#  配置区 —— 通过环境变量配置，不再硬编码密钥
# ============================================================

# 火山方舟（豆包）API 配置
# 获取方式：https://console.volcengine.com/ark
API_KEY = os.environ.get("ARK_API_KEY", "")
MODEL_EP_ID = os.environ.get("ARK_MODEL_EP_ID", "doubao-seed-2-1-pro-260628")
# 视觉模型接入点 ID（用于图片 OCR 识别题干）
# 需在火山方舟控制台为支持视觉理解的模型（如 doubao-seed-1.6）创建接入点
# 若留空则复用 MODEL_EP_ID（前提是该模型支持视觉理解）
VISION_MODEL_EP_ID = os.environ.get("ARK_VISION_MODEL_EP_ID", "")
ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
AI_TIMEOUT = 120          # AI 请求超时（秒）
AI_MAX_TOKENS = 2048      # AI 最大输出 token 数（降低以加速响应）
AI_TEMPERATURE = 0.7      # AI 采样温度
OCR_MAX_IMAGE_SIZE = 5 * 1024 * 1024   # OCR 图片上限 5MB（压缩后应远小于此）
OCR_TIMEOUT = 45                         # OCR 请求超时（秒），独立于 AI 生成
OCR_MAX_TOKENS = 800                     # OCR 输出上限（识别文字不需要太多）

# 服务配置
PORT = int(os.environ.get("PORT", "8000"))
HOST = "0.0.0.0"          # 绑定 0.0.0.0 以支持外网访问

# 文件路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NOTES_FILE = os.path.join(BASE_DIR, "notes.json")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

# 线程安全锁（保护 notes.json 读写）
notes_lock = threading.Lock()

# ============================================================
#  AI Prompt 模板
# ============================================================

SYSTEM_PROMPT = """你是一位资深高中数学教师，拥有丰富的高考备考教学经验。
你的任务是帮助学生整理错题、提炼套路、提升数学成绩。

【统一输出规则】
1. 输出结构化纯文本，适合直接复制打印；
2. 语言极其简洁，每句话不超过25个字，禁止写长段落；
3. 解题步骤要分步清晰，每步只写一个操作，计算过程单独列出；
4. 严格遵循高考范围，拒绝超纲内容；
5. 错因仅限五选一：概念不清 / 计算失误 / 审题遗漏 / 套路不会 / 思维漏洞；
6. 数学符号使用 Unicode 符号，禁止使用 LaTeX 语法。符号对照表：
   - 平方：x²、x³、x⁴（用上标字符）
   - 根号：√x、√(a+b)（用 √ 符号）
   - 分数：a/b 或 (a+b)/(c+d)
   - 乘号：用 ·（中点）或直接省略
   - 除号：÷
   - 不等号：≤、≥、≠、＜、＞
   - 约等号：≈
   - 角度：∠ABC=60°
   - 平行：∥
   - 垂直：⊥
   - 三角函数：sin、cos、tan、cot
   - 向量：用粗体或加箭头说明，如向量a或→AB
   - 属于：∈、∉
   - 任意：∀
   - 存在：∃
   - 无穷：∞
   - 圆周率：π
   - 因为/所以：∵、∴
   - 度：°
   - 弧度：rad
7. 每个步骤之间用空行分隔，保持视觉清晰；
8. 计算过程只列关键步骤，不要把所有代数变形都写出来。"""

SINGLE_ERROR_PROMPT = """请根据以下错题信息，严格按照模板生成复习资料。

【错题信息】
题干：{question}
错误描述：{error_desc}
错因分类：{error_type}

【输出模板】（严格按此格式输出，不要省略任何部分）

━━━━━━━━━━━━━━━━━━━━━━━━
一、错题归档
━━━━━━━━━━━━━━━━━━━━━━━━
【原题】（简述题意，不超过2句）
【关键条件】（列出题目给出的核心条件，每条一行）
【所求】（明确写出要求什么）

━━━━━━━━━━━━━━━━━━━━━━━━
二、解题步骤
━━━━━━━━━━━━━━━━━━━━━━━━
（每步只写一个操作，不要混在一起；计算过程另起一行）

Step 1：（一句话写做什么）
  > 关键计算：（只列核心算式，不展开过程）

Step 2：（一句话写做什么）
  > 关键计算：（只列核心算式）

Step 3：（一句话写做什么）
  > 结论：（最终答案）

━━━━━━━━━━━━━━━━━━━━━━━━
三、易错坑点
━━━━━━━━━━━━━━━━━━━━━━━━
1. 坑点：（一句话描述）
   防范：（一句话写怎么避免）

2. 坑点：（一句话描述）
   防范：（一句话写怎么避免）

━━━━━━━━━━━━━━━━━━━━━━━━
四、同类变式题
━━━━━━━━━━━━━━━━━━━━━━━━
（仅更换参数生成1道变式题{answer_hint}）
变式题：（写出题目）
思路提示：（2-3句话点拨思路，不给完整解答）"""

BATCH_ERROR_PROMPT = """请根据以下多条错题（以 --- 分隔），生成批量复习手册。

【错题列表】
{questions}

【输出模板】（严格按此格式输出，不要省略任何部分）

━━━━━━━━━━━━━━━━━━━━━━━━
一、错题汇总
━━━━━━━━━━━━━━━━━━━━━━━━
（每条一行，格式：题号 - 题目核心 - 错因）
1. 
2. 
3. 

━━━━━━━━━━━━━━━━━━━━━━━━
二、共性薄弱点
━━━━━━━━━━━━━━━━━━━━━━━━
1. 知识点：（一句话）
   表现：（一句话）

2. 知识点：（一句话）
   表现：（一句话）

━━━━━━━━━━━━━━━━━━━━━━━━
三、改进建议
━━━━━━━━━━━━━━━━━━━━━━━━
1.（一句话写具体行动）
2.（一句话写具体行动）
3.（一句话写具体行动）"""

REVIEW_CARD_PROMPT = """请为以下高中数学模块生成「套路复习卡」。

【模块】{module}

【输出模板】（严格按此格式输出，不要省略任何部分）

━━━━━━━━━━━━━━━━━━━━━━━━
一、高频考点
━━━━━━━━━━━━━━━━━━━━━━━━
1.（考点名称：一句话说明考查方式）
2.（考点名称：一句话说明考查方式）
3.（考点名称：一句话说明考查方式）

━━━━━━━━━━━━━━━━━━━━━━━━
二、分题型套路
━━━━━━━━━━━━━━━━━━━━━━━━
【题型1】名称
  识别：（一句话写怎么判断）
  Step 1：（一句话）
  Step 2：（一句话）
  Step 3：（一句话）

【题型2】名称
  识别：（一句话写怎么判断）
  Step 1：（一句话）
  Step 2：（一句话）
  Step 3：（一句话）

━━━━━━━━━━━━━━━━━━━━━━━━
三、二级结论
━━━━━━━━━━━━━━━━━━━━━━━━
1. 结论：（一句话写结论）
   适用：（一句话写什么时候用）

2. 结论：（一句话写结论）
   适用：（一句话写什么时候用）

━━━━━━━━━━━━━━━━━━━━━━━━
四、高频陷阱
━━━━━━━━━━━━━━━━━━━━━━━━
1. 陷阱：（一句话描述）
   防范：（一句话写怎么避免）

2. 陷阱：（一句话描述）
   防范：（一句话写怎么避免）"""

# ============================================================
#  工具函数
# ============================================================

def load_notes():
    """从 notes.json 读取所有错题记录"""
    if not os.path.exists(NOTES_FILE):
        return []
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def save_notes_to_file(notes):
    """将错题列表写入 notes.json"""
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)


def call_ark_api(messages):
    """
    调用火山方舟（豆包）Chat Completions API
    返回 AI 生成的文本内容
    """
    if "在此填入" in API_KEY or "在此填入" in MODEL_EP_ID:
        raise ValueError(
            "请先在 math_tool.py 配置区填入 API_KEY 和 MODEL_EP_ID。\n"
            "获取地址：https://console.volcengine.com/ark"
        )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "model": MODEL_EP_ID,
        "messages": messages,
        "temperature": AI_TEMPERATURE,
        "max_tokens": AI_MAX_TOKENS,
        "thinking": {"type": "disabled"},  # 关闭深度思考，加速响应
    }).encode("utf-8")

    req = urllib.request.Request(ARK_API_URL, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        # 尝试提取错误信息
        try:
            err_json = json.loads(error_body)
            msg = err_json.get("error", {}).get("message", error_body)
        except Exception:
            msg = error_body
        raise RuntimeError(f"API 请求失败 ({e.code}): {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}")


def call_ark_vision_api(image_data_url, prompt_text):
    """
    调用火山方舟视觉理解模型，识别图片中的文字
    image_data_url: data:image/xxx;base64,... 格式的字符串
    prompt_text:    给模型的文字指令
    返回模型生成的文本
    """
    if "在此填入" in API_KEY:
        raise ValueError(
            "请先在 math_tool.py 配置区填入 API_KEY。\n"
            "获取地址：https://console.volcengine.com/ark"
        )

    # 视觉模型优先使用 VISION_MODEL_EP_ID，为空则复用 MODEL_EP_ID
    vision_model = VISION_MODEL_EP_ID if VISION_MODEL_EP_ID else MODEL_EP_ID
    if "在此填入" in vision_model:
        raise ValueError(
            "请先配置视觉模型接入点 ID。\n"
            "在 math_tool.py 中设置 VISION_MODEL_EP_ID，或确保 MODEL_EP_ID 指向支持视觉理解的模型。"
        )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "model": vision_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url},
                    },
                    {
                        "type": "text",
                        "text": prompt_text,
                    },
                ],
            }
        ],
        "temperature": 0.1,   # OCR 识别用低温度，提高准确率
        "max_tokens": OCR_MAX_TOKENS,  # 识别文字不需要太多输出
        "thinking": {"type": "disabled"},  # OCR 不需要深度思考，关闭以加速响应
    }).encode("utf-8")

    req = urllib.request.Request(ARK_API_URL, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=OCR_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(error_body)
            msg = err_json.get("error", {}).get("message", error_body)
        except Exception:
            msg = error_body
        raise RuntimeError(f"视觉模型请求失败 ({e.code}): {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason}")


OCR_PROMPT = """请识别图片中的数学题目文字，完整准确地转录为文本。

要求：
1. 只输出题目原文，不要添加任何解释、标注或格式化符号；
2. 数学符号使用 Unicode 符号：平方用 x²、x³，根号用 √x，分数用 a/b，角度用 ∠ABC=60°；
3. 保持原题的换行和段落结构；
4. 如果图片中有多道题，每道题之间用空行分隔；
5. 忽略图片中的页码、水印等非题目内容；
6. 如果图片模糊或无法识别，直接输出"识别失败"三个字；
7. 禁止使用 LaTeX 语法（不要出现 $、\\in、\\mathbf、\\frac 等符号）。"""


# ============================================================
#  HTTP 请求处理器
# ============================================================

class MathToolHandler(http.server.BaseHTTPRequestHandler):

    # ------ 基础工具方法 ------

    def log_message(self, format, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        sys.stderr.write(f"[{ts}] {args[0]} {args[1]} {args[2]}\n")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self._send_json({"error": "前端文件 index.html 不存在，请检查 templates/ 目录"}, 404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_static_image(self, path):
        """安全地返回 images/ 目录下的图片文件"""
        # 防止路径穿越
        safe_name = os.path.basename(path)
        image_dir = os.path.join(BASE_DIR, "images")
        filepath = os.path.join(image_dir, safe_name)
        if not os.path.isfile(filepath):
            self._send_json({"error": "图片不存在"}, 404)
            return
        ext = os.path.splitext(safe_name)[1].lower()
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "application/octet-stream")
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ------ GET 路由 ------

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send_html(os.path.join(TEMPLATE_DIR, "index.html"))
        elif self.path == "/api/notes":
            notes = load_notes()
            self._send_json({"notes": notes})
        elif self.path.startswith("/images/"):
            self._send_static_image(self.path)
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._send_json({"error": "未找到"}, 404)

    # ------ POST 路由 ------

    def do_POST(self):
        try:
            if self.path == "/api/ai":
                self._handle_ai()
            elif self.path == "/api/ocr":
                self._handle_ocr()
            elif self.path == "/api/notes":
                self._handle_save_note()
            else:
                self._send_json({"error": "未找到"}, 404)
        except json.JSONDecodeError:
            self._send_json({"error": "请求体 JSON 格式错误"}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ------ DELETE 路由 ------

    def do_DELETE(self):
        try:
            if self.path.startswith("/api/notes/"):
                note_id = self.path.split("/")[-1]
                self._handle_delete_note(note_id)
            else:
                self._send_json({"error": "未找到"}, 404)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ------ OCR 图片识别 ------

    def _handle_ocr(self):
        data = self._read_body()
        image_data_url = data.get("image", "")

        if not image_data_url or not image_data_url.startswith("data:image/"):
            self._send_json({"error": "请上传有效的图片文件"}, 400)
            return

        # 检查图片大小（base64 字符串长度约為实际大小的 4/3）
        estimated_size = len(image_data_url) * 3 // 4
        if estimated_size > OCR_MAX_IMAGE_SIZE:
            self._send_json({"error": f"图片过大（超过 {OCR_MAX_IMAGE_SIZE // 1024 // 1024}MB），请压缩后重试"}, 400)
            return

        text = call_ark_vision_api(image_data_url, OCR_PROMPT)
        self._send_json({"text": text.strip()})

    # ------ AI 生成 ------

    def _handle_ai(self):
        data = self._read_body()
        ai_type = data.get("type", "")

        if ai_type == "single":
            messages = self._build_single_prompt(data)
        elif ai_type == "batch":
            messages = self._build_batch_prompt(data)
        elif ai_type == "review":
            messages = self._build_review_prompt(data)
        else:
            self._send_json({"error": f"未知的 AI 请求类型: {ai_type}"}, 400)
            return

        content = call_ark_api(messages)
        title = self._gen_title(ai_type, data)

        self._send_json({"content": content, "title": title})

    def _build_single_prompt(self, data):
        question = data.get("question", "").strip()
        error_desc = data.get("error_desc", "").strip()
        error_type = data.get("error_type", "概念不清")
        include_answer = data.get("include_answer", False)

        if not question:
            raise ValueError("题干不能为空")

        answer_hint = "，并附带参考答案" if include_answer else "，不提供答案"
        user_prompt = SINGLE_ERROR_PROMPT.format(
            question=question,
            error_desc=error_desc or "（未填写）",
            error_type=error_type,
            answer_hint=answer_hint,
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _build_batch_prompt(self, data):
        questions = data.get("questions", "").strip()
        if not questions:
            raise ValueError("错题内容不能为空")

        user_prompt = BATCH_ERROR_PROMPT.format(questions=questions)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _build_review_prompt(self, data):
        module = data.get("module", "导数").strip()
        if not module:
            raise ValueError("模块名称不能为空")

        user_prompt = REVIEW_CARD_PROMPT.format(module=module)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _gen_title(self, ai_type, data):
        now = datetime.now().strftime("%Y-%m-%d")
        if ai_type == "single":
            q = data.get("question", "")[:20]
            return f"错题：{q}…（{now}）"
        elif ai_type == "batch":
            return f"批量错题复习手册（{now}）"
        elif ai_type == "review":
            return f"{data.get('module', '数学')} 套路复习卡（{now}）"
        return f"笔记（{now}）"

    # ------ 错题保存 ------

    def _handle_save_note(self):
        data = self._read_body()

        note = {
            "id": uuid.uuid4().hex[:8],
            "type": data.get("type", ""),
            "title": data.get("title", "未命名"),
            "content": data.get("content", ""),
            "raw_input": data.get("raw_input", ""),
            "module": data.get("module", ""),
            "error_type": data.get("error_type", ""),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        with notes_lock:
            notes = load_notes()
            notes.insert(0, note)
            save_notes_to_file(notes)

        self._send_json({"id": note["id"], "message": "保存成功"})

    # ------ 错题删除 ------

    def _handle_delete_note(self, note_id):
        with notes_lock:
            notes = load_notes()
            filtered = [n for n in notes if n["id"] != note_id]
            if len(filtered) == len(notes):
                self._send_json({"error": "未找到该条记录"}, 404)
                return
            save_notes_to_file(filtered)

        self._send_json({"message": "删除成功"})


# ============================================================
#  主函数
# ============================================================

def main():
    # 配置检查
    api_configured = "在此填入" not in API_KEY and "在此填入" not in MODEL_EP_ID

    print()
    print("=" * 56)
    print("  高中数学错题 & 套路整理工具  Python 本地 MVP")
    print("=" * 56)

    if not api_configured:
        print()
        print("  ⚠  尚未配置 API 密钥！AI 功能暂不可用。")
        print("  请打开 math_tool.py，在「配置区」填入：")
        print("    API_KEY     — 火山方舟 API 密钥")
        print("    MODEL_EP_ID — 模型接入点 ID")
        print("  获取地址：https://console.volcengine.com/ark")
    else:
        print()
        print("  ✓  API 密钥已配置")

    # 视觉模型配置检查
    vision_configured = bool(VISION_MODEL_EP_ID) or api_configured
    if vision_configured and api_configured:
        if VISION_MODEL_EP_ID:
            print("  ✓  视觉模型已配置（OCR 图片识别可用）")
        else:
            print("  ℹ  未单独配置 VISION_MODEL_EP_ID，OCR 将复用 MODEL_EP_ID")
            print("     （需确保该模型支持视觉理解，如 doubao-seed-1-6）")

    print()
    print(f"  本机访问：  http://127.0.0.1:{PORT}")
    print(f"  局域网访问：http://<本机IP>:{PORT}")
    print(f"  题库文件：  {NOTES_FILE}")
    print()
    print("  按 Ctrl+C 停止服务")
    print("=" * 56)
    print()

    server = http.server.ThreadingHTTPServer((HOST, PORT), MathToolHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
