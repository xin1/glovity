# IMU 姿态估计、标定与多传感器工程实践

> 文档性质：原理说明、实现指南与调试记录  
> 硬件背景：ESP32/Arduino，JY901P 系列，多 IMU I²C 总线  
> 更新日期：2026-08-14

## 摘要

IMU 的优势是采样频率高、延迟低且不受视觉遮挡影响，但它测得的不是“无误差姿态”。陀螺仪测量角速度，积分后会累积零偏；加速度计测量比力，只有在运动加速度较小时才能稳定提供重力方向；磁力计可以约束航向，却容易受到电机、金属和电流产生的磁场干扰。

可靠的姿态系统需要同时处理传感器标定、时间步长、坐标系、姿态表示、观测有效性和融合算法。对于多 IMU 手套，还需解决地址管理、总线带宽、采样时序、个体安装外参和统一零位。本文在介绍基本原理的同时，整理了当前 JY901 系列 SDK 与Arduino的实操结论，并给出可执行的验证流程。

## 1. IMU 的测量对象

### 1.1 陀螺仪

陀螺仪输出机体系角速度。简化测量模型为：

$$
\boldsymbol\omega_m = \boldsymbol\omega + \mathbf b_g + \mathbf n_g
$$

其中 omega_m 是测量值，omega 是真实角速度，b_g 是随温度和时间变化的零偏，n_g 是噪声。姿态需要对角速度积分，因此即使固定零偏很小，也会随时间形成明显角度漂移。

上电静置估计零偏是最低成本的处理方法，但只能覆盖当时温度和启动状态。精度要求较高时，应记录温度—零偏曲线，并用 Allan 方差区分角度随机游走、零偏不稳定等噪声项。

陀螺仪直接测量的是角速度而非角度，所以需要通过一次积分才能得到角度值。在积分的过程中若有固定的、某一个方向的数据则会在积分的过程中不断加大影响导致角度偏差。通常来说陀螺仪的温漂是比较严重的，基本上温漂是正比于芯片的价格，越贵的片子漂的越少。温漂的数据既与温度相关又与时间相关，也就是说不同温度下不一样，不同上电时间下也不一样。通常的简单做法就是在上电的时候静止一段时间计算出此时的零偏，然后每次减去零偏。更高级的方法需要标定温度与零偏的关系，然后线性插补，另一方面使用艾伦方差分析法得到零偏和时间的关系。 对于其他的误差比如三轴不相互垂直，以及尺度因子不一致等误差都可以忽略。当然更好的情况是在电路上做一个温度控制，维持温度在50度左右（必须要在常温以上）。

### 1.2 加速度计

加速度计测量比力，而不是简单的“物体加速度”。常用模型为：

$$
\mathbf a_m = \mathbf R_{WB}^{\mathsf T}(\mathbf a_W-\mathbf g_W)
+ \mathbf b_a + \mathbf n_a
$$

静止或低动态时，测量值主要由重力方向决定，因此可约束横滚角和俯仰角。快速平移、碰撞或振动时，加速度计包含明显运动加速度，若仍把它完全当作重力，会造成姿态误修正。

加速度计常见确定性误差包括零偏、尺度因子和轴不正交。六位置标定比单面置零更可靠：分别令传感器的 X、Y、Z 轴朝向重力方向，利用已知的 g 估计偏置和比例。

对于加速度机同样会有零漂和尺度因子的误差，但是加速度计在静止时可直接得到角度不用积分，所以零漂的影响很小，但是尺度因子的影响较大。同样是重力加速度、各个面朝下时检测到的数值是不一样的，一般来说校准的方法有六面校准。就是各个面朝下然后记录重力的数值，计算得到尺度因子。目前mems传感器的精度已经比较高了，很多情况下只用正面朝上校准一次便可（仅适用于无人机）若要求不高，可不去校准加速度计，而对于云台有其他的校准思路。

### 1.3 磁力计

磁力计测量机体系中的地磁场方向，可为航向角提供绝对参考。主要误差包括：

