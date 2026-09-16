# imu_761 ROS2 串口驱动

`imu_761_ros2` 是面向 **761** 的 ROS2 串口驱动包(C++)。该驱动包通过执行ROS命令，启动驱动节点,该节点与硬件建立连接后将设备的数据包转化为标准ROS消息和自定义消息发布到话题上。

---

## 测试环境

| 项目 | 说明 |
|------|------|
| 操作系统 | Ubuntu 22.04 LTS |
| ROS | ROS2 **Humble** |
| 设备 |  761  |

Ubuntu系统通常自带CP210X驱动，默认不需要串口驱动。将设备接到电脑时，虚拟机会弹出<检测到新的USB设备>,选择连接到虚拟机，再选中对应的虚拟机点击确认。

查看被Ubuntu识别的USB-UART设备
1. ctrl + alt + t 打开终端后，` linux@ubuntu:~$ ls /dev ` (查看已存在的串口设备)

```shell
input            sg2           tty31   tty63      ttyS8    zero
kmsg             shm           tty32   tty7       ttyS9    zfs
log              snapshot      tty33   tty8       udmabuf
```


2.插入USB线，设备连接之后，再次执行` linux@ubuntu:~$ ls /dev `。确认dev目录下新增的设备号`ttyUSBX`，X看具体数值。

```shell
log              shm           tty32   tty7       ttyS9    zero
loop0            snapshot      tty33   tty8      `ttyUSB0`  zfs
loop1            snd           tty34   tty9       udmabuf
```

3.打开USB设备的可执行权限


临时调试(重新拔插后，权限恢复，需要重新chmod)
```shell
linux@ubuntu:~$ sudo chmod 777 /dev/ttyUSB0
```

长期调试(输入后，完全退出当前用户会话再登入或重启)
```shell
linux@ubuntu:~$ sudo usermod -aG dialout $USER
```

如果需要把该长期调试改动移除，完全退出当前用户会话再登入或重启
```shell
linux@ubuntu:~$ sudo deluser $USER dialout
```



## How to Install（编译安装）

本文不再赘述ROS的环境搭建和相关编译器的安装。
Ubuntu22.04安装ROS Humble具体参考`https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html`


### 1. 创建工作空间
此处以创建ros2_ws为例
```bash
linux@ubuntu:~$ mkdir -p ~/ros2_ws/src
```
此时工作空间目录如下:
~/ros2_ws/
└── src/


### 2. 放入 ros2 工作空间

将 驱动包`imu_761_ros2` 置于工作空间 `src/` 下，例如：

```shell
~/ros2_ws/
└── src/
    └── imu_761_ros2/
```

### 3. 编译

```bash
linux@ubuntu:~$ cd ~/ros2_ws
linux@ubuntu:~/ros2_ws$ colcon build
```
编译成功后出现完成度100%的信息。

### 4. 加载环境

**每个新终端都需要执行：**

```bash
linux@ubuntu:~$ source ~/ros2_ws/install/setup.bash
```


### 5. 确认包可见

```bash
ros2 pkg prefix imu_761_ros2
```

---

## 设备说明

本节点 **只负责读串口并发布 ROS 话题**。

该驱动默认启用的端口号为/dev/ttyUSB0，默认波特率115200，imu_topic/package_topic后为对应的话题名，imu_enable/package_enable的值true/false为使能或失能该话题。

设备原始坐标轴为右前上，如需转化前左上 `use_ros_flu: true`即可

### 示例 `param.yaml`

```yaml
imu_761_node:
  ros__parameters:
    port: /dev/ttyUSB0
    baud_rate: 115200
    frame_id: imu_link
    imu_data_show_flag: 0

    imu_enable: true
    imu_topic: imu/data

    package_enable: true
    package_topic: imu/package

    use_ros_flu: true
```

---
其中port、baud_rate根据实际设备的参数填入。如需自定义话题，自行改动。
---


## How to Use（运行）

### 1. 启动驱动

```bash
linux@ubuntu:~$ source ~/ros2_ws/install/setup.bash
linux@ubuntu:~$ ros2 launch imu_761_ros2 imu_761.launch.py
```

成功时终端示例：

