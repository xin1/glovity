# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/calibration_runs/20260907_151807/magnetometer_data.csv`
- 样本：8408（拟合内点 7886，剔除 522）
- 总体结论：**FAIL**

## 校准参数

硬铁偏置：`[2.159462557, 346.218525098, 176.465195210]`

完整 3×3 软铁矩阵：

```text
  [ 0.977984419,  0.013134807,  0.033431515]
  [ 0.013134807,  0.980230662,  0.055599162]
  [ 0.033431515,  0.055599162,  1.047567259]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 286.723432 |
| 原始半径 CV | 15.779% |
| 校准后半径均值 | 286.442405 |
| 校准后半径标准差 | 20.049501 |
| 校准后半径 CV | 6.999% |
| 相对 RMS 误差 | 6.993% |
| 相对绝对误差 P95 | 16.456% |
| 椭球长短轴比 | 1.150352 |
| 校准矩阵条件数 | 1.150352 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.696346 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- FAIL — `radius_cv_below_5_percent`
- FAIL — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