- 硬铁效应：固定磁场使点云整体平移；
- 软铁效应：材料改变磁场分布，使球形点云变成旋转、缩放后的椭球；
- 动态磁干扰：电机、电源线和大电流负载随工况改变磁场。

简单的最大—最小法可以估计每轴偏置与尺度，覆盖充分时再使用椭球拟合估计三维偏置和校正矩阵。磁场模长或方向明显偏离当地正常范围时，融合器应拒绝或降低磁力计权重。

磁力计的数据误差较大，校准便显得很重要。一般可以导出数据到matlab中然后采用椭球校准的方法，但是这样比较麻烦，主要用于写论文。。。而大多数飞控的做法都是直接在单片机上处理的，步骤如下：先头朝上，水平旋转一周，然后头朝下，再水平旋转一周。若计算能力有限，可直接求最大最小数据的中值，得到偏差，然后计算幅值。
save.mag_offset[i] = 0.5f *(max_t[i] + min_t[i]);//中值校准
save.mag_gain[i] = safe_div(200.0f ,(0.5f *(max_t[i] - min_t[i])),0);//幅值校准
若计算能力较充裕，采用LM算法可计算出三维的偏差和三维的尺度因子，具体参考天穹飞控代码。

## 2. 坐标系、单位与姿态表示

### 2.1 坐标系约定

