# LRA 可视化强度控制

这个版本使用 Arduino Uno/Nano 作为桥接控制器：浏览器通过 USB 串口向 Arduino 发送强度，Arduino 通过 UART 配置 JW-BLE-DL500，并使用 IO1 的低电平连续触发功能控制启停。

## 接线

| Arduino Uno/Nano | LRA 模块 | 说明 |
|---|---|---|
| D10 | TX | Arduino 软件串口 RX |
| D11 | RX | 建议使用 1k/2k 电阻分压，将 5V 降至约 3.3V |
| D6 | IO1 | 连续振动触发，程序以开漏方式使用 |
| GND | GND | 必须共地 |
| 外部 5V/1A | VIN | 不建议由 Arduino 3.3V 引脚供电 |

马达只能接 `LRA+` 与 `LRA-`，不能接地。

注意：分压只接在 `Arduino D11 -> LRA RX` 方向。`LRA TX -> Arduino D10`
应直接连接，不要再分压；否则 3.3V 信号会被继续降低，容易出现 `M�U_MODE`、
`O�` 等随机乱码。连接线尽量短，并确保两个设备可靠共地。

## 烧录 Arduino

使用 Arduino IDE 打开 `lra_visual_control.ino`，选择 Arduino Uno 或 Nano 后上传。

程序参数：

- Arduino USB 串口：115200 baud
- Arduino 到 LRA：9600 baud
- 实测 F0：170 Hz

## 振动模式

右侧五个按钮用于选择模式，选择后在左侧压力区域操作：

- 短振：默认 150 Hz、AMP 38，单次 60 ms；按下后锁定纵向位置，只识别横向移动
- 长振：默认 130 Hz、AMP 25，最短持续 3 s；按住超过 3 s 后，按多久就持续多久；按下后只识别横向移动
- 锯齿：固定实测 F0 170 Hz，高度控制峰值振幅；每轮按五级渐强后断开 40 ms，共播放两轮
- 摩擦：最底部固定 50 Hz，最顶部固定 170 Hz；鼠标越往上频率和振幅越大，只由垂直位置决定，与移动速度无关
- 碰撞：只有按下位置对应的模拟压力达到 6 N 才触发；默认 170 Hz、AMP 33、28 ms；按下后只识别横向移动

## 可编辑频率和振幅

网页“输出参数”区域可修改：

- 频率：20—500 Hz
- 最大振幅：AMP 25—47，对应约 1.83—3.26 Vpp

修改后点击“应用到设备”。网页发送 `PARAM 频率 最大振幅`，Arduino 再通过
`SET_FREQ`、`SET_AMP1` 和 `ACTION` 将参数写入 LRA。最大振幅同时作为五种预设
效果的输出上限；锯齿各段振幅会按同一比例缩放。

不同模式会载入不同默认值：短振 150 Hz/AMP 38，长振 130 Hz/AMP 25，
锯齿 170 Hz/峰值 AMP 42，摩擦 50—170 Hz/最大 AMP 35，碰撞 170 Hz/AMP 33。
摩擦的频率端点和锯齿的 170 Hz 频率固定，两者的最大振幅仍可编辑。
短振、长振和碰撞使用当前固定的频率与振幅；摩擦和锯齿通过垂直位置调节振幅。
摩擦频率按垂直位置线性映射：最底部 50 Hz、最顶部 170 Hz；频率只由当前位置决定，与变化速度无关。

五种效果使用非阻塞状态机，通过 `D6 -> LRA IO1` 产生和停止振动。模块串口协议
没有单独的停止指令，因此要让“立即停止”真正中断正在播放的效果，必须连接 IO1。

为降低触发延迟，网页会在选择模式时提前发送 `PREP`，先把该模式的频率和振幅
写入模块。真正按下控制区时，如果参数没有变化，Arduino 会直接触发 IO1，不再
重复配置。Arduino 在等待模块回复期间会保留最新控制命令，并把连续产生的
`HOLD` 合并成最新一条，避免串口积压后仍执行过时的鼠标位置。

## 压力阈值

普通鼠标没有真实压力数据，网页暂时把线条高度映射成 `0—10 N`。左侧只显示
压力数值，不再用压力区间自动选择振动类型；振动类型由右侧按钮选择。碰撞是
唯一带硬阈值的模式，必须在 6 N 或更高的位置按下才能触发。短振、长振和碰撞
在按下瞬间记录这个位置，之后锁定纵向输入，因此移动轨迹保持为横线。

