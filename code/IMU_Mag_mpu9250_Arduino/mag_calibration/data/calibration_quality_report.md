# 三轴磁力计校准质量报告

- 输入：`mag_calibration/data/magnetometer_data.csv`
- 样本：8927（拟合内点 8822，剔除 105）
- 总体结论：**PASS**

## 校准参数

硬铁偏置：`[6.389686822, 352.707584995, 172.800823587]`

完整 3×3 软铁矩阵：

```text
  [ 0.987382969, -0.014953821,  0.015126533]
  [-0.014953821,  0.964997378,  0.060486217]
  [ 0.015126533,  0.060486217,  1.053813057]
```

应用公式：`calibrated = soft_iron_matrix @ (raw - hard_iron_offset)`。

## 质量指标

| 指标 | 数值 |
|---|---:|
| 目标半径 | 296.530014 |
| 原始半径 CV | 5.920% |
| 校准后半径均值 | 296.971645 |
| 校准后半径标准差 | 12.884344 |
| 校准后半径 CV | 4.339% |
| 相对 RMS 误差 | 4.348% |
| 相对绝对误差 P95 | 8.817% |
| 椭球长短轴比 | 1.169684 |
| 校准矩阵条件数 | 1.169684 |
| 覆盖八分体 | 8 / 8 |
| 方向覆盖条件数 | 1.868929 |

## 自动检查

- PASS — `enough_samples`
- PASS — `enough_inliers`
- PASS — `all_octants_covered`
- PASS — `direction_coverage_reasonable`
- PASS — `radius_cv_below_5_percent`
- PASS — `p95_error_below_10_percent`
- PASS — `matrix_condition_below_5`

注：CV 与误差只按稳健拟合内点计算；被剔除点仍保存在校准后 CSV 中，并由 `fit_inlier` 标记。
