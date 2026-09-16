# PnP 位姿估计：成像模型、算法选择与工程实现

> 文档性质：计算机视觉位姿估计技术说明  
> 更新日期：2026-08-18

## 摘要

PnP（perspective-n-point）解决的是 3D—2D 位姿估计问题：已知一组物体或世界坐标系中的三维点，以及它们在图像中的二维像素位置，求该三维坐标系相对于相机的旋转和平移。输出的旋转 $\mathbf R$ 与平移 $\mathbf t$ 共同构成六维位姿。

PnP 本身并不负责识别物体，也不会自动生成可靠对应点。系统精度由相机标定、3D 模型、2D 特征定位、点的空间分布、求解器、异常点处理和坐标变换共同决定。工程上通常采用“去畸变/一致的相机模型—对应点检查—RANSAC PnP—非线性精修—质量门限—时序融合”的完整流程。

## 1. 问题定义

设物体坐标系中的三维点为：

$$
\mathbf P_i^o = [X_i,Y_i,Z_i]^\mathsf T
$$

其图像像素坐标为：

$$
\mathbf p_i = [u_i,v_i]^\mathsf T
$$

PnP 求解满足下式的旋转和平移：

$$
\mathbf P_i^c = \mathbf R_{co}\mathbf P_i^o + \mathbf t_{co}
$$

其中下标 `co` 表示“物体坐标系到相机坐标系”。齐次变换为：

$$
{}^c\mathbf T_o =
\begin{bmatrix}
\mathbf R_{co} & \mathbf t_{co} \\
\mathbf 0^\mathsf T & 1
\end{bmatrix}
$$

如果三维点定义在世界坐标系中，则同一输出表示世界到相机的变换 ${}^c\mathbf T_w$，而不是“相机在世界中的位姿”。相机在物体坐标系中的位姿需取逆：

$$
\mathbf R_{oc}=\mathbf R_{co}^{\mathsf T},\qquad
\mathbf t_{oc}=-\mathbf R_{co}^{\mathsf T}\mathbf t_{co}
$$

这是 PnP 接入机器人系统时最容易出现的方向错误之一。

## 2. 相机成像模型

### 2.1 针孔模型

忽略畸变时，三维点到像素的投影关系为：

$$
s
\begin{bmatrix}
u\\v\\1
\end{bmatrix}
=
\mathbf K
\begin{bmatrix}
\mathbf R & \mathbf t
\end{bmatrix}
\begin{bmatrix}
X\\Y\\Z\\1
\end{bmatrix}
$$

其中 s 与相机坐标系深度相关，相机内参矩阵为：

$$
\mathbf K=
\begin{bmatrix}
f_x & \gamma & c_x\\
0 & f_y & c_y\\
0 & 0 & 1
\end{bmatrix}
$$

f_x,f_y 是像素单位焦距，c_x,c_y 是主点，gamma 通常取 0。OpenCV 常用相机坐标约定为 x 向右、y 向下、z 向前；与机器人中常见的 x 向前、y 向左、z 向上并不相同，必须显式标定和转换。

##### 双目相机：

可以测量深度Z

