# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260908_171100/magnetometer_data.csv`
- 样本：4167（拟合内点 4037，剔除 130）
- 总体结论：**FAIL**

## 校准参数

硬铁偏置：`[29.179283051, 42.069037668, 6.566906723]`

完整 3×3 软铁矩阵：

```text
  [ 0.978634342, -0.022379596,  0.089240368]
  [-0.022379596,  1.054803438,  0.003390782]
  [ 0.089240368,  0.003390782,  0.977377684]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 286.698928 |
| 原始半径 CV | 8.885% |
| 校准后半径均值 | 286.274966 |
| 校准后半径标准差 | 13.754075 |
| 校准后半径 CV | 4.804% |
| 相对 RMS 误差 | 4.800% |
| 相对绝对误差 P95 | 10.538% |
| 椭球长短轴比 | 1.213841 |
| 校准矩阵条件数 | 1.213841 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.754318 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- FAIL — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
