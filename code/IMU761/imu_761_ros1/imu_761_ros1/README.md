# imu_761 ROS1 串口驱动

`imu_761_ros1` 是面向 **761** 的 ROS1串口驱动包(C++)。该驱动包通过执行ROS命令，启动驱动节点,该节点与硬件建立连接后将设备的数据包转化为标准ROS消息和自定义消息发布到话题上。

---

## 测试环境

| 项目 | 说明 |
|------|------|
| 操作系统 | Ubuntu 20.04 LTS |
| ROS | ROS1 **Noetic** |
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
linux@ubuntu:~$ sudo chmod 666 /dev/ttyUSB0
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
Ubuntu20.04安装ROS Noetic具体参考`https://wiki.ros.org/cn/noetic/Installation/Ubuntu`


### 1. 创建工作空间
此处以创建catkin_ws为例
```bash
linux@ubuntu:~$ mkdir -p ~/catkin_ws/src
```
此时工作空间目录如下:
~/catkin_ws/
└── src/


### 2. 放入 catkin 工作空间

将 驱动包`imu_761_ros1` 置于工作空间 `src/` 下，例如：

```shell
~/catkin_ws/
└── src/
    └── imu_761_ros1/
```

### 3. 编译

```bash
linux@ubuntu:~$ cd ~/catkin_ws
linux@ubuntu:~/catkin_ws$ catkin_make 
```
编译成功后出现完成度100%的信息。

### 4. 加载环境

**每个新终端都需要执行：**

```bash
linux@ubuntu:~$ source ~/catkin_ws/devel/setup.bash
```


### 5. 确认包可见

```bash
rospack find imu_761_ros1
```

---

## 设备说明

本节点 **只负责读串口并发布 ROS 话题**。

该驱动默认启用的端口号为/dev/ttyUSB0，默认波特率115200，imu_topic/package_topic后为对应的话题名，imu_enable/package_enable的值true/false为使能或失能该话题。

设备原始坐标轴为右前上，如需转化前左上 `use_ros_flu: true`即可

### 示例 `param.yaml`

```yaml
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
linux@ubuntu:~$ source ~/catkin_ws/devel/setup.bash
linux@ubuntu:~$ roslaunch imu_761_ros1 imu_761.launch
```

成功时终端示例：

```text
[INFO] [1780643383.609734597]: port = /dev/ttyUSB0
[INFO] [1780643383.610337741]: baud_rate = 115200
[INFO] [1780643383.610354683]: frame_id = imu_link
[INFO] [1780643383.610427807]: imu_enable = 1, imu_topic = imu/data
[INFO] [1780643383.610454590]: package_enable = 1, package_topic = imu/package
[INFO] [1780643383.610464647]: use_ros_flu = 1 (1=ROS FLU, 0=device RFU)
[INFO] [1780643385.299625872]: Serial open success!
```

若 `imu_data_show_flag: 1`，解析成功时会打印 `IMU frame OK`。

### 2. 查看话题与频率

另开新终端，先加载环境
```bash
linux@ubuntu:~$ source ~/catkin_ws/devel/setup.bash
```

```bash
看标准ROS数据
linux@ubuntu:~$ rostopic echo /imu/data
```
成功示例：

header: 
  seq: 19936
  stamp: 
    secs: 1780643584
    nsecs: 666412446
  frame_id: "imu_link"
orientation: 
  x: -0.006136171054095032
  y: -0.020241457968950275
  z: 0.8184463381767274
  w: 0.5741935968399049
orientation_covariance: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
angular_velocity: 
  x: -0.01857752308757963
  y: -0.005958420864145532
  z: -0.009860913615966216
angular_velocity_covariance: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
linear_acceleration: 
  x: 0.0769828937117476
  y: -0.2320358096516691
  z: 9.810309107029438
linear_acceleration_covariance: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
---

```bash
看设备自定义数据
linux@ubuntu:~$ rostopic echo /imu/package
```
成功示例：

header: 
  seq: 77023
  stamp: 
    secs: 1780644438
    nsecs: 285655556
  frame_id: "imu_link"
tag: "0x91"
main_status: "0x1C18"
temperature: 36
pressure: 0.0
timestamp_ms: 1061349
acc_x: 0.005280955228954554
acc_y: -0.022140612825751305
acc_z: 0.9967232942581177
gyr_x: -1.280135154724121
gyr_y: -0.4396737813949585
gyr_z: -0.7501197457313538
mag_x: -15.4140625
mag_y: -7.044921875
mag_z: -26.300708770751953
roll: -2.4260377883911133
pitch: -0.7002925872802734
yaw: 151.63003540039062
quat_w: 0.24511921405792236
quat_x: 0.0007359271985478699
quat_y: -0.022020965814590454
quat_z: 0.9692425727844238
---

```bash
linux@ubuntu:~$ rostopic list | grep imu    //列出当前所有活跃的 ROS 话题，过滤出包含 imu 字样的话题名
linux@ubuntu:~$ rostopic type /imu/data     //查看 /imu/data 话题的消息类型
linux@ubuntu:~$ rostopic type /imu/package //查看 /imu/package 话题的消息类型
linux@ubuntu:~$ rostopic hz /imu/data       //统计 /imu/data 话题的发布频率
linux@ubuntu:~$ rostopic hz /imu/package   //统计 /imu/package 话题的发布频率
linux@ubuntu:~$ rostopic echo /imu/data -n 1    //输出 /imu/data 话题的 1 条最新消息内容
linux@ubuntu:~$ rostopic echo /imu/package -n 1    //输出 /imu/package 话题的 1 条最新消息内容
linux@ubuntu:~$ rosmsg show imu_761_ros1/ImuPackage  //查看自定义消息定义
```

---

### 3. 查看rviz可视化

1.打开终端1
```bash
linux@ubuntu:~$ source ~/catkin_ws/devel/setup.bash
linux@ubuntu:~$ roslaunch imu_761_ros1 imu_761.launch
```
看到 Serial open success! 即可，确认波特率与产品一致

2.打开终端2
```bash
linux@ubuntu:~$ source ~/catkin_ws/devel/setup.bash
linux@ubuntu:~$ rosrun tf static_transform_publisher 0.0 0.0 0.0 0.0 0.0 0.0 fixed_frame imu_link 100
```

3.打开终端3
```bash
linux@ubuntu:~$ sudo apt install ros-noetic-imu-tools 
linux@ubuntu:~$ source ~/catkin_ws/devel/setup.bash
linux@ubuntu:~$ rviz
```

4.进入rviz界面内，点击左下角Add，在By display type内找到rviz_imu_plugin内的Imu，选中后点击OK。在左侧展示中，Global Options中的Fixed Frame选中imu_link；IMU中Topic选中/imu/data,Box properties中勾选Enable box即可看到方块跟随数据运动。