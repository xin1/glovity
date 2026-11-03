# STM32F103C8T6 蓝色小板：一组 UART 轮询两只 IMU761

适用条件：两只 IMU 已刷入瑞芬的**按地址查询、不自动输出**固件，地址分别为 `01`、`02`；此前已分别用 USB-TTL 在 115200、8N1 下查询成功。程序使用 IMU 的查询命令：

| 目标 | HEX 命令 | 校验和 |
|---|---|---|
| 01 | `68 04 01 03 08` | `04+01+03=08` |
| 02 | `68 04 02 03 09` | `04+02+03=09` |

USB-TTL 的成功测试是连接 STM32 前的基准。如果实际新固件使用其他波特率，要同时修改 CubeMX 的 USART1 配置；电脑监视波特率只需和 USART2 一致。

## 1. 接线

| STM32 蓝色小板 | 连接对象 |
|---|---|
| PA9 / USART1_TX | 分别接两只 IMU 的 RXD |
| PA10 / USART1_RX | 接两只 IMU TX 二极管合并后的公共线 |
| GND | 两只 IMU 的 GND，以及 USB-TTL GND |
| PA2 / USART2_TX | USB-TTL 的 RXD，用来在电脑上看结果 |

每只 IMU TX 各串一个二极管：**二极管阳极（无色环端）接 PA10 公共线，阴极（有色环端）接该 IMU 的 TXD**。PA10 公共线通过 **4.7 kΩ 电阻上拉到 3.3 V**。二极管推荐 BAT54/1N5819 等肖特基管；已经用 USB-TTL 成功的二极管和上拉电路可原样沿用。主机 PA9 到两只 IMU RXD 直接并联；不要把两只 IMU TXD 直接硬并联。USART2 的 PA3 和 USB-TTL TXD 此示例不用接。

两只 IMU 需要符合其规格的稳定电源，全部设备必须共地。STM32 PA9/PA10 是 3.3 V 逻辑；如 IMU 的 UART 引脚不是 3.3 V 逻辑，先做电平转换。不要假定蓝色小板板载 3.3 V 稳压器能带动两只 IMU；建议按 IMU 实际供电规格用单独稳压电源。接线时先断电，确认电源极性、二极管方向和公共线确实被拉到 3.3 V，再通电。

## 2. STM32CubeMX / STM32CubeIDE 逐项配置

1. 新建 **STM32 Project**，MCU 搜索 `STM32F103C8Tx`（对应 F103C8T6），Project Manager / Toolchain 选 `STM32CubeIDE`，HAL 库。
2. **System Core → SYS**：Debug 设为 `Serial Wire`，保留 SWD 下载线 PA13/PA14。
3. **System Core → RCC**：常见蓝色小板外部晶振为 8 MHz，此时 HSE 设为 `Crystal/Ceramic Resonator`。检查你的小板晶振标值；如果没有可靠 HSE，也可用 HSI，时钟树参数要按实际重算。
4. **Clock Configuration**：HSE=8 MHz、PLL Source=HSE、PLL Mul=×9、SYSCLK=72 MHz、AHB=72 MHz、APB1 Prescaler=/2（36 MHz）、APB2=/1（72 MHz）。让 CubeMX 自动处理 flash latency 和 ADC 时钟约束。
5. **Connectivity → USART1**：Mode=`Asynchronous`。Parameter Settings：Baud Rate=`115200`，Word Length=`8 Bits`，Parity=`None`，Stop Bits=`1`，Hardware Flow Control=`None`，Oversampling=`16`。确认 Pinout 为 `PA9 USART1_TX` 和 `PA10 USART1_RX`。**NVIC Settings 勾选 USART1 global interrupt**。
6. **Connectivity → USART2**：同样设置 `Asynchronous / 115200 / 8 Bits / None / 1 / None`，确认 `PA2 USART2_TX`。PA3 可以保留为 USART2_RX 而悬空；本程序不启用 USART2 接收中断。
7. Project Manager → Code Generator：建议勾选 `Generate peripheral initialization as a pair of '.c/.h' files per peripheral`，这样生成 `usart.c`/`usart.h`；不勾也可以，下面的 `main.c` 接法相同。生成代码。

USART1 的 115200 是 STM32 与 IMU 之间的波特率，USART2 的 115200 是 STM32 与电脑调试串口之间的波特率。两者可以相同，但属于两条独立 UART。电脑串口助手选 USB-TTL 所在端口、115200、8N1；显示选文本/ASCII，并开启接收换行显示。STM32 下载可用 ST-LINK SWD；下载口与 IMU 串口互不占用。

## 3. 放入代码

把本目录的 `Core/Inc/imu761_protocol.h`、`imu761_poll.h` 复制进生成工程的 `Core/Inc/`；把 `Core/Src/imu761_protocol.c`、`imu761_poll.c` 复制进 `Core/Src/`。在 STM32CubeIDE 中刷新工程，确认四个文件进入编译。

在生成工程 `main.c` 的 `/* USER CODE BEGIN Includes */` 中加入：

```c
#include "imu761_poll.h"
```

在 `main()` 的 `/* USER CODE BEGIN 2 */` 中、所有 `MX_..._Init()` 调用结束后加入：

```c
imu761_poll_init(&huart1, &huart2);
imu761_poll_run();
```

在 `main.c` 的 `/* USER CODE BEGIN 4 */` 区域加入 HAL 回调：

```c
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    imu761_poll_rx_callback(huart);
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    imu761_poll_error_callback(huart);
}
```

若你的工程已有这两个回调函数，就在已有函数内调用对应的 `imu761_poll_...` 函数，不要重复定义。确认 CubeMX 生成的 `stm32f1xx_it.c` 中 `USART1_IRQHandler()` 调用了 `HAL_UART_IRQHandler(&huart1)`；这是接收中断能工作的关键。`imu761_poll_run()` 不返回，生成的 `while(1)` 不执行，属于预期。

## 4. 运行与排查

上电后程序先等 3 秒让 IMU 启动，再监听公共 RX 线 1 秒，要求查询模式下没有自行输出；随后依次发送 01、02 的查询命令。每次只等待一个目标的 82 字节 HI91 回包，检查 `5A A5 4C 00` 帧头、类型 `91`、CRC 和地址，再输出一次角度。正常日志示例：

```text
IMU761 STM32 query 01/02; both UARTs 115200 8N1
ID=01 OK t=12006 roll=1.234 pitch=-2.345 yaw=90.000 rx=82 crc_bad=0
ID=02 OK t=12009 roll=0.123 pitch=1.456 yaw=89.987 rx=82 crc_bad=0
```

出现 `STOP` 时停止轮询，复位 STM32 后重试。这是为了避免超时后的迟到回包与下一只设备的查询重叠。日志里 `rx=0` 指 USART1 根本没有收到字节，优先查 PA9/PA10 与 IMU TX/RX 是否交叉、共地、供电、二极管方向、上拉、波特率，并可先只接一只 IMU 测试。`rx>0` 但超时可查噪声、串口格式、CRC 或固件回包格式。`wrong address` 表示收到了 CRC 正确但地址不是当前查询目标，需检查固件地址和非目标设备是否保持静默。

协议解析按此前实测的 82 字节 HI91 回包格式实现：第 10 字节为地址（零起始偏移 10），第 14–17 字节为小端时间戳，第 54/58/62 字节开始分别是小端 float 的 roll/pitch/yaw；CRC16 多项式 0x1021、初值 0，对前 4 字节和后 76 字节连续计算，帧中第 4–5 字节为小端 CRC。若新固件改变回包结构，需要同步修改 `imu761_protocol.c`。
