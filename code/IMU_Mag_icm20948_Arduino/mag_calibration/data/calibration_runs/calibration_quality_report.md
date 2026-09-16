# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260909_100818/magnetometer_data.csv`
- 样本：2609（拟合内点 2560，剔除 49）
- 总体结论：**PASS**

## 校准参数

硬铁偏置：`[-177.425226266, -0.741848544, 76.989787562]`

完整 3×3 软铁矩阵：

```text
  [ 0.954062013, -0.033383922,  0.057541401]
  [-0.033383922,  1.034050579, -0.001611646]
  [ 0.057541401, -0.001611646,  1.018252011]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 288.767080 |
| 原始半径 CV | 5.728% |
| 校准后半径均值 | 288.773836 |
| 校准后半径标准差 | 8.572153 |
| 校准后半径 CV | 2.968% |
| 相对 RMS 误差 | 2.969% |
| 相对绝对误差 P95 | 5.834% |
| 椭球长短轴比 | 1.165774 |
| 校准矩阵条件数 | 1.165774 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 2.076219 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
