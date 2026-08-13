"""测试点击全文按钮"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.adb_helper import ADBHelper

adb = ADBHelper(adb_path=r"C:\Program Files\Netease\MuMu\nx_main\adb.exe")
xml = adb.dump_ui()
btns = adb.find_elements_by_text(xml, "全文")
btns = [b for b in btns if "查看" not in b["text"] and "收起" not in b["text"]]
print(f"找到 {len(btns)} 个全文按钮:")
for b in btns:
    print(f"  text=[{b['text']}] center={b['center']}")
    adb.tap(b["center"][0], b["center"][1])
    print(f"  已点击 {b['center']}")
    adb.human_delay(1.5, 2.5)
print("点击完成,重新 dump 确认展开效果")
xml2 = adb.dump_ui()
btns2 = adb.find_elements_by_text(xml2, "全文")
btns2 = [b for b in btns2 if "查看" not in b["text"] and "收起" not in b["text"]]
print(f"展开后剩余 {len(btns2)} 个全文按钮(应为0或减少)")
