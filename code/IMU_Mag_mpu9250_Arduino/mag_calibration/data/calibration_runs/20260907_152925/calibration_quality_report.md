# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_152925/magnetometer_data.csv`
- 样本：3200（拟合内点 3153，剔除 47）
- 总体结论：**PASS**

## 校准参数

硬铁偏置：`[16.591560141, 342.608310980, 163.629495400]`

完整 3×3 软铁矩阵：

```text
  [ 0.981958413,  0.001891366,  0.013746876]
  [ 0.001891366,  0.991922933,  0.068296103]
  [ 0.013746876,  0.068296103,  1.031560442]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 284.023396 |
| 原始半径 CV | 6.271% |
| 校准后半径均值 | 283.719446 |
| 校准后半径标准差 | 10.342800 |
| 校准后半径 CV | 3.645% |
| 相对 RMS 误差 | 3.643% |
| 相对绝对误差 P95 | 7.198% |
| 椭球长短轴比 | 1.154085 |
| 校准矩阵条件数 | 1.154085 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.795370 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
