"""诊断:打印顶部区域所有 node 的完整属性(含 resource-id)"""
import xml.etree.ElementTree as ET

root = ET.parse(r"d:\TraeProject\Pytest_Framework\screenshots\diag\ui_dump_top.xml").getroot()
# 打印 y < 500 的所有节点的完整属性
for n in root.iter("node"):
    b = n.attrib.get("bounds", "")
    try:
        y1 = int(b.replace("][", ",").strip("[]").split(",")[1])
    except Exception:
        continue
    if y1 > 500:
        continue
    text = n.attrib.get("text", "")
    desc = n.attrib.get("content-desc", "")
    rid = n.attrib.get("resource-id", "")
    cls = n.attrib.get("class", "")
    print(f"y={y1:>4} cls={cls.split('.')[-1]:<20} rid={rid:<45} text={text[:30]:<30} desc={desc[:30]}")
