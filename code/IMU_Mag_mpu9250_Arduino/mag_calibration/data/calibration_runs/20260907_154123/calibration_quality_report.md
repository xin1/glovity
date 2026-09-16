# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_154123/magnetometer_data.csv`
- 样本：1536（拟合内点 1464，剔除 72）
- 总体结论：**FAIL**

## 校准参数

硬铁偏置：`[-2933.139180935, -2911.913180935, 3121.686180935]`

完整 3×3 软铁矩阵：

```text
  [ 9.134043387, -6.516474455, -0.897657831]
  [-6.516474455,  8.505439002, -1.652886397]
  [-0.897657831, -1.652886397,  1.480369475]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 19365.831024 |
| 原始半径 CV | 7.253% |
| 校准后半径均值 | 19237.663602 |
| 校准后半径标准差 | 763.624652 |
| 校准后半径 CV | 3.969% |
| 相对 RMS 误差 | 3.998% |
| 相对绝对误差 P95 | 8.953% |
| 椭球长短轴比 | 882.780873 |
| 校准矩阵条件数 | 882.780873 |
| 覆盖八分体 | 1 / 8 |
| 方向覆盖条件数 | 49108059.892934 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- FAIL — `all_octants_covered`
- FAIL — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- FAIL — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