![img](https://pica.zhimg.com/v2-03a212719e9084f0ab4c6aabdf68e48c_1440w.jpg)

- **基线**：两个光心的连线称为基线；
- **[极平面](https://zhida.zhihu.com/search?content_id=168577663&content_type=Article&match_order=1&q=极平面&zhida_source=entity)**：物点（空间点M）与两个光心的连线构成的平面称为极平面；
- **[极线](https://zhida.zhihu.com/search?content_id=168577663&content_type=Article&match_order=1&q=极线&zhida_source=entity)**：极平面与成像平面的交线
- **极点**：极线的一端，基线与像平面的交点
- **[像点](https://zhida.zhihu.com/search?content_id=168577663&content_type=Article&match_order=1&q=像点&zhida_source=entity)**：极线的一端，光心与物点连线与像平面的交点；

### 2.2 畸变

实际镜头存在径向和切向畸变。常见参数包括径向项 k_1,k_2,k_3 与切向项 p_1,p_2，广角或鱼眼镜头需要使用与其标定模型一致的投影函数。

有两种正确的处理方式：

1. 将原始像素点、相机内参和畸变参数一起传给 PnP；
2. 先把像素点去畸变到归一化平面，再使用与归一化坐标匹配的相机模型。

不能对点去畸变后仍重复传入原畸变参数，也不能拿鱼眼模型标定结果直接套普通针孔畸变公式。

## 3. PnP 与相关问题的边界

| 问题 | 输入 | 主要输出 | 与 PnP 的区别 |
| --- | --- | --- | --- |
| PnP | 已知 3D 点与对应 2D 像素 | 相机/物体相对位姿 | 3D—2D |
| 三角化 | 多视角中的对应 2D 点与相机位姿 | 3D 点 | 2D—2D 恢复空间点 |
| ICP | 两组三维点云 | 刚体变换 | 3D—3D |
| VIO | 连续图像与 IMU | 设备运动轨迹、速度、姿态和偏置 | 连续状态估计，不是单帧求解 |
| SLAM | 连续传感器数据 | 自身定位与环境地图 | 同时定位与建图 |

PnP 可以作为 VIO/SLAM 的一个视觉测量环节，也可以在已知标志物或物体模型上独立工作。单帧 PnP 不会自动维护跨帧轨迹，也不估计地图。

参考视频：https://www.bilibili.com/video/BV1nsq3BxEXt/?spm_id_from=333.337.search-card.all.click&vd_source=356cb431e7f534d2169174c253babd0c

## 4. 输入条件与可观测性

### 4.1 对应点数量与分布

数学上的 P3P 使用 3 对点并可能产生多个解；在 OpenCV 的 P3P/AP3P 接口中通常需要 4 对输入点，其中额外点用于消除歧义或评估解。实际系统应尽量使用更多高质量对应点。

点的空间分布比单纯数量更重要：

- 共线或接近共线的点无法稳定约束完整位姿；
- 点集中在很小图像区域时，对像素噪声非常敏感；
- 全部点共面时存在特定的姿态歧义和条件数问题，应使用平面专用方法；
- 深度变化充分、覆盖视场较广的非共面点通常更稳健。

### 4.2 尺度

PnP 平移的单位与三维模型一致。模型用毫米，输出 `tvec` 就是毫米；模型用米，输出就是米。若三维模型尺度错误，旋转可能看起来合理，但平移尺度会整体错误。

### 4.3 对应点质量

2D 点可来自角点、AprilTag/ArUco 标记、关键点网络或特征匹配。每个 2D 点必须与同一索引的 3D 点真实对应。左右镜像、角点顺序错误和目标对称性都会产生“重投影看似合理、实际朝向错误”的结果。

对于神经网络关键点，应保留每点置信度，并在求解前剔除低置信点。若使用标准 RANSAC 接口不能直接利用连续权重，可先按置信度筛选，再在非线性优化阶段引入加权重投影误差。

## 5. 常用求解方法

| 方法 | 特点 | 适用建议 |
| --- | --- | --- |
| P3P | 最小问题，最多存在多个候选解 | RANSAC 内核或少量点场景，需消歧 |
| AP3P | 对 P3P 的代数改进 | 与 P3P 类似，可比较数值稳定性 |
| EPnP | 计算效率高，适合多点 | 常用初始化或 RANSAC 求解器 |
| ITERATIVE | 最小化重投影误差的迭代方法 | 初值合理、点数充足时精度较好 |
| IPPE | 平面目标的位姿估计，可返回歧义解 | 标志板、平面物体、近正视场景 |
| IPPE_SQUARE | 针对按规定顺序输入的正方形四点 | 方形标志，必须遵守点序和坐标定义 |
| SQPnP | 全局一致的快速 PnP 方法 | 三点及以上、一般场景的可选方案 |

没有一种方法对所有场景都最优。较稳健的工程组合是：用 P3P/AP3P/EPnP 在 RANSAC 中获得内点和初值，再使用迭代最小化重投影误差进行精修。平面目标应优先评估 IPPE 系列，并保留候选解做时序与正深度判断。

## 6. 重投影误差与优化

给定候选位姿，将每个三维点重新投影到图像：

$$
\hat{\mathbf p}_i = \pi(\mathbf K,\mathbf d,
\mathbf R\mathbf P_i+\mathbf t)
$$

重投影残差为：

$$
\mathbf r_i=\mathbf p_i-\hat{\mathbf p}_i
$$

常用均方根误差（RMSE）为：

$$
e_{\text{rms}}=
\sqrt{\frac{1}{N}\sum_{i=1}^{N}\lVert\mathbf r_i\rVert^2}
$$

低重投影误差是必要条件，但不是位姿正确的充分条件。平面对称、模型点序错误或远距离弱透视条件下，错误位姿也可能具有较小像素误差。因此还应检查：

- 内点数量与内点比例；
- 所有有效点是否位于相机前方，即 Z_c>0；
- 平移距离与工作空间是否合理；
- 与上一帧相比是否出现不合理跳变；
- 多个候选解中哪一个满足物理和时序约束；
- 旋转和平移协方差或灵敏度是否过大。

## 7. 推荐工程流程

```text
相机内参/畸变标定
        ↓
确定 3D 模型坐标系与单位
        ↓
检测或跟踪 2D 点，并建立一一对应
        ↓
置信度、点数、共线性与覆盖范围检查
        ↓
RANSAC PnP 获取内点与初始位姿
        ↓
使用内点做非线性精修
        ↓
重投影、正深度、工作空间与时序质量门限
        ↓
坐标变换到机器人/世界坐标系
        ↓
滤波、发布与记录质量指标
```

### 7.1 Python/OpenCV 参考实现

```python
import cv2
import numpy as np


def estimate_pose_pnp(object_points, image_points, camera_matrix, dist_coeffs):
    object_points = np.asarray(object_points, dtype=np.float64).reshape(-1, 3)
    image_points = np.asarray(image_points, dtype=np.float64).reshape(-1, 2)

    if len(object_points) != len(image_points) or len(object_points) < 4:
        raise ValueError("PnP requires matching 3D/2D arrays with enough points")
    if not np.isfinite(object_points).all() or not np.isfinite(image_points).all():
        raise ValueError("PnP input contains NaN or infinity")

    ok, rvec, tvec, inliers = cv2.solvePnPRansac(
        objectPoints=object_points,
        imagePoints=image_points,
        cameraMatrix=camera_matrix,
        distCoeffs=dist_coeffs,
        iterationsCount=100,
        reprojectionError=3.0,
        confidence=0.999,
        flags=cv2.SOLVEPNP_EPNP,
    )

    if not ok or inliers is None or len(inliers) < 4:
        return None

    idx = inliers.ravel()
    rvec, tvec = cv2.solvePnPRefineLM(
        objectPoints=object_points[idx],
        imagePoints=image_points[idx],
        cameraMatrix=camera_matrix,
        distCoeffs=dist_coeffs,
        rvec=rvec,
        tvec=tvec,
    )

    projected, _ = cv2.projectPoints(
        object_points[idx], rvec, tvec, camera_matrix, dist_coeffs
    )
    projected = projected.reshape(-1, 2)
    residual = image_points[idx] - projected
    rmse_px = float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))

    rotation, _ = cv2.Rodrigues(rvec)
    points_camera = (rotation @ object_points[idx].T + tvec).T
    positive_depth = bool(np.all(points_camera[:, 2] > 0.0))

    return {
        "rotation_object_to_camera": rotation,
        "translation_object_to_camera": tvec.reshape(3),
        "rvec": rvec.reshape(3),
        "inlier_indices": idx,
        "inlier_ratio": len(idx) / len(object_points),
        "rmse_px": rmse_px,
        "positive_depth": positive_depth,
    }
```

示例中的 `3.0 px`、100 次迭代和 `0.999` 置信度只是初始设置，应依据图像分辨率、关键点噪声、实时性和误匹配比例调整。平面方形标志不应机械套用 EPnP，应单独比较 IPPE/IPPE_SQUARE。

### 7.2 坐标变换到机器人

若 PnP 得到物体到相机的 c·T_o，相机到机器人基座的外参为 b`T_c，则物体在机器人基座中的位姿为：

$$
{}^b\mathbf T_o = {}^b\mathbf T_c\,{}^c\mathbf T_o
$$

矩阵乘法次序必须从右到左按坐标流向检查。建议在代码变量名中保留 `T_target_source` 语义，例如 `T_base_camera @ T_camera_object`，不要只使用含义模糊的 `T1`、`T2`。

## 8. 相机标定与外参标定

### 8.1 相机内参

使用棋盘格、圆点板或 AprilTag 标定板采集多姿态图像，视角应覆盖画面中心、边缘、不同距离和不同倾角。应报告：

- 图像数量与分辨率；
- 使用的相机模型；
- 内参和畸变参数；
- 每张图与整体重投影误差；
- 被剔除图像及原因；
- 标定时的焦距、对焦和分辨率设置。

自动对焦、数字裁剪、分辨率改变或镜头重新安装都可能使原内参失效。

### 8.2 相机—机器人外参

PnP 只给出目标相对于相机的位姿。机器人要使用该结果，还需要相机与机器人基座/末端之间的外参：

- **Eye-to-hand**：相机固定在外部，标定 ${}^b\mathbf T_c$；
- **Eye-in-hand**：相机安装在末端，通常通过手眼标定求 ${}^e\mathbf T_c$。

外参精度会直接叠加到机器人目标位姿中。验证时应把已知标定物放在多个工作空间位置，比较转换后的三维误差，而不是只看相机图像上的重投影误差。

### 8.3 双目深度

双目通过匹配左右图像的同一空间点计算视差，在理想平行模型中：

$$
Z=\frac{fB}{d}
$$

其中 $f$ 为焦距，$B$ 为基线，$d$ 为视差。双目可直接提供尺度和深度，但需要双目标定、极线校正和可靠匹配。PnP 与双目并不互斥：双目深度可生成或验证 3D 点，PnP 则利用已知 3D—2D 对应估计刚体位姿。

## 9. PnP 与 IMU 融合

视觉 PnP 提供低频但有绝对参考的位姿；IMU 提供高频角速度和加速度。组合后可以改善快速运动、短时遮挡和帧间平滑性。

一个典型融合框架为：

1. IMU 在图像帧之间高频传播姿态、速度和位置；
2. 图像到达时用时间戳将状态传播到曝光时刻；
3. PnP 位姿作为观测修正累计漂移；
4. 同时估计陀螺仪/加速度计偏置；
5. PnP 内点少、重投影误差高或发生跳变时，拒绝该次观测。

相机和 IMU 融合前必须知道二者外参 ${}^c\mathbf T_i$，并校准时间偏移。若视觉和惯性时间错位，即使各自单独精确，也会在快速运动时产生系统性姿态误差。

对简单遥操作系统，也可先采用松耦合：IMU 提供高频旋转，PnP 提供位置和低频旋转校正。需要更高精度和一致性时，再使用 VIO/ESKF 或联合非线性优化。

## 10. 失败模式与排查

| 现象 | 可能原因 | 处理方向 |
| --- | --- | --- |
| 位置尺度整体错误 | 3D 模型单位错误 | 统一米/毫米并检查模型尺寸 |
| 位姿方向完全相反 | 把物体到相机当成相机到物体 | 对变换取逆并明确命名 |
| 目标静止但位姿抖动 | 2D 点噪声、点分布集中、焦距不准 | 亚像素定位、增大基线、精修与滤波 |
| 平面目标出现翻转 | 平面双解、近正视退化 | 使用 IPPE、正深度和时序约束消歧 |
| 快速运动时误差增大 | 运动模糊、滚动快门、时间不同步 | 缩短曝光、提高帧率、同步 IMU |
| 边缘位置误差明显 | 畸变模型或内参不准 | 重新标定并覆盖图像边缘 |
| 重投影小但机器人位置错 | 相机—机器人外参错误 | 独立验证外参和矩阵方向 |
| 偶发巨大跳变 | 错误对应点、RANSAC 阈值不当 | 增加置信度筛选与质量门限 |
| 绕某轴不稳定 | 点几何退化或目标对称 | 重新设计 3D 点布局/标志形状 |

## 11. 测试与验收建议

### 11.1 单元测试

先用已知 R,t 将 3D 点投影成理想 2D 点，再加入可控像素噪声与异常点，验证求解能恢复已知位姿。测试应覆盖：

- 非共面点、共面点和接近共线点；
- 不同距离、倾角与视场位置；
- 0～目标上限的像素噪声；
- 不同异常点比例；
- `±180°` 附近的旋转；
- 模型单位与坐标变换方向。

### 11.2 实物测试

使用尺寸可追溯的标定物和测量基准，至少报告：

- 平移误差：每轴均值、RMSE、95 分位；
- 旋转误差：相对旋转角；
- 重投影 RMSE 与内点比例；
- 成功率和错误跳变率；
- 计算耗时与端到端延迟；
- 不同距离、光照、遮挡和运动速度下的性能。

旋转误差可由：

$$
e_R=\arccos\left(\frac{\operatorname{tr}(\mathbf R_{gt}^{\mathsf T}\mathbf R_{est})-1}{2}\right)
$$

计算。实现时应把 `arccos` 的输入裁剪到 $[-1,1]$，避免浮点误差产生无效值。

## 12. 当前学习进展与建议路线

### 已覆盖内容

- 理解 PnP 的 3D—2D 输入与 6D 位姿输出；
- 区分相机内参、畸变和外参；
- 了解 P3P、AP3P、EPnP 与 ITERATIVE 的基本用途；
- 建立 PnP 与双目、VIO、SLAM、IMU 融合之间的关系；
- 明确 PnP 可用于遥操作链路中的视觉位姿估计。

### 建议下一步

1. 用合成数据完成 `projectPoints → solvePnP → 误差评价` 闭环。
2. 完成单目标定，保存内参、畸变、分辨率和标定报告。
3. 使用固定尺寸的平面标志，对比 ITERATIVE、EPnP 与 IPPE。
4. 加入 RANSAC、内点比例和重投影质量门限。
5. 标定相机到机器人或 IMU 的外参，并验证矩阵方向。
6. 采集静态、慢速、快速和部分遮挡数据，形成可复现基准。

## 13. 参考资料

- [《视觉 SLAM 十四讲》相关 PnP/ICP 学习视频](https://www.bilibili.com/video/BV1ie4y1f7XG/)
- [原笔记中的 PnP 学习视频](https://www.bilibili.com/video/BV1nsq3BxEXt/)
- [pose_estimation 示例仓库](https://github.com/nanfeng-dada/pose_estimation)
- [PnP 学习文章](https://zhuanlan.zhihu.com/p/423770706)

## 14. 总结

PnP 的算法调用并不复杂，真正决定系统可靠性的，是 3D—2D 对应质量、相机模型、点几何、异常值处理和坐标系管理。任何输出位姿都应同时携带内点比例、重投影误差、正深度与时序一致性等质量信息。接入遥操作或机器人控制前，还必须完成相机外参和安全门限验证；接入 IMU 时，则需要进一步保证外参与时间同步。