## 检测马达 F0

连接设备并等待初始化完成后，点击网页“输出参数”区域的“检测 F0”。检测前应把
马达可靠固定在最终安装位置，检测过程中不要触碰马达。网页会发送 `DETECT_F0`，
Arduino 再向驱动模块发送 `F0_DET`。成功时串口日志和界面会显示类似：

```text
F0_DET,OK!
LRA_F0:170Hz
EVENT F0 170
```

其中 `170 Hz` 就是检测到的谐振频率。驱动模块会自动把当前输出频率调整到检测值；
开始下一种网页振动效果时，程序会再写入该效果需要的频率。

## `MCU_MODE` 无回复时

网页能显示 `LRA controller started`，说明电脑到 Arduino 的 USB 串口已经正常；
`Timeout waiting for LRA response` 表示问题位于 Arduino 和 LRA 模块之间。

请严格按下面接线（不要接 Arduino 的 D0/RX、D1/TX）：

```text
LRA TX  -> Arduino D10
LRA RX  <- Arduino D11
LRA GND -> Arduino GND
LRA VIN -> 3.0-5.0 V 稳定电源
```

上传程序或打开网页串口前，先关闭占用同一个 COM 口的 Arduino 串口监视器。
如果接了 `D6 -> IO1`，排查串口时可以先断开这根线；它不影响参数配置。
发送 `MCU_MODE` 时观察模块指示灯：红灯闪烁表示模块已经收到指令，此时重点检查
`LRA TX -> D10`；完全不闪则重点检查 `D11 -> LRA RX`、模块供电和共地。

如果日志出现 `EVENT WARN RECOVERED_DAMAGED_OK`，表示程序从一次损坏的 `OK!`
回复中恢复并继续运行。偶发一次可以继续使用；如果频繁出现，说明 D10 接收方向
仍有信号质量问题，应检查是否误加分压、杜邦线过长或 GND 接触不良。
- 可调振幅：AMP 25—47，约 1.83—3.26 Vpp
- 网页心跳丢失 800 ms 后自动释放 IO1

## 启动网页

Web Serial 需要 Chrome/Edge，并应从 localhost 打开。进入 `web` 目录后运行：

```powershell
python -m http.server 8000
```

然后用 Chrome 或 Edge 打开：

```text
http://localhost:8000
```

点击“连接设备”，选择对应的 COM 口。打开串口时 Arduino 会自动复位；等待界面显示“设备就绪”后，先在右侧选择振动模式，再在左侧区域操作。

不要同时打开 Arduino IDE 串口监视器和网页，它们不能同时占用同一个 COM 口。

## 浏览器发送协议

```text
FRICTION_START FORCE_X10 MIN_FREQ MAX_FREQ MAX_AMP  按当前位置开始摩擦
FRICTION_HOLD FORCE_X10 MIN_FREQ MAX_FREQ MAX_AMP   更新位置并保持摩擦心跳
START AMP FREQ       兼容旧版的连续振动命令
HOLD AMP FREQ        兼容旧版的连续更新命令
PULSE FREQ AMP MS    播放自定义短振
LONG_START FREQ AMP  开始最短 3 s 的长振
LONG_HOLD FREQ AMP   更新长振并延长持续时间
LONG_RELEASE         松开长振；未到 3 s 时继续到 3 s
SAW_PLAY FREQ AMP    播放固定 170 Hz 锯齿，AMP 为峰值；五级渐强、40 ms 断开，共两轮（FREQ 保留用于兼容）
COLLISION FORCE_X10 FREQ AMP  压力达到 60（即 6.0 N）才播放碰撞短振
STOP                 立即释放 IO1
PARAM FREQ AMP        设置当前模式的基准频率和最大振幅
PREP FREQ AMP         预加载所选模式参数，不触发振动
DETECT_F0             检测马达谐振频率并返回 EVENT F0 频率
CONFIG               重新配置 LRA
```

`FRICTION_START/FRICTION_HOLD` 直接携带当前模拟力度，Arduino 根据力度计算频率和振幅，再转换为 LRA 的 `SET_FREQ`、`SET_AMP1`、数值和 `ACTION` 指令。这样摩擦频率的来源始终是力度，而不是鼠标移动速度或前端缓存的旧频率。
