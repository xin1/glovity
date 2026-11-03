from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.enum.style import WD_STYLE_TYPE


OUT = r"D:\Desktop\github\glovity\code\Frame_alignment\Glovity_手套合作方接入使用与测试说明_V1.0.docx"


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=100, start=110, bottom=100, end=110):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcMar = tcPr.first_child_found_in("w:tcMar")
    if tcMar is None:
        tcMar = OxmlElement("w:tcMar")
        tcPr.append(tcMar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tcMar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tcMar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def set_repeat_table_header(row):
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement("w:tblHeader")
    tblHeader.set(qn("w:val"), "true")
    trPr.append(tblHeader)


def set_repeat_table_header(row):
    trPr = row._tr.get_or_add_trPr()
    existing = trPr.find(qn("w:tblHeader"))
    if existing is None:
        existing = OxmlElement("w:tblHeader")
        trPr.append(existing)
    existing.set(qn("w:val"), "true")


def prevent_row_split(row):
    trPr = row._tr.get_or_add_trPr()
    if trPr.find(qn("w:cantSplit")) is None:
        trPr.append(OxmlElement("w:cantSplit"))


def set_cell_width(cell, cm):
    tcPr = cell._tc.get_or_add_tcPr()
    tcW = tcPr.find(qn("w:tcW"))
    if tcW is None:
        tcW = OxmlElement("w:tcW")
        tcPr.append(tcW)
    tcW.set(qn("w:w"), str(int(cm * 567)))
    tcW.set(qn("w:type"), "dxa")


def font_run(run, size=10.5, bold=False, color="000000", name="Microsoft YaHei"):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def add_text(p, text, bold=False, size=10.5, color="000000"):
    r = p.add_run(text)
    font_run(r, size=size, bold=bold, color=color)
    return r


def set_para(p, before=0, after=6, line=1.25, keep=False):
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = line
    pf.keep_with_next = keep


def add_para(doc, text="", bold_lead=None, style=None, after=6):
    p = doc.add_paragraph(style=style)
    set_para(p, after=after)
    if bold_lead and text.startswith(bold_lead):
        add_text(p, bold_lead, bold=True)
        add_text(p, text[len(bold_lead):])
    else:
        add_text(p, text)
    return p


def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    set_para(p, after=3)
    add_text(p, text)
    return p


def add_number(doc, text):
    p = doc.add_paragraph(style="List Number")
    set_para(p, after=4)
    add_text(p, text)
    return p


def add_code(doc, lines):
    p = doc.add_paragraph()
    set_para(p, before=2, after=8, line=1.05)
    p.paragraph_format.left_indent = Cm(0.6)
    for i, line in enumerate(lines):
        if i:
            p.add_run().add_break()
        r = p.add_run(line)
        font_run(r, size=9, name="Consolas")
        r._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    return p


def add_table(doc, headers, rows, widths=None, small=False):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.style = "Table Grid"
    hdr = table.rows[0]
    set_repeat_table_header(hdr)
    prevent_row_split(hdr)
    for i, text in enumerate(headers):
        cell = hdr.cells[i]
        set_cell_shading(cell, "1F4E78")
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        set_cell_margins(cell)
        if widths:
            set_cell_width(cell, widths[i])
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_para(p, after=0, line=1.1)
        add_text(p, text, bold=True, size=9 if small else 9.5, color="FFFFFF")
    for ri, row in enumerate(rows):
        new_row = table.add_row()
        prevent_row_split(new_row)
        cells = new_row.cells
        for i, text in enumerate(row):
            cell = cells[i]
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            set_cell_margins(cell)
            if widths:
                set_cell_width(cell, widths[i])
            if ri % 2 == 1:
                set_cell_shading(cell, "F3F7FA")
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if i == 0 and len(headers) > 2 else WD_ALIGN_PARAGRAPH.LEFT
            set_para(p, after=0, line=1.15)
            add_text(p, str(text), size=8.5 if small else 9.2)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return table


doc = Document()
sec = doc.sections[0]
sec.top_margin = Cm(2.2)
sec.bottom_margin = Cm(2.0)
sec.left_margin = Cm(2.25)
sec.right_margin = Cm(2.25)

styles = doc.styles
normal = styles["Normal"]
normal.font.name = "Microsoft YaHei"
normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
normal.font.size = Pt(10.5)
normal.font.color.rgb = RGBColor(0, 0, 0)
normal.paragraph_format.space_after = Pt(6)
normal.paragraph_format.line_spacing = 1.25

for style_name, size, before, after in (("Title", 23, 0, 14), ("Heading 1", 16, 14, 8), ("Heading 2", 12.5, 10, 5), ("Heading 3", 11, 8, 4)):
    st = styles[style_name]
    st.font.name = "Microsoft YaHei"
    st._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    st.font.size = Pt(size)
    st.font.bold = True
    st.font.color.rgb = RGBColor(0, 0, 0)
    st.paragraph_format.space_before = Pt(before)
    st.paragraph_format.space_after = Pt(after)
    st.paragraph_format.keep_with_next = True

title_pPr = styles["Title"]._element.get_or_add_pPr()
title_pBdr = title_pPr.find(qn("w:pBdr"))
if title_pBdr is not None:
    title_pPr.remove(title_pBdr)

for list_name in ("List Bullet", "List Bullet 2", "List Number"):
    st = styles[list_name]
    st.font.name = "Microsoft YaHei"
    st._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    st.font.size = Pt(10.5)

# Cover
p = doc.add_paragraph(style="Title")
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(70)
# Remove the built-in Word Title style border so the title remains plain.
pPr = p._p.get_or_add_pPr()
pBdr = pPr.find(qn("w:pBdr"))
if pBdr is not None:
    pPr.remove(pBdr)
add_text(p, "Glovity 手套合作方接入使用与测试说明", bold=True, size=23)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
set_para(p, after=24)
add_text(p, "控制接口  无线接收  数据协议  联调验收", size=12)

add_table(doc, ["文档项目", "内容"], [
    ("文档版本", "V1.0"),
    ("发布日期", "2026年10月22日"),
    ("适用对象", "合作方硬件、固件及上位机开发人员"),
    ("当前验证配置", "左手套一只、ESP-NOW接收器一只、默认30 Hz、180字节V2普通帧"),
], widths=[4.0, 11.2])

add_para(doc, "本说明用于完成Glovity手套与合作方控制器及接收软件的接入。合作方通过REC控制录制区间，通过OUTPUT_EN控制普通IMU数据是否输出；IMU数据由手套经ESP-NOW发送到配套接收器，再由接收器通过USB虚拟串口交付上位机。四针控制接口不承载IMU串行数据。")
add_para(doc, "本次交付已完成单只左手套的USB直连、ESP-NOW无线、REC状态和OUTPUT_EN门控测试。双手同时工作、100 Hz、长时间压力、电气保护和精确跨设备同步不在本次已验证范围内。", bold_lead="本次交付已完成")

doc.add_page_break()

doc.add_heading("1 系统组成与数据路径", level=1)
add_para(doc, "交付系统由手套、四针控制线、ESP-NOW接收器和上位机SDK组成。每只手套应使用独立四针接口；左右手的3V3_REF不得直接并联。")
add_code(doc, [
    "合作方控制器 REC OUTPUT_EN GND",
    "                 │",
    "                 ▼",
    "Glovity 手套 → ESP-NOW 2.4 GHz → 配套接收器 → USB → 合作方软件",
])
add_para(doc, "无线链路使用ESP-NOW，不是BLE。合作方无需通过操作系统蓝牙配对；手套与接收器使用预先写入固件的STA MAC地址进行配对。")

doc.add_heading("2 四针控制接口", level=1)
add_table(doc, ["脚号", "信号", "方向", "定义与要求"], [
    ("1", "3V3_REF", "手套输出参考", "受控3.3 V参考及预留。合作方仅允许高阻检测，不得反向供电，也不得作为持续外部电源或就绪信号。"),
    ("2", "OUTPUT_EN", "输入到手套", "高或释放时允许普通IMU数据输出；低时停止提交新的普通帧。建议合作方使用开漏输出。"),
    ("3", "REC", "输入到手套", "3.3 V高表示录制，低表示停止。必须作为持续状态电平使用。"),
    ("4", "GND", "公共参考", "双方必须共地，不是可省略的预留脚。"),
], widths=[1.3, 2.7, 3.0, 8.2])
add_para(doc, "脚号以连接器焊盘编号为准，不得按照片左右方向或线色判断。制线和交付前应由双方确认Pin 1标识、插头观察方向、端子料号以及线束是否交叉。")

doc.add_heading("2.1 电气条件", level=2)
add_bullet(doc, "所有控制信号按3.3 V逻辑设计，禁止接入5 V、12 V或24 V。")
add_bullet(doc, "REC由合作方主动驱动接近0 V或3.3 V。REC悬空时由手套侧下拉为低，不表示录制。")
add_bullet(doc, "OUTPUT_EN建议使用开漏或等效只拉低输出。释放时由手套侧约10 kΩ上拉为高，拉低电流约0.33 mA。")
add_bullet(doc, "手套断电但合作方控制器仍上电时，应避免REC或OUTPUT_EN经保护结构向手套倒灌。建议采用断电高阻驱动或具备断电保护的缓冲器。")
add_bullet(doc, "3V3_REF可能随原IR电源控制而关闭，只用于参考或预留，不得给合作方设备供电。")

doc.add_heading("2.2 控制状态", level=2)
add_table(doc, ["OUTPUT_EN", "REC", "手套内部行为", "普通数据输出"], [
    ("高或释放", "低", "正常采集和计时", "输出；recording=0 或 False"),
    ("高或释放", "高", "正常采集和计时", "输出；recording=1 或 True"),
    ("低", "任意", "继续采集、计时、递增帧号并跟踪REC", "停止提交新普通帧；不保存待补发历史"),
], widths=[2.7, 2.0, 5.2, 5.3])
add_para(doc, "协议中的recording=0与SDK中的recording=False含义相同；recording=1与recording=True含义相同。")

doc.add_heading("3 推荐使用流程", level=1)
doc.add_heading("3.1 上电与连接", level=2)
add_number(doc, "确认控制器输出为3.3 V逻辑，连接GND、REC和OUTPUT_EN。3V3_REF如不使用，应单独绝缘。")
add_number(doc, "将配套接收器通过USB数据线连接上位机。")
add_number(doc, "给手套供电。手套与接收器无需BLE配对，已配对固件会自动建立ESP-NOW链路。")
add_number(doc, "保持REC为低，释放OUTPUT_EN，等待上位机收到有效V2帧。")
add_number(doc, "开始录制时持续拉高REC；结束时拉低REC，并继续接收，直到观察到recording=0或False的有效帧。")
add_number(doc, "如需停止普通数据输出，在确认结束帧后拉低OUTPUT_EN。恢复时释放OUTPUT_EN；恢复后只输出新帧，不补发停发期间的数据。")

doc.add_heading("3.2 上位机快速验证", level=2)
add_para(doc, "以下示例在Windows PowerShell中运行。将COM53替换为设备管理器中接收器对应的实际端口。USB虚拟串口的baud参数用于接口兼容，本次测试使用921600。")
add_code(doc, [
    r".\.venv\Scripts\python.exe .\TeleX-SDK\examples\monitor_recording.py `",
    r"  --port COM53 `",
    r"  --baud 921600",
])
add_para(doc, "正常输出示例：")
add_code(doc, [
    "frames=217 hand=left protocol=v2-180 seq=1869 timestamp_us=63559949 recording=True battery=36% fail_mask=0",
    "frames=275 hand=left protocol=v2-180 seq=1927 timestamp_us=65493271 recording=False battery=38% fail_mask=0",
])
add_table(doc, ["字段", "检查方法"], [
    ("protocol", "应为v2-180。"),
    ("hand", "left表示左手，right表示右手。"),
    ("seq", "正常连续输出时持续递增；uint16按65536回绕。"),
    ("timestamp_us", "手套本轮读取开始的本地微秒计时；重启后重新起算。"),
    ("recording", "REC低为False，REC高为True。"),
    ("battery", "0至100的缓存电量值，短时存在ADC波动。"),
    ("fail_mask", "0表示11组姿态读取均成功；非0时对应bit为失败IMU，不应使用该IMU姿态。"),
], widths=[3.2, 12.0])

doc.add_heading("4 REC与OUTPUT_EN行为", level=1)
doc.add_heading("4.1 REC帧归属", level=2)
add_para(doc, "手套在每轮IMU读取开始时取得REC快照，并把状态写入该帧flags bit0。若REC在一帧读取过程中改变，已经开始的帧保持原状态，后续新开始读取的帧采用新状态。默认30 Hz时，名义帧间隔约33.33 ms。短于帧间隔的脉冲可能不会体现在任何普通帧中，因此REC必须使用持续电平。")
add_para(doc, "REC只标记录制区间，不会清零或暂停frame_seq与timestamp_us，也不代表两只手套或相机已经完成硬件同步。")

doc.add_heading("4.2 OUTPUT_EN边界", level=2)
add_para(doc, "OUTPUT_EN拉低后，手套停止提交新的普通IMU数据，但内部继续采集、计时、递增frame_seq并读取REC。已经进入无线、USB或接收器下游缓冲的数据仍可能形成少量尾帧，因此不承诺拉低瞬间零字节。")
add_para(doc, "OUTPUT_EN恢复为高后，从当前新采集帧恢复输出，不补发禁止期间的数据。接收端应看到frame_seq和timestamp_us自然跳过停发区间。管理、状态、错误和校准消息不属于普通IMU数据，不能据此假设OUTPUT_EN低时整条链路绝对静默。")

doc.add_heading("5 180字节V2普通帧", level=1)
add_para(doc, "一帧对应一只手，固定180字节，多字节整数为小端序，int16为有符号二进制补码。无线应用载荷与手套USB普通帧采用同一格式。")
add_table(doc, ["偏移", "字段", "字节", "定义"], [
    ("0至1", "magic", "2", "固定AA 55"),
    ("2", "version", "1", "固定02"),
    ("3", "hand_id", "1", "00左手，01右手"),
    ("4", "flags", "1", "bit0 recording；bit1手腕角速度有效；其余为0"),
    ("5", "battery_percent", "1", "0至100，缓存电量"),
    ("6至7", "frame_seq", "2", "uint16，每轮读取递增，按65536回绕"),
    ("8至15", "timestamp_us", "8", "uint64，本轮读取开始的手套本地时间，单位微秒"),
    ("16至17", "imu_fail_mask", "2", "bit0至10对应11颗IMU失败位；bit11至15为0"),
    ("18至171", "imu[11][7]", "154", "每颗IMU依次为Roll Pitch Yaw Q0 Q1 Q2 Q3"),
    ("172至177", "wrist_gyro[3]", "6", "Gx Gy Gz"),
    ("178至179", "crc16", "2", "CRC覆盖字节0至177，低字节在前"),
], widths=[2.0, 3.2, 1.5, 8.5], small=True)
add_para(doc, "CRC使用CRC-16/CCITT-FALSE：多项式0x1021，初值0xFFFF，不反射，最终异或0x0000。接收端应搜索AA 55、确认version=02、等待180字节并校验CRC和字段范围。USB是字节流，单次读取可能得到半帧、多帧或任意分段，不能按一次read等于一帧处理。")

doc.add_heading("5.1 数值换算", level=2)
add_table(doc, ["内容", "换算"], [
    ("欧拉角", "原始int16 ÷ 32768 × 180，单位度"),
    ("四元数", "原始int16 ÷ 32768，顺序w x y z"),
    ("手腕角速度", "原始int16 ÷ 32768 × 2000，单位度每秒"),
    ("失败IMU", "imu_fail_mask对应位为1时，该IMU的7个值无效，不得作为正常姿态使用"),
], widths=[4.0, 11.2])

doc.add_heading("6 时间戳与对齐边界", level=1)
add_bullet(doc, "timestamp_us是每只手套自己的本地计时，不是UTC、相机曝光时刻、接收器到达时间或合作方控制器时间。")
add_bullet(doc, "REC和OUTPUT_EN变化均不重置timestamp_us；设备重启或重新上电后本地计时重新起算。")
add_bullet(doc, "左右手的本地时间起点和时钟彼此独立，不能直接相减作为同步误差。")
add_bullet(doc, "首个recording=1帧只能用于录制分段，不能反推出REC物理上升沿的精确时刻。")
add_bullet(doc, "如需毫秒级或更严格的跨设备对齐，需要另行定义同步观测、时钟映射、误差预算和验收方法。")

doc.add_heading("7 本次交付测试记录", level=1)
add_para(doc, "以下为2026年10月22日对单只左手套和配套接收器进行的阶段性验证。结果说明本次样机在所列条件下通过，不扩大为双手、100 Hz、长期运行或所有电气环境的性能承诺。")
add_table(doc, ["测试项目", "方法与观察", "结果"], [
    ("USB直连与协议", "手套USB连接SDK，持续解析固定180字节V2帧。", "通过"),
    ("默认帧率", "相邻timestamp_us约33,333微秒，约30 Hz。", "通过"),
    ("REC低与高", "STM32 PA0每5秒输出0 V和约3.3 V；SDK观察recording在False和True之间切换。", "通过"),
    ("REC不重置", "REC切换期间seq和timestamp_us持续递增，未清零，普通数据持续输出。", "通过"),
    ("IMU有效性", "稳定运行阶段fail_mask=0，11组姿态读取正常。", "通过"),
    ("单手无线", "左手套经ESP-NOW发送至接收器，再由接收器COM口进入SDK；frames 217至666与seq 1869至2318均增加449。", "通过；该观察区间未见丢帧"),
    ("OUTPUT_EN门控", "拉低后输出停止，恢复后继续；停发前seq 333、timestamp 12,351,192，恢复观察到seq 615、timestamp 21,751,086。", "通过；内部计时与序号继续，未补发历史"),
    ("停发期间跟踪REC", "停发前seq 2672且recording=True；恢复时seq 2798且recording=False。", "通过"),
], widths=[3.0, 9.6, 2.6], small=True)

doc.add_heading("7.1 尚未纳入本次已验证范围", level=2)
add_bullet(doc, "右手套以及左右手同时无线输出。")
add_bullet(doc, "50 Hz和100 Hz配置、持续吞吐和IMU有效更新率。")
add_bullet(doc, "长时间压力、复杂2.4 GHz干扰、接收器拔插、USB背压和队列满载。")
add_bullet(doc, "REC上电前已高、帧号65535回绕以及全部临界边沿组合。")
add_bullet(doc, "线缆长度、ESD、热插拔、单方断电、防倒灌和双手3V3_REF隔离。")
add_bullet(doc, "左右手、相机或NOKOV之间的精确时间同步。")

doc.add_heading("8 合作方联调验收建议", level=1)
add_table(doc, ["序号", "验收项", "预期结果", "记录"], [
    ("1", "接收器USB识别", "出现稳定COM口，SDK可打开", "□通过  □不通过"),
    ("2", "默认数据流", "OUTPUT_EN释放且REC低时持续收到v2-180，recording=False", "□通过  □不通过"),
    ("3", "REC拉高", "后续新帧为recording=True；seq和timestamp不清零", "□通过  □不通过"),
    ("4", "REC拉低", "后续新帧为recording=False；数据继续输出", "□通过  □不通过"),
    ("5", "OUTPUT_EN拉低", "允许少量尾帧，随后普通帧停止", "□通过  □不通过"),
    ("6", "OUTPUT_EN恢复", "恢复当前新帧；seq和时间跳过停发区间；不补发历史", "□通过  □不通过"),
    ("7", "数据质量", "正常条件fail_mask=0；非0位对应数据不作为有效姿态", "□通过  □不通过"),
    ("8", "断电状态", "无异常发热、倒灌或错误供电", "□通过  □不通过"),
], widths=[1.2, 4.0, 7.3, 2.7], small=True)
add_para(doc, "联调记录建议同时保存原始180字节帧、frame_seq、timestamp_us、recording、imu_fail_mask、接收端单调到达时间以及合作方控制器REC操作时间。")

doc.add_heading("9 故障排查", level=1)
add_table(doc, ["现象", "优先检查"], [
    ("程序只显示Listening且无帧", "确认端口是接收器、串口未被Arduino监视器占用、手套已供电、配对固件与MAC匹配。"),
    ("能收到帧但REC不变化", "检查REC是否接到Pin 3、双方是否共地、合作方是否主动输出0 V和3.3 V、是否误将高阻当作高电平。"),
    ("OUTPUT_EN拉低仍有少量帧", "可能是在途或下游缓存尾帧；持续观察是否停止，不以拉低瞬间零字节为验收条件。"),
    ("恢复后seq跳变", "这是停发期间内部仍在采集的预期表现；确认没有批量补发历史帧。"),
    ("fail_mask非0", "按bit0至10定位对应IMU，检查供电、I2C连线、地址和连接器；对应姿态不得当作有效数据。"),
    ("seq和timestamp同时变小", "可能发生手套重启或重新上电；建立新会话，并排除接收器残留旧帧或无线乱序。"),
], widths=[5.1, 10.1], small=True)

doc.add_heading("10 交付与安全事项", level=1)
add_bullet(doc, "每只手套提供独立SH1.0 4P或双方确认的等效防呆接口，并随货提供Pin 1方向图和线序表。")
add_bullet(doc, "四针接口只用于控制和参考，不包含TX或RX；IMU数据必须通过手套USB或配套ESP-NOW接收器USB读取。")
add_bullet(doc, "不得使用3V3_REF给外部设备供电，不得将左右手3V3_REF直接并联。")
add_bullet(doc, "不得向REC、OUTPUT_EN或3V3_REF施加超过3.3 V逻辑范围的电压。")
add_bullet(doc, "合作方在正式设备上应确认线缆长度、信号完整性、ESD、防倒灌、热插拔和单方断电行为。")
add_bullet(doc, "本版默认30 Hz。100 Hz仅作为后续目标，在完成双手链路、有效更新率和长时间压力验收前，不作为已交付性能。")

doc.add_heading("附录 A 本次配对信息", level=1)
add_table(doc, ["设备", "端口示例", "STA MAC", "说明"], [
    ("左手套", "COM16", "8C:FD:49:88:EB:68", "hand_id=0；V2手套固件"),
    ("接收器", "COM53", "14:C1:9F:2E:79:98", "接收并转发左手V2帧"),
], widths=[3.0, 2.5, 4.4, 5.3])
add_para(doc, "COM端口号由Windows动态分配，仅为本次测试示例。更换电脑、USB口或驱动后可能变化，应按设备管理器实际端口使用。配对依赖STA MAC，不依赖COM号。")

# Footer with page number field
footer = sec.footer
fp = footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
set_para(fp, before=5, after=0)
add_text(fp, "Glovity 手套合作方接入使用与测试说明  V1.0    第 ", size=8.5, color="666666")
run = fp.add_run()
fldChar1 = OxmlElement("w:fldChar")
fldChar1.set(qn("w:fldCharType"), "begin")
instrText = OxmlElement("w:instrText")
instrText.set(qn("xml:space"), "preserve")
instrText.text = " PAGE "
fldChar2 = OxmlElement("w:fldChar")
fldChar2.set(qn("w:fldCharType"), "end")
run._r.extend([fldChar1, instrText, fldChar2])
font_run(run, size=8.5, color="666666")
add_text(fp, " 页", size=8.5, color="666666")

doc.core_properties.title = "Glovity 手套合作方接入使用与测试说明"
doc.core_properties.subject = "控制接口 无线接收 数据协议 联调验收"
doc.core_properties.author = "Glovity"
doc.core_properties.keywords = "Glovity, REC, OUTPUT_EN, ESP-NOW, V2-180"
doc.save(OUT)
print(OUT)