系统至少涉及传感器坐标系 $S$、肢体段坐标系 $B$ 和世界坐标系 $W$。安装外参 B`R_S 用于把每颗 IMU 的测量统一到对应肢体段：

$$
{}^{B}\boldsymbol\omega = {}^{B}\mathbf R_S\,{}^{S}\boldsymbol\omega
$$

文档和数据文件必须明确：

- 右手系还是左手系；
- 轴方向，例如 x  前、y 左、z 上；
- 旋转是主动旋转还是坐标变换；
- 四元数顺序是 `[w, x, y, z]` 还是 `[x, y, z, w]`；
- 角速度使用 `rad/s` 还是 `deg/s`，角度使用弧度还是度。

内部计算建议统一使用 SI 单位和弧度，只在界面显示或设备协议边界转换。

### 2.2 欧拉角、旋转矩阵与四元数

欧拉角直观，但旋转顺序不同会得到不同结果，并在特定姿态出现万向节锁（gimbal lock）。这不是程序线程意义上的“死锁”，而是欧拉角参数化在该姿态附近丢失一个独立旋转方向。

旋转矩阵没有奇异性，但需要 9 个数并维护正交约束。单位四元数仅需 4 个数，适合连续积分与插值，是姿态融合的常用内部表示；对外显示时再转换成 roll、pitch、yaw。

单位四元数必须满足：

$$
\lVert\mathbf q\rVert=1
$$

数值积分后应归一化，防止浮点误差逐步破坏旋转性质。

![欧拉角](https://i-blog.csdnimg.cn/blog_migrate/0ade395a3c0a596f7b3bcc9773a0922d.gif)

四元数与三维旋转：https://github.com/Krasjet/quaternion

### 2.3 **插值：**

数据检测时是离散的，插值可以预测两点之间连续的运动轨迹

对于 IMU 姿态，比较标准的方法是：

SLERP（Spherical Linear Interpolation，球面线性插值）

它不是简单地在四元数的四个分量之间画直线，而是在单位四元数球面上进行插值。

公式：q(t)=sinθsin((1−t)θ)q0+sinθsin(tθ)q1

其中：θ=arccos(q0⋅q1)

这样得到的结果始终沿着两个姿态之间的合理旋转路径变化。

 SLERP 还存在一个实际问题

当：q0⋅q1≈1

说明两个姿态非常接近。

此时：sinθ≈0

公式里面会出现：sinθ1

可能产生数值不稳定。

所以工程代码一般会设置一个阈值。

例如：

```
if (dot > 0.9995f) {
    // 两个四元数非常接近
    // 改用线性插值
    q = q0 + t * (q1 - q0);
    normalize(q);
}
else {
    // 正常使用 SLERP
}
```

## 3. 标定体系

### 3.1 内参标定模型

对加速度计或陀螺仪，可用统一线性模型表示主要确定性误差：

$$
\mathbf y_c = \mathbf M(\mathbf y_m-\mathbf b)
$$

其中 $\mathbf b$ 是零偏，$\mathbf M$ 同时描述尺度因子、轴不正交和轴间耦合。若只进行简单校正，$\mathbf M$ 可取对角矩阵；精度要求较高时再估计完整 $3\times3$ 矩阵。

<img width="400" height="130" alt="image" src="https://github.com/user-attachments/assets/ac3c17fc-d6e9-4d8a-835a-e199b3a82583" />

建议分层完成标定：

1. **静态零偏**：上电静置，剔除启动瞬态后计算均值。

   Bias（偏置/零偏）可以理解成：

   > **传感器在“真实值为 0”时，仍然测出来的一个固定误差。**

   举个最直观的例子

   把 MPU-6500 放在桌子上，完全不动。

   理论上：

   Gx=Gy=Gz=0∘/s

   但实际可能读到：

   ```
   Gx = 0.08 °/s
   Gy = -0.12 °/s
   Gz = 0.15 °/s
   ```

   这些不应该存在的数值，就是 Gyroscope Bias（陀螺仪零偏）。可以使用互补滤波解决。

2. **尺度与轴误差**：加速度计执行六位置标定；陀螺仪使用已知角速度转台或已知角度旋转。

   <p align="center">
   <img width="300" height="226" alt="image" src="https://github.com/user-attachments/assets/8f2b4d17-7d9e-4efd-a43d-42e333b93585" />
   <img width="400" height="230" alt="image" src="https://github.com/user-attachments/assets/d67a54eb-baab-4da4-80e0-5802f1d76577" />
   </p>

3. **磁力计椭球标定**：在真实安装状态下覆盖尽可能多方向采样。

4. **温度补偿**：在目标温度范围内分段采样，拟合每轴零偏与尺度。

5. **随机误差分析**：用长时间静态数据计算 Allan deviation，为滤波器噪声参数提供依据。

原笔记中提到将器件恒温在约 50 ℃，这是部分高稳定系统的硬件方案，不应作为通用默认做法。是否恒温取决于器件额定温度、功耗、封装和系统热设计。

### 3.2 安装外参与人体零位

内参标定解决“传感器测得准不准”，外参标定解决“传感器坐标轴与被测肢体是否一致”。多 IMU 手套中，每颗传感器安装方向略有不同，必须估计 ${}^{B}\mathbf R_S$。

常用方法是在规定手型下记录静态姿态，将该姿态定义为人体段零位；随后所有输出相对该零位表达。若需要与相机或机器人融合，还需标定 IMU/手套坐标系到相机、机器人基座或世界坐标系的刚体变换。

<img width="400" height="250" alt="image" src="https://github.com/user-attachments/assets/ebe3f7fb-a5af-40c5-acd6-c01c9c58e345" />

### 3.3 标定结果管理

仓库中的可视化工具使用以下校正：

```python
corrected_deg = matrix @ ((raw_deg - bias_deg) * scale)
```

对应实现见 [`AngleCalibration.apply`](../code/Arduino/tools/imu_visualizer.py)，示例配置见 [`imu_calibration.example.json`](../code/Arduino/tools/imu_calibration.example.json)。三个参数含义为：

- `bias_deg`：每轴角度零偏；
- `scale`：每轴比例系数；
- `matrix`：轴交换、符号修正或小角度安装补偿矩阵。

该模型适合显示层与简单安装修正。若要校正原始惯性数据，建议在滤波前分别对加速度、角速度和磁场应用各自的标定参数，不要只校正最终欧拉角。

配置文件还应增加设备序列号、量程、采样率、标定日期、温度范围、算法版本和校验和，避免传感器与标定文件错配。

<img width="600" height="300" alt="image" src="https://github.com/user-attachments/assets/293edd33-2944-44cf-b38e-690e098fe342" />

## 4. 姿态融合方法

### 4.1 互补滤波

互补滤波利用陀螺仪的短时动态性能与加速度计的长期重力参考：

```cpp
gyro_angle += gyro_rate * dt;
acc_angle = atan2(acc_y, acc_z);
angle = alpha * gyro_angle + (1.0f - alpha) * acc_angle;
```

`alpha` 越大，响应更接近陀螺仪，动态平滑但漂移修正慢；`alpha` 越小，加速度计修正更强，但线性加速度和振动更容易进入姿态。`alpha = 0.98` 只是常见起点，必须结合采样周期和目标截止频率确定，不能脱离 `dt` 固定照搬。

### 4.2 Mahony 滤波

mahony主要把

```
陀螺仪 Gyroscope
        +
