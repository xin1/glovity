import csv
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import plotly.graph_objects as go
from scipy.optimize import least_squares
import json
from pathlib import Path

plt.rcParams['font.sans-serif'] = [
    'Noto Sans CJK SC',    # 首选简体
    'Noto Sans CJK JP',
    'Noto Serif CJK JP',
    'DejaVu Sans'
]
plt.rcParams['axes.unicode_minus'] = False


class MagnetometerCalibration:
    """磁力计校准和可视化类"""
    
    def __init__(self, csv_file):
        """初始化并读取CSV数据"""
        self.csv_file = csv_file
        self.raw_data = None
        self.calibrated_data = None
        self.calibration_matrix = None
        self.offset = None
        self.scale = None
        self.load_data()
    
    def load_data(self):
        """从CSV文件加载数据"""
        data = []
        with open(self.csv_file, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                try:
                    x = float(row[0])
                    y = float(row[1])
                    z = float(row[2])
                    data.append([x, y, z])
                except (ValueError, IndexError):
                    continue
        self.raw_data = np.array(data)
        print(f"✓ 已加载 {len(self.raw_data)} 个数据点")
    
    def calibrate_ellipsoid_fit(self):
        """
        使用椭球体拟合进行磁力计校准
        参考: https://github.com/kriswiner/MPU6050/wiki/Soft-Iron-and-Hard-Iron-calibration-of-IMU-and-IMU%2BMAG
        """
        print("\n开始磁力计校准...")
        
        # 提取X, Y, Z数据
        data = self.raw_data.copy()
        
        # 初始偏移估计 - 估计椭球的中心坐标
        offset_initial = np.mean(data, axis=0)
        
        # 椭球体参数：中心(cx, cy, cz)和半径(rx, ry, rz)
        # 我们要拟合: (x-cx)²/rx² + (y-cy)²/ry² + (z-cz)²/rz² = 1
        def residuals(params):
            """计算残差"""
            cx, cy, cz, rx, ry, rz = params
            numerator = (data[:, 0] - cx)**2 / (rx**2) + \
                       (data[:, 1] - cy)**2 / (ry**2) + \
                       (data[:, 2] - cz)**2 / (rz**2)
            return numerator - 1
        
        # 初始参数猜测 - 估计椭球的3个长轴长度
        rx = np.max(data[:, 0]) - np.min(data[:, 0]) 
        ry = np.max(data[:, 1]) - np.min(data[:, 1])
        rz = np.max(data[:, 2]) - np.min(data[:, 2])
        
        x0 = [offset_initial[0], offset_initial[1], offset_initial[2], 
              rx/2, ry/2, rz/2]
        
        # 最小二乘法拟合，目标是最小化残差向量的平方和：min Σ (残差)²
        result = least_squares(residuals, x0, max_nfev=5000)
        
        cx, cy, cz, rx, ry, rz = result.x
        
        # 硬铁校准（偏移）
        self.offset = np.array([cx, cy, cz])
        
        # 软铁校准（缩放和旋转）
        # 计算缩放因子，使所有轴的范围相同
        mean_radius = (rx + ry + rz) / 3
        self.scale = np.array([mean_radius/rx, mean_radius/ry, mean_radius/rz])
        
        # 构建校准矩阵（对角矩阵）
        self.calibration_matrix = np.diag(self.scale)
        
        print(f"✓ 校准完成")
        print(f"  硬铁偏移 (Hard Iron): [{cx:.2f}, {cy:.2f}, {cz:.2f}]")
        print(f"  软铁缩放 (Soft Iron Scale): [{self.scale[0]:.4f}, {self.scale[1]:.4f}, {self.scale[2]:.4f}]")
        print(f"  椭球体半径: rx={rx:.2f}, ry={ry:.2f}, rz={rz:.2f}")
        
        # 应用校准
        self.calibrated_data = (self.raw_data - self.offset) * self.scale
        
        return self.offset, self.scale
    
    def plot_3d_interactive(self):
        """使用Plotly创建交互式3D图表"""
        print("\n正在生成交互式3D图表...")
        
        fig = go.Figure()
        
        # 原始数据
        fig.add_trace(go.Scatter3d(
            x=self.raw_data[:, 0],
            y=self.raw_data[:, 1],
            z=self.raw_data[:, 2],
            mode='markers',
            marker=dict(
                size=4,
                color='blue',
                opacity=0.7,
                symbol='circle'
            ),
            name='原始数据',
            hovertemplate='X: %{x:.1f}<br>Y: %{y:.1f}<br>Z: %{z:.1f}<extra></extra>'
        ))
        
        # 校准后的数据
        if self.calibrated_data is not None:
            fig.add_trace(go.Scatter3d(
                x=self.calibrated_data[:, 0],
                y=self.calibrated_data[:, 1],
                z=self.calibrated_data[:, 2],
                mode='markers',
                marker=dict(
                    size=4,
                    color='red',
                    opacity=0.7,
                    symbol='diamond'
                ),
                name='校准后数据',
                hovertemplate='X: %{x:.1f}<br>Y: %{y:.1f}<br>Z: %{z:.1f}<extra></extra>'
            ))
        
        # 更新布局
        fig.update_layout(
            title='3轴磁力计数据 - 交互式3D视图<br><sub>可拖动、缩放和旋转</sub>',
            scene=dict(
                xaxis=dict(title='X轴 (μT)', showgrid=True),
                yaxis=dict(title='Y轴 (μT)', showgrid=True),
                zaxis=dict(title='Z轴 (μT)', showgrid=True),
                aspectmode='data'
            ),
            width=1000,
            height=800,
            hovermode='closest',
            showlegend=True
        )
        
        output_file = Path(self.csv_file).parent / '3d_interactive_plot.html'
        fig.write_html(str(output_file))
        print(f"✓ 3D交互式图表已保存: {output_file}")
    
    def plot_2d_projections(self):
        """绘制2D投影图"""
        print("\n正在生成2D投影图...")
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 12))
        fig.suptitle('磁力计3轴数据 - 2D投影图', fontsize=16, fontweight='bold')
        
        # X-Y投影
        ax = axes[0, 0]
        ax.scatter(self.raw_data[:, 0], self.raw_data[:, 1], 
                  label='原始数据', alpha=0.6, s=30, color='blue')
        if self.calibrated_data is not None:
            ax.scatter(self.calibrated_data[:, 0], self.calibrated_data[:, 1],
                      label='校准后数据', alpha=0.6, s=30, color='red', marker='^')
        ax.set_xlabel('X轴 (μT)', fontsize=11, fontweight='bold')
        ax.set_ylabel('Y轴 (μT)', fontsize=11, fontweight='bold')
        ax.set_title('X-Y平面投影')
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.axis('equal')
        
        # X-Z投影
        ax = axes[0, 1]
        ax.scatter(self.raw_data[:, 0], self.raw_data[:, 2],
                  label='原始数据', alpha=0.6, s=30, color='blue')
        if self.calibrated_data is not None:
            ax.scatter(self.calibrated_data[:, 0], self.calibrated_data[:, 2],
                      label='校准后数据', alpha=0.6, s=30, color='red', marker='^')
        ax.set_xlabel('X轴 (μT)', fontsize=11, fontweight='bold')
        ax.set_ylabel('Z轴 (μT)', fontsize=11, fontweight='bold')
        ax.set_title('X-Z平面投影')
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.axis('equal')
        
        # Y-Z投影
        ax = axes[1, 0]
        ax.scatter(self.raw_data[:, 1], self.raw_data[:, 2],
                  label='原始数据', alpha=0.6, s=30, color='blue')
        if self.calibrated_data is not None:
            ax.scatter(self.calibrated_data[:, 1], self.calibrated_data[:, 2],
                      label='校准后数据', alpha=0.6, s=30, color='red', marker='^')
        ax.set_xlabel('Y轴 (μT)', fontsize=11, fontweight='bold')
        ax.set_ylabel('Z轴 (μT)', fontsize=11, fontweight='bold')
        ax.set_title('Y-Z平面投影')
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.axis('equal')
        
        # 校准效果对比
        ax = axes[1, 1]
        if self.calibrated_data is not None:
            raw_magnitude = np.linalg.norm(self.raw_data, axis=1)
            cal_magnitude = np.linalg.norm(self.calibrated_data, axis=1)
            
            ax.plot(raw_magnitude, 'b-', label='原始数据幅值', alpha=0.7, linewidth=1)
            ax.plot(cal_magnitude, 'r-', label='校准后幅值', alpha=0.7, linewidth=1)
            ax.axhline(y=np.mean(cal_magnitude), color='r', linestyle='--', 
                      label=f'校准均值: {np.mean(cal_magnitude):.1f}')
            ax.set_xlabel('采样点', fontsize=11, fontweight='bold')
            ax.set_ylabel('幅值 (μT)', fontsize=11, fontweight='bold')
            ax.set_title('校准前后幅值对比')
            ax.grid(True, alpha=0.3)
            ax.legend()
        
        plt.tight_layout()
        output_file = Path(self.csv_file).parent / '2d_projections.png'
        plt.savefig(str(output_file), dpi=150, bbox_inches='tight')
        print(f"✓ 2D投影图已保存: {output_file}")
        plt.close()
    
    def plot_3d_matplotlib(self):
        """使用Matplotlib创建3D静态图表"""
        print("\n正在生成3D Matplotlib图表...")
        
        fig = plt.figure(figsize=(16, 6))
        
        # 原始数据3D图
        ax1 = fig.add_subplot(121, projection='3d')
        ax1.scatter(self.raw_data[:, 0], self.raw_data[:, 1], self.raw_data[:, 2],
                   c='blue', marker='o', s=20, alpha=0.6, label='原始数据')
        ax1.set_xlabel('X轴 (μT)')
        ax1.set_ylabel('Y轴 (μT)')
        ax1.set_zlabel('Z轴 (μT)')
        ax1.set_title('原始磁力计数据')
        ax1.legend()
        ax1.grid(True)
        
        # 校准后数据3D图
        if self.calibrated_data is not None:
            ax2 = fig.add_subplot(122, projection='3d')
            ax2.scatter(self.calibrated_data[:, 0], self.calibrated_data[:, 1], 
                       self.calibrated_data[:, 2],
                       c='red', marker='^', s=20, alpha=0.6, label='校准后数据')
            ax2.set_xlabel('X轴 (μT)')
            ax2.set_ylabel('Y轴 (μT)')
            ax2.set_zlabel('Z轴 (μT)')
            ax2.set_title('校准后磁力计数据')
            ax2.legend()
            ax2.grid(True)
        
        plt.tight_layout()
        output_file = Path(self.csv_file).parent / '3d_matplotlib.png'
        plt.savefig(str(output_file), dpi=150, bbox_inches='tight')
        print(f"✓ 3D Matplotlib图表已保存: {output_file}")
        plt.close()
    
    def save_calibration_matrix(self):
        """保存校准矩阵和参数"""
        print("\n正在保存校准参数...")
        
        calibration_data = {
            "offset_hard_iron": self.offset.tolist(),
            "scale_soft_iron": self.scale.tolist(),
            "calibration_matrix": self.calibration_matrix.tolist(),
            "description": "磁力计校准参数",
            "usage": "calibrated = (raw - offset) * scale"
        }
        
        output_file = Path(self.csv_file).parent / 'calibration_matrix.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(calibration_data, f, indent=2, ensure_ascii=False)
        
        print(f"✓ 校准矩阵已保存: {output_file}")
        
        # 同时打印详细信息
        print("\n" + "="*50)
        print("校准矩阵详细信息")
        print("="*50)
        print(f"硬铁补偿 (Hard Iron Offset):")
        print(f"  [{self.offset[0]:.4f}, {self.offset[1]:.4f}, {self.offset[2]:.4f}]")
        print(f"\n软铁缩放 (Soft Iron Scale):")
        print(f"  [{self.scale[0]:.6f}, {self.scale[1]:.6f}, {self.scale[2]:.6f}]")
        print(f"\n校准矩阵:")
        print(f"  [{self.calibration_matrix[0, 0]:.6f}    0.000000       0.000000   ]")
        print(f"  [   0.000000  {self.calibration_matrix[1, 1]:.6f}       0.000000   ]")
        print(f"  [   0.000000       0.000000  {self.calibration_matrix[2, 2]:.6f}   ]")
        print("\n使用公式: calibrated_data = (raw_data - offset) * scale")
        print("="*50 + "\n")
    
    def print_statistics(self):
        """打印数据统计信息"""
        print("\n" + "="*50)
        print("原始数据统计")
        print("="*50)
        for axis, name in enumerate(['X', 'Y', 'Z']):
            col_data = self.raw_data[:, axis]
            print(f"{name}轴:")
            print(f"  最小值: {np.min(col_data):>8.2f} μT")
            print(f"  最大值: {np.max(col_data):>8.2f} μT")
            print(f"  均值:   {np.mean(col_data):>8.2f} μT")
            print(f"  标准差: {np.std(col_data):>8.2f} μT")
        
        raw_magnitude = np.linalg.norm(self.raw_data, axis=1)
        print(f"\n总幅值:")
        print(f"  最小值: {np.min(raw_magnitude):>8.2f} μT")
        print(f"  最大值: {np.max(raw_magnitude):>8.2f} μT")
        print(f"  均值:   {np.mean(raw_magnitude):>8.2f} μT")
        print(f"  标准差: {np.std(raw_magnitude):>8.2f} μT")
        
        if self.calibrated_data is not None:
            print("\n" + "="*50)
            print("校准后数据统计")
            print("="*50)
            for axis, name in enumerate(['X', 'Y', 'Z']):
                col_data = self.calibrated_data[:, axis]
                print(f"{name}轴:")
                print(f"  最小值: {np.min(col_data):>8.2f} μT")
                print(f"  最大值: {np.max(col_data):>8.2f} μT")
                print(f"  均值:   {np.mean(col_data):>8.2f} μT")
                print(f"  标准差: {np.std(col_data):>8.2f} μT")
            
            cal_magnitude = np.linalg.norm(self.calibrated_data, axis=1)
            print(f"\n总幅值:")
            print(f"  最小值: {np.min(cal_magnitude):>8.2f} μT")
            print(f"  最大值: {np.max(cal_magnitude):>8.2f} μT")
            print(f"  均值:   {np.mean(cal_magnitude):>8.2f} μT")
            print(f"  标准差: {np.std(cal_magnitude):>8.2f} μT")
        
        print("="*50 + "\n")


def main():
    # 数据文件路径
    csv_file = Path(__file__).resolve().parent / 'data' / 'magnetometer_data.csv'
    
    print("="*50)
    print("3轴磁力计数据处理和校准工具")
    print("="*50)
    
    # 创建校准对象
    mag = MagnetometerCalibration(str(csv_file))
    
    # 打印原始数据统计
    mag.print_statistics()
    
    # 进行校准
    mag.calibrate_ellipsoid_fit()
    
    # 打印校准后统计
    mag.print_statistics()
    
    # 生成可视化
    mag.plot_3d_interactive()
    mag.plot_2d_projections()
    mag.plot_3d_matplotlib()
    
    # 保存校准矩阵
    mag.save_calibration_matrix()
    
    print("✓ 所有处理完成！")
    print("\n生成的文件:")
    print("  1. 3d_interactive_plot.html - 交互式3D图表（可拖动）")
    print("  2. 2d_projections.png - 2D投影图和效果对比")
    print("  3. 3d_matplotlib.png - 3D静态图表")
    print("  4. calibration_matrix.json - 校准参数")


if __name__ == "__main__":
    main()
