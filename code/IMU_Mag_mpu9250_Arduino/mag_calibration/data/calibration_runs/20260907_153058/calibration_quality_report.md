# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_153058/magnetometer_data.csv`
- 样本：3106（拟合内点 3106，剔除 0）
- 总体结论：**PASS**

## 校准参数

硬铁偏置：`[12.059709563, 352.987690111, 159.387178101]`

完整 3×3 软铁矩阵：

```text
  [ 0.992893591,  0.008108619,  0.025552210]
  [ 0.008108619,  0.967682727,  0.076124535]
  [ 0.025552210,  0.076124535,  1.047477772]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 283.477433 |
| 原始半径 CV | 5.711% |
| 校准后半径均值 | 283.033619 |
| 校准后半径标准差 | 8.087890 |
| 校准后半径 CV | 2.858% |
| 相对 RMS 误差 | 2.857% |
| 相对绝对误差 P95 | 5.763% |
| 椭球长短轴比 | 1.194164 |
| 校准矩阵条件数 | 1.194164 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.721035 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
