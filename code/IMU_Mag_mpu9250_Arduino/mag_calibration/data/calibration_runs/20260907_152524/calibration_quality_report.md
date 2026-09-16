# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_152524/magnetometer_data.csv`
- 样本：8871（拟合内点 8742，剔除 129）
- 总体结论：**PASS**

## 校准参数

硬铁偏置：`[12.654238434, 343.999875409, 162.117351750]`

完整 3×3 软铁矩阵：

```text
  [ 0.981194935,  0.012536540,  0.019422619]
  [ 0.012536540,  0.967953052,  0.054308012]
  [ 0.019422619,  0.054308012,  1.056486416]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 289.994437 |
| 原始半径 CV | 8.457% |
| 校准后半径均值 | 290.437641 |
| 校准后半径标准差 | 11.150287 |
| 校准后半径 CV | 3.839% |
| 相对 RMS 误差 | 3.848% |
| 相对绝对误差 P95 | 7.822% |
| 椭球长短轴比 | 1.154301 |
| 校准矩阵条件数 | 1.154301 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.626793 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