加速度计 Accelerometer
        ↓
    Mahony Filter
        ↓
     四元数
        ↓
 Roll / Pitch / Yaw
```

简单理解：

> **陀螺仪负责“跟踪我转了多少”，加速度计负责“告诉我重力方向有没有偏”，Mahony 用两者不断纠正姿态。**

Mahony 滤波从当前姿态预测重力或磁场方向，并与测量方向比较。方向误差常用叉积表示：
$$
\mathbf e = \hat{\mathbf v}\times\mathbf v_m
$$

再用比例—积分反馈修正角速度：

$$
\boldsymbol\omega_c =
\boldsymbol\omega_m + K_p\mathbf e + K_i\int\mathbf e\,dt
$$

修正后的角速度用于更新四元数。K_p 控制姿态误差的收敛速度，过大会放大振动；K_i 用于补偿稳定零偏，过大会造成慢振荡或在错误观测下积累。调参时一般先令 K_i=0 调整 K_p，再逐步加入较小的 K_i，同时设置积分限幅和观测失效时的抗饱和策略。

简单理解：

- Kp 太小 → 漂移修正慢
- Kp 太大 → 容易抖动
- Ki 太小 → 长期零偏修正弱
- Ki 太大 → 可能产生震荡或不稳定

所以通常先：

> **调整 Kp，再慢慢调整 Ki。**

为什么需要 Mahony？可以抑制积分漂移。

**短期漂移靠校准和滤波，姿态漂移靠 Mahony/Madgwick 融合，长期 Yaw 漂移必须增加绝对方向参考**

### 4.3 Madgwick 滤波

Madgwick 使用梯度下降最小化由重力和磁场方向形成的目标函数。它实现紧凑、计算量可控，常用于资源有限的嵌入式系统。其增益同样需要根据采样率、陀螺仪噪声和运动强度调整。

Mahony 与 Madgwick 都不能凭空获得不可观测信息：六轴 IMU 的 roll/pitch 可由重力长期约束，但 yaw 没有绝对参考，仍会随陀螺仪零偏漂移。长期稳定航向需要磁力计、视觉、光学定位、双天线 GNSS 或其他绝对方向观测。

### 4.4 卡尔曼滤波与误差状态滤波

卡尔曼方法通过状态模型预测，再用观测修正。实际系统常把姿态、陀螺仪零偏、速度和位置纳入状态，并使用误差状态扩展卡尔曼滤波（ESKF）保持旋转更新的数值稳定性。

是否使用 EKF 不应只取决于“算法更高级”。当系统需要融合视觉位置、磁场、零偏和多种异步观测，并且能够建立合理的噪声模型时，EKF/ESKF 更合适；只有姿态输出且计算资源有限时，Mahony、Madgwick 或互补滤波往往更易验证和维护。

直观的动画：https://nbviewer.jupyter.org/github/rlabbe/Kalman-and-Bayesian-Filters-in-Python/blob/master/table_of_contents.ipynb 

## 5. 滤波、插值与时间处理

> **常见：加速度：低通；角速度：高通；磁力：低通**

### 5.1 原始信号滤波

“加速度低通、角速度高通、磁力低通”不能作为普遍固定规则。姿态融合本身已利用不同传感器的频率特性，额外滤波应依据噪声频谱和控制带宽设计：

- 加速度和磁力计可使用低通抑制高频噪声，但需评估相位延迟；
- 角速度通常也需要抗混叠和适度低通，而不是简单高通，否则低速真实运动会被削弱；
- 对脉冲异常可使用限幅、中值滤波或创新检验；
- 所有数字滤波参数都应与实际采样频率绑定。

### 5.2 时间步长

姿态积分对 `dt` 非常敏感。应使用单调时钟记录每次真实采样时刻，不应假定串口输出或主循环严格等周期。若出现丢包或长间隔，需要限制最大 `dt`，并对该段结果标记低置信度。

### 5.3 四元数 SLERP

两个单位四元数 $\mathbf q_0$、$\mathbf q_1$ 之间的球面线性插值为：

$$
\operatorname{slerp}(\mathbf q_0,\mathbf q_1;t)
=
\frac{\sin((1-t)\theta)}{\sin\theta}\mathbf q_0
+
\frac{\sin(t\theta)}{\sin\theta}\mathbf q_1
$$

其中：

$$
\theta=\arccos(\mathbf q_0\cdot\mathbf q_1),\qquad 0\le t\le1
$$

工程实现需要两项处理：

1. 若点积小于 0，将其中一个四元数取反，以选择较短旋转路径； q 与 -q 表示同一姿态。
2. 若点积非常接近 1，例如大于 `0.9995`，改用归一化线性插值，避免 sin\theta 接近 0 导致数值不稳定。

## 6. JY901 系列与 Arduino 实操记录

### 6.1 **jy901官方手册**

https://www.wit-motion.cn/proztmz/22.html

https://github.com/WITMOTION/WitStandardProtocol_JY901/blob/main/Arduino/README.md

https://github.com/WITMOTION/WitStandardProtocol_JY901/blob/main/Arduino/README.md

https://wit-motion.yuque.com/wumwnr/ltst03/rqyk6g?singleDoc#

### 6.2 imu与jy901相关视频

【基于IMU与视觉的手部追踪】 https://www.bilibili.com/video/BV1eA41167cJ/?share_source=copy_web&vd_source=e4b130c78a33c632b8aa35c61ff71a3b

【角度传感器JY901S，比MPU6050好用】 https://www.bilibili.com/video/BV1fpMqzrEjN/?share_source=copy_web&vd_source=e4b130c78a33c632b8aa35c61ff71a3b

【传感器模块JY901P通过串口指令进行磁场校准】 https://www.bilibili.com/video/BV14VCEBBEmZ/?share_source=copy_web&vd_source=e4b130c78a33c632b8aa35c61ff71a3b

【【2026电赛】TI MSPM0快速入门课 - 维特陀螺仪（JY61P/JY901S/……）】 https://www.bilibili.com/video/BV17YTYzsEgv/?share_source=copy_web&vd_source=e4b130c78a33c632b8aa35c61ff71a3b

https://github.com/xioTechnologies/Gait-Tracking-With-x-IMU

### 6.3 实际测试

官方代码，进行优化可检测多传感器数据
<img width="2159" height="465" alt="image-20260818115704151" src="https://github.com/user-attachments/assets/e2a09249-605c-471f-900e-60192f7f8b0a" />

写了个demo使其展示进行观察

<img width="2105" height="500" alt="image-20260818154121214" src="https://github.com/user-attachments/assets/a8da4e6f-37cd-4723-8669-e5cfd85caf9e" />

### 6.4 学习代码

数据换算位置在 [wit_c_sdk_iic.ino (line 65)](D:/Desktop/Arduino/Arduino_sdk/examples/wit_c_sdk_iic/wit_c_sdk_iic.ino:65)：

```c++
fAcc[i] = (int16_t)sReg[AX+i] / 32768.0f * 16.0f;
fGyro[i] = (int16_t)sReg[GX+i] / 32768.0f * 2000.0f;
fAngle[i] = (int16_t)sReg[Roll+i] / 32768.0f * 180.0f;
```

传感器扫描在 [AutoScanSensor (line 280)](D:/Desktop/Arduino/Arduino_sdk/examples/wit_c_sdk_iic/wit_c_sdk_iic.ino:280)，它从 `0x01` 扫到 `0x7E`，最多记录两颗传感器：

```
#define MAX_SENSOR_NUM  2
```

所以现在这个 I2C 示例是支持双 IMU 的。

真正写寄存器的位置在 SDK：

- 加速度/陀螺仪标定开始：[WitStartAccCali (line 388)](D:/Desktop/Arduino/Arduino_sdk/wit_c_sdk.c:388)
  写 `CALSW = CALGYROACC`
- 加速度/陀螺仪标定停止并保存：[WitStopAccCali (line 402)](D:/Desktop/Arduino/Arduino_sdk/wit_c_sdk.c:402)
  写 `CALSW = NORMAL`，再写 `SAVE = SAVE_PARAM`
- 磁场标定开始：[WitStartMagCali (line 410)](D:/Desktop/Arduino/Arduino_sdk/wit_c_sdk.c:410)
  写 `CALSW = CALMAGMM`
- 磁场标定结束：[WitStopMagCali (line 421)](D:/Desktop/Arduino/Arduino_sdk/wit_c_sdk.c:421)
  写 `CALSW = NORMAL`

姿态算法模式寄存器在 [REG.h (line 162)](D:/Desktop/Arduino/Arduino_sdk/REG.h:162)：

```
#define ALGRITHM9 0
#define ALGRITHM6 1
```

它对应 `AXIS6` 寄存器：

```
#define AXIS6 0x24
```

含义通常是：

- `ALGRITHM9`：九轴算法，融合加速度、陀螺仪、磁力计
- `ALGRITHM6`：六轴算法，融合加速度、陀螺仪，不依赖磁力计修正航向

角度校准类在 [AngleCalibration (line 43)](D:/Desktop/Arduino/tools/imu_visualizer.py:43)，应用公式在 [apply (line 56)](D:/Desktop/Arduino/tools/imu_visualizer.py:56)：

```
return self.matrix @ ((raw_deg - self.bias_deg) * self.scale)
```

也就是：

1. 减角度零偏 `bias_deg`
2. 乘比例系数 `scale`
3. 乘 3x3 矩阵 `matrix`

配置文件在 [imu_calibration.example.json (line 1)](D:/Desktop/Arduino/tools/imu_calibration.example.json:1)，里面每个 IMU 都有：

```
"bias_deg": [0.0, 0.0, 0.0],
"scale": [1.0, 1.0, 1.0],
"matrix": [[1,0,0],[0,1,0],[0,0,1]]
```

## 7. 参考资料

- [JY901 官方资料入口](https://www.wit-motion.cn/proztmz/22.html)
- [WITMOTION Arduino 标准协议示例](https://github.com/WITMOTION/WitStandardProtocol_JY901/blob/main/Arduino/README.md)
- [仓库内 Arduino SDK 说明](../code/Arduino/README.md)
- [Gait Tracking With x-IMU](https://github.com/xioTechnologies/Gait-Tracking-With-x-IMU)
- [Quaternion and 3D rotation notes](https://github.com/Krasjet/quaternion)
- 卡尔曼滤波：[Kalman and Bayesian Filters in Python](https://github.com/rlabbe/Kalman-and-Bayesian-Filters-in-Python)
- 数据标定参考视频： https://www.bilibili.com/video/BV13d4y177rZ/?share_source=copy_web&vd_source=e4b130c78a33c632b8aa35c61ff71a3b

## 8. 结论

IMU 姿态解算的主要难点不是把某个滤波公式写进程序，而是保证输入数据具备正确的时间、单位、坐标和标定。六轴系统无法长期观测绝对航向，磁力计也不是在所有环境中都可信。对于 11 颗 IMU 的可穿戴系统，应先建立可追踪的原始数据与标定体系，再逐步增加融合、关节约束和遥操作映射，这样问题才能被定位、复现和量化。