```text
[imu_761_node-1] [INFO] [1780646349.490498588] [imu_761_node]: port = /dev/ttyUSB0
[imu_761_node-1] [INFO] [1780646349.490577041] [imu_761_node]: baud_rate = 115200
[imu_761_node-1] [INFO] [1780646349.490585638] [imu_761_node]: frame_id = imu_link
[imu_761_node-1] [INFO] [1780646349.490593875] [imu_761_node]: imu_enable = 1, imu_topic = imu/data
[imu_761_node-1] [INFO] [1780646349.490600409] [imu_761_node]: package_enable = 1, package_topic = imu/package
[imu_761_node-1] [INFO] [1780646349.490606479] [imu_761_node]: use_ros_flu = 1 (1=ROS FLU, 0=device RFU)
[imu_761_node-1] [INFO] [1780646351.499237399] [imu_761_node]: Serial open success!
```

若 `imu_data_show_flag: 1`，解析成功时会打印 `IMU frame OK`。

### 2. 查看话题与频率

另开新终端，先加载环境
```bash
linux@ubuntu:~$ source ~/ros2_ws/install/setup.bash
```

```bash
看标准ROS数据
linux@ubuntu:~$ ros2 topic echo /imu/data
```
成功示例：

header:
  stamp:
    sec: 1780646450
    nanosec: 654100818
  frame_id: imu_link
orientation:
  x: -0.0016561419470235723
  y: -0.02378166466951371
  z: 0.9605666995048525
  w: 0.27702572941780096
orientation_covariance:
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
angular_velocity:
  x: -0.019750537356840247
  y: -0.007109763660035632
  z: -0.011996328764451522
angular_velocity_covariance:
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
linear_acceleration:
  x: 0.029177149909222495
  y: -0.27516442848863076
  z: 9.75603098887503
linear_acceleration_covariance:
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
- 0.0
---

```bash
看设备自定义数据
linux@ubuntu:~$ ros2 topic echo /imu/package
```
成功示例：

header:
  stamp:
    sec: 1780646474
    nanosec: 305244128
  frame_id: imu_link
tag: '0x91'
main_status: '0x1C18'
temperature: 34
pressure: 0.0
timestamp_ms: 153639
acc_x: 0.0029604120645672083
acc_y: -0.02953118458390236
acc_z: 0.9993740320205688
gyr_x: -1.3090640306472778
gyr_y: -0.46431398391723633
gyr_z: -0.7492037415504456
mag_x: -14.400390625
mag_y: -7.76171875
mag_z: -15.164515495300293
roll: -2.7676007747650146
pitch: -0.6236550211906433
yaw: 149.20828247070312
quat_w: 0.2655317783355713
quat_x: -0.0011657187715172768
quat_y: -0.024727024137973785
quat_z: 0.9637842774391174
---


```bash
linux@ubuntu:~$ ros2 topic list | grep imu    //列出当前所有活跃的 ROS 话题，过滤出包含 imu 字样的话题名
linux@ubuntu:~$ ros2 topic type /imu/data     //查看 /imu/data 话题的消息类型
linux@ubuntu:~$ ros2 topic type /imu/package //查看 /imu/package 话题的消息类型
linux@ubuntu:~$ ros2 topic hz /imu/data       //统计 /imu/data 话题的发布频率
linux@ubuntu:~$ ros2 topic hz /imu/package   //统计 /imu/package 话题的发布频率
linux@ubuntu:~$ ros2 topic echo /imu/data --once    //输出 /imu/data 话题的 1 条最新消息内容
linux@ubuntu:~$ ros2 topic echo /imu/package --once    //输出 /imu/package 话题的 1 条最新消息内容
linux@ubuntu:~$ ros2 interface show imu_761_ros2/msg/ImuPackage  //查看自定义消息定义
```

---

### 3. 查看rviz可视化

1.打开终端1
```bash
linux@ubuntu:~$ source ~/ros2_ws/install/setup.bash
linux@ubuntu:~$ ros2 launch imu_761_ros2 imu_761.launch.py
```
看到 Serial open success! 即可，确认波特率与产品一致

2.打开终端2
```bash
linux@ubuntu:~$ source ~/ros2_ws/install/setup.bash
linux@ubuntu:~$ ros2 run tf2_ros static_transform_publisher --frame-id fixed_frame --child-frame-id imu_link
```

3.打开终端3
```bash
linux@ubuntu:~$ sudo apt install ros-humble-imu-tools 
linux@ubuntu:~$ source ~/ros2_ws/install/setup.bash
linux@ubuntu:~$ rviz2
```

4.进入rviz2界面内，点击左下角Add，在By display type内找到rviz_imu_plugin内的Imu，选中后点击OK。在左侧展示中，Global Options中的Fixed Frame选中imu_link；IMU中Topic选中/imu/data,Box properties中勾选Enable box即可看到方块跟随数据运动。