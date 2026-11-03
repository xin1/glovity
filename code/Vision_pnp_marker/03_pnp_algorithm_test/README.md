# 03 PnP Algorithm Test

这个目录放 PnP 算法合成测试。它不连接摄像头，也不检测真实标记，主要用于确认位姿估计算法流程是否可靠。

## 文件

- `pnp_algorithm_test.py`：生成虚拟 3D 点和 2D 投影点，加入噪声和离群点，然后用 PnP + RANSAC 求解位姿。

## 它测试什么

测试流程大致是：

1. 生成一组已知 3D 点。
2. 构造一个真实相机位姿。
3. 用相机模型把 3D 点投影到 2D 图像。
4. 给 2D 点加入像素噪声。
5. 人为加入一部分离群点。
6. 使用 `solvePnPRansac` 估计位姿。
7. 使用 LM 优化进一步细化位姿。
8. 统计误差并判断是否通过质量门限。

## 运行命令

从项目根目录运行：

```powershell
cd D:\Desktop\github\glovity\code\Vision_pnp_marker
C:\Python313\python.exe .\03_pnp_algorithm_test\pnp_algorithm_test.py
```

## 输出怎么看

典型输出类似：

```text
Quality gate:          PASS
Inliers:               68/80 (85.0%)
Inlier reproj. RMSE:   0.986 px
All-point RMSE:        159.603 px
Rotation error:        0.0736 deg
Translation error:     0.000299 model units
Outliers rejected:     12/12
Minimum depth:         1.2758 model units
Solver time:           9.934 ms
```

重点看：

- `Quality gate`：最终质量判断，`PASS` 表示测试通过。
- `Inliers`：RANSAC 认为可信的点数量。
- `Inlier reproj. RMSE`：内点重投影误差，单位是像素。
- `All-point RMSE`：所有点误差。因为包含故意加入的离群点，所以可能很大。
- `Rotation error`：旋转误差。
- `Translation error`：平移误差。
- `Outliers rejected`：离群点剔除数量。
- `Solver time`：求解耗时。

## 和实时程序的区别

- 这个测试用的是虚拟数据。
- 实时程序用的是摄像头真实画面。
- 这个测试验证算法逻辑。
- 实时程序验证真实环境中的识别、标定、光照、角点检测和 PnP 整体效果。

## 什么时候运行它

- 修改 PnP 求解逻辑后。
- 调整 RANSAC 阈值后。
- 想确认算法在噪声和离群点下是否稳定。
- 排查问题时，用它区分“算法逻辑问题”和“真实摄像头/标记检测问题”。

