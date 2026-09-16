# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260908_170304/magnetometer_data.csv`
- 样本：7807（拟合内点 7488，剔除 319）
- 总体结论：**FAIL**

## 校准参数

硬铁偏置：`[23.502908345, 49.201357377, 5.130479517]`

完整 3×3 软铁矩阵：

```text
  [ 0.993878277, -0.011234108,  0.069503313]
  [-0.011234108,  1.019597940,  0.032829897]
  [ 0.069503313,  0.032829897,  0.992911595]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 291.987836 |
| 原始半径 CV | 10.861% |
| 校准后半径均值 | 291.972208 |
| 校准后半径标准差 | 19.824343 |
| 校准后半径 CV | 6.790% |
| 相对 RMS 误差 | 6.789% |
| 相对绝对误差 P95 | 14.475% |
| 椭球长短轴比 | 1.168276 |
| 校准矩阵条件数 | 1.168276 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.520174 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- FAIL — `radius_cv_below_5_percent`
- FAIL — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
