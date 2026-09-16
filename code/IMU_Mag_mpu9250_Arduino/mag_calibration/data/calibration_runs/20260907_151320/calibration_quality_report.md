# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_151320/magnetometer_data.csv`
- 样本：6345（拟合内点 6049，剔除 296）
- 总体结论：**FAIL**

## 校准参数

硬铁偏置：`[28.759275312, 344.190984299, 178.149957529]`

完整 3×3 软铁矩阵：

```text
  [ 0.969774476, -0.029700238,  0.006988821]
  [-0.029700238,  1.019698813,  0.054447344]
  [ 0.006988821,  0.054447344,  1.015133202]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 287.927797 |
| 原始半径 CV | 19.432% |
| 校准后半径均值 | 285.365011 |
| 校准后半径标准差 | 24.789260 |
| 校准后半径 CV | 8.687% |
| 相对 RMS 误差 | 8.655% |
| 相对绝对误差 P95 | 19.094% |
| 椭球长短轴比 | 1.143784 |
| 校准矩阵条件数 | 1.143784 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.164662 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- FAIL — `radius_cv_below_5_percent`
- FAIL — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
