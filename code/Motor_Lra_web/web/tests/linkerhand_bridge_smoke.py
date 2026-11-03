import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "web"))

import rclpy
from sensor_msgs.msg import JointState

from linkerhand_bridge import LinkerHandWebNode, TactileState


def main() -> None:
    rclpy.init()
    state = TactileState()
    joints = JointState()
    joints.position = [100.0] * 25
    state.update_joints(joints)
    node = LinkerHandWebNode(state, "left")
    try:
        result = node.start_motion([120] * 25, 0.5, 90, 130, True)
        time.sleep(0.75)
        assert result["duration"] == 0.5
        assert state.snapshot()["motion"]["status"] == "completed"

        state.update_joints(joints)
        node.start_motion([140] * 25, 3.0, 50, 100, True)
        time.sleep(0.12)
        held = node.stop_motion()
        assert held is not None and len(held) == 25
        assert state.snapshot()["motion"]["status"] == "cancelled"
        print("PASS: smooth trajectory and cancellation")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
