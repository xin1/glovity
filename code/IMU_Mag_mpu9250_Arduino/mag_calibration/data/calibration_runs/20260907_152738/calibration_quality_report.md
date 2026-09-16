# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_152738/magnetometer_data.csv`
- 样本：6514（拟合内点 6373，剔除 141）
- 总体结论：**PASS**

## 校准参数

硬铁偏置：`[11.786419160, 346.095288064, 159.768017649]`

完整 3×3 软铁矩阵：

```text
  [ 0.990653437,  0.005792196,  0.019423363]
  [ 0.005792196,  0.979213811,  0.070885595]
  [ 0.019423363,  0.070885595,  1.036394106]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 282.238133 |
| 原始半径 CV | 5.815% |
| 校准后半径均值 | 282.609002 |
| 校准后半径标准差 | 10.610380 |
| 校准后半径 CV | 3.754% |
| 相对 RMS 误差 | 3.762% |
| 相对绝对误差 P95 | 8.265% |
| 椭球长短轴比 | 1.169067 |
| 校准矩阵条件数 | 1.169067 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.278338 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
