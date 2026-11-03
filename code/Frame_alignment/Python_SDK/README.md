# TeleX SDK

> 📦 面向开发者的 **TeleX 感知前端 SDK**（非上位机应用）  
> 一次 `TeleXSession.read()` 同时返回双手 **Tracker 6D** 与 **手指 IMU / 关节**（腕部局部系）

包名 `telex`，安装后：`import telex`。仓库目录名：`TeleX-SDK`。

---

## 硬件要求

| 组件 | 说明 |
|------|------|
| 双目相机 | V4L2 鱼眼立体相机（OpenCV 索引，如 `--camera 2`） |
| 标定 | 包内默认 `telex.tracker` data yml；可按设备替换 |
| Hub | **Receiver_RX**，默认 `/dev/ttyACM0` @ **2000000** baud |
| 手套 | 左右手无线 IMU（与 Hub 配套） |

Tracker 与手套共用 **一个** Hub 串口，避免抢占。Hub 不可用时默认可降级为纯视觉（除非 `require_glove=True`）。

> ⚠️ `pose.tvec` 单位为 **毫米**，`joints` 为 **米**（腕部局部系）。

---

## 安装

```bash
cd TeleX-SDK
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

可选：`pip install -e ".[docs]"` / `pip install -e ".[test]"`。

开发工具（Ruff、Pyright、覆盖率、属性测试与提交钩子）：`pip install -e ".[dev]"`。

验证：

```bash
python -c "from telex import TeleXSession; import telex; print(telex.__version__)"
telex --help
```

---

## 快速开始

```python
from telex import TeleXSession, IMU_LABELS, JOINT_LABELS

with TeleXSession(camera=2, side="both") as telex:
    r = telex.read(frame="mid", strict=False)
    if r.left and r.left.pose is not None:
        print("left tracker tvec mm", r.left.pose.tvec.ravel())
    if r.left and r.left.joints is not None:
        print("index tip (m)", r.left.joints[4], JOINT_LABELS[4])
    if r.left and r.left.imus is not None:
        print("wrist quat", r.left.imus[0], IMU_LABELS[0])
```

### 参数要点

| 参数 | 说明 |
|------|------|
| `side` | `"both"` / `"left"` / `"right"` |
| `enable_imu_fusion` | tracker 腕部 IMU 融合；Hub 失败则纯视觉 |
| `require_glove` | `True` 时 Hub 失败则构造失败 |
| `glove_calib_left` / `glove_calib_right` | 左/右手三步校准 `.npy` 路径（可选，见下） |
| `read(frame=)` | `"mid"` 或 `"left"` |
| `read(strict=)` | `True` 时启用侧需同时有 pose 与 glove 数据 |

---

## 手指骨架 FK 与三步校准

`joints`（shape `(21, 3)`，米）由 `telex.glove.fk.compute_skeleton_joints` 计算：

- **11 路 IMU 全量参与**：按地址顺序 `0x50..0x59, 0x60` 映射到 16 关节槽位
  （腕部 Y180 安装修正，PIP 复制到 DIP），与旧管线 / 三步校准工具逐位一致；
- **每指独立弯曲**：MCP / PIP 两段由各自 IMU 驱动，DIP 按比例耦合（四指 2/3，拇指 0.6）；
- **腕部相对化**：整手刚性转动时骨架保持不变，`joints[0]`（腕）恒为原点；
- **坐标约定**：指尖 +Y，手背 +Z（掌心 -Z），拇指侧：左手 +X / 右手 -X；
- **21 关节布局**：0 腕；1-4 食指；5-8 中指；9-12 小指；13-16 无名指；17-20 拇指
  （每指 MCP/PIP/DIP/TIP，与骨架可视化的 `BONES` 连接表相同）。

### 校准文件（强烈建议提供）

使用 **Remote_USB_ThreeStepCalibration** 产出的 `.npy`
（`{"imu_calib_quats": (3,16,4), "side": ...}`）修正 IMU 贴装误差：

```python
with TeleXSession(
    camera=2,
    side="both",
    glove_calib_left="imu_calibration_left.npy",
    glove_calib_right="imu_calibration_right.npy",
) as telex:
    r = telex.read()
```

不提供校准文件时退回未校准模式（仅腕部相对旋转，姿态会带贴装偏差）；
文件加载失败仅告警一次并退回，不中断数据流。文件修改后（mtime 变化）自动重新加载。

---

## CLI 与联调

```bash
# 轻量打印（可选三步校准文件）
telex --camera 2 --side both --glove-calib-left imu_calibration_left.npy
# 或
python examples/monitor_hands.py --camera 2 --side both

# 硬件联调：OpenCV 可视化 + 终端 HUD（需相机 / Hub）
python tests/test_telex.py --camera 2
```

---

## 测试

| 类型 | 命令 | 说明 |
|------|------|------|
| 单元测试（无硬件） | `pytest tests/test_hub_parse.py -q` | Hub 解析 / FK / API 辅助 |
| FK + 校准单测（无硬件） | `pytest tests/test_glove_fk.py -q` | 合成三步校准 → 骨架几何正确性 |
| 硬件联调 | `python tests/test_telex.py --camera 2` | 依赖真实相机与 Hub |

---

## 文档

| 入口 | 说明 |
|------|------|
| 📖 [`docs/TeleX-SDK-开发文档.md`](docs/TeleX-SDK-开发文档.md) | **推荐**：单文件完整指南（简介 / 安装 / 使用向导 / API / FAQ） |
| 🌐 Sphinx 分册 | `docs/introduction.md` 等，可生成 HTML 站点 |

```bash
pip install -e ".[docs]"
cd docs && make html
# 打开 docs/_build/html/index.html
```
