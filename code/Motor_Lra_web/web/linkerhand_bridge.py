"""Bridge LinkerHand ROS 2 tactile topics to the local web dashboard."""

from __future__ import annotations

import argparse
import copy
import json
import math
import mimetypes
import os
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import Float32MultiArray, String


FINGERS = ("thumb", "index", "middle", "ring", "little")


def matrix_peak_newtons(points: float) -> float:
    """Provisional force estimate: 10 raw points = 0.5 N, 140 = 15 N."""
    raw = max(0.0, finite_number(points, 0.0))
    return min(15.0, raw / 20.0 + 8.0 * raw * (raw - 10.0) / 18200.0)


def finite_number(value: Any, default: float = -1.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def force_channel(values: list[Any], offset: int) -> list[float]:
    result = [finite_number(value) for value in values[offset : offset + 5]]
    return result + [-1.0] * (5 - len(result))


def clean_matrix(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    if value and not isinstance(value[0], list):
        value = [value]
    rows: list[list[float]] = []
    for row in value[:12]:
        if isinstance(row, list):
            rows.append([finite_number(cell) for cell in row[:12]])
    return rows


class TactileState:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._last_message = 0.0
        self._state: dict[str, Any] = {
            "sequence": 0,
            "connected": False,
            "updated_at": None,
            "force": {
                "normal": [-1.0] * 5,
                "tangential": [-1.0] * 5,
                "direction": [-1.0] * 5,
                "approach": [-1.0] * 5,
            },
            "matrices": {finger: [] for finger in FINGERS},
            "mass": {finger: -1.0 for finger in FINGERS},
            "matrix_stats": {
                finger: {"sum": -1.0, "max": -1.0, "peak_n": -1.0, "contacts": 0, "valid": False}
                for finger in FINGERS
            },
            "hand": {},
            "joints": {"position": [-1.0] * 25, "name": [], "updated_at": None},
            "motion": {"active": False, "status": "idle"},
        }

    def _publish(self) -> None:
        self._sequence += 1
        self._last_message = time.monotonic()
        self._state["sequence"] = self._sequence
        self._state["updated_at"] = time.time()
        self._condition.notify_all()

    def update_force(self, values: list[Any]) -> None:
        with self._condition:
            self._state["force"] = {
                "normal": force_channel(values, 0),
                "tangential": force_channel(values, 5),
                "direction": force_channel(values, 10),
                "approach": force_channel(values, 15),
            }
            self._publish()

    def update_matrices(self, payload: dict[str, Any]) -> None:
        with self._condition:
            for finger in FINGERS:
                matrix = clean_matrix(payload.get(f"{finger}_matrix"))
                self._state["matrices"][finger] = matrix
                valid = [cell for row in matrix for cell in row if cell >= 0]
                self._state["matrix_stats"][finger] = {
                    "sum": round(sum(valid), 3) if valid else -1.0,
                    "max": max(valid) if valid else -1.0,
                    "peak_n": round(matrix_peak_newtons(max(valid)), 3) if valid else -1.0,
                    "contacts": sum(cell > 0 for cell in valid),
                    "valid": bool(valid),
                }
            self._publish()

    def update_mass(self, payload: dict[str, Any]) -> None:
        with self._condition:
            for finger in FINGERS:
                value = payload.get(f"{finger}_mass", -1)
                self._state["mass"][finger] = finite_number(value)
            self._publish()

    def update_hand(self, payload: dict[str, Any]) -> None:
        with self._condition:
            self._state["hand"] = payload
            self._publish()

    def update_joints(self, message: JointState) -> None:
        with self._condition:
            position = [finite_number(value) for value in list(message.position)[:25]]
            position.extend([-1.0] * (25 - len(position)))
            self._state["joints"] = {
                "position": position,
                "name": list(message.name)[:25],
                "updated_at": time.time(),
            }
            self._publish()

    def set_motion(self, active: bool, status: str) -> None:
        with self._condition:
            self._state["motion"] = {"active": active, "status": status}
            self._publish()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            result = copy.deepcopy(self._state)
            result["connected"] = time.monotonic() - self._last_message < 2.0
            return result

    def wait_for_change(self, sequence: int, timeout: float = 15.0) -> dict[str, Any]:
        with self._condition:
            self._condition.wait_for(lambda: self._sequence != sequence, timeout)
            return self.snapshot()


class LinkerHandWebNode(Node):
    def __init__(self, state: TactileState, hand: str) -> None:
        super().__init__("linkerhand_web_bridge")
        self.state = state
        prefix = f"/cb_{hand}_hand"
        self.create_subscription(Float32MultiArray, f"{prefix}_force", self._force, 10)
        self.create_subscription(String, f"{prefix}_matrix_touch", self._matrix, 10)
        self.create_subscription(String, f"{prefix}_matrix_touch_mass", self._mass, 10)
        self.create_subscription(String, f"{prefix}_info", self._hand_info, 10)
        self.create_subscription(JointState, f"{prefix}_state", self._joint_state, 10)
        self.joint_publisher = self.create_publisher(JointState, f"{prefix}_control_cmd", 10)
        self.setting_publisher = self.create_publisher(String, "/cb_hand_setting_cmd", 10)
        self.hand_type = hand
        self._motion_lock = threading.Lock()
        self._motion_generation = 0
        self.get_logger().info(f"Web bridge subscribed to {prefix}_*")

    def _force(self, message: Float32MultiArray) -> None:
        self.state.update_force(list(message.data))

    def _json(self, text: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else None
        except (TypeError, json.JSONDecodeError) as exc:
            self.get_logger().warning(f"Ignoring invalid tactile JSON: {exc}")
            return None

    def _matrix(self, message: String) -> None:
        if payload := self._json(message.data):
            self.state.update_matrices(payload)

    def _mass(self, message: String) -> None:
        if payload := self._json(message.data):
            self.state.update_mass(payload)

    def _hand_info(self, message: String) -> None:
        if payload := self._json(message.data):
            self.state.update_hand(payload)

    def _joint_state(self, message: JointState) -> None:
        self.state.update_joints(message)

    @staticmethod
    def _joint_values(values: list[Any], minimum: float = 0.0) -> list[int]:
        if len(values) != 25:
            raise ValueError("L25 requires exactly 25 values")
        return [round(max(minimum, min(255.0, finite_number(value, minimum)))) for value in values]

    def _publish_joints(self, positions: list[int]) -> None:
        message = JointState()
        message.header.stamp = self.get_clock().now().to_msg()
        message.position = [float(value) for value in positions]
        self.joint_publisher.publish(message)

    def _publish_setting(self, command: str, values: list[int]) -> None:
        message = String()
        parameter = "speed" if command == "set_speed" else "torque"
        message.data = json.dumps({
            "setting_cmd": command,
            "params": {"hand_type": self.hand_type, parameter: values},
        })
        self.setting_publisher.publish(message)

    def command_joints(self, positions: list[Any]) -> list[int]:
        if len(positions) != 25:
            raise ValueError("L25 requires exactly 25 joint values")
        values = self._joint_values(positions)
        with self._motion_lock:
            self._motion_generation += 1
        self._publish_joints(values)
        return values

    def start_motion(self, positions: list[Any], speed: int) -> dict[str, Any]:
        target = self._joint_values(positions)
        speed = round(max(10, min(255, finite_number(speed, 25))))
        with self._motion_lock:
            self._motion_generation += 1
        self._publish_setting("set_speed", [speed] * 25)
        self.state.set_motion(False, "completed")
        self._publish_joints(target)
        return {"position": target, "speed": speed}


def make_handler(web_root: Path, state: TactileState, node: LinkerHandWebNode):
    class DashboardHandler(SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=os.fspath(web_root), **kwargs)

        def log_message(self, format_string: str, *args: Any) -> None:
            if self.path.startswith("/api/") and args and str(args[1]) == "200":
                return
            super().log_message(format_string, *args)

        def _headers(self, content_type: str, length: int | None = None) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            path = self.path.split("?", 1)[0]
            if path == "/api/linkerhand/state":
                body = json.dumps(state.snapshot(), ensure_ascii=False).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self._headers("application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == "/api/linkerhand/events":
                self._events()
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802 - stdlib method name
            path = self.path.split("?", 1)[0]
            if path not in ("/api/linkerhand/joints", "/api/linkerhand/motion"):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16384:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                positions = payload.get("position") if isinstance(payload, dict) else None
                if not isinstance(positions, list):
                    raise ValueError("position must be an array")
                if path == "/api/linkerhand/motion":
                    result = node.start_motion(
                        positions,
                        payload.get("speed", 25),
                    )
                else:
                    result = {"position": node.command_joints(positions)}
                body = json.dumps({"ok": True, **result}).encode("utf-8")
                self.send_response(HTTPStatus.OK)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                body = json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
                self.send_response(HTTPStatus.BAD_REQUEST)
            self._headers("application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _events(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            sequence = -1
            try:
                while True:
                    snapshot = state.wait_for_change(sequence)
                    sequence = snapshot["sequence"]
                    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"event: tactile\ndata: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    mimetypes.add_type("text/javascript", ".js")
    return DashboardHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--hand", choices=("left", "right"), default="left")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in the default browser")
    parser.add_argument(
        "--web-root",
        type=Path,
        default=Path(__file__).resolve().parent,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    web_root = args.web_root.resolve()
    if not (web_root / "index.html").is_file():
        raise SystemExit(f"Web root has no index.html: {web_root}")

    rclpy.init()
    state = TactileState()
    node = LinkerHandWebNode(state, args.hand)
    ros_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    ros_thread.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(web_root, state, node))
    dashboard_url = f"http://{args.host}:{args.port}/"
    print(f"LinkerHand tactile dashboard: {dashboard_url}")
    print("Keep this window open. Press Ctrl+C to stop.")
    if args.open:
        threading.Timer(0.4, webbrowser.open, args=(dashboard_url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        # serve_forever runs on this thread. Calling shutdown() from the same
        # thread deadlocks, while server_close() releases the socket directly.
        server.server_close()
        node.destroy_node()
        rclpy.shutdown()
        ros_thread.join(timeout=2)


if __name__ == "__main__":
    main()
