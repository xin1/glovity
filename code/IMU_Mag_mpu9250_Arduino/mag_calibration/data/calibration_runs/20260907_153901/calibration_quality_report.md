# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_153901/magnetometer_data.csv`
- 样本：1183（拟合内点 1095，剔除 88）
- 总体结论：**FAIL**

## 校准参数

硬铁偏置：`[-3542.643474648, -3628.666474648, 3798.682474648]`

完整 3×3 软铁矩阵：

```text
  [ 0.709777945,  0.081870723,  0.218191832]
  [ 0.081870723,  1.226972908,  0.689326530]
  [ 0.218191832,  0.689326530,  1.586543103]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 3943.972320 |
| 原始半径 CV | 9.220% |
| 校准后半径均值 | 3921.844759 |
| 校准后半径标准差 | 156.743244 |
| 校准后半径 CV | 3.997% |
| 相对 RMS 误差 | 4.014% |
| 相对绝对误差 P95 | 8.825% |
| 椭球长短轴比 | 3.493844 |
| 校准矩阵条件数 | 3.493844 |
| 覆盖八分体 | 3 / 8 |
| 方向覆盖条件数 | 134.704173 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- FAIL — `all_octants_covered`
- FAIL — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
