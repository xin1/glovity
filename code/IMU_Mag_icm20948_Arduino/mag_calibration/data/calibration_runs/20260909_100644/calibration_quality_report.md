# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260909_100644/magnetometer_data.csv`
- 样本：4143（拟合内点 4142，剔除 1）
- 总体结论：**FAIL**

## 校准参数

硬铁偏置：`[-172.652516108, 21.924780110, 67.106538851]`

完整 3×3 软铁矩阵：

```text
  [ 0.980697528, -0.016366712,  0.080200530]
  [-0.016366712,  0.987608358,  0.033096453]
  [ 0.080200530,  0.033096453,  1.040521797]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 291.359634 |
| 原始半径 CV | 7.346% |
| 校准后半径均值 | 291.799901 |
| 校准后半径标准差 | 15.177595 |
| 校准后半径 CV | 5.201% |
| 相对 RMS 误差 | 5.211% |
| 相对绝对误差 P95 | 11.261% |
| 椭球长短轴比 | 1.206541 |
| 校准矩阵条件数 | 1.206541 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.304622 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- FAIL — `radius_cv_below_5_percent`
- FAIL — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
