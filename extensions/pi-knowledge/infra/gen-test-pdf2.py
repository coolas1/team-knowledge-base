"""生成测试用噪声 PDF：邮件转发链打印件（中文）"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import Color, grey, lightgrey, HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

OUTPUT_DIR = r"D:\knowledge-vault\test-dirty"
os.makedirs(OUTPUT_DIR, exist_ok=True)

WIDTH, HEIGHT = A4
FONT = "STSong-Light"
BLUE = HexColor("#1a56db")
DARK = HexColor("#333333")
GREY_TEXT = HexColor("#666666")


def draw_print_header(c, page_num):
    """浏览器/Outlook 打印邮件时自动加的页眉"""
    c.saveState()
    c.setFont(FONT, 7)
    c.setFillColor(grey)
    c.drawString(1.5 * cm, HEIGHT - 1 * cm, "发件人: 张薇 <zhangwei@xingchen-tech.com>")
    c.drawRightString(WIDTH - 1.5 * cm, HEIGHT - 1 * cm, "2024/6/18 09:32")
    c.line(1.5 * cm, HEIGHT - 1.15 * cm, WIDTH - 1.5 * cm, HEIGHT - 1.15 * cm)
    c.restoreState()


def draw_print_footer(c, page_num):
    """打印页脚"""
    c.saveState()
    c.setFont(FONT, 7)
    c.setFillColor(grey)
    c.drawCentredString(WIDTH / 2, 1 * cm, f"第 {page_num} 页，共 4 页")
    c.restoreState()


def new_page(c, page_num, total=4):
    c.showPage()
    draw_print_header(c, page_num)
    draw_print_footer(c, page_num)
    return HEIGHT - 2.5 * cm


def draw_signature(c, y, name, title, dept, phone, email):
    """邮件签名档噪声"""
    c.setFont(FONT, 9)
    c.setFillColor(DARK)
    c.drawString(2 * cm, y, "此致")
    y -= 0.45 * cm
    c.drawString(2 * cm, y, "敬礼！")
    y -= 0.7 * cm
    c.setFillColor(BLUE)
    c.drawString(2 * cm, y, name)
    y -= 0.5 * cm
    c.setFillColor(DARK)
    c.setFont(FONT, 8)
    lines = [
        f"{title} | {dept}",
        f"星辰科技有限公司",
        f"电话: {phone} | 邮箱: {email}",
        f"地址: 北京市海淀区中关村科技园区创新大厦18层",
        f"网址: www.xingchen-tech.com",
    ]
    for line in lines:
        c.drawString(2 * cm, y, line)
        y -= 0.4 * cm
    y -= 0.3 * cm
    # 法律免责声明
    c.setFont(FONT, 6.5)
    c.setFillColor(grey)
    disclaimer = [
        "【重要声明】本邮件及其附件可能包含机密或特权信息，仅供指定的收件人使用。",
        "如果您不是本邮件的指定收件人，请立即通知发件人并删除本邮件。任何未经授权的",
        "披露、复制、分发或基于本邮件内容的行为都是严格禁止的。星辰科技有限公司不对本",
        "邮件传输过程中可能产生的错误或遗漏承担任何责任。",
    ]
    for line in disclaimer:
        c.drawString(2 * cm, y, line)
        y -= 0.32 * cm
    return y - 0.5 * cm


def draw_quote_header(c, y, from_name, from_email, date, to_name):
    """转发引用头"""
    c.setFont(FONT, 9)
    c.setFillColor(GREY_TEXT)
    c.drawString(2.5 * cm, y, "──────────────────────────────────────────────")
    y -= 0.5 * cm
    c.drawString(2.5 * cm, y, f"发件人: {from_name} <{from_email}>")
    y -= 0.45 * cm
    c.drawString(2.5 * cm, y, f"发送时间: {date}")
    y -= 0.45 * cm
    c.drawString(2.5 * cm, y, f"收件人: {to_name}")
    y -= 0.45 * cm
    c.drawString(2.5 * cm, y, "主题: 回复: 回复: 回复: 回复: Re: Q3数据迁移方案确认")
    y -= 0.5 * cm
    c.drawString(2.5 * cm, y, "──────────────────────────────────────────────")
    y -= 0.6 * cm
    return y


def main():
    filepath = os.path.join(OUTPUT_DIR, "邮件-Q3数据迁移方案确认.pdf")
    c = canvas.Canvas(filepath, pagesize=A4)
    c.setTitle("Re: Re: Re: Re: Q3数据迁移方案确认")

    # === 第 1 页：最新一封（真正有用的内容只有几行） ===
    draw_print_header(c, 1)
    draw_print_footer(c, 1)
    y = HEIGHT - 2.5 * cm

    # 邮件头
    c.setFont(FONT, 10)
    c.setFillColor(DARK)
    headers = [
        ("发件人：", "张薇 <zhangwei@xingchen-tech.com>"),
        ("发送时间：", "2024年6月18日 09:32"),
        ("收件人：", "王建国 <wangjianguo@xingchen-tech.com>; 李明辉 <liminghui@xingchen-tech.com>"),
        ("抄送：", "陈志远 <chenzhiyuan@xingchen-tech.com>; 数据中台项目组 <data-platform@xingchen-tech.com>"),
        ("主题：", "回复: 回复: 回复: 回复: Re: Q3数据迁移方案确认"),
        ("重要性：", "高"),
    ]
    for label, value in headers:
        c.setFont(FONT, 9)
        c.setFillColor(GREY_TEXT)
        c.drawString(2 * cm, y, label)
        c.setFillColor(DARK)
        c.drawString(4 * cm, y, value)
        y -= 0.55 * cm

    y -= 0.3 * cm
    c.line(2 * cm, y, WIDTH - 2 * cm, y)
    y -= 0.7 * cm

    # 正文（真正有用的内容）
    c.setFont(FONT, 11)
    c.setFillColor(DARK)
    body = [
        "王总、明辉：",
        "",
        "确认Q3数据迁移方案如下：",
        "",
        "1. 迁移时间窗口：7月15日（周一）22:00 - 7月16日（周二）06:00",
        "2. 迁移范围：ERP核心库（Oracle → PostgreSQL），涉及表约320张",
        "3. 回滚方案：保留Oracle只读副本至8月15日，期间发现严重问题可回切",
        "4. 验证标准：迁移后全量数据比对，差异率需<0.01%",
        "5. 责任人：李明辉负责技术执行，张薇负责业务验证，王建国总协调",
        "",
        "请各方在今天下班前回复确认。如有异议请电话沟通。",
        "",
        "另外提醒：迁移当晚需要DBA值班，请明辉提前安排。",
    ]
    for line in body:
        c.drawString(2 * cm, y, line)
        y -= 0.6 * cm

    y -= 0.5 * cm
    # 签名档
    y = draw_signature(c, y, "张薇", "高级项目经理", "技术架构部", "+86-10-8888-6666", "zhangwei@xingchen-tech.com")

    # === 第 2 页：第一层引用（上一封回复） ===
    y = new_page(c, 2)
    y = draw_quote_header(c, y, "王建国", "wangjianguo@xingchen-tech.com",
                          "2024年6月17日 16:45", "张薇; 李明辉")

    c.setFont(FONT, 10)
    c.setFillColor(DARK)
    quote1 = [
        "张薇：",
        "",
        "方案整体没问题，补充两点：",
        "1. 迁移前需要做一次全量备份，建议用pg_dump逻辑备份+物理备份双保险",
        "2. 7月15日那个窗口我和运维确认过了，当晚没有批处理任务，可以用",
        "",
        "预算方面，Oracle许可到明年3月才到期，迁移完也不能马上退，这个成本",
        "需要跟财务说明一下。",
    ]
    for line in quote1:
        c.drawString(2.5 * cm, y, line)
        y -= 0.55 * cm

    y -= 0.4 * cm
    y = draw_signature(c, y, "王建国", "技术总监", "技术架构部", "+86-10-8888-5555", "wangjianguo@xingchen-tech.com")

    # 第二层引用
    y -= 0.3 * cm
    y = draw_quote_header(c, y, "李明辉", "liminghui@xingchen-tech.com",
                          "2024年6月17日 14:20", "王建国; 张薇")

    c.setFont(FONT, 10)
    c.setFillColor(DARK)
    quote2 = [
        "王总：",
        "",
        "迁移脚本已经测试完毕，在测试环境跑了3轮，耗时约5.5小时。",
        "主要风险点：",
        "- 有12张表包含CLOB字段，需要用特殊工具迁移（已解决）",
        "- 3个存储过程需要手工改写为PG函数（预计2天工作量）",
        "- 序列（sequence）的当前值需要在切换时重新同步",
        "",
        "详细方案见附件《ERP数据迁移技术方案V1.2.pdf》。",
    ]
    for line in quote2:
        if y < 3 * cm:
            y = new_page(c, 3)
            c.setFont(FONT, 10)
            c.setFillColor(DARK)
        c.drawString(2.5 * cm, y, line)
        y -= 0.55 * cm

    y -= 0.4 * cm
    y = draw_signature(c, y, "李明辉", "高级数据库工程师", "基础架构组", "+86-10-8888-7777", "liminghui@xingchen-tech.com")

    # === 第 3 页：更深层引用 ===
    y = new_page(c, 3)
    y = draw_quote_header(c, y, "张薇", "zhangwei@xingchen-tech.com",
                          "2024年6月16日 10:05", "王建国; 李明辉; 陈志远")

    c.setFont(FONT, 10)
    c.setFillColor(DARK)
    quote3 = [
        "各位：",
        "",
        "根据上周架构委员会的决议，Q3需要完成ERP核心库从Oracle到PostgreSQL的",
        "迁移。请明辉出一版迁移方案，下周一前发出来评审。",
        "",
        "背景说明：公司决定逐步去Oracle化，ERP是第一个试点。主要原因是Oracle",
        "许可费每年380万，迁移后预计年节省250万+。",
        "",
        "时间要求：9月底前完成全部迁移和验证。",
    ]
    for line in quote3:
        c.drawString(2.5 * cm, y, line)
        y -= 0.55 * cm

    y -= 0.4 * cm
    y = draw_signature(c, y, "张薇", "高级项目经理", "技术架构部", "+86-10-8888-6666", "zhangwei@xingchen-tech.com")

    # 第四层引用
    y -= 0.3 * cm
    y = draw_quote_header(c, y, "陈志远", "chenzhiyuan@xingchen-tech.com",
                          "2024年6月14日 09:00", "全体技术管理层")

    c.setFont(FONT, 9)
    c.setFillColor(DARK)
    quote4 = [
        "各位同事：",
        "",
        "经公司管理层研究决定，启动\"去Oracle化\"专项。请各部门配合：",
        "1. 技术架构部：制定整体迁移路线图",
        "2. 各业务线：配合梳理系统依赖",
        "3. 财务部：核算许可费节省预期",
        "",
        "此事已列入2024年度OKR，请高度重视。",
    ]
    for line in quote4:
        if y < 3 * cm:
            y = new_page(c, 4)
            c.setFont(FONT, 9)
            c.setFillColor(DARK)
        c.drawString(2.5 * cm, y, line)
        y -= 0.5 * cm

    y -= 0.4 * cm
    y = draw_signature(c, y, "陈志远", "技术副总裁", "技术中心", "+86-10-8888-0001", "chenzhiyuan@xingchen-tech.com")

    # === 第 4 页：最底层引用 + 大量尾部噪声 ===
    y = new_page(c, 4)
    y = draw_quote_header(c, y, "系统管理员", "admin@xingchen-tech.com",
                          "2024年6月13日 08:00", "全体")

    c.setFont(FONT, 9)
    c.setFillColor(DARK)
    quote5 = [
        "【IT通知】",
        "",
        "各位同事好：",
        "邮件系统将于本周六（6月15日）02:00-04:00进行升级维护。",
        "届时邮件收发将暂停约2小时，请提前做好准备。",
        "如有紧急事务请通过企业微信联系。",
        "",
        "IT服务台",
    ]
    for line in quote5:
        c.drawString(2.5 * cm, y, line)
        y -= 0.5 * cm

    y -= 0.4 * cm
    y = draw_signature(c, y, "IT服务台", "信息技术部", "运维组", "+86-10-8888-9999", "it-helpdesk@xingchen-tech.com")

    # 尾部打印噪声
    y -= 1 * cm
    c.setFont(FONT, 7)
    c.setFillColor(grey)
    tail_noise = [
        "────────────────────────────────────────────────────────────────────",
        "本邮件通过星辰科技企业邮箱系统发送。系统自动添加此免责声明。",
        "邮件追踪ID: XC-2024-0618-093215-8847231",
        "反垃圾邮件评分: 0.2/10 (正常)",
        "DKIM签名: 已验证通过",
        "SPF检查: Pass (sender IP: 203.156.78.90)",
        "加密状态: TLS 1.3 已启用",
        "",
        "如果您收到了这封邮件但并非预期收件人，请立即联系 it-helpdesk@xingchen-tech.com",
        "并永久删除此邮件及其所有附件。根据《中华人民共和国网络安全法》和《数据安全法》，",
        "未经授权查阅、复制、传播他人电子邮件属于违法行为。",
        "",
        "星辰科技有限公司 | 北京市海淀区中关村科技园区创新大厦18层 | 邮编: 100080",
        "统一社会信用代码: 91110108MA01XXXX | 电话: 010-8888-0000 | 传真: 010-8888-0001",
    ]
    for line in tail_noise:
        if y < 2 * cm:
            break
        c.drawString(1.5 * cm, y, line)
        y -= 0.35 * cm

    c.save()
    print(f"已生成: {filepath}")
    print(f"文件大小: {os.path.getsize(filepath) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
