本目录与采集库对齐目录骨架（`port/`）。

算法库不访问 IMU 总线，**不需要实现 `cust_imu_*`**。编 `libhy_imu_proc.a` 时不要加入任何 port 源文件。
