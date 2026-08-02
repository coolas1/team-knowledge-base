"""生成测试用噪声 PDF：技术方案文档（中文）"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import Color, grey, lightgrey
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# 注册中文字体
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

OUTPUT_DIR = r"D:\knowledge-vault\test-dirty"
os.makedirs(OUTPUT_DIR, exist_ok=True)

WIDTH, HEIGHT = A4
FONT = "STSong-Light"


def draw_header(c, page_num):
    """每页重复的页眉噪声"""
    c.saveState()
    c.setFont(FONT, 8)
    c.setFillColor(grey)
    c.drawString(2 * cm, HEIGHT - 1.2 * cm, "文档编号：TECH-2024-0892 | 密级：内部公开 | 版本：V2.3")
    c.drawRightString(WIDTH - 2 * cm, HEIGHT - 1.2 * cm, "星辰科技有限公司")
    c.line(2 * cm, HEIGHT - 1.4 * cm, WIDTH - 2 * cm, HEIGHT - 1.4 * cm)
    c.restoreState()


def draw_footer(c, page_num, total=10):
    """每页重复的页脚噪声"""
    c.saveState()
    c.setFont(FONT, 8)
    c.setFillColor(grey)
    c.drawCentredString(WIDTH / 2, 1.2 * cm, f"第 {page_num} 页 共 {total} 页")
    c.drawString(2 * cm, 1.2 * cm, "【星辰科技 内部资料 请勿外传】")
    c.drawRightString(WIDTH - 2 * cm, 1.2 * cm, "2024-05-15 打印")
    c.restoreState()


def draw_watermark(c):
    """斜置水印噪声"""
    c.saveState()
    c.setFont(FONT, 36)
    c.setFillColor(Color(0.85, 0.85, 0.85, alpha=0.3))
    c.translate(WIDTH / 2, HEIGHT / 2)
    c.rotate(45)
    c.drawCentredString(0, 0, "星辰科技 内部公开")
    c.drawCentredString(0, -80, "XINGCHEN TECH")
    c.restoreState()


def new_page(c, page_num, total=10):
    c.showPage()
    draw_watermark(c)
    draw_header(c, page_num)
    draw_footer(c, page_num, total)
    return HEIGHT - 3 * cm  # 返回内容起始 y


def main():
    filepath = os.path.join(OUTPUT_DIR, "技术方案-数据中台建设.pdf")
    c = canvas.Canvas(filepath, pagesize=A4)
    c.setTitle("数据中台建设技术方案")
    c.setAuthor("星辰科技技术委员会")

    total_pages = 10

    # === 第 1 页：封面 ===
    draw_watermark(c)
    draw_header(c, 1)
    draw_footer(c, 1, total_pages)

    y = HEIGHT - 6 * cm
    c.setFont(FONT, 28)
    c.drawCentredString(WIDTH / 2, y, "数据中台建设技术方案")
    y -= 2 * cm
    c.setFont(FONT, 14)
    c.drawCentredString(WIDTH / 2, y, "（V2.3 最终定稿）")
    y -= 3 * cm
    c.setFont(FONT, 12)
    info_lines = [
        "编制部门：技术架构部",
        "项目负责人：王建国",
        "编制日期：2024年1月15日",
        "最后修订：2024年5月8日",
        "",
        "文档编号：TECH-2024-0892",
        "密级：内部公开",
        "版权所有 © 2024 星辰科技有限公司",
        "本文档包含星辰科技内部机密信息，未经授权不得复制、传播或披露。",
    ]
    for line in info_lines:
        c.drawCentredString(WIDTH / 2, y, line)
        y -= 0.7 * cm

    # === 第 2 页：修订历史 + 审批栏（纯模板噪声） ===
    y = new_page(c, 2, total_pages)
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "修订历史")
    y -= 1 * cm
    c.setFont(FONT, 10)
    revisions = [
        ("V1.0", "2024-01-15", "王建国", "初稿创建"),
        ("V1.1", "2024-02-03", "王建国", "补充数据治理章节"),
        ("V1.2", "2024-02-28", "李明辉", "修改技术选型部分"),
        ("V2.0", "2024-03-20", "李明辉", "架构重构，引入湖仓一体方案"),
        ("V2.1", "2024-04-11", "李明辉", "根据架构委员会评审意见修改"),
        ("V2.2", "2024-04-25", "张薇", "补充安全合规章节"),
        ("V2.3", "2024-05-08", "张薇", "最终定稿，提交审批"),
    ]
    c.drawString(2 * cm, y, "版本        日期            修订人      修订内容")
    y -= 0.5 * cm
    c.line(2 * cm, y, WIDTH - 2 * cm, y)
    y -= 0.5 * cm
    for ver, date, author, desc in revisions:
        c.drawString(2 * cm, y, f"{ver}      {date}    {author}    {desc}")
        y -= 0.6 * cm

    y -= 1.5 * cm
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "审批栏")
    y -= 1 * cm
    c.setFont(FONT, 10)
    approvals = [
        ("技术负责人", "陈志远", "（已签字）", "2024-05-10"),
        ("架构委员会主任", "刘洋", "（已签字）", "2024-05-12"),
        ("信息安全部", "周磊", "（已签字）", "2024-05-13"),
        ("CTO", "赵鹏飞", "（已签字）", "2024-05-15"),
    ]
    c.drawString(2 * cm, y, "角色              姓名        签字          日期")
    y -= 0.5 * cm
    c.line(2 * cm, y, WIDTH - 2 * cm, y)
    y -= 0.5 * cm
    for role, name, sign, date in approvals:
        c.drawString(2 * cm, y, f"{role}      {name}    {sign}    {date}")
        y -= 0.6 * cm

    y -= 2 * cm
    c.setFont(FONT, 9)
    c.drawString(2 * cm, y, "免责声明：本文档仅供星辰科技内部使用，不构成任何商业承诺或合同依据。")
    y -= 0.5 * cm
    c.drawString(2 * cm, y, "文档中涉及的技术方案可能随项目进展调整，最终以实际实施为准。")
    y -= 0.5 * cm
    c.drawString(2 * cm, y, "未经书面授权，任何第三方不得引用、复制或传播本文档的任何部分。")

    # === 第 3 页：目录（TOC 噪声） ===
    y = new_page(c, 3, total_pages)
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "目  录")
    y -= 1.2 * cm
    c.setFont(FONT, 11)
    toc = [
        "1. 项目背景 ................................................ 4",
        "2. 需求分析 ................................................ 5",
        "   2.1 业务现状 ............................................ 5",
        "   2.2 核心痛点 ............................................ 5",
        "   2.3 建设目标 ............................................ 6",
        "3. 总体架构设计 ............................................ 7",
        "   3.1 架构分层 ............................................ 7",
        "   3.2 数据流向 ............................................ 8",
        "4. 技术选型 ................................................ 9",
        "   4.1 存储引擎 ............................................ 9",
        "   4.2 计算引擎 ............................................ 10",
        "   4.3 调度系统 ............................................ 10",
        "5. 数据治理体系 ............................................ 11",
        "6. 安全与合规 .............................................. 12",
        "7. 实施计划与预算 .......................................... 13",
        "附录A：术语表 .............................................. 14",
        "附录B：技术选型对比详表 .................................... 15",
    ]
    for item in toc:
        c.drawString(2.5 * cm, y, item)
        y -= 0.65 * cm

    # === 第 4 页：正文开始 - 项目背景 ===
    y = new_page(c, 4, total_pages)
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "1. 项目背景")
    y -= 1.2 * cm
    c.setFont(FONT, 11)

    paras = [
        "随着公司业务的快速增长，各业务线数据孤岛问题日益严重。当前公司共有12个核心业务系统，",
        "包括ERP、CRM、WMS、MES、OA等，各系统独立建设，数据格式不统一，接口标准不一致。",
        "",
        "根据2023年度数据资产盘点报告，公司现有数据总量约850TB，其中结构化数据占比62%，",
        "半结构化数据占比28%，非结构化数据占比10%。数据分散在各个系统中，缺乏统一的管理和",
        "利用手段。",
        "",
        "当前存在以下核心痛点：",
        "（1）数据分散在12个业务系统中，格式不统一，无法进行跨系统关联分析；",
        "（2）报表开发周期长（平均2-3周），无法支撑管理层的快速决策需求；",
        "（3）数据质量参差不齐，同一指标在不同系统中口径不一致，导致\"数出多门\"；",
        "（4）数据安全风险分散，各系统各自管理权限，缺乏统一的审计和管控能力。",
        "",
        "本项目旨在建设统一的数据中台，实现数据的采集、存储、计算、治理、服务一体化，",
        "支撑公司数字化转型战略。详细需求参见《数据中台需求规格说明书》（文档编号：",
        "REQ-2024-0156）。",
    ]
    for line in paras:
        if y < 3 * cm:
            y = new_page(c, 5, total_pages)
            c.setFont(FONT, 11)
        c.drawString(2 * cm, y, line)
        y -= 0.6 * cm

    # === 第 5 页：需求分析 ===
    y -= 1 * cm
    if y < 5 * cm:
        y = new_page(c, 5, total_pages)
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "2. 需求分析")
    y -= 1 * cm
    c.setFont(FONT, 13)
    c.drawString(2 * cm, y, "2.1 业务现状")
    y -= 0.9 * cm
    c.setFont(FONT, 11)

    paras2 = [
        "当前数据架构为典型的\"烟囱式\"架构，各业务系统各自维护独立的数据库：",
        "- ERP系统：Oracle 19c，数据量约120TB",
        "- CRM系统：MySQL 8.0，数据量约45TB",
        "- WMS系统：SQL Server 2019，数据量约80TB",
        "- MES系统：PostgreSQL 14，数据量约200TB",
        "- OA系统：MySQL 5.7，数据量约15TB",
        "- 其他系统：各类数据库，合计约390TB",
        "",
        "各系统间通过点对点接口（共47个）进行数据交换，接口维护成本高，",
        "数据一致性无法保证。",
    ]
    for line in paras2:
        if y < 3 * cm:
            y = new_page(c, 6, total_pages)
            c.setFont(FONT, 11)
        c.drawString(2 * cm, y, line)
        y -= 0.6 * cm

    # === 第 6 页：建设目标 + 架构图占位 ===
    y = new_page(c, 6, total_pages)
    c.setFont(FONT, 13)
    c.drawString(2 * cm, y, "2.2 建设目标")
    y -= 1 * cm
    c.setFont(FONT, 11)
    goals = [
        "（1）统一数据底座：建设湖仓一体的数据存储与计算平台；",
        "（2）统一数据标准：建立企业级数据标准体系和元数据管理；",
        "（3）统一数据服务：通过API网关对外提供标准化数据服务；",
        "（4）统一数据安全：实现细粒度权限管控和全链路审计；",
        "（5）降低用数门槛：报表开发周期从2-3周缩短至1-2天。",
    ]
    for line in goals:
        c.drawString(2 * cm, y, line)
        y -= 0.6 * cm

    y -= 1.5 * cm
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "3. 总体架构设计")
    y -= 1 * cm
    c.setFont(FONT, 11)
    c.drawString(2 * cm, y, "系统总体架构如下图所示：")
    y -= 0.8 * cm

    # 模拟图片占位框
    c.saveState()
    c.setStrokeColor(lightgrey)
    c.setFillColor(Color(0.95, 0.95, 0.95))
    c.rect(3 * cm, y - 6 * cm, WIDTH - 6 * cm, 6 * cm, fill=1)
    c.setFillColor(grey)
    c.setFont(FONT, 12)
    c.drawCentredString(WIDTH / 2, y - 3 * cm, "（图 3-1：数据中台总体架构图）")
    c.drawCentredString(WIDTH / 2, y - 3.6 * cm, "[图片未能提取]")
    c.restoreState()
    y -= 7 * cm

    c.setFont(FONT, 11)
    c.drawString(2 * cm, y, "如图3-1所示，数据中台总体架构分为五层：数据采集层、数据存储层、")
    y -= 0.6 * cm
    c.drawString(2 * cm, y, "数据计算层、数据治理层和数据服务层。各层之间通过标准化接口通信。")

    # === 第 7 页：架构分层详述 ===
    y = new_page(c, 7, total_pages)
    c.setFont(FONT, 13)
    c.drawString(2 * cm, y, "3.1 架构分层说明")
    y -= 1 * cm
    c.setFont(FONT, 11)
    layers = [
        "数据采集层：",
        "  支持批量采集（T+1，基于DataX）和实时采集（CDC，基于Flink CDC）两种模式。",
        "  批量采集覆盖Oracle、MySQL、SQL Server、PostgreSQL等主流数据源。",
        "  实时采集通过监听数据库binlog实现，延迟<5秒。",
        "",
        "数据存储层：",
        "  采用湖仓一体架构。数据湖基于Apache Iceberg格式，存储原始明细数据；",
        "  数据仓库基于StarRocks，存储加工后的汇总指标数据。",
        "  对象存储（MinIO）用于存放非结构化数据和Iceberg底层文件。",
        "",
        "数据计算层：",
        "  离线计算：Apache Spark 3.5，用于ETL加工和复杂分析；",
        "  实时计算：Apache Flink 1.18，用于实时指标计算和CEP；",
        "  统一调度：Apache DolphinScheduler，管理所有任务依赖和执行。",
        "",
        "数据治理层：",
        "  元数据管理：Apache Atlas，自动采集血缘关系；",
        "  数据质量：自研DQC规则引擎，支持完整性/一致性/时效性检查；",
        "  数据标准：统一指标定义、维度编码、命名规范。",
        "",
        "数据服务层：",
        "  统一API网关（基于Kong），对外提供RESTful数据查询服务；",
        "  支持SQL查询、指标查询、明细下载三种服务模式；",
        "  内置限流、熔断、鉴权能力。",
    ]
    for line in layers:
        if y < 3 * cm:
            y = new_page(c, 8, total_pages)
            c.setFont(FONT, 11)
        c.drawString(2 * cm, y, line)
        y -= 0.55 * cm

    # === 第 8 页：技术选型 ===
    y = new_page(c, 8, total_pages)
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "4. 技术选型")
    y -= 1 * cm
    c.setFont(FONT, 13)
    c.drawString(2 * cm, y, "4.1 存储引擎选型")
    y -= 0.9 * cm
    c.setFont(FONT, 11)
    c.drawString(2 * cm, y, "经过对主流数据湖格式的全面评估（详见附录B），最终选择Apache Iceberg：")
    y -= 0.8 * cm

    # 表格
    table_data = [
        ("特性", "Iceberg", "Hudi", "Delta Lake"),
        ("ACID事务", "完整支持", "支持", "支持"),
        ("Schema演进", "完整支持(加/删/改列)", "部分支持", "支持"),
        ("时间旅行", "支持", "支持", "支持"),
        ("引擎兼容性", "Spark/Flink/Trino/Presto", "Spark/Flink/Hive", "Spark为主"),
        ("分区演进", "支持(无需重写数据)", "不支持", "不支持"),
        ("社区活跃度", "Apache顶级,多厂商贡献", "Apache顶级", "Databricks主导"),
    ]
    col_x = [2 * cm, 5 * cm, 9 * cm, 13 * cm]
    for i, row in enumerate(table_data):
        if i == 0:
            c.setFont(FONT, 10)
            c.line(2 * cm, y - 0.1 * cm, WIDTH - 2 * cm, y - 0.1 * cm)
        else:
            c.setFont(FONT, 9)
        for j, cell in enumerate(row):
            c.drawString(col_x[j], y, cell)
        y -= 0.6 * cm
        if i == 0:
            c.line(2 * cm, y + 0.2 * cm, WIDTH - 2 * cm, y + 0.2 * cm)
    c.line(2 * cm, y + 0.2 * cm, WIDTH - 2 * cm, y + 0.2 * cm)

    y -= 1 * cm
    c.setFont(FONT, 11)
    c.drawString(2 * cm, y, "选型结论：Iceberg的引擎兼容性最优，支持多引擎并发读写，且不绑定单一计算")
    y -= 0.6 * cm
    c.drawString(2 * cm, y, "引擎，符合公司\"避免厂商锁定\"的技术战略要求。")

    # === 第 9 页：实施计划 ===
    y = new_page(c, 9, total_pages)
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "7. 实施计划与预算")
    y -= 1.2 * cm
    c.setFont(FONT, 11)
    c.drawString(2 * cm, y, "项目分三期实施，总工期约12个月：")
    y -= 0.9 * cm

    plan = [
        ("阶段", "时间", "核心目标", "预算"),
        ("一期", "2024 Q3", "基础平台搭建，核心5系统接入", "180万"),
        ("二期", "2024 Q4", "数据治理体系，API服务上线", "160万"),
        ("三期", "2025 Q1", "全量接入，智能分析应用", "140万"),
    ]
    col_x2 = [2 * cm, 4 * cm, 7 * cm, 14 * cm]
    for i, row in enumerate(plan):
        if i == 0:
            c.setFont(FONT, 10)
            c.line(2 * cm, y - 0.1 * cm, WIDTH - 2 * cm, y - 0.1 * cm)
        else:
            c.setFont(FONT, 10)
        for j, cell in enumerate(row):
            c.drawString(col_x2[j], y, cell)
        y -= 0.65 * cm
    c.line(2 * cm, y + 0.2 * cm, WIDTH - 2 * cm, y + 0.2 * cm)

    y -= 1.2 * cm
    c.setFont(FONT, 11)
    budget_lines = [
        "预算明细（总计480万元）：",
        "  - 硬件及云资源：200万元（含服务器、存储、网络扩容）",
        "  - 软件许可：150万元（含StarRocks企业版、Kong企业版、监控平台）",
        "  - 实施服务：130万元（含外部咨询、定制开发、培训）",
        "",
        "注：以上预算不含内部人力成本。如需计入人力，按15人×12个月估算，",
        "内部人力成本约为270万元（按人均1.5万/月计算）。",
    ]
    for line in budget_lines:
        c.drawString(2 * cm, y, line)
        y -= 0.6 * cm

    # === 第 10 页：附录 + 尾部噪声 ===
    y = new_page(c, 10, total_pages)
    c.setFont(FONT, 16)
    c.drawString(2 * cm, y, "附录A：术语表")
    y -= 1 * cm
    c.setFont(FONT, 10)
    terms = [
        ("CDC", "Change Data Capture，变更数据捕获"),
        ("湖仓一体", "Lakehouse，数据湖与数据仓库融合的架构范式"),
        ("DQC", "Data Quality Center，数据质量中心"),
        ("OLAP", "Online Analytical Processing，联机分析处理"),
        ("ETL", "Extract-Transform-Load，数据抽取转换加载"),
        ("CEP", "Complex Event Processing，复杂事件处理"),
        ("binlog", "Binary Log，MySQL数据库变更日志"),
        ("API网关", "统一的服务入口，提供路由、鉴权、限流等能力"),
    ]
    for term, desc in terms:
        c.drawString(2.5 * cm, y, f"{term}：{desc}")
        y -= 0.6 * cm

    y -= 2 * cm
    c.setFont(FONT, 9)
    c.setFillColor(grey)
    footer_noise = [
        "————————————————————————————————————————",
        "本文档由星辰科技技术架构部编制，最终解释权归技术委员会所有。",
        "文档编号：TECH-2024-0892 | 密级：内部公开 | 版本：V2.3",
        "版权所有 © 2024 星辰科技有限公司 保留一切权利",
        "如需获取本文档最新版本，请访问内部文档管理系统（DMS）下载。",
        "打印副本仅供参考，以DMS系统中的电子版为准。",
    ]
    for line in footer_noise:
        c.drawString(2 * cm, y, line)
        y -= 0.5 * cm

    c.save()
    print(f"已生成: {filepath}")
    print(f"文件大小: {os.path.getsize(filepath) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
