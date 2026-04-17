"""
app.py  –  Flask server powering the Jarvis AI Agent.

Endpoints:
  POST /ask         – Process a user message, return response + optional command
  POST /execute     – Force-execute a command that was flagged dangerous (after user confirms)
  POST /reset       – Clear conversation history
  GET  /health      – Health check
  GET  /system-info – Real-time CPU / RAM / battery / disk metrics
"""

import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from agent import ask_jarvis, interpret_command_output, reset_history
from memory_store import clear_all_memories, get_memory_stats, list_memories, set_memory_pinned
from offline_router import try_offline_route
from system_control import execute_command, get_system_info, is_dangerous

# Point static_folder to the frontend directory
backend_dir = os.path.dirname(os.path.abspath(__file__))
frontend_dir = os.path.abspath(os.path.join(backend_dir, '..', 'frontend'))
app = Flask(__name__, static_folder=frontend_dir, static_url_path='')
CORS(app)  # allow the frontend to call us

@app.route("/", methods=["GET"])
def index():
    """Serve the main frontend HTML file."""
    return send_from_directory(frontend_dir, 'index.html')

@app.route("/<path:filename>")
def serve_static(filename):
    """Serve other static files like CSS and JS."""
    return send_from_directory(frontend_dir, filename)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "online", "agent": "CIPHER"})


@app.route("/ask", methods=["POST"])
def ask():
    """
    Main endpoint. Receives user text, queries the LLM (or offline router),
    optionally executes a system command, and returns everything to the frontend.
    """
    import time as _time

    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    _t0 = _time.perf_counter()

    # 1. Try the offline router first (instant, no LLM needed)
    offline_result = try_offline_route(user_message)

    if offline_result:
        # Offline-routed: fast path
        result = offline_result
        provider_used = "offline"
        latency_ms = round((_time.perf_counter() - _t0) * 1000)
    else:
        # 2. Fall through to the LLM
        result = ask_jarvis(user_message)
        provider_used = result.get("provider", "unknown")
        latency_ms = result.get("latency_ms", round((_time.perf_counter() - _t0) * 1000))

    response_payload = {
        "thought": result.get("thought", ""),
        "response": result.get("response", ""),
        "command": result.get("command"),
        "provider": provider_used,
        "latency_ms": latency_ms,
        "memory_count": result.get("memory_count", 0),
        "learned_memories": result.get("learned_memories", 0),
        "command_output": None,
        "command_success": None,
        "requires_confirmation": False,
    }

    # 3. If a command was generated, decide whether to execute it
    cmd = result.get("command")
    if cmd:
        if is_dangerous(cmd):
            # Don't execute — ask the user to confirm via the UI
            response_payload["requires_confirmation"] = True
        else:
            # Safe command — execute immediately
            success, output = execute_command(cmd)
            response_payload["command_output"] = output
            response_payload["command_success"] = success

            # 4. Feed command output back to LLM for natural interpretation
            #    (only for LLM-routed commands with meaningful output)
            if provider_used != "offline" and output:
                interpretation = interpret_command_output(cmd, output, success)
                if interpretation:
                    response_payload["response"] = interpretation

    return jsonify(response_payload)


@app.route("/execute", methods=["POST"])
def force_execute():
    """
    Execute a command that was previously flagged as dangerous,
    after the user has confirmed in the UI.
    """
    data = request.get_json(force=True)
    cmd = data.get("command", "").strip()

    if not cmd:
        return jsonify({"error": "No command provided"}), 400

    success, output = execute_command(cmd)
    return jsonify({"command_output": output, "command_success": success})


@app.route("/reset", methods=["POST"])
def reset():
    """Clear short-term conversation history (does not delete long-term memory)."""
    reset_history()
    return jsonify({"status": "Short-term chat memory cleared, Uzair."})


@app.route("/system-info", methods=["GET"])
def system_info():
    """Return real-time CPU / RAM / battery / disk metrics."""
    try:
        info = get_system_info()
        return jsonify(info)
    except Exception as exc:
        return jsonify({"error": f"System info unavailable: {exc}"}), 503


@app.route("/memory", methods=["GET"])
def memory_stats():
    """Get long-term memory storage stats."""
    try:
        return jsonify(get_memory_stats())
    except Exception as exc:
        return jsonify({"error": f"Memory backend unavailable: {exc}"}), 503


@app.route("/memory/clear", methods=["POST"])
def memory_clear():
    """Clear all long-term memories from persistent storage."""
    try:
        clear_all_memories()
        return jsonify({"status": "Long-term memory cleared."})
    except Exception as exc:
        return jsonify({"error": f"Memory backend unavailable: {exc}"}), 503


@app.route("/memory/list", methods=["GET"])
def memory_list():
    """List stored long-term memories for inspection/management."""
    limit = request.args.get("limit", default=100, type=int)
    try:
        return jsonify({"memories": list_memories(limit=max(1, min(limit, 500)))})
    except Exception as exc:
        return jsonify({"error": f"Memory backend unavailable: {exc}", "memories": []}), 503


@app.route("/memory/pin", methods=["POST"])
def memory_pin():
    """Pin or unpin a memory so it won't/will decay out over time."""
    data = request.get_json(force=True)
    memory_id = (data.get("id") or "").strip()
    pinned = bool(data.get("pinned", True))

    if not memory_id:
        return jsonify({"error": "Missing memory id"}), 400

    try:
        ok = set_memory_pinned(memory_id=memory_id, pinned=pinned)
    except Exception as exc:
        return jsonify({"error": f"Memory backend unavailable: {exc}"}), 503
    if not ok:
        return jsonify({"error": "Memory id not found"}), 404

    return jsonify({"status": "updated", "id": memory_id, "pinned": pinned})


if __name__ == "__main__":
    print("\nCIPHER Backend Online - http://localhost:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
