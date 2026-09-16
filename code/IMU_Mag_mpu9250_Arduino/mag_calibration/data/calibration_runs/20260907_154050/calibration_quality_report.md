# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_154050/magnetometer_data.csv`
- 样本：2060（拟合内点 2036，剔除 24）
- 总体结论：**PASS**

## 校准参数

硬铁偏置：`[50.461973685, 106.041986124, 118.363032980]`

完整 3×3 软铁矩阵：

```text
  [ 0.986054920,  0.020482229,  0.039051674]
  [ 0.020482229,  0.975234644,  0.080758035]
  [ 0.039051674,  0.080758035,  1.048452812]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 283.233590 |
| 原始半径 CV | 6.484% |
| 校准后半径均值 | 282.865880 |
| 校准后半径标准差 | 11.939416 |
| 校准后半径 CV | 4.221% |
| 相对 RMS 误差 | 4.217% |
| 相对绝对误差 P95 | 8.689% |
| 椭球长短轴比 | 1.208644 |
| 校准矩阵条件数 | 1.208644 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.493218 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
