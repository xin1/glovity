# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_153811/magnetometer_data.csv`
- 样本：2045（拟合内点 1983，剔除 62）
- 总体结论：**PASS**

## 校准参数

硬铁偏置：`[18.049831145, 348.002897537, 167.881965397]`

完整 3×3 软铁矩阵：

```text
  [ 0.954231083,  0.010770618,  0.010083183]
  [ 0.010770618,  1.002459093,  0.069323836]
  [ 0.010083183,  0.069323836,  1.050405663]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 284.848252 |
| 原始半径 CV | 7.142% |
| 校准后半径均值 | 284.972524 |
| 校准后半径标准差 | 12.167000 |
| 校准后半径 CV | 4.270% |
| 相对 RMS 误差 | 4.272% |
| 相对绝对误差 P95 | 9.824% |
| 椭球长短轴比 | 1.159127 |
| 校准矩阵条件数 | 1.159127 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.380819 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
