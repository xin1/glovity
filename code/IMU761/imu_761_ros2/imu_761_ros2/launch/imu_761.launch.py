from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    pkg_dir = get_package_share_directory('imu_761_ros2')
    param_file = os.path.join(pkg_dir, 'cfg', 'param.yaml')

    return LaunchDescription([
        Node(
            package='imu_761_ros2',
            executable='imu_761_node',
            name='imu_761_node',
            output='screen',
            parameters=[param_file],
        ),
    ])