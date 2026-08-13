"""诊断:打印所有含'全文'字样的节点完整属性"""
import sys
import xml.etree.ElementTree as ET

path = sys.argv[1] if len(sys.argv) > 1 else r"d:\TraeProject\Pytest_Framework\screenshots\diag\ui_dump_full2.xml"
root = ET.parse(path).getroot()
print("=== 含'全文'字样的所有节点 ===")
for n in root.iter("node"):
    text = n.attrib.get("text", "")
    desc = n.attrib.get("content-desc", "")
    if "全文" in text or "全文" in desc:
        cls = n.attrib.get("class", "").split('.')[-1]
        rid = n.attrib.get("resource-id", "")
        b = n.attrib.get("bounds", "")
        click = n.attrib.get("clickable", "")
        print(f"clickable={click} text=[{text}] desc=[{desc}] cls={cls} rid={rid} bounds={b}")
