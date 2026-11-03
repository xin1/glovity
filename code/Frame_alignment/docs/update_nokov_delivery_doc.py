"""Refresh the existing partner-facing DOCX for the verified 30 Hz delivery."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


DOCUMENT = Path(__file__).parent / "Glovity_NOKOV_V1.0_20261023" / "01_Documents" / "Glovity_NOKOV基站接入使用与测试说明_V1.0.docx"


def set_paragraph_text(paragraph, value: str) -> None:
    existing = next((run for run in reversed(paragraph.runs) if run.text), None)
    properties = deepcopy(existing._element.rPr) if existing is not None and existing._element.rPr is not None else None
    paragraph.clear()
    run = paragraph.add_run(value)
    if properties is not None:
        run._element.insert(0, properties)


def set_cell_text(cell, value: str) -> None:
    set_paragraph_text(cell.paragraphs[0], value)
    for paragraph in cell.paragraphs[1:]:
        paragraph.clear()


document = Document(DOCUMENT)
paragraph_updates = {
    0: "Glovity NOKOV基站接入与测试说明",
    3: "本说明用于Glovity手套与NOKOV基站的控制接口和数据接收软件接入。NOKOV基站通过REC控制录制区间，通过OUTPUT_EN控制普通IMU数据是否输出；手套经ESP-NOW将数据发送至配套接收器，由接收器通过USB虚拟串口交付采集主机。四针控制接口不传输IMU串行数据。",
    4: "本次交付已完成单只左手套的USB直连、ESP-NOW无线、REC状态、OUTPUT_EN门控及30 Hz持续10分钟测试。右手套和双手同时运行尚未实测。",
    8: "NOKOV基站 REC OUTPUT_EN GND\n                 │\n                 ▼\nGlovity 手套 → ESP-NOW 2.4 GHz → 配套接收器 → USB → NOKOV基站采集主机",
    50: "timestamp_us是每只手套自己的本地计时，不是UTC、NOKOV基站采样时刻、接收器到达时间或基站主机时间。",
    56: "2026年10月22日至23日，单只左手套与配套接收器完成以下测试。持续测试使用30 Hz左手固件和v2.3接收器；结果仅覆盖本次样机与所列条件，不能扩大为右手、双手或所有电气及无线环境的性能承诺。",
    60: "IMU物理有效更新率和姿态精度。",
    61: "复杂2.4 GHz干扰、接收器拔插、USB背压极限和队列满载。",
    64: "左右手与NOKOV基站之间的精确时间同步。",
    65: "8 NOKOV基站联调验收建议",
    67: "联调记录建议同时保存原始180字节帧、frame_seq、timestamp_us、recording、imu_fail_mask、接收端单调到达时间以及NOKOV基站REC操作时间。",
    76: "本版默认每只手30 Hz。右手配对、双手联合运行及最终线束电气条件仍需另行验收。",
}
for index, value in paragraph_updates.items():
    set_paragraph_text(document.paragraphs[index], value)

metadata = document.tables[0]
set_cell_text(metadata.cell(2, 1), "2026年10月23日")
set_cell_text(metadata.cell(3, 1), "NOKOV基站硬件、固件及采集软件开发人员")
set_cell_text(metadata.cell(4, 1), "左手套一只、v2.3接收器一只、默认30 Hz、180字节V2普通帧")

test_table = document.tables[6]
test_row = next((row for row in test_table.rows if "单手30 Hz持续运行" in row.cells[0].text), None)
if test_row is None:
    row_xml = deepcopy(test_table.rows[-1]._tr)
    test_table._tbl.append(row_xml)
    test_row = test_table.rows[-1]
for cell, value in zip(
    test_row.cells,
    (
        "单手30 Hz持续运行",
        "双串口抓取约10分钟；手套USB与接收器稳定区间各18001帧且序号连续；重叠17990帧逐字节一致。接收器统计入队18001帧、队列溢出0。",
        "通过；启动和串口旧缓存不计入稳定区间",
    ),
):
    set_cell_text(cell, value)

for paragraph in document.paragraphs:
    text = paragraph.text
    replacements = {
        "合作方控制器": "NOKOV基站",
        "合作方软件": "NOKOV基站采集软件",
        "合作方设备": "NOKOV基站设备",
        "合作方": "NOKOV基站",
        "相机": "NOKOV基站",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text != paragraph.text:
        set_paragraph_text(paragraph, text)

for table in document.tables:
    for row in table.rows:
        for cell in row.cells:
            text = cell.text.replace("合作方", "NOKOV基站").replace("相机", "NOKOV基站")
            if text != cell.text:
                set_cell_text(cell, text)

for section in document.sections:
    for paragraph in section.footer.paragraphs:
        if paragraph.text.strip():
            set_paragraph_text(paragraph, "Glovity NOKOV基站接入与测试说明  V1.0")

document.core_properties.title = "Glovity NOKOV基站接入与测试说明"
document.save(DOCUMENT)
print(DOCUMENT)
